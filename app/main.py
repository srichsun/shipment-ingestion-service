"""Shipment ingestion service.

A single-file FastAPI app that receives shipment events, authenticates the
caller, validates the payload, applies a business rule, and simulates
forwarding the event to a downstream platform.

Layout follows the four sections below: Data Models, Custom Exceptions,
External Integration, API Endpoint.
"""
import logging
import os
import secrets
import uuid
from datetime import datetime
from enum import Enum

from fastapi import FastAPI, Header, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("shipment_ingestion")

app = FastAPI(title="Shipment Ingestion Service")


# ============================================================
# Data Models
# ============================================================

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
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
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
        },
    )

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

    Carries the HTTP status plus a stable machine-readable ``code`` and a
    human-readable ``message``.
    """

    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message


@app.exception_handler(APIError)
def handle_api_error(request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(RequestValidationError)
def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Reshape FastAPI's default 422 into the same error envelope, with the
    per-field problems under ``details``."""
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
        422: {"model": ErrorResponse, "description": "Payload validation or business-rule error."},
    },
)
def receive_shipment(event: ShipmentEvent, x_api_key: str = Header(default="")):
    authenticate(x_api_key)

    # Surface unknown fields as a schema-drift signal without rejecting them.
    if event.model_extra:
        logger.warning(
            "Unknown fields in shipment payload fulfillment_id=%s fields=%s",
            event.fulfillment_id,
            sorted(event.model_extra),
        )

    apply_business_rules(event)

    # Idempotency (not implemented): shipment events are at-least-once, so the
    # same fulfillment_id may arrive more than once. A production version would
    # check fulfillment_id here and skip re-forwarding on a duplicate, returning
    # the same acknowledgement. See README.

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
