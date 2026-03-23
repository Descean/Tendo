"""Schemas Pydantic pour User."""

from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field


class UserBase(BaseModel):
    phone_number: str = Field(..., min_length=10, max_length=20)
    name: str = ""
    company: Optional[str] = None
    sectors: List[str] = Field(default_factory=list)
    regions: List[str] = Field(default_factory=list)
    preferred_sources: List[str] = Field(default_factory=list)


class UserCreate(UserBase):
    pass


class UserUpdate(BaseModel):
    name: Optional[str] = None
    company: Optional[str] = None
    sectors: Optional[List[str]] = None
    regions: Optional[List[str]] = None
    preferred_sources: Optional[List[str]] = None
    email_monitoring_consent: Optional[bool] = None
    email_address: Optional[str] = None


class UserResponse(UserBase):
    id: int
    created_at: datetime
    is_active: bool
    trial_end: Optional[datetime] = None
    subscription_status: str
    subscription_plan: Optional[str] = None
    email_monitoring_consent: bool = False

    model_config = {"from_attributes": True}


class UserProfile(BaseModel):
    """Profil utilisateur pour l'API web."""
    id: int
    phone_number: str
    name: str
    company: Optional[str] = None
    sectors: List[str] = []
    regions: List[str] = []
    subscription_status: str
    subscription_plan: Optional[str] = None

    model_config = {"from_attributes": True}
