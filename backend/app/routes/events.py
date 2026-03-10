"""Event endpoints – create events and assign houses."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

from app.database import get_db
from app.models import FundraiserEvent, EventHouse, MasterHouse, Visit
from app.schemas import (
    EventCreate, EventOut, EventAssignRequest,
    EventHouseOut, VisitCreate, VisitOut,
)

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
def create_event(body: EventCreate, db: Session = Depends(get_db)):
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
    events = db.query(FundraiserEvent).order_by(FundraiserEvent.created_at.desc()).all()
    return [_enrich_event(e, db) for e in events]


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: str, db: Session = Depends(get_db)):
    event = db.query(FundraiserEvent).get(event_id)
    if not event:
        raise HTTPException(404, "Event not found")
    return _enrich_event(event, db)


@router.post("/{event_id}/assign", response_model=dict)
def assign_houses(event_id: str, body: EventAssignRequest, db: Session = Depends(get_db)):
    """Generate event assignments from imported master houses."""
    event = db.query(FundraiserEvent).get(event_id)
    if not event:
        raise HTTPException(404, "Event not found")

    q = db.query(MasterHouse).filter(
        MasterHouse.latitude.isnot(None),
        MasterHouse.longitude.isnot(None),
    )
    if body.zip_codes:
        q = q.filter(MasterHouse.zip_code.in_(body.zip_codes))
    if body.street_names:
        from sqlalchemy import or_
        patterns = [MasterHouse.normalized_address.ilike(f"%{s.upper()}%") for s in body.street_names]
        q = q.filter(or_(*patterns))
    if body.limit:
        q = q.limit(body.limit)

    houses = q.all()
    added = 0
    for h in houses:
        exists = db.query(EventHouse).filter(
            EventHouse.event_id == event.id,
            EventHouse.house_id == h.id,
        ).first()
        if not exists:
            db.add(EventHouse(
                event_id=event.id,
                house_id=h.id,
                assigned_to=body.assigned_to,
            ))
            added += 1

    db.commit()
    return {"assigned": added, "total_in_event": added}


@router.get("/{event_id}/houses", response_model=list[EventHouseOut])
def list_event_houses(
    event_id: str,
    status: str = Query(None),
    db: Session = Depends(get_db),
):
    q = (
        db.query(EventHouse)
        .options(joinedload(EventHouse.house))
        .filter(EventHouse.event_id == event_id)
    )
    if status:
        q = q.filter(EventHouse.status == status)
    return q.order_by(EventHouse.priority.desc()).all()


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
