"""
x402_config.py — Croudify x402 Resource Server (Algorand TestNet)
=================================================================

Uses the x402-avm package (GoPlausible) which provides native Algorand
AVM support including TestNet USDC micropayments.

Environment variables:
    X402_PAY_TO_ADDRESS  — Algorand address (base32, no checksum) to receive USDC
    X402_NETWORK         — "algorand-testnet" (default) or "algorand-mainnet"
    X402_PRICE           — Price per request e.g. "$0.001" (default)
    X402_FACILITATOR_URL — Override GoPlausible facilitator (optional)
"""

import logging
import os

logger = logging.getLogger("CrowdPulse.x402")

PAY_TO_ADDRESS   = os.environ.get("X402_PAY_TO_ADDRESS", "")
NETWORK          = os.environ.get("X402_NETWORK", "algorand-testnet")
PRICE_PER_CALL   = os.environ.get("X402_PRICE", "$0.001")
FACILITATOR_URL  = os.environ.get(
    "X402_FACILITATOR_URL",
    "https://x402.goplus.com/facilitator",
)

x402_server   = None
_x402_enabled = False


def init_x402() -> bool:
    """
    Initialise the x402-avm resource server for Algorand TestNet.

    Returns True  → payment middleware should be applied.
    Returns False → graceful fallback, endpoint is open (no payment required).
    """
    global x402_server, _x402_enabled

    if not PAY_TO_ADDRESS:
        logger.warning(
            "[x402] X402_PAY_TO_ADDRESS not set. "
            "POST /api/v1/crowd/predict will be OPEN (no payment required). "
            "Set X402_PAY_TO_ADDRESS (Algorand address) to enable payment gating."
        )
        return False

    try:
        from x402 import x402ResourceServer                           # noqa
        from x402.http import HTTPFacilitatorClient
        from x402.mechanisms.avm import AvmServerScheme              # Algorand AVM

        facilitator  = HTTPFacilitatorClient(url=FACILITATOR_URL)
        x402_server  = x402ResourceServer(facilitator)
        x402_server.register("algorand:*", AvmServerScheme())
        x402_server.initialize()

        _x402_enabled = True
        logger.info(
            f"[x402] ✅ Algorand TestNet enabled. "
            f"network={NETWORK} pay_to={PAY_TO_ADDRESS[:8]}… "
            f"price={PRICE_PER_CALL} facilitator={FACILITATOR_URL}"
        )
        return True

    except ImportError as e:
        logger.warning(
            f"[x402] x402-avm not installed ({e}). "
            "Run: pip install \"x402-avm[fastapi,avm]\". "
            "Endpoint will be OPEN until installed."
        )
        return False
    except Exception as e:
        logger.error(f"[x402] Initialisation failed: {e}. Endpoint will be OPEN.")
        return False


def is_x402_enabled() -> bool:
    return _x402_enabled


def get_route_config() -> dict:
    """
    Returns the route → RouteConfig mapping for PaymentMiddlewareASGI.
    Only called when x402 is enabled.
    """
    from x402 import ResourceConfig                                   # noqa
    from x402.http.types import RouteConfig, PaymentOption

    return {
        "/api/v1/crowd/predict": RouteConfig(
            accepts=[PaymentOption(
                scheme="exact",
                network=NETWORK,
                pay_to=PAY_TO_ADDRESS,
                price=PRICE_PER_CALL,
            )]
        )
    }
