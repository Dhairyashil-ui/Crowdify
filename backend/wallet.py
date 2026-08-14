"""
wallet.py — Croudify Wallet & Organisation Layer
=================================================

Mounted as an APIRouter on the main FastAPI app in server.py.
This file deliberately has zero imports from server.py so that the
frame-processing pipeline is never affected.

Design decisions (hackathon):
  • 1 INR = 1 Croudify Credit  (1:1 mapping, trivial to change later)
  • A single "Demo Organisation" is auto-created on first startup.
  • Organisation ID is returned from GET /wallet/demo-org so the
    Authority Dashboard can identify itself without a login system.
  • Razorpay keys come from RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET env vars.
  • Signature verification is HMAC-SHA256 over "order_id|payment_id".
"""

import hashlib
import hmac
import logging
import os
from datetime import datetime
from typing import Optional

# Load .env file so keys are available even when imported before server.py runs load_dotenv
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass

import httpx
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger("CrowdPulse.Wallet")

# ── Razorpay config ────────────────────────────────────────────────────────────
RAZORPAY_KEY_ID         = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET     = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
RAZORPAY_API_BASE       = "https://api.razorpay.com/v1"

# ── Credit conversion ──────────────────────────────────────────────────────────
# 1 INR → 1 Croudify Credit (hackathon 1:1 mapping)
def inr_to_credits(amount_inr: int) -> int:
    return amount_inr

# ── Low-balance threshold ──────────────────────────────────────────────────────
LOW_BALANCE_THRESHOLD = 20    # credits — dashboard shows warning below this

# ── Hackathon feature pricing (credits per intelligence unit / second) ─────────
FEATURE_PRICING: dict[str, int] = {
    "person_detection": 1,
    "density":          1,
    "movement":         1,
    "speed":            1,
    "direction":        1,
    "flow":             2,
    "compression":      2,
    "exit_blockage":    2,
    "behaviour":        3,
    "risk_prediction":  3,
}

# Default feature set: all enabled
DEFAULT_FEATURES: dict[str, bool] = {k: True for k in FEATURE_PRICING}

# ── Router ─────────────────────────────────────────────────────────────────────
wallet_router = APIRouter(tags=["wallet"])

# ── DB reference (injected by server.py at startup) ───────────────────────────
_db = None

def set_wallet_db(database):
    """Called from server.py startup() to hand the Motor db reference."""
    global _db
    _db = database


def _require_db():
    if _db is None:
        raise HTTPException(status_code=503, detail="Database not available")


# ── Pydantic models ────────────────────────────────────────────────────────────

class OrgCreate(BaseModel):
    name:       str
    email:      str
    department: str = ""
    phone:      str = ""


class RechargeRequest(BaseModel):
    organization_id: str
    amount_inr: int                  # 100 | 500 | 1000


class VerifyPaymentRequest(BaseModel):
    organization_id: str
    order_id:        str
    payment_id:      str
    signature:       str
    amount_inr:      int


class UsageDebitRequest(BaseModel):
    organization_id: str
    feature:         str             # DENSITY | MOVEMENT | RISK_PREDICTION | FLOW
    credits:         int             # positive — stored as negative in ledger


class OrgFeaturesUpdate(BaseModel):
    person_detection: bool = True
    density:          bool = True
    movement:         bool = True
    speed:            bool = True
    direction:        bool = True
    flow:             bool = True
    compression:      bool = True
    exit_blockage:    bool = True
    behaviour:        bool = True
    risk_prediction:  bool = True


# ── Helpers ────────────────────────────────────────────────────────────────────

def _to_str_id(doc: dict) -> dict:
    """Convert MongoDB ObjectId _id to string 'id' and remove _id."""
    doc["id"] = str(doc.pop("_id"))
    return doc


def _iso(dt: datetime) -> str:
    return dt.isoformat() + "Z" if dt else None


