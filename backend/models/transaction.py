"""models/transaction.py — Transaction type enum."""
from enum import Enum


class TransactionType(str, Enum):
    RECHARGE     = "RECHARGE"
    AI_USAGE     = "AI_USAGE"
    X402_API     = "X402_API"
    ADJUSTMENT   = "ADJUSTMENT"
