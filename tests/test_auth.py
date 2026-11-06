"""Tests for login and for what each account can reach. These use the real access rules,
unlike the other test files, which log in with tests.factories.AllAccess."""

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from rhu import accounts, auth
from rhu.main import create_app
from tests.factories import make_batch, wait_for_stored

OWN = "BRGY-08"
OTHER = "BRGY-01"
OWN_STATION = f"{OWN}-MAIN"
OTHER_STATION = f"{OTHER}-MAIN"
PUBLIC = {"/api/health", "/api/config", "/api/barangays", "/api/auth/login", "/api/auth/logout"}


@pytest.fixture
def server(tmp_path):
    with TestClient(create_app(tmp_path / "test.db", analytics_workers=1, analytics_interval=3600)) as c:
        yield c


def password_of(client, username):
    return accounts.read_passwords(client.app.state.db_path)[username]


def log_in(client, username):
    client.cookies.clear()
    res = client.post("/api/auth/login", json={"username": username, "password": password_of(client, username)})
    assert res.status_code == 200
    return res


def wrong_login(client, username=OWN):
    return client.post("/api/auth/login", json={"username": username, "password": "wrong-password"})


def register(client, node_id, barangay_id):
    return client.post("/api/nodes/register", json={"type": "REGISTER", "node_id": node_id, "barangay_id": barangay_id})


def closed_with(client, url):
    with client.websocket_connect(url) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    return closed.value.code


# ---- accounts and passwords ------------------------------------------------------------

def test_every_barangay_has_an_account_plus_rhu_and_load_test(server):
    usernames = accounts.read_passwords(server.app.state.db_path).keys()
    assert len(usernames) == 28
    assert {"RHU", "LOADTEST", OWN} <= usernames


def test_passwords_are_not_in_the_database(server):
    plain = password_of(server, OWN)
    log_in(server, OWN)
    with closing(sqlite3.connect(server.app.state.db_path)) as conn:
        dump = "\n".join(conn.iterdump())
    assert plain not in dump
    assert server.cookies[auth.COOKIE] not in dump  # nor the login token


def test_each_account_gets_its_own_password(server):
    passwords = list(accounts.read_passwords(server.app.state.db_path).values())
    assert len(set(passwords)) == len(passwords)


def test_restarting_keeps_passwords_and_logins(tmp_path):
    db_path = tmp_path / "test.db"
    with TestClient(create_app(db_path, analytics_workers=1, analytics_interval=3600)) as first:
        before = accounts.read_passwords(db_path)
        log_in(first, OWN)
        token = first.cookies[auth.COOKIE]
    with TestClient(create_app(db_path, analytics_workers=1, analytics_interval=3600)) as second:
        assert accounts.read_passwords(db_path) == before
        second.cookies.set(auth.COOKIE, token)
        assert second.get("/api/auth/me").json()["username"] == OWN


def test_reset_changes_the_password_and_logs_out_devices(server):
    old = password_of(server, OWN)
    other_before = password_of(server, OTHER)
    log_in(server, OWN)

    new = accounts.reset_password(server.app.state.db_path, OWN)

    assert server.get("/api/auth/me").status_code == 401
    assert server.post("/api/auth/login", json={"username": OWN, "password": old}).status_code == 401
    assert server.post("/api/auth/login", json={"username": OWN, "password": new}).status_code == 200
    assert password_of(server, OWN) == new
    assert password_of(server, OTHER) == other_before


def test_resetting_an_unknown_account_fails(server):
    with pytest.raises(KeyError):
        accounts.reset_password(server.app.state.db_path, "NOBODY")


# ---- logging in and out ----------------------------------------------------------------

def test_login_tells_the_page_where_to_go(server):
    assert log_in(server, OWN).json() == {"username": OWN, "role": "BHU", "barangay_id": OWN, "home": "/node/"}
    assert log_in(server, "RHU").json() == {"username": "RHU", "role": "RHU", "barangay_id": None, "home": "/dashboard/"}


def test_login_cookie_cannot_be_read_by_scripts(server):
    cookie = log_in(server, OWN).headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


@pytest.mark.parametrize("username", [OWN, "NOBODY"])
def test_wrong_login_says_the_same_thing(server, username):
    res = wrong_login(server, username)
    assert res.status_code == 401
    assert res.json()["detail"] == "Wrong health unit or password."


def test_another_accounts_password_does_not_work(server):
    res = server.post("/api/auth/login", json={"username": OWN, "password": password_of(server, OTHER)})
    assert res.status_code == 401


