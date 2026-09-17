"""Pydantic schemas for visitor lead capture."""

import re
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class VisitorCreate(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)
    full_name: str | None = Field(default=None, max_length=255)
    company: str | None = Field(default=None, max_length=255)
    role: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)
    source: str = Field(default="website_modal", max_length=100)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if not EMAIL_REGEX.match(cleaned):
            raise ValueError("Invalid email address format")
        return cleaned


class VisitorOut(BaseModel):
    id: str
    email: str
    full_name: str | None = None
    company: str | None = None
    role: str | None = None
    notes: str | None = None
    source: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class VisitorListResponse(BaseModel):
    total: int
    items: list[VisitorOut]


class VisitorRegisterResponse(BaseModel):
    status: str
    message: str
    lead: VisitorOut
