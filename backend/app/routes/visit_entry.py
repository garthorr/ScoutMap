"""Admin visit entry: an adult records visits for scouts.

Used live on a phone while walking with the scouts, or afterwards from a
paper walk sheet. Saves come in batches with a browser-made `client_id`,
so a batch that is re-sent after a dropped connection is only saved once.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import EventHouse, FundraiserEvent, ScoutFormField, ScoutRoster, Visit
from app.routes.auth import require_admin
from app.routes.scout import natural_key, walk_order_key

router = APIRouter(prefix="/api/events", tags=["visit-entry"], dependencies=[Depends(require_admin)])

# Form fields that also have their own column on visits (reports read these)
LEGACY_KEYS = ("door_answer", "donation_given", "donation_amount", "former_scout", "avoid_house", "notes")


# ---------------------------------------------------------------------------
# Helpers (also used by the scout app)
# ---------------------------------------------------------------------------
def visit_values(v: Visit) -> dict:
    """All form answers for a visit, keyed by field_key."""
    values = {k: getattr(v, k) for k in LEGACY_KEYS if getattr(v, k) is not None}
    if v.custom_data:
        try:
            values.update(json.loads(v.custom_data))
        except (json.JSONDecodeError, TypeError):
            pass
    return values


def _visit_summary(v: Visit) -> dict:
    return {
        "id": str(v.id),
        "client_id": v.client_id,
        "scout_name": v.scout_name,
        "roster_id": str(v.scout_roster_id) if v.scout_roster_id else None,
        "values": visit_values(v),
        "visited_at": v.visited_at.isoformat() if v.visited_at else None,
        "entered_by": v.entered_by,
    }


def _clean_values(fields: list[ScoutFormField], raw: dict[str, Any]) -> tuple[dict, Optional[str]]:
    """Keep answers for known fields, in the right type. Returns (values, error)."""
    values = {}
    for f in fields:
        val = raw.get(f.field_key)
        if f.field_type == "toggle":
            val = None if val is None or val == "" else bool(val)
        elif f.field_type == "checkbox":
            val = bool(val)
        elif f.field_type == "number":
            if val is None or val == "":
                val = None
            else:
                try:
                    val = float(val)
                except (TypeError, ValueError):
                    return {}, f'"{f.label}" must be a number.'
                if val < 0:
                    return {}, f'"{f.label}" can\'t be negative.'
        else:
            val = str(val).strip() if val is not None else ""
            val = val or None
        values[f.field_key] = val

        missing = val is None or (f.field_type == "checkbox" and val is False)
        if f.required and missing:
            return {}, f'"{f.label}" is required.'
    return values, None


def _apply_values(visit: Visit, values: dict) -> None:
    for key in LEGACY_KEYS:
        setattr(visit, key, False if key == "avoid_house" else None)
    for key, val in values.items():
        if key in LEGACY_KEYS:
            setattr(visit, key, bool(val) if key == "avoid_house" else val)
    visit.custom_data = json.dumps(values)
    if values.get("door_answer") is False:
        visit.outcome = "not_home"
    elif values.get("donation_given"):
        visit.outcome = "donated"
    else:
        visit.outcome = "other"


def _set_scout(visit: Visit, scout: ScoutRoster) -> None:
    visit.scout_roster_id = scout.id
    visit.scout_name = scout.name
    visit.scout_id = scout.scout_id


def _when(ts: Optional[datetime]) -> datetime:
    """Time the visit was recorded on the phone, if believable; otherwise now (UTC)."""
    now = datetime.utcnow()
    if ts is None:
        return now
    if ts.tzinfo:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    if ts > now + timedelta(minutes=5) or ts < now - timedelta(days=30):
        return now
    return ts


def _active_fields(db: Session) -> list[ScoutFormField]:
    return (db.query(ScoutFormField)
            .filter(ScoutFormField.active == True)  # noqa: E712
            .order_by(ScoutFormField.position, ScoutFormField.created_at).all())


def _event_or_404(db: Session, event_id: uuid.UUID) -> FundraiserEvent:
    event = db.query(FundraiserEvent).filter(FundraiserEvent.id == event_id).first()
    if not event:
        raise HTTPException(404, "Event not found")
    return event


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/{event_id}/entry")
def entry_houses(event_id: uuid.UUID, group: Optional[str] = None, db: Session = Depends(get_db)):
    """Houses (in walk order) with their visits, for one walk group or the whole event."""
    event = _event_or_404(db, event_id)
    labels = [label for (label,) in
              db.query(EventHouse.assigned_to)
              .filter(EventHouse.event_id == event_id, EventHouse.assigned_to.isnot(None))
              .distinct().all() if label]

    q = (db.query(EventHouse)
         .options(joinedload(EventHouse.house), joinedload(EventHouse.visits))
         .filter(EventHouse.event_id == event_id))
    if group:
        q = q.filter(EventHouse.assigned_to == group)
    rows = [eh for eh in q.all() if eh.house]
    # Groups in natural order (2 before 10), ungrouped houses last, walk order inside a group
    rows.sort(key=lambda eh: (eh.assigned_to is None, natural_key(eh.assigned_to or ""), walk_order_key(eh.house)))

    return {
        "event": {"id": str(event.id), "name": event.name},
        "groups": sorted(labels, key=natural_key),
        "houses": [
            {
                "event_house_id": str(eh.id),
                "group": eh.assigned_to,
                "address": eh.house.full_address,
                "owner_name": eh.house.owner_name,
                "status": eh.status,
                "visits": [_visit_summary(v) for v in eh.visits],
            }
            for eh in rows
        ],
    }


class EntryVisit(BaseModel):
    client_id: str = Field(min_length=8, max_length=64)
    event_house_id: uuid.UUID
    roster_id: uuid.UUID                 # the scout who went to the door
    visited_at: Optional[datetime] = None
    values: dict[str, Any] = {}


class EntryBatch(BaseModel):
    visits: list[EntryVisit] = Field(max_length=500)


@router.post("/{event_id}/visits/batch")
def save_visits(
    event_id: uuid.UUID,
    body: EntryBatch,
    admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Save one or many visits. Each is handled on its own; problems come back per visit."""
    _event_or_404(db, event_id)
    fields = _active_fields(db)
    houses = {eh.id: eh for eh in db.query(EventHouse).filter(
        EventHouse.event_id == event_id,
        EventHouse.id.in_({v.event_house_id for v in body.visits}),
    ).all()} if body.visits else {}
    scouts = {s.id: s for s in db.query(ScoutRoster).filter(
        ScoutRoster.id.in_({v.roster_id for v in body.visits}),
    ).all()} if body.visits else {}
    already = {cid: vid for cid, vid in db.query(Visit.client_id, Visit.id).filter(
        Visit.client_id.in_({v.client_id for v in body.visits}),
    ).all()} if body.visits else {}

    results = []
    for item in body.visits:
        if item.client_id in already:  # re-sent after a dropped connection
            results.append({"client_id": item.client_id, "status": "duplicate", "visit_id": str(already[item.client_id])})
            continue
        eh = houses.get(item.event_house_id)
        scout = scouts.get(item.roster_id)
        values, error = _clean_values(fields, item.values)
        if not eh:
            error = "That house isn't in this event any more."
        elif not scout:
            error = "That scout isn't on the roster any more."
        if error:
            results.append({"client_id": item.client_id, "status": "error", "error": error})
            continue

        visit = Visit(id=uuid.uuid4(), event_house_id=eh.id, visited_at=_when(item.visited_at),
                      entered_by=admin, client_id=item.client_id)
        _set_scout(visit, scout)
        _apply_values(visit, values)
        eh.status = "visited"
        db.add(visit)
        already[item.client_id] = visit.id
        results.append({"client_id": item.client_id, "status": "saved", "visit_id": str(visit.id)})

    db.commit()
    return {"results": results}


