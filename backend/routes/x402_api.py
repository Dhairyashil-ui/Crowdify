"""
routes/x402_api.py — Re-exports the crowd_api_router from crowd_api.py.
Provides the structured import path routes.x402_api.crowd_api_router.
"""
from crowd_api import crowd_api_router  # noqa: F401

__all__ = ["crowd_api_router"]
