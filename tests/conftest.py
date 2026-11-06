import pytest
from fastapi.testclient import TestClient

from rhu import accounts
from tests.factories import NODE, create_test_app

accounts.SCRYPT_N = 2  # password hashing is slow on purpose; test servers create 28 accounts each


@pytest.fixture
def client(tmp_path):
    """A test client backed by a fresh, temporary database."""
    with TestClient(create_test_app(tmp_path / "test.db")) as c:  # the context manager runs the startup (init_db)
        yield c


@pytest.fixture
def registered(client):
    """A client with NODE already registered."""
    client.post("/api/nodes/register", json={"type": "REGISTER", "node_id": NODE, "barangay_id": "BRGY-08"})
    return client
