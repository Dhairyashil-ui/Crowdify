"""
services/razorpay_service.py — Razorpay order creation and payment verification.
Wraps the Razorpay-specific logic from wallet.py in one import point.
"""
import logging
import os
import razorpay

logger = logging.getLogger("CrowdPulse.razorpay")

_rzp_client = None


def _get_client() -> razorpay.Client:
    global _rzp_client
    if _rzp_client is None:
        key_id     = os.environ.get("RAZORPAY_KEY_ID", "")
        key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
        if not key_id or not key_secret:
            raise RuntimeError("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set")
        _rzp_client = razorpay.Client(auth=(key_id, key_secret))
    return _rzp_client


def create_razorpay_order(amount_inr: int) -> dict:
    """Create a Razorpay order. Returns the order dict."""
    client = _get_client()
    order = client.order.create({
        "amount":   amount_inr * 100,  # paise
        "currency": "INR",
        "payment_capture": 1,
    })
    logger.info(f"[Razorpay] Order created: {order['id']} amount={amount_inr} INR")
    return order


def verify_razorpay_payment(
    order_id: str,
    payment_id: str,
    signature: str,
) -> bool:
    """Verify Razorpay payment signature. Returns True if valid."""
    client = _get_client()
    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id":   order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature":  signature,
        })
        logger.info(f"[Razorpay] Payment verified: {payment_id}")
        return True
    except Exception as e:
        logger.error(f"[Razorpay] Signature verification failed: {e}")
        return False
