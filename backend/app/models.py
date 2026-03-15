import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Float, Text, Boolean, DateTime, Integer,
    ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


# ---------------------------------------------------------------------------
# Source imports – one row per file/batch upload
# ---------------------------------------------------------------------------
class SourceImport(Base):
    __tablename__ = "source_imports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_name = Column(String(100), nullable=False)       # e.g. "dallas_gis", "dcad"
    file_name = Column(String(255))
    import_batch_id = Column(String(100), nullable=False)
    record_count = Column(Integer, default=0)
    status = Column(String(20), default="pending")          # pending / running / completed / failed
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    notes = Column(Text)

    links = relationship("HouseSourceLink", back_populates="source_import")


# ---------------------------------------------------------------------------
# Master houses – canonical address record, one per physical location
# ---------------------------------------------------------------------------
class MasterHouse(Base):
    __tablename__ = "master_houses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Normalized address fields
    address_number = Column(String(20))
    street_name = Column(String(200))
    unit = Column(String(50))
    city = Column(String(100), default="Dallas")
    state = Column(String(2), default="TX")
    zip_code = Column(String(10), index=True)
    full_address = Column(String(500), nullable=False)
    normalized_address = Column(String(500), nullable=False, index=True)

    latitude = Column(Float)
    longitude = Column(Float)

    # Owner / resident info (from public data)
    owner_name = Column(String(300))
    resident_name = Column(String(300))

    # Parcel / appraisal identifiers
    parcel_id = Column(String(100))
    account_number = Column(String(100))

    # Appraisal data from DCAD
    legal_description = Column(Text)
    land_value = Column(Float)
    improvement_value = Column(Float)
    total_appraised_value = Column(Float)

    # Manual entry flag
    manually_created = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    source_links = relationship("HouseSourceLink", back_populates="house")
    event_houses = relationship("EventHouse", back_populates="house")

    __table_args__ = (
        Index("ix_master_houses_normalized", "normalized_address"),
        Index("ix_master_houses_parcel", "parcel_id"),
        Index("ix_master_houses_latlon", "latitude", "longitude"),
    )


# ---------------------------------------------------------------------------
# House source links – provenance for each master house record
# ---------------------------------------------------------------------------
class HouseSourceLink(Base):
    __tablename__ = "house_source_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    house_id = Column(UUID(as_uuid=True), ForeignKey("master_houses.id"), nullable=False, index=True)
    source_import_id = Column(UUID(as_uuid=True), ForeignKey("source_imports.id"), nullable=False, index=True)
    source_name = Column(String(100), nullable=False)
    source_record_id = Column(String(200))
    source_last_updated = Column(DateTime)
    import_batch_id = Column(String(100))
    raw_data = Column(Text)                                  # JSON snapshot of source row
    match_method = Column(String(50))                        # exact / normalized / manual
    created_at = Column(DateTime, default=datetime.utcnow)

    house = relationship("MasterHouse", back_populates="source_links")
    source_import = relationship("SourceImport", back_populates="links")

    __table_args__ = (
        UniqueConstraint("house_id", "source_import_id", "source_record_id",
                         name="uq_house_source_link"),
    )


# ---------------------------------------------------------------------------
# Fundraiser events
# ---------------------------------------------------------------------------
class FundraiserEvent(Base):
    __tablename__ = "fundraiser_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    event_date = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    event_houses = relationship("EventHouse", back_populates="event")


# ---------------------------------------------------------------------------
# Event houses – junction between an event and master houses
# ---------------------------------------------------------------------------
class EventHouse(Base):
    __tablename__ = "event_houses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("fundraiser_events.id"), nullable=False, index=True)
    house_id = Column(UUID(as_uuid=True), ForeignKey("master_houses.id"), nullable=False, index=True)
    assigned_to = Column(String(200))
    priority = Column(Integer, default=0)
    status = Column(String(30), default="pending")          # pending / visited / skipped
    created_at = Column(DateTime, default=datetime.utcnow)

    event = relationship("FundraiserEvent", back_populates="event_houses")
    house = relationship("MasterHouse", back_populates="event_houses")
    visits = relationship("Visit", back_populates="event_house")

    __table_args__ = (
        UniqueConstraint("event_id", "house_id", name="uq_event_house"),
        Index("ix_event_houses_status", "status"),
        Index("ix_event_houses_assigned_to", "assigned_to"),
    )


