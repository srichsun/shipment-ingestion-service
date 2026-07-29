"""Shipment ingestion service.

A single-file FastAPI app that receives shipment events, authenticates the
caller, validates the payload, applies a business rule, and simulates
forwarding the event to a downstream platform.

Layout follows the sections below: Data Models, Custom Exceptions, External
Integration, Data-quality Sink, API Endpoint.
"""
import logging
import os
import secrets
import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from fastapi import FastAPI, Header, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger("shipment_ingestion")

app = FastAPI(title="Shipment Ingestion Service")


# ============================================================
# Data Models
# ============================================================

SAMPLE_PAYLOAD = {
    "fulfillment_id": "FUL_99821",
    "order_id": "ORD-12345",
    "status": "shipped",
    "shipped_at": "2026-01-01T10:30:00Z",
    "tracking_number": "1Z999AA10123456784",
    "carrier": "UPS",
    "shipped_items": [
        {"order_item_id": "ITEM-10001", "sku": "BLUE-SHIRT-S", "quantity_shipped": 2},
        {"order_item_id": "ITEM-10002", "sku": "RED-HAT-OS", "quantity_shipped": 1},
    ],
}


class ShipmentStatus(str, Enum):
    PENDING = "pending"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class Carrier(str, Enum):
    UPS = "UPS"
    FEDEX = "FedEx"
    DHL = "DHL"


class ShippedItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    order_item_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    quantity_shipped: int = Field(gt=0)  # must be a positive integer


class ShipmentEvent(BaseModel):
    # Tolerant Reader: accept unknown fields (rather than rejecting) so upstream
    # can evolve its schema without breaking ingestion. Captured extras are
    # logged as a schema-drift signal in the endpoint.
    model_config = ConfigDict(extra="allow", json_schema_extra={"example": SAMPLE_PAYLOAD})

    fulfillment_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    status: ShipmentStatus
    carrier: Carrier
    shipped_items: list[ShippedItem] = Field(min_length=1)
    shipped_at: datetime | None = None
    tracking_number: str | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list | None = None


class ErrorResponse(BaseModel):
    """The envelope every error shares — declared so the OpenAPI docs show the
    real error shape instead of FastAPI's default."""
    error: ErrorDetail


# ============================================================
# Custom Exceptions
# ============================================================

class APIError(Exception):
    """An error we return to the caller in a consistent envelope.

    Carries the HTTP status, a stable machine-readable ``code``, a
    human-readable ``message``, and optional per-field ``details``.
    """

    def __init__(self, status_code: int, code: str, message: str, details: list | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


@app.exception_handler(APIError)
def handle_api_error(request: Request, exc: APIError) -> JSONResponse:
    error: dict = {"code": exc.code, "message": exc.message}
    if exc.details is not None:
        error["details"] = exc.details
    return JSONResponse(status_code=exc.status_code, content={"error": error})


@app.exception_handler(RequestValidationError)
def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Fallback: reshape FastAPI's default 422 into the same error envelope for
    any route that relies on automatic body validation."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request payload failed validation.",
                "details": jsonable_encoder(exc.errors()),
            }
        },
    )


# ============================================================
# External Integration
# ============================================================

def forward_to_downstream(event: ShipmentEvent, correlation_id: str) -> None:
    """Simulate forwarding the validated event to a downstream platform.

    A real integration would POST to an external API here; for this exercise a
    log line stands in for that call. The correlation id ties this log line to
    the acknowledgement returned to the caller, so a single event can be traced
    end to end.
    """
    logger.info(
        "Forwarding shipment to downstream platform "
        "fulfillment_id=%s correlation_id=%s",
        event.fulfillment_id,
        correlation_id,
    )


# ============================================================
# Data-quality Sink (quarantine / dead-letter)
# ============================================================
#
# A shipment is an atomic fact ("this fulfillment shipped these items"). If any
# part is invalid — e.g. one shipped_item has an empty SKU — we do NOT silently
# accept a partial shipment (that would under-report what shipped and corrupt
# downstream analytics). Instead the *whole* event is captured to a dead-letter
# (quarantine) sink with a reason and an alert, so the service stays up, bad
# data is never lost, and it can be reviewed and replayed later — rather than
# just bounced back with a bare 422 that nobody follows up on.
#
# The sink and alerting are mocked here (an in-memory list and a warning log),
# in the same spirit as the mocked downstream.

