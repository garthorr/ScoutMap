"""Event endpoints – create events and assign houses."""

import json
import re
import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_
from typing import Optional

from app.database import get_db
from app.models import FundraiserEvent, EventHouse, MasterHouse, Visit, ScoutRoster
from app.schemas import (
    EventCreate, EventOut, EventAssignRequest,
    EventHouseOut, VisitCreate, VisitOut,
)
from app.routes.auth import get_current_user, require_admin

router = APIRouter(prefix="/api/events", tags=["events"])


def _enrich_event(event: FundraiserEvent, db: Session) -> dict:
    count = db.query(func.count(EventHouse.id)).filter(
        EventHouse.event_id == event.id
    ).scalar()
    return EventOut(
        id=event.id,
        name=event.name,
        description=event.description,
        event_date=event.event_date,
        created_at=event.created_at,
        house_count=count or 0,
    )


@router.post("/", response_model=EventOut)
def create_event(body: EventCreate, _admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    event = FundraiserEvent(
        name=body.name,
        description=body.description,
        event_date=body.event_date,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return _enrich_event(event, db)


@router.get("/", response_model=list[EventOut])
def list_events(_admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    # Single query: get all events with house counts via subquery
    count_sub = (
        db.query(EventHouse.event_id, func.count(EventHouse.id).label("cnt"))
        .group_by(EventHouse.event_id)
        .subquery()
    )
    rows = (
        db.query(FundraiserEvent, func.coalesce(count_sub.c.cnt, 0))
        .outerjoin(count_sub, FundraiserEvent.id == count_sub.c.event_id)
        .order_by(FundraiserEvent.created_at.desc())
        .all()
    )
    return [
        EventOut(
            id=ev.id, name=ev.name, description=ev.description,
            event_date=ev.event_date, created_at=ev.created_at,
            house_count=cnt,
        )
        for ev, cnt in rows
    ]


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: str, _admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    event = db.query(FundraiserEvent).filter(FundraiserEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Event not found")
    return _enrich_event(event, db)


class EventUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    event_date: Optional[str] = None


@router.put("/{event_id}", response_model=EventOut)
def update_event(
    event_id: str,
    body: EventUpdate,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    event = db.query(FundraiserEvent).filter(FundraiserEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Event not found")
    if body.name is not None:
        event.name = body.name.strip()
    if body.description is not None:
        event.description = body.description.strip() or None
    if body.event_date is not None:
        from datetime import datetime as dt
        try:
            event.event_date = dt.fromisoformat(body.event_date.replace("Z", "+00:00")) if body.event_date else None
        except ValueError:
            event.event_date = None
    db.commit()
    db.refresh(event)
    return _enrich_event(event, db)


@router.delete("/{event_id}")
def delete_event(
    event_id: str,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    event = db.query(FundraiserEvent).filter(FundraiserEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Event not found")

    # Delete visits for all event_houses in this event
    event_house_ids = [
        eh.id for eh in db.query(EventHouse).filter(EventHouse.event_id == event_id).all()
    ]
    if event_house_ids:
        db.query(Visit).filter(Visit.event_house_id.in_(event_house_ids)).delete(synchronize_session=False)
    db.query(EventHouse).filter(EventHouse.event_id == event_id).delete(synchronize_session=False)
    db.delete(event)
    db.commit()
    return {"ok": True}


@router.post("/{event_id}/assign", response_model=dict)
def assign_houses(event_id: uuid.UUID, body: EventAssignRequest, _admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    """Generate event assignments from imported master houses."""
    event = db.query(FundraiserEvent).filter(FundraiserEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Event not found")

    if body.house_ids:
        # Direct assignment by house IDs (from map selection)
        house_ids_str = body.house_ids
        houses = db.query(MasterHouse).filter(MasterHouse.id.in_(house_ids_str)).all()
        house_ids = [h.id for h in houses]
    else:
        q = db.query(MasterHouse).filter(
            MasterHouse.latitude.isnot(None),
            MasterHouse.longitude.isnot(None),
        )
        if body.zip_codes:
            q = q.filter(MasterHouse.zip_code.in_(body.zip_codes))
        if body.street_names:
            patterns = [MasterHouse.normalized_address.ilike(f"%{s.strip().upper()}%") for s in body.street_names]
            q = q.filter(or_(*patterns))
        if body.limit:
            q = q.limit(body.limit)

        houses = q.all()
        house_ids = [h.id for h in houses]

    # Batch-fetch existing assignments
    existing_ids = set()
    if house_ids:
        existing_ids = {
            row[0] for row in
            db.query(EventHouse.house_id)
            .filter(EventHouse.event_id == event.id, EventHouse.house_id.in_(house_ids))
            .all()
        }

    added = 0
    for h in houses:
        if h.id not in existing_ids:
            db.add(EventHouse(
                event_id=event.id,
                house_id=h.id,
                assigned_to=body.assigned_to,
            ))
            added += 1

    regrouped = move_to_group(db, event.id, existing_ids, body.assigned_to)
    db.commit()
    total = db.query(func.count(EventHouse.id)).filter(
        EventHouse.event_id == event.id
    ).scalar() or 0
    return {"assigned": added, "regrouped": regrouped, "total_in_event": total}


def move_to_group(db: Session, event_id, house_ids, label: Optional[str]) -> int:
    """Put houses already in the event into the named group (map selection with a group name)."""
    label = (label or "").strip()
    if not label or not house_ids:
        return 0
    ids = list(house_ids)
    moved = 0
    for i in range(0, len(ids), 500):
        moved += (
            db.query(EventHouse)
            .filter(
                EventHouse.event_id == event_id,
                EventHouse.house_id.in_(ids[i:i + 500]),
                or_(EventHouse.assigned_to.is_(None), EventHouse.assigned_to != label),
            )
            .update({"assigned_to": label}, synchronize_session=False)
        )
    return moved


class WalkGroupRequest(BaseModel):
    group_size: int = Field(20, ge=1, le=500)     # houses per group
    keep_existing: bool = True                    # only group houses not already in a group


@router.post("/{event_id}/walk-groups")
def create_walk_groups(
    event_id: uuid.UUID,
    body: WalkGroupRequest,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Auto-assign houses already in this event into walkable groups.

    Uses the houses already assigned to the event (EventHouse rows).
    Groups by street name, sorts by address number so scouts walk
    in order, then splits each street into chunks of ``group_size``.
    Each chunk becomes a numbered group label. By default, houses that
    already have a group (e.g. drawn on the map) keep it.
    """
    event = db.query(FundraiserEvent).filter(FundraiserEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Event not found")

    all_houses = (
        db.query(EventHouse)
        .options(joinedload(EventHouse.house))
        .filter(EventHouse.event_id == event.id)
        .all()
    )
    if not all_houses:
        return {"groups": [], "total_assigned": 0, "kept": 0, "skipped_no_street": 0,
                "message": "No houses in this event yet. Add houses on the map first."}

    # Houses already in a group are left alone unless the admin asked to redo everything
    existing_labels = {eh.assigned_to for eh in all_houses if eh.assigned_to}
    if body.keep_existing:
        candidates = [eh for eh in all_houses if not eh.assigned_to]
        kept = len(all_houses) - len(candidates)
    else:
        candidates = all_houses
        kept = 0
        existing_labels = set()

    event_houses = [eh for eh in candidates if eh.house and eh.house.street_name]
    skipped_no_street = len(candidates) - len(event_houses)
    if not event_houses:
        return {"groups": [], "total_assigned": 0, "kept": kept, "skipped_no_street": skipped_no_street,
                "message": "Every house is already in a group." if kept else "No houses with a street name to group."}

    # Group by street, sort within each street by address number
    by_street: dict[str, list] = defaultdict(list)
    for eh in event_houses:
        h = eh.house
        if not h or not h.street_name:
            continue
        street = h.street_name.upper().strip()
        by_street[street].append({"eh": eh, "house": h})

    for street in by_street:
        by_street[street].sort(key=lambda x: _addr_sort_key(x["house"].address_number))

    # Build groups: chunk each street into group_size, label them
    # Continue numbering after any groups that are being kept
    groups = []
    group_num = 1 + max(
        (int(m.group(1)) for m in (re.match(r"Group (\d+)", label) for label in existing_labels) if m),
        default=0,
    )
    for street in sorted(by_street.keys()):
        street_items = by_street[street]
        for i in range(0, len(street_items), body.group_size):
            chunk = street_items[i:i + body.group_size]
            first_num = chunk[0]["house"].address_number or "?"
            last_num = chunk[-1]["house"].address_number or "?"
            if first_num == last_num:
                label = f"Group {group_num} — {first_num} {street}"
            else:
                label = f"Group {group_num} — {first_num}-{last_num} {street}"
            groups.append({"label": label, "event_houses": [x["eh"] for x in chunk]})
            group_num += 1

    # Update group labels on existing EventHouse rows
    group_summaries = []
    for g in groups:
        for eh in g["event_houses"]:
            eh.assigned_to = g["label"]
        group_summaries.append({"label": g["label"], "houses": len(g["event_houses"])})

    db.commit()
    return {"groups": group_summaries, "total_assigned": len(event_houses),
            "kept": kept, "skipped_no_street": skipped_no_street}


def _addr_sort_key(addr_num: str | None) -> int:
    """Extract leading integer from address number for sorting."""
    if not addr_num:
        return 0
    digits = ""
    for c in addr_num:
        if c.isdigit():
            digits += c
        else:
            break
    return int(digits) if digits else 0


# ---------------------------------------------------------------------------
# Duplicate event (copies all house assignments, not visits)
# ---------------------------------------------------------------------------
@router.post("/{event_id}/duplicate")
def duplicate_event(
    event_id: str,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    event = db.query(FundraiserEvent).filter(FundraiserEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Event not found")

    new_event = FundraiserEvent(
        name=f"{event.name} (Copy)",
        description=event.description,
        event_date=event.event_date,
    )
    db.add(new_event)
    db.flush()

    # Copy house assignments
    ehs = db.query(EventHouse).filter(EventHouse.event_id == event_id).all()
    for eh in ehs:
        db.add(EventHouse(
            event_id=new_event.id,
            house_id=eh.house_id,
            assigned_to=eh.assigned_to,
            priority=eh.priority,
        ))
    db.commit()
    return {"ok": True, "new_event_id": str(new_event.id), "name": new_event.name, "houses_copied": len(ehs)}


# ---------------------------------------------------------------------------
# Walk group manipulation
# ---------------------------------------------------------------------------
class ReassignGroupBody(BaseModel):
    old_label: str
    new_label: str


@router.put("/{event_id}/groups/reassign")
def reassign_group(
    event_id: str,
    body: ReassignGroupBody,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Rename a walk group label."""
    updated = db.query(EventHouse).filter(
        EventHouse.event_id == event_id,
        EventHouse.assigned_to == body.old_label,
    ).update({"assigned_to": body.new_label}, synchronize_session=False)
    db.commit()
    return {"ok": True, "updated": updated}


class MergeGroupsBody(BaseModel):
    source_labels: list[str]
    target_label: str


@router.put("/{event_id}/groups/merge")
def merge_groups(
    event_id: str,
    body: MergeGroupsBody,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Merge multiple walk groups into one."""
    updated = db.query(EventHouse).filter(
        EventHouse.event_id == event_id,
        EventHouse.assigned_to.in_(body.source_labels),
    ).update({"assigned_to": body.target_label}, synchronize_session=False)
    db.commit()
    return {"ok": True, "updated": updated}


class DeleteGroupBody(BaseModel):
    label: str


@router.delete("/{event_id}/groups")
def delete_group(
    event_id: str,
    label: str = Query(...),
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Remove all house assignments for a walk group."""
    ehs = db.query(EventHouse).filter(
        EventHouse.event_id == event_id,
        EventHouse.assigned_to == label,
    ).all()
    eh_ids = [eh.id for eh in ehs]
    if eh_ids:
        db.query(Visit).filter(Visit.event_house_id.in_(eh_ids)).delete(synchronize_session=False)
    db.query(EventHouse).filter(
        EventHouse.event_id == event_id,
        EventHouse.assigned_to == label,
    ).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "removed": len(ehs)}


class RemoveEventHousesBody(BaseModel):
    event_house_ids: list[str]


@router.post("/{event_id}/houses/remove")
def remove_event_houses(
    event_id: str,
    body: RemoveEventHousesBody,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Remove specific houses from an event (unassign them)."""
    if not body.event_house_ids:
        return {"ok": True, "removed": 0}

    CHUNK = 500
    deleted = 0
    for i in range(0, len(body.event_house_ids), CHUNK):
        chunk = body.event_house_ids[i:i + CHUNK]
        # Find matching event_houses in this event
        eh_ids = [
            row[0] for row in
            db.query(EventHouse.id).filter(
                EventHouse.id.in_(chunk),
                EventHouse.event_id == event_id,
            ).all()
        ]
        if eh_ids:
            db.query(Visit).filter(Visit.event_house_id.in_(eh_ids)).delete(synchronize_session=False)
            deleted += db.query(EventHouse).filter(EventHouse.id.in_(eh_ids)).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "removed": deleted}


@router.get("/{event_id}/houses", response_model=list[EventHouseOut])
def list_event_houses(
    event_id: str,
    status: str = Query(None),
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = (
        db.query(EventHouse)
        .join(MasterHouse, EventHouse.house_id == MasterHouse.id)
        .options(joinedload(EventHouse.house))
        .filter(EventHouse.event_id == event_id)
    )
    if status:
        q = q.filter(EventHouse.status == status)
    return q.order_by(EventHouse.assigned_to, MasterHouse.address_number).all()


# --- Visits ---
@router.post("/{event_id}/houses/{event_house_id}/visits", response_model=VisitOut)
def record_visit(
    event_id: uuid.UUID,
    event_house_id: uuid.UUID,
    body: VisitCreate,
    user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scout_name, scout_id, roster_id = body.scout_name, body.scout_id, None
    is_scout = user.startswith("scout:")
    # Scout sessions: the logged-in scout. Admins: the roster scout they picked, if any.
    lookup = uuid.UUID(user.split(":", 1)[1]) if is_scout else body.roster_id
    scout = db.query(ScoutRoster).filter(ScoutRoster.id == lookup).first() if lookup else None
    if scout:
        scout_name, scout_id, roster_id = scout.name, scout.scout_id, scout.id

    eh = db.query(EventHouse).filter(
        EventHouse.id == event_house_id,
        EventHouse.event_id == event_id,
    ).first()
    if not eh:
        raise HTTPException(404, "Event house not found")

    visit = Visit(
        event_house_id=eh.id,
        outcome=body.outcome,
        donation_amount=body.donation_amount,
        tickets_purchased=body.tickets_purchased,
        notes=body.notes,
        follow_up=body.follow_up,
        volunteer_name=body.volunteer_name,
        scout_name=scout_name,
        scout_id=scout_id,
        door_answer=body.door_answer,
        donation_given=body.donation_given,
        former_scout=body.former_scout,
        avoid_house=body.avoid_house,
        custom_data=json.dumps(body.custom_data) if body.custom_data else None,
        scout_roster_id=roster_id,
        entered_by=None if is_scout else user,
    )
    eh.status = "visited"
    db.add(visit)
    db.commit()
    db.refresh(visit)
    return visit


@router.get("/{event_id}/houses/{event_house_id}/visits", response_model=list[VisitOut])
def list_visits(event_id: str, event_house_id: str, _admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    return (
        db.query(Visit)
        .filter(Visit.event_house_id == event_house_id)
        .order_by(Visit.visited_at.desc())
        .all()
    )
