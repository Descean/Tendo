"""Schemas Pydantic pour Publication."""

from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field


class PublicationBase(BaseModel):
    source: str
    reference: str
    title: str
    summary: Optional[str] = None
    budget: Optional[float] = None
    deadline: Optional[datetime] = None
    pdf_url: Optional[str] = None
    category: str = "marché"
    sectors: List[str] = Field(default_factory=list)
    regions: List[str] = Field(default_factory=list)


class PublicationCreate(PublicationBase):
    html_content: Optional[str] = None
    published_date: Optional[datetime] = None
    authority_email: Optional[str] = None
    authority_name: Optional[str] = None


class PublicationResponse(PublicationBase):
    id: int
    published_date: Optional[datetime] = None
    created_at: datetime
    is_processed: bool

    model_config = {"from_attributes": True}


class PublicationSearch(BaseModel):
    query: Optional[str] = None
    source: Optional[str] = None
    category: Optional[str] = None
    sectors: Optional[List[str]] = None
    regions: Optional[List[str]] = None
    min_budget: Optional[float] = None
    max_budget: Optional[float] = None
    deadline_before: Optional[datetime] = None
    page: int = 1
    per_page: int = 20
