"""Shipment ingestion service.

A single-file FastAPI app that receives shipment events, authenticates the
caller, validates the payload, applies a business rule, and simulates
forwarding the event to a downstream platform.

Layout follows the four sections below: Data Models, Custom Exceptions,
External Integration, API Endpoint.
"""
import logging
from datetime import datetime
from enum import Enum

from fastapi import FastAPI, Request, status
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
