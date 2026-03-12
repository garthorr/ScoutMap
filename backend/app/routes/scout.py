"""Scout-facing API and admin roster/data endpoints."""

import csv
import io
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import Optional

from app.database import get_db
from app.models import (
    FundraiserEvent, EventHouse, MasterHouse, Visit, ScoutRoster,
)

router = APIRouter(prefix="/api/scout", tags=["scout"])


# ---------------------------------------------------------------------------
# Roster CRUD (admin-managed)
# ---------------------------------------------------------------------------
class RosterCreate(BaseModel):
    name: str
    scout_id: Optional[str] = None


class RosterOut(BaseModel):
    id: str
    name: str
    scout_id: Optional[str] = None
    active: bool = True

    class Config:
        from_attributes = True


@router.get("/roster", response_model=list[RosterOut])
def list_roster(active_only: bool = False, db: Session = Depends(get_db)):
    q = db.query(ScoutRoster)
    if active_only:
        q = q.filter(ScoutRoster.active == True)  # noqa: E712
    return [
        RosterOut(id=str(s.id), name=s.name, scout_id=s.scout_id, active=s.active)
        for s in q.order_by(ScoutRoster.name).all()
    ]


@router.post("/roster", response_model=RosterOut)
def add_scout(body: RosterCreate, db: Session = Depends(get_db)):
    s = ScoutRoster(name=body.name, scout_id=body.scout_id)
    db.add(s)
    db.commit()
    db.refresh(s)
    return RosterOut(id=str(s.id), name=s.name, scout_id=s.scout_id, active=s.active)