class EntryVisitUpdate(BaseModel):
    roster_id: uuid.UUID
    values: dict[str, Any] = {}


def _visit_in_event(db: Session, event_id: uuid.UUID, visit_id: uuid.UUID) -> Visit:
    visit = (db.query(Visit).join(EventHouse, Visit.event_house_id == EventHouse.id)
             .filter(Visit.id == visit_id, EventHouse.event_id == event_id).first())
    if not visit:
        raise HTTPException(404, "Visit not found")
    return visit


@router.put("/{event_id}/visits/{visit_id}")
def update_visit(event_id: uuid.UUID, visit_id: uuid.UUID, body: EntryVisitUpdate, db: Session = Depends(get_db)):
    """Fix a visit's answers or which scout made it."""
    visit = _visit_in_event(db, event_id, visit_id)
    scout = db.query(ScoutRoster).filter(ScoutRoster.id == body.roster_id).first()
    if not scout:
        raise HTTPException(400, "That scout isn't on the roster any more.")
    values, error = _clean_values(_active_fields(db), body.values)
    if error:
        raise HTTPException(400, error)
    _set_scout(visit, scout)
    _apply_values(visit, values)
    db.commit()
    return _visit_summary(visit)


@router.delete("/{event_id}/visits/{visit_id}")
def delete_visit(event_id: uuid.UUID, visit_id: uuid.UUID, db: Session = Depends(get_db)):
    """Undo a visit. The house goes back to "not visited" if it has no other visits."""
    visit = _visit_in_event(db, event_id, visit_id)
    eh = db.query(EventHouse).filter(EventHouse.id == visit.event_house_id).first()
    db.delete(visit)
    db.flush()
    if eh and not db.query(Visit.id).filter(Visit.event_house_id == eh.id).first():
        eh.status = "pending"
    db.commit()
    return {"ok": True}