DEAD_LETTER: list[dict] = []


def quarantine(fulfillment_id: str | None, code: str, reason: str, event: Any) -> None:
    """Capture a bad event in the dead-letter sink and raise an alert."""
    DEAD_LETTER.append(
        {"fulfillment_id": fulfillment_id, "code": code, "reason": reason, "event": event}
    )
    logger.warning(
        "ALERT quarantined shipment fulfillment_id=%s code=%s reason=%s",
        fulfillment_id, code, reason,
    )


def _first_error(exc: ValidationError) -> str:
    """Turn a Pydantic error into one readable line for the quarantine reason."""
    err = exc.errors()[0]
    loc = ".".join(str(p) for p in err["loc"])
    return f"{loc}: {err['msg']}"


# ============================================================
# API Endpoint
# ============================================================

def authenticate(x_api_key: str) -> None:
    """Reject the request unless it carries the expected API key."""
    expected = os.getenv("API_KEY", "")
    if not expected or not secrets.compare_digest(x_api_key, expected):
        raise APIError(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED", "Invalid or missing API key.")


def apply_business_rules(event: ShipmentEvent) -> None:
    """Enforce cross-field business rules.

    Returns 422 on violation: the payload is well-formed but not processable in
    this state. It shares the 422 status with schema errors and is told apart by
    the ``code`` (a 400 would wrongly imply a malformed request).
    """
    if event.status == ShipmentStatus.SHIPPED and not event.tracking_number:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "MISSING_TRACKING_NUMBER",
            "A shipment marked as 'shipped' must include a tracking number.",
        )


@app.post(
    "/v1/shipments",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid API key."},
        422: {
            "model": ErrorResponse,
            "description": "Validation or business-rule error — the event is captured to the dead-letter sink.",
        },
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"example": SAMPLE_PAYLOAD}},
        }
    },
)
async def receive_shipment(request: Request, x_api_key: str = Header(default="")):
    """Ingest one shipment event.

    Order matters: authenticate first (a 401 is an auth problem, never a
    data-quality one, so unauthenticated junk never reaches the dead-letter
    sink). Then validate the whole event — a shipment is atomic, so any invalid
    part quarantines the entire event rather than accepting it in part. Valid
    events are forwarded and acknowledged with 202 + a correlation id.
    """
    authenticate(x_api_key)

    try:
        raw = await request.json()
    except Exception:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "VALIDATION_ERROR",
            "Request body is not valid JSON.",
        )

    if not isinstance(raw, dict):
        quarantine(None, "VALIDATION_ERROR", "Request body must be a JSON object.", raw)
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "VALIDATION_ERROR",
            "Request body must be a JSON object.",
        )

    # Structural validation. If any field or shipped_item is invalid, the whole
    # event is captured (a shipment is atomic) and rejected with the details.
    try:
        event = ShipmentEvent(**raw)
    except ValidationError as exc:
        quarantine(raw.get("fulfillment_id"), "VALIDATION_ERROR", _first_error(exc), raw)
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "VALIDATION_ERROR",
            "Request payload failed validation.", details=jsonable_encoder(exc.errors()),
        )

    # Cross-field business rule; a violation is captured too, not just bounced.
    try:
        apply_business_rules(event)
    except APIError as exc:
        quarantine(event.fulfillment_id, exc.code, exc.message, raw)
        raise

    # Surface unknown fields as a schema-drift signal without rejecting them.
    if event.model_extra:
        logger.warning(
            "Unknown fields in shipment payload fulfillment_id=%s fields=%s",
            event.fulfillment_id,
            sorted(event.model_extra),
        )

    # Idempotency (not implemented): shipment events are at-least-once, so the
    # same fulfillment_id may arrive more than once. A production version would
    # check fulfillment_id here and skip re-forwarding on a duplicate. See README.

    # A correlation id is minted per request and returned in the acknowledgement
    # and stamped on the downstream log line, so a single request can be traced
    # end to end when troubleshooting across systems.
    correlation_id = uuid.uuid4().hex
    forward_to_downstream(event, correlation_id)
    return {
        "status": "accepted",
        "fulfillment_id": event.fulfillment_id,
        "correlation_id": correlation_id,
    }