@router.delete("/roster/{roster_id}")
def remove_scout(roster_id: str, db: Session = Depends(get_db)):
    s = db.query(ScoutRoster).filter(ScoutRoster.id == roster_id).first()
    if not s:
        raise HTTPException(404, "Scout not found")
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.post("/roster/import")
async def import_roster_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Import scouts from a CSV file.

    Expected columns (header row required):
      name  — Scout's full name (required)
      scout_id — Scout ID number (optional)

    Extra columns are ignored. Duplicate names (case-insensitive) are skipped.
    """
    content = await file.read()
    text = content.decode("utf-8-sig")  # handle BOM from Excel
    reader = csv.DictReader(io.StringIO(text))

    # Normalize header names: strip whitespace, lowercase
    if reader.fieldnames:
        reader.fieldnames = [f.strip().lower() for f in reader.fieldnames]

    if not reader.fieldnames or "name" not in reader.fieldnames:
        raise HTTPException(
            400,
            "CSV must have a header row with at least a 'name' column. "
            "Optional: 'scout_id'. Example:\n\nname,scout_id\nJohn Smith,12345\nJane Doe,",
        )

    # Load existing names for dedup
    existing = {
        s.name.strip().lower()
        for s in db.query(ScoutRoster.name).all()
        if s.name
    }

    added = 0
    skipped = 0
    for row in reader:
        name = (row.get("name") or "").strip()
        if not name:
            skipped += 1
            continue
        if name.lower() in existing:
            skipped += 1
            continue

        scout_id = (row.get("scout_id") or row.get("id") or "").strip() or None
        db.add(ScoutRoster(name=name, scout_id=scout_id))
        existing.add(name.lower())
        added += 1

    db.commit()
    return {"added": added, "skipped": skipped}


@router.patch("/roster/{roster_id}")
def toggle_scout(roster_id: str, db: Session = Depends(get_db)):
    s = db.query(ScoutRoster).filter(ScoutRoster.id == roster_id).first()
    if not s:
        raise HTTPException(404, "Scout not found")
    s.active = not s.active
    db.commit()
    return {"id": str(s.id), "active": s.active}


# ---------------------------------------------------------------------------
# Scout-facing endpoints
# ---------------------------------------------------------------------------
@router.get("/events")
def list_scout_events(db: Session = Depends(get_db)):
    """Return events with their walk group labels."""
    events = db.query(FundraiserEvent).order_by(
        FundraiserEvent.created_at.desc()
    ).all()

    # Batch-fetch all distinct group labels for all events in one query
    event_ids = [ev.id for ev in events]
    groups_by_event = defaultdict(list)
    if event_ids:
        group_rows = (
            db.query(EventHouse.event_id, EventHouse.assigned_to)
            .filter(EventHouse.event_id.in_(event_ids), EventHouse.assigned_to.isnot(None))
            .distinct()
            .all()
        )
        for event_id, label in group_rows:
            groups_by_event[event_id].append(label)

    return [
        {
            "id": str(ev.id),
            "name": ev.name,
            "event_date": ev.event_date.isoformat() if ev.event_date else None,
            "groups": sorted(groups_by_event.get(ev.id, [])),
        }
        for ev in events
    ]


@router.get("/events/{event_id}/houses")
def list_group_houses(
    event_id: str,
    group: str = Query(..., description="Walk group label"),
    db: Session = Depends(get_db),
):
    """Return houses in a specific walk group, with visit status."""
    houses = (
        db.query(EventHouse)
        .join(MasterHouse, EventHouse.house_id == MasterHouse.id)
        .options(joinedload(EventHouse.house), joinedload(EventHouse.visits))
        .filter(
            EventHouse.event_id == event_id,
            EventHouse.assigned_to == group,
        )
        .order_by(MasterHouse.address_number)
        .all()
    )
    result = []
    for eh in houses:
        last_visit = eh.visits[-1] if eh.visits else None
        result.append({
            "event_house_id": str(eh.id),
            "event_id": str(eh.event_id),
            "address": eh.house.full_address,
            "owner_name": eh.house.owner_name,
            "status": eh.status,
            "visited": bool(last_visit),
            "last_visit": {
                "door_answer": last_visit.door_answer,
                "donation_given": last_visit.donation_given,
                "donation_amount": last_visit.donation_amount,
                "former_scout": last_visit.former_scout,
                "avoid_house": last_visit.avoid_house,
                "notes": last_visit.notes,
            } if last_visit else None,
        })
    return result


# ---------------------------------------------------------------------------
# Admin: scout data aggregation
# ---------------------------------------------------------------------------
@router.get("/data")
def scout_data(
    event_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Return all visit data entered by scouts, with house/event info."""
    q = (
        db.query(Visit)
        .join(EventHouse, Visit.event_house_id == EventHouse.id)
        .join(MasterHouse, EventHouse.house_id == MasterHouse.id)
        .join(FundraiserEvent, EventHouse.event_id == FundraiserEvent.id)
        .options(
            joinedload(Visit.event_house).joinedload(EventHouse.house),
            joinedload(Visit.event_house).joinedload(EventHouse.event),
        )
        .filter(Visit.scout_name.isnot(None))
    )
    if event_id:
        q = q.filter(EventHouse.event_id == event_id)

    visits = q.order_by(Visit.visited_at.desc()).all()
    result = []
    for v in visits:
        result.append({
            "id": str(v.id),
            "visited_at": v.visited_at.isoformat() if v.visited_at else None,
            "scout_name": v.scout_name,
            "scout_id": v.scout_id,
            "address": v.event_house.house.full_address,
            "zip_code": v.event_house.house.zip_code,
            "group_label": v.event_house.assigned_to,
            "event_name": v.event_house.event.name,
            "event_id": str(v.event_house.event_id),
            "door_answer": v.door_answer,
            "donation_given": v.donation_given,
            "donation_amount": v.donation_amount,
            "former_scout": v.former_scout,
            "avoid_house": v.avoid_house,
            "notes": v.notes,
            "outcome": v.outcome,
        })
    return result


@router.get("/data/summary")
def scout_data_summary(
    event_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Aggregate stats for scout data."""
    q = (
        db.query(Visit)
        .join(EventHouse, Visit.event_house_id == EventHouse.id)
        .filter(Visit.scout_name.isnot(None))
    )
    if event_id:
        q = q.filter(EventHouse.event_id == event_id)

    visits = q.all()
    scouts = {}
    for v in visits:
        key = v.scout_name or "Unknown"
        if key not in scouts:
            scouts[key] = {
                "scout_name": key,
                "scout_id": v.scout_id,
                "total_visits": 0,
                "doors_answered": 0,
                "donations": 0,
                "donation_total": 0.0,
                "former_scouts": 0,
                "avoid_houses": 0,
            }
        s = scouts[key]
        s["total_visits"] += 1
        if v.door_answer:
            s["doors_answered"] += 1
        if v.donation_given:
            s["donations"] += 1
            s["donation_total"] += v.donation_amount or 0
        if v.former_scout:
            s["former_scouts"] += 1
        if v.avoid_house:
            s["avoid_houses"] += 1

    return {
        "total_visits": len(visits),
        "total_donations": sum(v.donation_amount or 0 for v in visits if v.donation_given),
        "scouts": sorted(scouts.values(), key=lambda s: s["total_visits"], reverse=True),
    }
