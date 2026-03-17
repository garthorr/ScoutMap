"""Pydantic schemas for API request / response models."""

import json
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


# --- Source Imports ---
class SourceImportCreate(BaseModel):
    source_name: str
    notes: Optional[str] = None


class SourceImportOut(BaseModel):
    id: UUID
    source_name: str
    file_name: Optional[str] = None
    import_batch_id: str
    record_count: int
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    notes: Optional[str] = None

    class Config:
        from_attributes = True


# --- Master Houses ---
class MasterHouseOut(BaseModel):
    id: UUID
    full_address: str
    normalized_address: str
    address_number: Optional[str] = None
    street_name: Optional[str] = None
    unit: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    owner_name: Optional[str] = None
    resident_name: Optional[str] = None
    parcel_id: Optional[str] = None
    account_number: Optional[str] = None
    total_appraised_value: Optional[float] = None
    property_type: Optional[str] = None
    manually_created: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class MasterHouseCreate(BaseModel):
    full_address: str
    unit: Optional[str] = None
    city: str = "Dallas"
    state: str = "TX"
    zip_code: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    owner_name: Optional[str] = None
    notes: Optional[str] = None


# --- Events ---
class EventCreate(BaseModel):
    name: str
    description: Optional[str] = None
    event_date: Optional[datetime] = None


class EventOut(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    event_date: Optional[datetime] = None
    created_at: datetime
    house_count: int = 0

    class Config:
        from_attributes = True


class EventAssignRequest(BaseModel):
    zip_codes: Optional[list[str]] = None
    street_names: Optional[list[str]] = None
    house_ids: Optional[list[str]] = None
    limit: Optional[int] = None
    assigned_to: Optional[str] = None


# --- Event Houses ---
class EventHouseOut(BaseModel):
    id: UUID
    event_id: UUID
    house_id: UUID
    assigned_to: Optional[str] = None
    priority: int = 0
    status: str = "pending"
    house: MasterHouseOut

    class Config:
        from_attributes = True


# --- Visits ---
class VisitCreate(BaseModel):
    outcome: Optional[str] = None
    donation_amount: Optional[float] = None
    tickets_purchased: int = 0
    notes: Optional[str] = None
    follow_up: bool = False
    volunteer_name: Optional[str] = None
    # Scout-entered fields
    scout_name: Optional[str] = None
    scout_id: Optional[str] = None
    door_answer: Optional[bool] = None
    donation_given: Optional[bool] = None
    former_scout: Optional[bool] = None
    avoid_house: bool = False
    # Dynamic form fields
    custom_data: Optional[dict] = None


class VisitOut(BaseModel):
    id: UUID
    event_house_id: UUID
    visited_at: datetime
    outcome: Optional[str] = None
    donation_amount: Optional[float] = None
    tickets_purchased: int = 0
    notes: Optional[str] = None
    follow_up: bool = False
    volunteer_name: Optional[str] = None
    scout_name: Optional[str] = None
    scout_id: Optional[str] = None
    door_answer: Optional[bool] = None
    donation_given: Optional[bool] = None
    former_scout: Optional[bool] = None
    avoid_house: bool = False
    custom_data: Optional[dict] = None

    @field_validator("custom_data", mode="before")
    @classmethod
    def parse_custom_data(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return None
        return v

    class Config:
        from_attributes = True


# --- Unmatched ---
class UnmatchedRecordOut(BaseModel):
    id: UUID
    source_name: str
    source_record_id: Optional[str] = None
    raw_address: Optional[str] = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


# --- Stats ---
class DashboardStats(BaseModel):
    total_houses: int
    total_events: int
    total_visits: int
    total_donations: float
    unmatched_count: int
    import_count: int
    total_scouts: int = 0
    assigned_houses: int = 0
    houses_visited: int = 0
