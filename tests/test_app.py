"""Tests for the read-state API endpoints."""

from netviber.app import create_app


def test_api_read_round_trip(tmp_path):
    from fastapi.testclient import TestClient

    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        res = client.get("/api/read")
        assert res.status_code == 200
        assert res.json() == {"read": {}}

        res = client.post("/api/read", json=["item1", "item2", "item1"])
        assert res.status_code == 200
        assert res.json() == {"marked": 2}

        res = client.get("/api/read")
        assert set(res.json()["read"]) == {"item1", "item2"}

        res = client.get("/api/state")
        store = app.state.store
        assert res.json()["read"] == store.state["read"]
