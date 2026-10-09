"""Tests for watermark state endpoints in the Delivery Lab API."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import app.services.delivery_lab_service as svc
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_watermark_state():
    """Clear watermark state before each test."""
    svc._watermark_state.clear()
    yield
    svc._watermark_state.clear()


def _push_metrics(table_id: str, watermark: str) -> None:
    client.post("/api/v1/delivery-lab/metrics", json={
        "service_id": "test-svc",
        "stage": "cdc",
        "table_id": table_id,
        "records_written": 5,
        "bytes_written": 100,
        "polled_at": datetime.now(UTC).isoformat(),
        "new_watermark": watermark,
    })


def test_get_all_states_empty_initially():
    r = client.get("/api/v1/delivery-lab/tables/state")
    assert r.status_code == 200
    assert r.json() == []


def test_push_metrics_with_watermark_populates_state():
    wm = "2026-05-01T10:00:00+00:00"
    _push_metrics("enrollment", wm)

    r = client.get("/api/v1/delivery-lab/tables/state")
    assert r.status_code == 200
    states = r.json()
    assert len(states) == 1
    assert states[0]["table_id"] == "enrollment"
    assert states[0]["last_watermark"] == wm
    assert states[0]["last_run_status"] == "success"
    assert states[0]["last_row_count"] == 5


def test_get_table_state_by_id():
    wm = "2026-05-01T10:00:00+00:00"
    _push_metrics("enrollment", wm)

    r = client.get("/api/v1/delivery-lab/tables/enrollment/state")
    assert r.status_code == 200
    assert r.json()["last_watermark"] == wm


def test_get_table_state_404_for_unknown_table():
    r = client.get("/api/v1/delivery-lab/tables/unknown/state")
    assert r.status_code == 404


def test_reset_watermark_updates_state():
    _push_metrics("enrollment", "2026-05-01T10:00:00+00:00")

    new_wm = "2026-04-01T00:00:00+00:00"
    r = client.put("/api/v1/delivery-lab/tables/enrollment/watermark", json={"watermark": new_wm})
    assert r.status_code == 204

    r = client.get("/api/v1/delivery-lab/tables/enrollment/state")
    assert r.json()["last_watermark"] == new_wm


def test_reset_watermark_creates_state_for_new_table():
    wm = "2026-04-01T00:00:00+00:00"
    r = client.put("/api/v1/delivery-lab/tables/new_table/watermark", json={"watermark": wm})
    assert r.status_code == 204

    r = client.get("/api/v1/delivery-lab/tables/new_table/state")
    assert r.status_code == 200
    assert r.json()["last_watermark"] == wm


def test_push_metrics_without_watermark_does_not_affect_state():
    client.post("/api/v1/delivery-lab/metrics", json={
        "service_id": "test-svc",
        "stage": "cdc",
        "table_id": "enrollment",
        "records_written": 5,
        "bytes_written": 100,
        "polled_at": datetime.now(UTC).isoformat(),
    })

    r = client.get("/api/v1/delivery-lab/tables/state")
    assert r.json() == []


def test_state_path_segment_not_captured_as_table_id():
    r = client.get("/api/v1/delivery-lab/tables/state")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
