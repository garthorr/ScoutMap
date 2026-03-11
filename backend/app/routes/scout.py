"""Scout-facing API — lightweight endpoints for field use."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import FundraiserEvent, EventHouse, MasterHouse

router = APIRouter(prefix="/api/scout", tags=["scout"])


@router.get("/events")
def list_scout_events(db: Session = Depends(get_db)):
    """Return events with their walk group labels."""
    events = db.query(FundraiserEvent).order_by(
        FundraiserEvent.created_at.desc()
    ).all()
    result = []
    for ev in events:
        groups = (
            db.query(EventHouse.assigned_to)
            .filter(EventHouse.event_id == ev.id, EventHouse.assigned_to.isnot(None))
            .distinct()
            .all()
        )
        result.append({
            "id": str(ev.id),
            "name": ev.name,
            "event_date": ev.event_date.isoformat() if ev.event_date else None,
            "groups": sorted([g[0] for g in groups]),
        })
    return result


@router.get("/events/{event_id}/groups/{group_label}/houses")
def list_group_houses(event_id: str, group_label: str, db: Session = Depends(get_db)):
    """Return houses in a specific walk group, with visit status."""
    houses = (
        db.query(EventHouse)
        .join(MasterHouse, EventHouse.house_id == MasterHouse.id)
        .options(joinedload(EventHouse.house), joinedload(EventHouse.visits))
        .filter(
            EventHouse.event_id == event_id,
            EventHouse.assigned_to == group_label,
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