def test_too_many_wrong_passwords_lock_the_login(server):
    for _ in range(auth.MAX_FAILED_LOGINS):
        assert wrong_login(server).status_code == 401

    locked = wrong_login(server)
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    right = {"username": OWN, "password": password_of(server, OWN)}
    assert server.post("/api/auth/login", json=right).status_code == 429  # even the right one, for now
    assert wrong_login(server, OTHER).status_code == 401  # other accounts are not affected


def test_lock_ends_after_the_wait(server, monkeypatch):
    for _ in range(auth.MAX_FAILED_LOGINS):
        wrong_login(server)
    now = auth.time.monotonic()
    monkeypatch.setattr(auth.time, "monotonic", lambda: now + auth.LOCKOUT_SECONDS + 1)
    log_in(server, OWN)


def test_a_successful_login_clears_the_failures(server):
    for _ in range(auth.MAX_FAILED_LOGINS - 1):
        wrong_login(server)
    log_in(server, OWN)
    assert wrong_login(server).status_code == 401


def test_logout_ends_the_session_for_good(server):
    log_in(server, OWN)
    token = server.cookies[auth.COOKIE]
    assert server.post("/api/auth/logout").status_code == 200

    server.cookies.set(auth.COOKIE, token)  # someone kept a copy of the cookie
    assert server.get("/api/auth/me").status_code == 401


def test_expired_session_is_refused(server):
    log_in(server, OWN)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="milliseconds")
    with closing(sqlite3.connect(server.app.state.db_path)) as conn, conn:
        conn.execute("UPDATE sessions SET expires_at = ?", (past,))
    assert server.get("/api/auth/me").status_code == 401


def test_made_up_token_is_refused(server):
    server.cookies.set(auth.COOKIE, "made-up")
    assert server.get("/api/auth/me").status_code == 401


# ---- nothing is open by accident -------------------------------------------------------

def test_every_api_route_needs_a_login_unless_listed_as_public(server):
    """Also protects routes added later: a new one fails here until it has a rule."""
    checked = 0
    for path, operations in server.get("/openapi.json").json()["paths"].items():
        if path in PUBLIC:
            continue
        url = path.format(
            barangay_id=OWN, client_id="x", consultation_id="x",
            batch_id="00000000-0000-4000-8000-000000000000", alert_id="x",
        )
        for method in operations:
            assert server.request(method, url).status_code == 401, f"{method} {url}"
            checked += 1
    assert checked > 20


@pytest.mark.parametrize("path", sorted(PUBLIC - {"/api/auth/login", "/api/auth/logout"}))
def test_public_routes_reveal_no_records(server, path):
    assert server.get(path).status_code == 200


# ---- BHU -------------------------------------------------------------------------------

def test_bhu_opens_its_own_records_only(server):
    log_in(server, OWN)
    assert server.get(f"/api/bhu/{OWN}/clients").status_code == 200
    assert server.get(f"/api/bhu/{OTHER}/clients").status_code == 403
    assert server.get(f"/api/bhu/{OTHER}/backup").status_code == 403
    assert server.put(f"/api/bhu/{OTHER}/clients/x", json={}).status_code == 403


def test_bhu_sees_trends_but_not_rhu_controls(server):
    log_in(server, OWN)
    assert server.get("/api/stats/summary").status_code == 200
    for method, url in [
        ("GET", "/api/alerts"),
        ("POST", "/api/alerts/x/status"),
        ("POST", "/api/analytics/run"),
        ("GET", "/api/analytics"),
        ("GET", "/api/monitor/stats"),
        ("GET", "/api/server/addresses"),
    ]:
        assert server.request(method, url, json={"status": "RESOLVED"}).status_code == 403, url


def test_bhu_sends_for_its_own_barangay_only(server):
    log_in(server, OWN)
    assert register(server, OWN_STATION, OWN).status_code == 200
    assert register(server, OTHER_STATION, OTHER).status_code == 403

    batch = make_batch(OWN_STATION, count=2)
    assert server.post("/api/batches", json=batch).status_code == 202
    wait_for_stored(server, batch["batch_id"])
    assert server.post("/api/batches", json=make_batch(OTHER_STATION, count=2)).status_code == 403


# ---- RHU -------------------------------------------------------------------------------

