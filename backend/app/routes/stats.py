"""Dashboard statistics endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db
from app.schemas import DashboardStats

router = APIRouter(prefix="/api/stats", tags=["stats"])


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
   FROM visits)                                     AS houses_visited,
  (SELECT coalesce(sum(amount), 0) FROM donations)  AS standalone_donations
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
        standalone_donations=row.standalone_donations or 0,
    )
