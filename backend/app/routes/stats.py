"""Dashboard statistics endpoint."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import case, func, text

from app.database import get_db
from app.models import EventHouse, FundraiserEvent, ScoutRoster, Visit
from app.schemas import DashboardStats
from app.routes.auth import require_admin

# Admin-only: scouts never need these endpoints
router = APIRouter(prefix="/api/stats", tags=["stats"], dependencies=[Depends(require_admin)])


# Single SQL query that computes all dashboard counters in one DB round-trip.
_STATS_SQL = text("""
SELECT
  (SELECT count(*) FROM master_houses)              AS total_houses,
  (SELECT count(*) FROM fundraiser_events)          AS total_events,
  (SELECT count(*) FROM visits)                     AS total_visits,
  (SELECT coalesce(sum(donation_amount), 0) FROM visits) AS total_donations,
  (SELECT count(*) FROM unmatched_records
   WHERE status = 'pending')                        AS unmatched_count,
  (SELECT count(*) FROM source_imports)             AS import_count,
  (SELECT count(*) FROM scout_roster
   WHERE active = true)                             AS total_scouts,
  (SELECT count(*) FROM event_houses)               AS assigned_houses,
  (SELECT count(DISTINCT event_house_id)
   FROM visits)                                     AS houses_visited
""")


@router.get("/", response_model=DashboardStats)
def dashboard(db: Session = Depends(get_db)):
    row = db.execute(_STATS_SQL).one()
    return DashboardStats(
        total_houses=row.total_houses or 0,
        total_events=row.total_events or 0,
        total_visits=row.total_visits or 0,
        total_donations=row.total_donations or 0,
        unmatched_count=row.unmatched_count or 0,
        import_count=row.import_count or 0,
        total_scouts=row.total_scouts or 0,
        assigned_houses=row.assigned_houses or 0,
        houses_visited=row.houses_visited or 0,
    )


@router.get("/checklist")
def checklist(event_id: uuid.UUID, db: Session = Depends(get_db)):
    """Progress numbers for one event, used by the dashboard's step-by-step checklist."""
    if not db.query(FundraiserEvent.id).filter(FundraiserEvent.id == event_id).first():
        raise HTTPException(404, "Event not found")

    has_group = case((func.coalesce(EventHouse.assigned_to, "") != "", 1))
    houses, grouped, groups = (
        db.query(
            func.count(EventHouse.id),
            func.count(has_group),
            func.count(func.distinct(func.nullif(EventHouse.assigned_to, ""))),
        )
        .filter(EventHouse.event_id == event_id)
        .one()
    )
    visits, houses_visited, donations = (
        db.query(
            func.count(Visit.id),
            func.count(func.distinct(Visit.event_house_id)),
            func.coalesce(func.sum(Visit.donation_amount), 0),
        )
        .join(EventHouse, Visit.event_house_id == EventHouse.id)
        .filter(EventHouse.event_id == event_id)
        .one()
    )
    scouts_ready = (
        db.query(func.count(ScoutRoster.login_code))
        .filter(ScoutRoster.active == True)  # noqa: E712
        .scalar()
    )
    return {
        "houses": houses,
        "grouped": grouped,
        "groups": groups,
        "visits": visits,
        "houses_visited": houses_visited,
        "donations": float(donations or 0),
        "scouts_ready": scouts_ready,
    }
