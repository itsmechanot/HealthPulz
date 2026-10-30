import sqlite3

from rhu import db


def register(client, node_id="BRGY-08-K7QX", barangay_id="BRGY-08"):
    return client.post(
        "/api/nodes/register",
        json={"type": "REGISTER", "node_id": node_id, "barangay_id": barangay_id},
    )


def test_root_serves_landing_page(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "BHS Node" in res.text


def test_node_page_is_served(client):
    res = client.get("/node/")
    assert res.status_code == 200
    assert "/js/node/main.js" in res.text


def test_scripts_are_served_as_javascript(client):
    # Browsers refuse ES module scripts with any other content type.
    res = client.get("/js/node/main.js")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/javascript")


def test_unknown_api_path_is_still_json_404(client):
    res = client.get("/api/does-not-exist")
    assert res.status_code == 404


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_config_has_all_barangays_and_diseases(client):
    body = client.get("/api/config").json()
    assert body["protocol_version"] == 1
    assert len(body["barangays"]) == 26
    assert {d["disease_code"] for d in body["diseases"]} == {"DEN", "ILI", "AWD", "LEP", "TYP", "MEA"}


def test_reference_data_is_seeded(client):
    conn = db.connect(client.app.state.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM barangays").fetchone()[0] == 26
        assert conn.execute("SELECT COUNT(*) FROM diseases").fetchone()[0] == 6
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
