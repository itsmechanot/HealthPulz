import sqlite3

from rhu import db


def register(client, node_id="BRGY-08-K7QX", barangay_id="BRGY-08"):
    return client.post(
        "/api/nodes/register",
        json={"type": "REGISTER", "node_id": node_id, "barangay_id": barangay_id},
    )


def test_root_redirects_to_docs(client):
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == "/docs"


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
