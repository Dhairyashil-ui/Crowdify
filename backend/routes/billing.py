"""
routes/billing.py — Billing summary endpoint.
GET /wallet/{org_id}/billing-summary is part of wallet_router in wallet.py.
This module provides a named import point for clarity.
"""
from wallet import wallet_router  # noqa: F401

__all__ = ["wallet_router"]
