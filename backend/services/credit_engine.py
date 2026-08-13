"""
services/credit_engine.py — Credit consumption and feature-flag service.
Delegates to wallet.py which holds the canonical DB-backed implementation.
"""
from wallet import consume_credits, get_org_features_internal, FEATURE_PRICING

__all__ = ["consume_credits", "get_org_features_internal", "FEATURE_PRICING"]
