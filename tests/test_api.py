"""End-to-end tests for the shipment ingestion endpoint."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

API_KEY = "test-key"
HEADERS = {"X-API-Key": API_KEY}


def _payload(**overrides):
    payload = {
        "fulfillment_id": "FUL_1",
        "order_id": "ORD_1",
        "status": "shipped",
        "carrier": "UPS",
        "tracking_number": "1Z999",
        "shipped_items": [
            {"order_item_id": "I1", "sku": "SKU1", "quantity_shipped": 2},
        ],
    }
    payload.update(overrides)
    return payload


def test_accepts_valid_shipment(monkeypatch):
    monkeypatch.setenv("API_KEY", API_KEY)
    resp = client.post("/v1/shipments", json=_payload(), headers=HEADERS)
    assert resp.status_code == 202
    body = resp.json()
    assert body["fulfillment_id"] == "FUL_1"
    assert body["correlation_id"]  # present and non-empty for tracing


def test_missing_api_key_returns_401(monkeypatch):
    monkeypatch.setenv("API_KEY", API_KEY)
    resp = client.post("/v1/shipments", json=_payload())
    assert resp.status_code == 401


def test_shipped_without_tracking_returns_422(monkeypatch):
    monkeypatch.setenv("API_KEY", API_KEY)
    resp = client.post(
        "/v1/shipments", json=_payload(tracking_number=None), headers=HEADERS
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "MISSING_TRACKING_NUMBER"


def test_bad_carrier_returns_422(monkeypatch):
    monkeypatch.setenv("API_KEY", API_KEY)
    resp = client.post(
        "/v1/shipments", json=_payload(carrier="SF-Express"), headers=HEADERS
    )
    assert resp.status_code == 422


def test_errors_share_one_envelope(monkeypatch):
    """401 and 422 errors all return {"error": {"code", "message"}}."""
    monkeypatch.setenv("API_KEY", API_KEY)

    unauthorized = client.post("/v1/shipments", json=_payload())
    missing_tracking = client.post(
        "/v1/shipments", json=_payload(tracking_number=None), headers=HEADERS
    )
    bad_schema = client.post(
        "/v1/shipments", json=_payload(carrier="SF-Express"), headers=HEADERS
    )

    for resp, code in [
        (unauthorized, "UNAUTHORIZED"),
        (missing_tracking, "MISSING_TRACKING_NUMBER"),
        (bad_schema, "VALIDATION_ERROR"),
    ]:
        body = resp.json()
        assert set(body) == {"error"}
        assert body["error"]["code"] == code
        assert "message" in body["error"]