def test_rhu_cannot_open_client_records(server):
    log_in(server, "RHU")
    assert server.get(f"/api/bhu/{OWN}/clients").status_code == 403
    assert server.get(f"/api/bhu/{OWN}/backup").status_code == 403


def test_rhu_uses_its_own_controls_but_sends_no_reports(server):
    log_in(server, "RHU")
    assert server.get("/api/stats/summary").status_code == 200
    assert server.get("/api/alerts").status_code == 200
    assert server.get("/api/monitor/stats").status_code == 200
    assert register(server, OWN_STATION, OWN).status_code == 403


# ---- load-test tool --------------------------------------------------------------------

def test_load_test_account_sends_for_any_barangay_and_does_nothing_else(server):
    log_in(server, "LOADTEST")
    assert register(server, OWN_STATION, OWN).status_code == 200
    assert register(server, OTHER_STATION, OTHER).status_code == 200
    batch = make_batch(OTHER_STATION, count=2)
    assert server.post("/api/batches", json=batch).status_code == 202
    wait_for_stored(server, batch["batch_id"])

    assert server.get(f"/api/bhu/{OWN}/clients").status_code == 403
    assert server.get("/api/alerts").status_code == 403


# ---- WebSockets ------------------------------------------------------------------------

def test_sockets_refuse_anyone_not_logged_in(server):
    assert closed_with(server, f"/ws/node/{OWN_STATION}") == auth.CLOSE_NOT_LOGGED_IN
    assert closed_with(server, "/ws/monitor") == auth.CLOSE_NOT_LOGGED_IN


def test_bhu_connects_as_its_own_station_only(server):
    log_in(server, OWN)
    register(server, OWN_STATION, OWN)
    assert closed_with(server, f"/ws/node/{OTHER_STATION}") == auth.CLOSE_NOT_ALLOWED
    with server.websocket_connect(f"/ws/node/{OWN_STATION}") as ws:
        ws.send_json({"type": "HELLO", "node_id": OWN_STATION})
        assert ws.receive_json()["type"] == "WELCOME"


def test_monitor_feed_is_for_the_rhu(server):
    log_in(server, OWN)
    assert closed_with(server, "/ws/monitor") == auth.CLOSE_NOT_ALLOWED
    log_in(server, "RHU")
    with server.websocket_connect("/ws/monitor") as ws:
        assert ws.receive_json()["type"] == "MONITOR"


# ---- pages -----------------------------------------------------------------------------

def redirect_of(client, path):
    res = client.get(path, follow_redirects=False)
    return res.headers["location"] if res.is_redirect else None


@pytest.mark.parametrize("path", ["/node/", "/node/index.html", "/NODE/", "/dashboard/", "/monitor/"])
def test_pages_send_visitors_to_the_login_page(server, path):
    assert redirect_of(server, path) == "/"


def test_login_page_and_its_files_are_open_to_visitors(server):
    assert server.get("/").status_code == 200
    assert server.get("/css/base.css").status_code == 200
    assert server.get("/js/login/main.js").status_code == 200


def test_bhu_is_kept_on_its_own_side(server):
    log_in(server, OWN)
    assert redirect_of(server, "/") == "/node/"
    assert redirect_of(server, "/dashboard/") == "/node/"
    assert redirect_of(server, "/monitor/") == "/node/"
    assert redirect_of(server, "/node/") is None
    assert "/js/node/main.js" in server.get("/node/").text


def test_rhu_is_kept_on_its_own_side(server):
    log_in(server, "RHU")
    assert redirect_of(server, "/") == "/dashboard/"
    assert redirect_of(server, "/node/") == "/dashboard/"
    assert redirect_of(server, "/monitor/") is None


def test_each_side_gets_its_page_and_no_link_to_the_other(server):
    log_in(server, OWN)
    bhu = server.get("/node/").text
    assert "Trends from RHU Naval" in bhu
    assert not any(f'href="{target}"' in bhu for target in ("/", "/dashboard/", "/monitor/"))

    log_in(server, "RHU")
    for page, other_rhu_page in (("/dashboard/", "/monitor/"), ("/monitor/", "/dashboard/")):
        rhu = server.get(page).text
        assert f"/js{page}main.js" in rhu
        assert f'href="{other_rhu_page}"' in rhu, page
        assert not any(f'href="{target}"' in rhu for target in ("/", "/node/")), page


def test_pages_are_not_kept_in_the_browser_after_logout(server):
    log_in(server, OWN)
    assert server.get("/node/").headers["cache-control"] == "no-store"
