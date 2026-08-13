"""models/wallet_models.py — Pydantic models for wallet and feature config."""
from pydantic import BaseModel
from typing import List, Optional


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


class CrowdPredictRequest(BaseModel):
    camera_id: Optional[str] = None
    image:     Optional[str] = None   # base64-encoded JPEG
    features:  List[str] = [
        "person_detection", "density", "movement", "speed", "direction",
        "flow", "compression", "exit_blockage", "behaviour", "risk_prediction",
    ]


class CrowdPredictResponse(BaseModel):
    risk_score:     float
    risk_level:     str
    density:        str
    movement:       str
    compression:    str
    flow_magnitude: float
    people_count:   int
    recommendation: str
    features_used:  List[str]
    credits_cost:   str
