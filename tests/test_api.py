import socket
import sqlite3

from rhu import db, network


def register(client, node_id="BRGY-08-K7QX", barangay_id="BRGY-08"):
    return client.post(
        "/api/nodes/register",
        json={"type": "REGISTER", "node_id": node_id, "barangay_id": barangay_id},
    )


def test_server_addresses(client):
    body = client.get("/api/server/addresses").json()
    assert isinstance(body["addresses"], list)
    assert not any(ip.startswith("127.") for ip in body["addresses"])


def test_a_port_with_a_server_counts_as_reachable():
    with socket.create_server(("127.0.0.1", 0)) as server:
        assert network.accepts_connections("127.0.0.1", server.getsockname()[1])


def test_a_closed_port_is_not_reachable():
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
    assert not network.accepts_connections("127.0.0.1", port)


def test_server_reports_when_phones_cannot_connect(client, monkeypatch):
    monkeypatch.setattr(network, "lan_addresses", lambda: ["192.168.1.5"])
    monkeypatch.setattr(network, "accepts_connections", lambda address, port: False)
    assert client.get("http://testserver:8000/api/server/addresses").json()["reachable"] is False

    monkeypatch.setattr(network, "accepts_connections", lambda address, port: True)
    assert client.get("http://testserver:8000/api/server/addresses").json()["reachable"] is True


def test_no_false_alarm_when_it_cannot_be_checked(client, monkeypatch):
    monkeypatch.setattr(network, "lan_addresses", lambda: [])
    assert client.get("http://testserver:8000/api/server/addresses").json()["reachable"] is True


def test_scripts_are_served_as_javascript(client):
    # Browsers refuse ES module scripts with any other content type.
    res = client.get("/js/node/main.js")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/javascript")


def test_icon_sprite_is_served_as_svg(client):
    res = client.get("/img/icons.svg")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/svg+xml")
    assert 'id="logo"' in res.text


def test_map_matches_the_barangay_list(client):
    map_data = client.get("/data/naval-map.json").json()
    barangays = client.get("/api/config").json()["barangays"]
    assert {b["barangay_id"]: b["psgc"] for b in map_data["barangays"]} == {b["barangay_id"]: b["psgc"] for b in barangays}
    assert all(b["path"].startswith("M") and len(b["center"]) == 2 for b in map_data["barangays"])
    assert map_data["naval"]["path"] and map_data["towns"]  # Naval highlighted among the other towns of Biliran


def test_neighbors_are_symmetric(client):
    barangays = {b["barangay_id"]: b for b in client.get("/api/config").json()["barangays"]}
    for b in barangays.values():
        for neighbor in b["neighbors"]:
            assert b["barangay_id"] in barangays[neighbor]["neighbors"]


def test_unknown_api_path_is_still_json_404(client):
    res = client.get("/api/does-not-exist")
    assert res.status_code == 404


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_config_has_all_barangays_and_diseases(client):
    body = client.get("/api/config").json()
    assert body["protocol_version"] == 1
    assert len(body["barangays"]) == 26
    groups = {}
    for d in body["diseases"]:
        groups.setdefault(d["group"], set()).add(d["disease_code"])
    assert groups == {
        "common": {"ARI", "SKN", "PNE", "WND", "KID", "HTN", "ART", "TB", "FEV", "DIA"},
        "urgent": {"AFP", "DIP", "MEA", "NTT", "TET", "PER"},
        "monitored": {"DEN", "LEP", "TYP"},
        "other": {"OTH"},
    }
    single = {d["disease_code"] for d in body["diseases"] if d["single_case_alert"]}
    assert single == groups["urgent"]
    no_outbreaks = {d["disease_code"] for d in body["diseases"] if not d["threshold_alerts"]}
    assert no_outbreaks == {"WND", "KID", "HTN", "ART", "OTH"} | groups["urgent"]


def test_reference_data_is_seeded(client):
    conn = db.connect(client.app.state.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM barangays").fetchone()[0] == 26
        assert conn.execute("SELECT COUNT(*) FROM diseases").fetchone()[0] == 20
    finally:
        conn.close()


def test_barangay_list_is_id_and_name_only(client):
    first = client.get("/api/barangays").json()[0]
    assert first == {"barangay_id": "BRGY-01", "name": "Agpangi"}


def test_register_node(client):
    res = register(client)
    assert res.status_code == 200
    assert res.json() == {
        "type": "REGISTERED",
        "node_id": "BRGY-08-K7QX",
        "barangay_id": "BRGY-08",
        "barangay": "Caraycaray",
    }


def test_register_is_idempotent(client):
    assert register(client).status_code == 200
    assert register(client).status_code == 200

    conn = sqlite3.connect(client.app.state.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 1
    finally:
        conn.close()


def test_several_nodes_can_serve_one_barangay(client):
    assert register(client, node_id="BRGY-08-AAAA").status_code == 200
    assert register(client, node_id="BRGY-08-BBBB").status_code == 200


def test_register_rejects_node_id_from_another_barangay(client):
    assert register(client, node_id="BRGY-01-K7QX", barangay_id="BRGY-08").status_code == 400


def test_register_rejects_malformed_node_id(client):
    assert register(client, node_id="hello").status_code == 400


def test_register_unknown_barangay(client):
    assert register(client, node_id="BRGY-99-K7QX", barangay_id="BRGY-99").status_code == 404
