"""Scout-facing API and admin roster/data endpoints."""

import csv
import io
import json
import re
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session, contains_eager, joinedload
from sqlalchemy import func, case
from typing import Optional

from app.database import get_db
from app.models import (
    AuthSession, FundraiserEvent, EventHouse, MasterHouse, Visit, ScoutRoster,
)
from app.routes.auth import _short_names, get_current_user, new_scout_password, require_admin

router = APIRouter(prefix="/api/scout", tags=["scout"])


def natural_key(label: str) -> list:
    """Sort key so "Group 2" comes before "Group 10"."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", label or "")]


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
    has_password: bool = False
    password: Optional[str] = None  # only filled in right after it's generated

    class Config:
        from_attributes = True


@router.get("/roster", response_model=list[RosterOut])
def list_roster(
    active_only: bool = False,
    user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Scouts only see "First L." — full names are for admins
    is_scout = user.startswith("scout:")
    q = db.query(ScoutRoster)
    if active_only:
        q = q.filter(ScoutRoster.active == True)  # noqa: E712
    scouts = q.order_by(ScoutRoster.name).all()
    names = _short_names([s.name for s in scouts]) if is_scout else [s.name for s in scouts]
    return [
        RosterOut(id=str(s.id), name=name,
                  scout_id=None if is_scout else s.scout_id, active=s.active,
                  has_password=bool(s.password_hash))
        for s, name in zip(scouts, names)
    ]


@router.post("/roster", response_model=RosterOut)
def add_scout(body: RosterCreate, _admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    s = ScoutRoster(name=body.name.strip(), scout_id=body.scout_id)
    db.add(s)
    db.flush()  # assigns s.id
    password = new_scout_password(s, db)
    db.commit()
    return RosterOut(id=str(s.id), name=s.name, scout_id=s.scout_id, active=s.active,
                     has_password=True, password=password)


@router.delete("/roster/{roster_id}")
def remove_scout(roster_id: str, _admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    s = db.query(ScoutRoster).filter(ScoutRoster.id == roster_id).first()
    if not s:
        raise HTTPException(404, "Scout not found")
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.post("/roster/import")
async def import_roster_csv(file: UploadFile = File(...), _admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    """Import scouts from a CSV file.

    Expected columns (header row required):
      name  — Scout's full name (required)
      scout_id — Scout ID number (optional)

    Extra columns are ignored. Duplicate names (case-insensitive) are skipped.
    Each new scout gets a random 6-digit password, returned once in the response.
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

    added = []
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
        scout = ScoutRoster(name=name, scout_id=scout_id)
        db.add(scout)
        db.flush()  # assigns scout.id
        added.append({"name": name, "password": new_scout_password(scout, db)})
        existing.add(name.lower())

    db.commit()
    return {"added": len(added), "skipped": skipped, "passwords": added}


@router.patch("/roster/{roster_id}")
def toggle_scout(roster_id: str, _admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    s = db.query(ScoutRoster).filter(ScoutRoster.id == roster_id).first()
    if not s:
        raise HTTPException(404, "Scout not found")
    s.active = not s.active
    if not s.active:
        # Deactivating signs the scout out everywhere
        db.query(AuthSession).filter(AuthSession.email == f"scout:{s.id}").delete()
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
            "groups": sorted(groups_by_event.get(ev.id, []), key=natural_key),
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
        .options(contains_eager(EventHouse.house), joinedload(EventHouse.visits))
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
                "custom_data": json.loads(last_visit.custom_data) if last_visit.custom_data else None,
            } if last_visit else None,
        })
    return result


# ---------------------------------------------------------------------------
# Admin: scout data aggregation
# ---------------------------------------------------------------------------
@router.get("/data")
def scout_data(
    event_id: Optional[str] = None,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return one page of visit data entered by scouts, newest first.

    The summary endpoint's total_visits is the full count for paging.
    """
    q = (
        db.query(Visit)
        .join(EventHouse, Visit.event_house_id == EventHouse.id)
        .join(MasterHouse, EventHouse.house_id == MasterHouse.id)
        .join(FundraiserEvent, EventHouse.event_id == FundraiserEvent.id)
        # Reuse the joins above instead of joining the same tables again
        .options(
            contains_eager(Visit.event_house).contains_eager(EventHouse.house),
            contains_eager(Visit.event_house).contains_eager(EventHouse.event),
        )
        .filter(Visit.scout_name.isnot(None))
    )
    if event_id:
        q = q.filter(EventHouse.event_id == event_id)

    visits = q.order_by(Visit.visited_at.desc(), Visit.id).offset(offset).limit(limit).all()
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
            "custom_data": json.loads(v.custom_data) if v.custom_data else None,
        })
    return result


@router.get("/data/summary")
def scout_data_summary(
    event_id: Optional[str] = None,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Aggregate stats for scout data using SQL GROUP BY."""
    q = (
        db.query(
            func.coalesce(Visit.scout_name, "Unknown").label("scout_name"),
            Visit.scout_id,
            func.count(Visit.id).label("total_visits"),
            func.count(case((Visit.door_answer == True, 1))).label("doors_answered_raw"),
            func.count(case((Visit.donation_given == True, 1))).label("donations_raw"),
            func.coalesce(
                func.sum(case((Visit.donation_given == True, Visit.donation_amount), else_=0)),
                0,
            ).label("donation_total"),
            func.count(case((Visit.former_scout == True, 1))).label("former_scouts_raw"),
            func.count(case((Visit.avoid_house == True, 1))).label("avoid_houses_raw"),
        )
        .join(EventHouse, Visit.event_house_id == EventHouse.id)
        .filter(Visit.scout_name.isnot(None))
    )
    if event_id:
        q = q.filter(EventHouse.event_id == event_id)

    rows = q.group_by(func.coalesce(Visit.scout_name, "Unknown"), Visit.scout_id).all()

    scouts = []
    total_visits = 0
    total_donations = 0.0
    for r in rows:
        total_visits += r.total_visits
        donation_total = float(r.donation_total or 0)
        total_donations += donation_total
        scouts.append({
            "scout_name": r.scout_name,
            "scout_id": r.scout_id,
            "total_visits": r.total_visits,
            "doors_answered": int(r.doors_answered_raw or 0),
            "donations": int(r.donations_raw or 0),
            "donation_total": donation_total,
            "former_scouts": int(r.former_scouts_raw or 0),
            "avoid_houses": int(r.avoid_houses_raw or 0),
        })

    scouts.sort(key=lambda s: s["total_visits"], reverse=True)

    return {
        "total_visits": total_visits,
        "total_donations": total_donations,
        "scouts": scouts,
    }