async def _get_org_or_404(org_id: str) -> dict:
    """Fetch org document or raise 404."""
    try:
        oid = ObjectId(org_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid organization_id")
    doc = await _db.organizations.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return doc


async def _get_wallet_or_404(org_id: str) -> dict:
    """Fetch wallet document or raise 404."""
    try:
        oid = ObjectId(org_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid organization_id")
    doc = await _db.wallets.find_one({"organization_id": org_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Wallet not found for this organisation")
    return doc


# ── Organisation endpoints ─────────────────────────────────────────────────────

@wallet_router.post("/org/create")
async def create_organisation(body: OrgCreate):
    """
    Create a new organisation and its corresponding wallet atomically.

    Returns the created org (with id) and wallet (with id).
    """
    _require_db()

    # Prevent duplicate emails
    existing = await _db.organizations.find_one({"email": body.email.lower().strip()})
    if existing:
        raise HTTPException(status_code=409, detail="An organisation with this email already exists")

    now = datetime.utcnow()

    # ── Insert organisation ────────────────────────────────────────────────────
    org_doc = {
        "name":       body.name.strip(),
        "email":      body.email.lower().strip(),
        "department": body.department.strip(),
        "phone":      body.phone.strip(),
        "status":     "active",
        "created_at": now,
    }
    org_result = await _db.organizations.insert_one(org_doc)
    org_id_str = str(org_result.inserted_id)

    # ── Create wallet ──────────────────────────────────────────────────────────
    wallet_doc = {
        "organization_id": org_id_str,
        "balance":         100,
        "currency":        "CREDIT",
        "updated_at":      now,
    }
    wallet_result = await _db.wallets.insert_one(wallet_doc)

    # ── Record welcome bonus transaction ───────────────────────────────────────
    await _db.wallet_transactions.insert_one({
        "organization_id": org_id_str,
        "type":            "RECHARGE",
        "source":          "SYSTEM",
        "feature":         None,
        "amount_inr":      0,
        "credits":         100,
        "status":          "SUCCESS",
        "reference_id":    f"WELCOME-{org_id_str}",
        "created_at":      now,
    })

    logger.info(f"[ORG] Created org '{body.name}' (id={org_id_str}) with wallet {wallet_result.inserted_id}")

    return {
        "organisation": {
            "id":         org_id_str,
            "name":       org_doc["name"],
            "email":      org_doc["email"],
            "department": org_doc["department"],
            "phone":      org_doc["phone"],
            "status":     org_doc["status"],
            "created_at": _iso(now),
        },
        "wallet": {
            "id":              str(wallet_result.inserted_id),
            "organization_id": org_id_str,
            "balance":         100,
            "currency":        "CREDIT",
        },
    }


# Clean REST alias: POST /organizations
@wallet_router.post("/organizations")
async def create_organisation_v2(body: OrgCreate):
    """
    Step 23 flow — Organisation signup:

      POST /organizations
           ↓
      Organisation created
           ↓
      Wallet created (balance = 0)
           ↓
      Ready for first recharge via POST /wallet/create-order

    Delegates to /org/create internally.
    """
    return await create_organisation(body)


@wallet_router.get("/org/list")
async def list_organisations():
    """Return all active organisations (for admin / developer dashboard)."""
    _require_db()
    cursor = _db.organizations.find({}, sort=[("created_at", -1)]).limit(200)
    docs = await cursor.to_list(200)
    return [
        {
            "id":         str(d["_id"]),
            "name":       d.get("name", ""),
            "email":      d.get("email", ""),
            "status":     d.get("status", "active"),
            "created_at": _iso(d.get("created_at")),
        }
        for d in docs
    ]


@wallet_router.get("/org/by-email")
async def get_org_by_email(email: str):
    """
    Look up an existing authority organisation by email address.
    Called by the frontend on Google sign-in to detect returning authorities.
    Returns 404 if this email has not registered yet.
    """
    _require_db()
    doc = await _db.organizations.find_one({"email": email.lower().strip()})
    if not doc:
        raise HTTPException(status_code=404, detail="Authority not registered")
    org_id_str = str(doc["_id"])
    wallet = await _db.wallets.find_one({"organization_id": org_id_str})
    balance = wallet.get("balance", 0) if wallet else 0
    return {
        "id":                    org_id_str,
        "name":                  doc.get("name", ""),
        "email":                 doc.get("email", ""),
        "department":            doc.get("department", ""),
        "phone":                 doc.get("phone", ""),
        "status":                doc.get("status", "active"),
        "created_at":            _iso(doc.get("created_at")),
        "balance":               balance,
        "currency":              "CREDIT",
        "low_balance_threshold": LOW_BALANCE_THRESHOLD,
        "low_balance":           balance <= LOW_BALANCE_THRESHOLD,
    }


@wallet_router.get("/org/{org_id}")
async def get_organisation(org_id: str):
    """Return a single organisation by id."""
    _require_db()
    doc = await _get_org_or_404(org_id)
    return {
        "id":         str(doc["_id"]),
        "name":       doc.get("name", ""),
        "email":      doc.get("email", ""),
        "department": doc.get("department", ""),
        "phone":      doc.get("phone", ""),
        "status":     doc.get("status", "active"),
        "created_at": _iso(doc.get("created_at")),
    }


@wallet_router.get("/wallet/demo-org")
async def get_demo_org():
    """
    Returns (or creates) the canonical demo organisation used by the
    Authority Dashboard when no login system is configured.

    The demo org is identified by email = 'demo@croudify.internal'.
    """
    _require_db()

    DEMO_EMAIL = "demo@croudify.internal"
    demo_org = await _db.organizations.find_one({"email": DEMO_EMAIL})

    if demo_org is None:
        # Auto-create on first call
        now = datetime.utcnow()
        org_doc = {
            "name":       "Demo Stadium",
            "email":      DEMO_EMAIL,
            "status":     "active",
            "created_at": now,
        }
        org_result = await _db.organizations.insert_one(org_doc)
        org_id_str = str(org_result.inserted_id)

        wallet_doc = {
            "organization_id": org_id_str,
            "balance":         0,
            "currency":        "CREDIT",
            "updated_at":      now,
        }
        await _db.wallets.insert_one(wallet_doc)
        logger.info(f"[ORG] Demo org auto-created. id={org_id_str}")

        demo_org = await _db.organizations.find_one({"_id": org_result.inserted_id})

    org_id_str = str(demo_org["_id"])
    wallet = await _db.wallets.find_one({"organization_id": org_id_str})
    balance = wallet.get("balance", 0) if wallet else 0

    return {
        "id":                    org_id_str,
        "name":                  demo_org.get("name"),
        "email":                 demo_org.get("email"),
        "status":                demo_org.get("status"),
        "balance":               balance,
        "currency":              "CREDIT",
        "low_balance_threshold": LOW_BALANCE_THRESHOLD,
        "low_balance":           balance <= LOW_BALANCE_THRESHOLD,
    }


async def get_demo_org() -> dict:
    """
    Internal Python helper (importable by server.py) that returns the canonical
    demo org dict without going through an HTTP route.
    """
    DEMO_EMAIL = "demo@croudify.internal"
    demo_org = await _db.organizations.find_one({"email": DEMO_EMAIL})
    if demo_org is None:
        return {"id": None}
    org_id_str = str(demo_org["_id"])
    wallet = await _db.wallets.find_one({"organization_id": org_id_str})
    balance = wallet.get("balance", 0) if wallet else 0
    return {
        "id":      org_id_str,
        "balance": balance,
        "name":    demo_org.get("name"),
    }


# ── Wallet endpoints ───────────────────────────────────────────────────────────

@wallet_router.get("/wallet/{org_id}")
async def get_wallet(org_id: str):
    """Return balance and recent 10 transactions for an organisation."""
    _require_db()
    await _get_org_or_404(org_id)          # validates org exists
    wallet = await _get_wallet_or_404(org_id)

    # Recent transactions (newest first, max 10 for the widget)
    cursor = _db.wallet_transactions.find(
        {"organization_id": org_id},
        sort=[("created_at", -1)],
    ).limit(10)
    txns = await cursor.to_list(10)

    return {
        "organization_id": org_id,
        "balance":         wallet.get("balance", 0),
        "currency":        wallet.get("currency", "CREDIT"),
        "updated_at":      _iso(wallet.get("updated_at")),
        "recent_transactions": [
            {
                "id":           str(t["_id"]),
                "type":         t.get("type"),
                "source":       t.get("source"),
                "feature":      t.get("feature"),
                "amount_inr":   t.get("amount_inr"),
                "credits":      t.get("credits"),
                "status":       t.get("status"),
                "reference_id": t.get("reference_id"),
                "created_at":   _iso(t.get("created_at")),
            }
            for t in txns
        ],
    }


@wallet_router.get("/wallet/{org_id}/transactions")
async def get_wallet_transactions(org_id: str, limit: int = 100):
    """Return full transaction ledger for an organisation."""
    _require_db()
    await _get_org_or_404(org_id)
    cursor = _db.wallet_transactions.find(
        {"organization_id": org_id},
        sort=[("created_at", -1)],
    ).limit(limit)
    txns = await cursor.to_list(limit)
    return [
        {
            "id":           str(t["_id"]),
            "type":         t.get("type"),
            "source":       t.get("source"),
            "feature":      t.get("feature"),
            "amount_inr":   t.get("amount_inr"),
            "credits":      t.get("credits"),
            "status":       t.get("status"),
            "reference_id": t.get("reference_id"),
            "created_at":   _iso(t.get("created_at")),
        }
        for t in txns
    ]


# ── Razorpay: Create order ─────────────────────────────────────────────────────

@wallet_router.post("/wallet/create-order")
async def create_razorpay_order(body: RechargeRequest):
    """
    Create a Razorpay order for the given INR amount.

    Returns:
        {order_id, amount, currency, key_id, credits_to_add, organization_id}
    """
    _require_db()
    await _get_org_or_404(body.organization_id)
    await _get_wallet_or_404(body.organization_id)

    if body.amount_inr < 1:
        raise HTTPException(status_code=400, detail="amount_inr must be at least 1")

    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Razorpay not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET env vars.",
        )

    payload = {
        "amount":   body.amount_inr * 100,   # Razorpay accepts paise
        "currency": "INR",
        "receipt":  f"croudify_{body.organization_id[:8]}_{int(datetime.utcnow().timestamp())}",
        "notes": {
            "organization_id": body.organization_id,
            "product":         "Croudify Credits",
        },
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{RAZORPAY_API_BASE}/orders",
                json=payload,
                auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
            )
            resp.raise_for_status()
            order = resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"[RAZORPAY] create-order failed: {e.response.text}")
        raise HTTPException(status_code=502, detail=f"Razorpay error: {e.response.text}")
    except Exception as e:
        logger.error(f"[RAZORPAY] create-order exception: {e}")
        raise HTTPException(status_code=502, detail="Could not reach Razorpay")

    logger.info(f"[RAZORPAY] Order created: {order['id']} for org={body.organization_id} amount=₹{body.amount_inr}")

    return {
        "order_id":        order["id"],
        "amount":          body.amount_inr * 100,
        "currency":        "INR",
        "key_id":          RAZORPAY_KEY_ID,
        "credits_to_add":  inr_to_credits(body.amount_inr),
        "organization_id": body.organization_id,
    }


# ── Razorpay: Verify payment & credit wallet ───────────────────────────────────

@wallet_router.post("/wallet/verify-payment")
async def verify_razorpay_payment(body: VerifyPaymentRequest):
    """
    Verify the Razorpay payment signature, then:
      1. Credit the wallet with Croudify Credits
      2. Insert a RECHARGE transaction into the ledger

    Signature algorithm:
        HMAC-SHA256(order_id + "|" + payment_id, key=RAZORPAY_KEY_SECRET)
    """
    _require_db()
    await _get_org_or_404(body.organization_id)
    wallet = await _get_wallet_or_404(body.organization_id)

    if not RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Razorpay not configured")

    # ── Signature verification ─────────────────────────────────────────────────
    expected_sig = hmac.new(
        RAZORPAY_KEY_SECRET.encode("utf-8"),
        f"{body.order_id}|{body.payment_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, body.signature):
        logger.warning(
            f"[RAZORPAY] Signature mismatch for org={body.organization_id} "
            f"payment={body.payment_id}"
        )
        # Insert FAILED transaction for audit
        await _db.wallet_transactions.insert_one({
            "organization_id": body.organization_id,
            "type":            "RECHARGE",
            "source":          "RAZORPAY",
            "amount_inr":      body.amount_inr,
            "credits":         0,
            "status":          "FAILED",
            "reference_id":    body.payment_id,
            "created_at":      datetime.utcnow(),
        })
        raise HTTPException(status_code=400, detail="Payment signature verification failed")

    # ── Verify ONLY (NO CREDIT ADDITION) ───────────────────────────────────────
    # The frontend calls this to ensure the payment flow completed.
    # We do NOT add credits here to prevent spoofing. Credits are added via the Webhook.
    
    return {
        "status":          "SUCCESS",
        "message":         "Payment verified. Credits will be added via webhook shortly.",
        "reference_id":    body.payment_id,
        "organization_id": body.organization_id,
    }


# ── Razorpay: Webhook (Server-to-Server) ───────────────────────────────────────

@wallet_router.post("/wallet/razorpay-webhook")
async def razorpay_webhook(request: Request):
    """
    Secure server-to-server webhook from Razorpay.
    Listens for 'order.paid' or 'payment.captured'.
    
    This is the ONLY place where we actually increment the wallet balance.
    """
    _require_db()
    
    # 1. Verify webhook signature
    body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")
    
    if RAZORPAY_WEBHOOK_SECRET:
        expected_sig = hmac.new(
            RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        
        if not hmac.compare_digest(expected_sig, signature):
            logger.error("[WEBHOOK] Razorpay webhook signature mismatch!")
            raise HTTPException(status_code=400, detail="Invalid signature")

    import json
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
        
    event = data.get("event")
    if event not in ("order.paid", "payment.captured"):
        return {"status": "ignored", "event": event}
        
    payload = data.get("payload", {})
    payment_entity = payload.get("payment", {}).get("entity", {})
    
    payment_id = payment_entity.get("id")
    order_id = payment_entity.get("order_id")
    amount_inr = payment_entity.get("amount", 0) // 100  # paise to INR
    notes = payment_entity.get("notes", {})
    
    organization_id = notes.get("organization_id")
    
    if not organization_id:
        logger.error(f"[WEBHOOK] Payment {payment_id} missing organization_id in notes.")
        return {"status": "error", "message": "missing organization_id"}
        
    # 2. Idempotency Check: Did we already process this payment?
    existing_txn = await _db.wallet_transactions.find_one({
        "reference_id": payment_id,
        "type": "RECHARGE",
        "status": "SUCCESS"
    })
    
    if existing_txn:
        logger.info(f"[WEBHOOK] Payment {payment_id} already processed (idempotent return).")
        return {"status": "already_processed"}
        
    # 3. Add Credits
    credits_to_add = inr_to_credits(amount_inr)
    now = datetime.utcnow()

    await _db.wallets.update_one(
        {"organization_id": organization_id},
        {
            "$inc": {"balance": credits_to_add},
            "$set": {"updated_at": now},
        },
    )

    # 4. Record RECHARGE transaction
    await _db.wallet_transactions.insert_one({
        "organization_id": organization_id,
        "type":            "RECHARGE",
        "source":          "RAZORPAY",
        "feature":         None,
        "amount_inr":      amount_inr,
        "credits":         credits_to_add,
        "status":          "SUCCESS",
        "reference_id":    payment_id,
        "created_at":      now,
    })

    updated_wallet = await _db.wallets.find_one({"organization_id": organization_id})
    new_balance = updated_wallet.get("balance", credits_to_add)

    logger.info(
        f"[WEBHOOK] ✅ Payment captured. "
        f"org={organization_id} +{credits_to_add} credits → balance={new_balance}"
    )

    return {"status": "ok"}



# ── Internal: AI Usage debit ───────────────────────────────────────────────────

@wallet_router.post("/wallet/debit")
async def debit_ai_usage(body: UsageDebitRequest):
    """
    Deduct credits for an AI feature usage event.

    Called internally by the pipeline or the dashboard.
    ``credits`` in the request is a positive integer (e.g. 3 for RISK_PREDICTION).
    The ledger stores it as a negative value (e.g. -3).

    If the wallet has insufficient credits, returns 402 Payment Required.
    """
    _require_db()
    await _get_org_or_404(body.organization_id)
    wallet = await _get_wallet_or_404(body.organization_id)

    current_balance = wallet.get("balance", 0)
    if current_balance < body.credits:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient credits. Balance={current_balance}, required={body.credits}",
        )

    now = datetime.utcnow()
    deduct = -abs(body.credits)     # always negative in ledger

    await _db.wallets.update_one(
        {"organization_id": body.organization_id},
        {
            "$inc": {"balance": deduct},
            "$set": {"updated_at": now},
        },
    )

    await _db.wallet_transactions.insert_one({
        "organization_id": body.organization_id,
        "type":            "AI_USAGE",
        "source":          "SYSTEM",
        "feature":         body.feature.upper(),
        "amount_inr":      None,
        "credits":         deduct,
        "status":          "SUCCESS",
        "reference_id":    None,
        "created_at":      now,
    })

    updated = await _db.wallets.find_one({"organization_id": body.organization_id})
    new_balance = updated.get("balance", 0)

    logger.info(
        f"[WALLET] AI_USAGE debit: org={body.organization_id} "
        f"feature={body.feature} credits={deduct} → balance={new_balance}"
    )

    return {
        "status":          "SUCCESS",
        "feature":         body.feature.upper(),
        "credits_debited": abs(body.credits),
        "new_balance":     new_balance,
        "currency":        "CREDIT",
    }


# ── Credit Engine (Python API) ─────────────────────────────────────────────────

async def consume_credits(organization_id: str, feature: str, amount: int) -> bool:
    """
    Core backend function to deduct credits directly from the server pipeline.
    
    Returns:
        True  if successful (or if amount is 0).
        False if insufficient funds (or db not ready).
    """
    if _db is None or amount <= 0:
        return amount <= 0
        
    try:
        wallet = await _get_wallet_or_404(organization_id)
        current_balance = wallet.get("balance", 0)
        
        if current_balance < amount:
            logger.warning(
                f"[BILLING] REJECTED {feature}: org={organization_id} "
                f"req={amount} > bal={current_balance}"
            )
            return False
            
        now = datetime.utcnow()
        deduct = -abs(amount)
        
        await _db.wallets.update_one(
            {"organization_id": organization_id},
            {
                "$inc": {"balance": deduct},
                "$set": {"updated_at": now},
            },
        )
        
        await _db.wallet_transactions.insert_one({
            "organization_id": organization_id,
            "type":            "AI_USAGE",
            "source":          "SYSTEM",
            "feature":         feature.upper(),
            "amount_inr":      None,
            "credits":         deduct,
            "status":          "SUCCESS",
            "reference_id":    None,
            "created_at":      now,
        })
        
        return True
        
    except Exception as e:
        logger.error(f"[BILLING] Error during consume_credits: {e}")
        return False


# ── Feature Configuration endpoints ───────────────────────────────────────────

@wallet_router.get("/org/{org_id}/features")
async def get_org_features(org_id: str):
    """
    Return the enabled/disabled feature set for an organisation.
    Defaults to all-enabled if not yet configured.
    """
    _require_db()
    await _get_org_or_404(org_id)

    doc = await _db.org_features.find_one({"organization_id": org_id})
    if doc:
        doc.pop("_id", None)
        doc.pop("organization_id", None)
        doc.pop("updated_at", None)
        features = doc
    else:
        features = DEFAULT_FEATURES.copy()

    # Calculate cost per intelligence unit based on enabled features
    cost = sum(FEATURE_PRICING[k] for k, v in features.items() if v and k in FEATURE_PRICING)

    return {
        "organization_id":       org_id,
        "features":              features,
        "credits_per_unit":      cost,
        "pricing_table":         FEATURE_PRICING,
        "low_balance_threshold": LOW_BALANCE_THRESHOLD,
    }


@wallet_router.put("/org/{org_id}/features")
async def update_org_features(org_id: str, body: OrgFeaturesUpdate):
    """
    Update the enabled/disabled feature set for an organisation.
    Returns the new configuration and recalculated cost per intelligence unit.
    """
    _require_db()
    await _get_org_or_404(org_id)

    features = body.model_dump()
    now = datetime.utcnow()

    await _db.org_features.update_one(
        {"organization_id": org_id},
        {"$set": {**features, "organization_id": org_id, "updated_at": now}},
        upsert=True,
    )

    cost = sum(FEATURE_PRICING[k] for k, v in features.items() if v and k in FEATURE_PRICING)

    logger.info(f"[FEATURES] org={org_id} updated features. cost/unit={cost}")

    return {
        "organization_id":  org_id,
        "features":         features,
        "credits_per_unit": cost,
        "pricing_table":    FEATURE_PRICING,
    }


async def get_org_features_internal(organization_id: str) -> dict:
    """
    Internal Python helper for server.py to fetch feature flags without HTTP.
    Returns DEFAULT_FEATURES if DB unavailable or org not configured.
    """
    if _db is None:
        return DEFAULT_FEATURES.copy()
    try:
        doc = await _db.org_features.find_one({"organization_id": organization_id})
        if doc:
            return {k: doc.get(k, True) for k in DEFAULT_FEATURES}
        return DEFAULT_FEATURES.copy()
    except Exception:
        return DEFAULT_FEATURES.copy()


# ── x402 API usage recorder ────────────────────────────────────────────────────

async def record_x402_usage(
    organization_id: str,
    tx_hash: str | None = None,
    amount_usd: float = 0.001,
    feature: str = "CROWD_PREDICT",
) -> None:
    """
    Fire-and-forget: record an x402 API payment as a separate ledger entry.
    Does NOT touch the Croudify Credit wallet balance.
    """
    if _db is None:
        return
    try:
        await _db.wallet_transactions.insert_one({
            "organization_id": organization_id,
            "type":            "X402_API",
            "source":          "x402",
            "feature":         feature,
            "amount_inr":      None,
            "amount_usd":      amount_usd,
            "credits":         0,            # x402 payments don't consume Credits
            "status":          "SUCCESS",
            "reference_id":    tx_hash,
            "created_at":      datetime.utcnow(),
        })
        logger.info(f"[x402] Recorded API usage: org={organization_id} tx={tx_hash} ${amount_usd}")
    except Exception as e:
        logger.error(f"[x402] Failed to record usage: {e}")


# ── Billing summary endpoint ───────────────────────────────────────────────────

@wallet_router.get("/wallet/{org_id}/billing-summary")
async def get_billing_summary(org_id: str):
    """
    Returns the full billing summary for the billing page:
    - Current balance
    - Today's credit usage (AI_USAGE transactions since UTC midnight)
    - Per-feature breakdown for today
    - Last 50 transactions (Credits + x402)
    """
    _require_db()
    await _get_org_or_404(org_id)

    wallet = await _db.wallets.find_one({"organization_id": org_id})
    balance = wallet.get("balance", 0) if wallet else 0

    # Today's UTC midnight
    from datetime import datetime, timezone
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # All transactions today
    cursor = _db.wallet_transactions.find(
        {"organization_id": org_id, "created_at": {"$gte": today_start}},
        sort=[("created_at", -1)]
    )
    today_txns = await cursor.to_list(500)

    # Today's credit usage (negative credits = spend)
    today_credit_usage = sum(
        abs(t["credits"]) for t in today_txns
        if t.get("type") == "AI_USAGE" and t.get("credits", 0) < 0
    )

    # Per-feature breakdown for today
    feature_breakdown: dict[str, int] = {}
    for t in today_txns:
        if t.get("type") == "AI_USAGE" and t.get("credits", 0) < 0:
            feat = t.get("feature", "UNKNOWN")
            feature_breakdown[feat] = feature_breakdown.get(feat, 0) + abs(t["credits"])

    # x402 API call count today
    x402_calls_today = sum(1 for t in today_txns if t.get("type") == "X402_API")

    # Last 50 transactions (all types)
    cursor_all = _db.wallet_transactions.find(
        {"organization_id": org_id},
        sort=[("created_at", -1)]
    ).limit(50)
    recent = await cursor_all.to_list(50)

    def _fmt_txn(t: dict) -> dict:
        created = t.get("created_at")
        time_str = created.strftime("%H:%M") if created else "—"
        txn_type = t.get("type", "")
        feature  = t.get("feature", "")
        if txn_type == "RECHARGE":
            label = "Wallet Recharge"
        elif txn_type == "X402_API":
            label = f"x402 API — {feature}"
        else:
            label = feature or txn_type
        return {
            "type":       txn_type,
            "credits":    t.get("credits", 0),
            "amount_usd": t.get("amount_usd"),
            "label":      label,
            "status":     t.get("status", "SUCCESS"),
            "reference":  t.get("reference_id"),
            "time":       time_str,
        }

    return {
        "organization_id":    org_id,
        "balance":            balance,
        "low_balance":        balance <= LOW_BALANCE_THRESHOLD,
        "low_balance_threshold": LOW_BALANCE_THRESHOLD,
        "today_credit_usage": today_credit_usage,
        "today_x402_calls":   x402_calls_today,
        "feature_breakdown":  feature_breakdown,
        "recent_transactions": [_fmt_txn(t) for t in recent],
        "currency":           "CREDIT",
    }
