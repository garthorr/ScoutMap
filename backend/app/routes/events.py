"""Event endpoints – create events and assign houses."""

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_
from typing import Optional

from app.database import get_db
from app.models import FundraiserEvent, EventHouse, MasterHouse, Visit
from app.schemas import (
    EventCreate, EventOut, EventAssignRequest,
    EventHouseOut, VisitCreate, VisitOut,
)
from app.routes.auth import require_admin

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
def list_events(db: Session = Depends(get_db)):
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
def get_event(event_id: str, db: Session = Depends(get_db)):
    event = db.query(FundraiserEvent).filter(FundraiserEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Event not found")
    return _enrich_event(event, db)


@router.post("/{event_id}/assign", response_model=dict)
def assign_houses(event_id: str, body: EventAssignRequest, _admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    """Generate event assignments from imported master houses."""
    event = db.query(FundraiserEvent).filter(FundraiserEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Event not found")

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

    db.commit()
    total = db.query(func.count(EventHouse.id)).filter(
        EventHouse.event_id == event.id
    ).scalar() or 0
    return {"assigned": added, "total_in_event": total}


class WalkGroupRequest(BaseModel):
    zip_code: str
    group_size: int = 20                          # houses per group
    street_names: Optional[list[str]] = None      # optional filter


@router.post("/{event_id}/walk-groups")
def create_walk_groups(
    event_id: str,
    body: WalkGroupRequest,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Auto-assign houses into walkable groups of adjacent addresses.

    Groups houses by street name, sorts by address number so scouts walk
    in order, then splits each street into chunks of ``group_size``.
    Each chunk becomes a numbered group.
    """
    event = db.query(FundraiserEvent).filter(FundraiserEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Event not found")

    # Fetch candidate houses with coordinates in this ZIP
    q = db.query(MasterHouse).filter(
        MasterHouse.zip_code == body.zip_code.strip(),
        MasterHouse.latitude.isnot(None),
        MasterHouse.longitude.isnot(None),
        MasterHouse.street_name.isnot(None),
    )
    if body.street_names:
        patterns = [
            MasterHouse.street_name.ilike(f"%{s.strip()}%")
            for s in body.street_names
        ]
        q = q.filter(or_(*patterns))

    houses = q.all()
    if not houses:
        return {"groups": [], "total_assigned": 0, "message": "No houses found for that ZIP/filter."}

    # Group by street, sort within each street by address number
    by_street: dict[str, list[MasterHouse]] = defaultdict(list)
    for h in houses:
        street = (h.street_name or "UNKNOWN").upper().strip()
        by_street[street].append(h)

    for street in by_street:
        by_street[street].sort(key=lambda h: _addr_sort_key(h.address_number))

    # Build groups: chunk each street into group_size, label them
    groups = []
    group_num = 1
    # Sort streets alphabetically for consistent ordering
    for street in sorted(by_street.keys()):
        street_houses = by_street[street]
        for i in range(0, len(street_houses), body.group_size):
            chunk = street_houses[i:i + body.group_size]
            # Range label like "5700-5800 Portsmouth Ln"
            first_num = chunk[0].address_number or "?"
            last_num = chunk[-1].address_number or "?"
            if first_num == last_num:
                label = f"Group {group_num} — {first_num} {street}"
            else:
                label = f"Group {group_num} — {first_num}-{last_num} {street}"
            groups.append({"label": label, "house_ids": [h.id for h in chunk]})
            group_num += 1

    # Batch-fetch existing EventHouse rows for this event
    all_house_ids = [hid for g in groups for hid in g["house_ids"]]
    existing_map = {}
    if all_house_ids:
        for eh in (
            db.query(EventHouse)
            .filter(EventHouse.event_id == event.id, EventHouse.house_id.in_(all_house_ids))
            .all()
        ):
            existing_map[eh.house_id] = eh

    # Create EventHouse rows
    total_assigned = 0
    group_summaries = []
    for g in groups:
        added = 0
        for house_id in g["house_ids"]:
            existing = existing_map.get(house_id)
            if not existing:
                new_eh = EventHouse(
                    event_id=event.id,
                    house_id=house_id,
                    assigned_to=g["label"],
                )
                db.add(new_eh)
                existing_map[house_id] = new_eh
                added += 1
            else:
                # Update group label for already-assigned houses
                existing.assigned_to = g["label"]
        total_assigned += added
        group_summaries.append({"label": g["label"], "houses": len(g["house_ids"]), "new": added})

    db.commit()
    return {"groups": group_summaries, "total_assigned": total_assigned}


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


@router.get("/{event_id}/houses", response_model=list[EventHouseOut])
def list_event_houses(
    event_id: str,
    status: str = Query(None),
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
    event_id: str,
    event_house_id: str,
    body: VisitCreate,
    db: Session = Depends(get_db),
):
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
        scout_name=body.scout_name,
        scout_id=body.scout_id,
        door_answer=body.door_answer,
        donation_given=body.donation_given,
        former_scout=body.former_scout,
        avoid_house=body.avoid_house,
    )
    eh.status = "visited"
    db.add(visit)
    db.commit()
    db.refresh(visit)
    return visit


@router.get("/{event_id}/houses/{event_house_id}/visits", response_model=list[VisitOut])
def list_visits(event_id: str, event_house_id: str, db: Session = Depends(get_db)):
    return (
        db.query(Visit)
        .filter(Visit.event_house_id == event_house_id)
        .order_by(Visit.visited_at.desc())
        .all()
    )
