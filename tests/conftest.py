import pytest
from fastapi.testclient import TestClient

from rhu.main import create_app


@pytest.fixture
def client(tmp_path):
    """A test client backed by a fresh, temporary database."""
    app = create_app(tmp_path / "test.db")
    with TestClient(app) as c:  # the context manager runs the startup (init_db)
        yield c
