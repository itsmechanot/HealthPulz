import pytest
from fastapi.testclient import TestClient

from rhu.main import create_app
from tests.factories import NODE


@pytest.fixture
def client(tmp_path):
    """A test client backed by a fresh, temporary database."""
    # One analytics worker: starting worker processes is slow on Windows (test_analytics covers the pool).
    # A long interval keeps the scheduler to its startup run; tests trigger runs via POST /api/analytics/run.
    app = create_app(tmp_path / "test.db", analytics_workers=1, analytics_interval=3600)
    with TestClient(app) as c:  # the context manager runs the startup (init_db)
        yield c


@pytest.fixture
def registered(client):
    """A client with NODE already registered."""
    client.post("/api/nodes/register", json={"type": "REGISTER", "node_id": NODE, "barangay_id": "BRGY-08"})
    return client
