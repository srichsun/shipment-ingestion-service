"""Tests for shipment payload schemas (structural validation)."""
import pytest
from pydantic import ValidationError

from app.main import ShipmentEvent


def _valid_payload():
    return {
        "fulfillment_id": "FUL_1",
        "order_id": "ORD_1",
        "status": "shipped",
        "carrier": "UPS",
        "tracking_number": "1Z999",
        "shipped_items": [
            {"order_item_id": "I1", "sku": "SKU1", "quantity_shipped": 2},
        ],
    }


def test_valid_payload_parses():
    event = ShipmentEvent(**_valid_payload())
    assert event.carrier == "UPS"
    assert event.shipped_items[0].quantity_shipped == 2


def test_unknown_carrier_rejected():
    payload = _valid_payload()
    payload["carrier"] = "SF-Express"
    with pytest.raises(ValidationError):
        ShipmentEvent(**payload)


def test_non_positive_quantity_rejected():
    payload = _valid_payload()
    payload["shipped_items"][0]["quantity_shipped"] = 0
    with pytest.raises(ValidationError):
        ShipmentEvent(**payload)


def test_empty_required_string_rejected():
    # A required string of "" is meaningless (and would poison dedup/tracing).
    for field in ("fulfillment_id", "order_id"):
        payload = _valid_payload()
        payload[field] = ""
        with pytest.raises(ValidationError):
            ShipmentEvent(**payload)


def test_empty_item_string_rejected():
    for field in ("order_item_id", "sku"):
        payload = _valid_payload()
        payload["shipped_items"][0][field] = ""
        with pytest.raises(ValidationError):
            ShipmentEvent(**payload)


def test_unknown_fields_are_tolerated_and_captured():
    payload = _valid_payload()
    payload["surprise_field"] = "whatever"
    event = ShipmentEvent(**payload)
    # Known fields still parse; the extra is captured (not silently dropped) so
    # it can be logged as schema drift.
    assert event.fulfillment_id == "FUL_1"
    assert "surprise_field" in (event.model_extra or {})