# ---------------------------------------------------------------------------
# Visits – outcome of knocking on a door
# ---------------------------------------------------------------------------
class Visit(Base):
    __tablename__ = "visits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_house_id = Column(UUID(as_uuid=True), ForeignKey("event_houses.id"), nullable=False, index=True)
    visited_at = Column(DateTime, default=datetime.utcnow)
    outcome = Column(String(50))                             # donated / pledged / not_home / refused / other
    donation_amount = Column(Float)
    tickets_purchased = Column(Integer, default=0)
    notes = Column(Text)
    follow_up = Column(Boolean, default=False)
    volunteer_name = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)

    # Scout-entered fields
    scout_name = Column(String(200))
    scout_id = Column(String(100))
    door_answer = Column(Boolean)                            # true = answered, false = no answer
    donation_given = Column(Boolean)                         # true = donated
    former_scout = Column(Boolean)                           # true = former scout
    avoid_house = Column(Boolean, default=False)             # flag to avoid in future

    # Dynamic form fields (JSON dict keyed by field_key)
    custom_data = Column(Text)                               # JSON string

    event_house = relationship("EventHouse", back_populates="visits")

    __table_args__ = (
        Index("ix_visits_visited_at", "visited_at"),
        Index("ix_visits_scout_name", "scout_name"),
    )


# ---------------------------------------------------------------------------
# Scout form fields – admin-configurable fields for the scout visit form
# ---------------------------------------------------------------------------
class ScoutFormField(Base):
    __tablename__ = "scout_form_fields"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    field_key = Column(String(100), nullable=False, unique=True)   # slug identifier
    label = Column(String(200), nullable=False)                     # display label
    field_type = Column(String(30), nullable=False)                 # toggle, checkbox, text, number, textarea, select
    required = Column(Boolean, default=False)
    position = Column(Integer, default=0)                           # sort order
    options = Column(Text)                                          # JSON array for select fields
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_scout_form_fields_position", "position"),
    )


# ---------------------------------------------------------------------------
# Unmatched records – staging for admin review
# ---------------------------------------------------------------------------
class UnmatchedRecord(Base):
    __tablename__ = "unmatched_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_import_id = Column(UUID(as_uuid=True), ForeignKey("source_imports.id"), nullable=False)
    source_name = Column(String(100), nullable=False)
    source_record_id = Column(String(200))
    raw_address = Column(String(500))
    raw_data = Column(Text)
    status = Column(String(20), default="pending")          # pending / resolved / ignored
    resolved_house_id = Column(UUID(as_uuid=True), ForeignKey("master_houses.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Scout roster – managed by admin, used in scout app dropdown
# ---------------------------------------------------------------------------
class ScoutRoster(Base):
    __tablename__ = "scout_roster"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    scout_id = Column(String(100))
    password_hash = Column(String(200))  # bcrypt hash, set by admin
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Auth – email allowlist, OTP codes, sessions
# ---------------------------------------------------------------------------
class AllowedEmail(Base):
    __tablename__ = "allowed_emails"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(320), nullable=False, unique=True)  # exact or wildcard like "*@example.com"
    created_at = Column(DateTime, default=datetime.utcnow)


class AuthCode(Base):
    __tablename__ = "auth_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(320), nullable=False, index=True)
    code = Column(String(6), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_auth_codes_email_used", "email", "used"),
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token = Column(String(64), nullable=False, unique=True, index=True)
    email = Column(String(320), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
