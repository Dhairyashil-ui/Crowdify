"""
routes/wallet_routes.py — Re-exports the canonical wallet_router from wallet.py.
Provides the structured import path routes.wallet_routes.wallet_router.
"""
from wallet import wallet_router  # noqa: F401

__all__ = ["wallet_router"]
