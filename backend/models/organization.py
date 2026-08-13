"""models/organization.py — Pydantic models for Organisation."""
from pydantic import BaseModel


class OrgCreate(BaseModel):
    name: str
    email: str
