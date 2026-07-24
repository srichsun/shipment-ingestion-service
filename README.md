# Shipment Ingestion Service

A lightweight REST API that ingests shipment events from warehouses and carriers,
validates them, enforces business rules, and forwards them to downstream order and
analytics systems.

> Personal demo project exploring how to design a robust, evolvable integration
> service for e-commerce fulfillment.

## Status

Work in progress — see commit history for incremental build.

## Tech

- Python 3.11+
- FastAPI + Pydantic v2
- pytest
