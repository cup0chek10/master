from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class UserData(BaseModel):
    id: int
    first_name: str = "Гостя"
    username: str | None = None


class BookingCreate(BaseModel):
    service_id: int = Field(gt=0)
    start_time: datetime


class RescheduleRequest(BaseModel):
    start_time: datetime


class MasterDecision(BaseModel):
    booking_id: int = Field(gt=0)
    decision: Literal["confirm", "reject"]


class NotificationPreference(BaseModel):
    enabled: bool


class BookingView(BaseModel):
    id: int
    service: str
    category: str
    duration_minutes: int
    start_time: datetime
    end_time: datetime
    status: str


class SlotQuery(BaseModel):
    service_id: int = Field(gt=0)
    date: date
