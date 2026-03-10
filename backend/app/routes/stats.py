"""Dashboard statistics endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import MasterHouse, FundraiserEvent, Visit, UnmatchedRecord, SourceImport
from app.schemas import DashboardStats

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/", response_model=DashboardStats)
def dashboard(db: Session = Depends(get_db)):
    return DashboardStats(
        total_houses=db.query(func.count(MasterHouse.id)).scalar() or 0,
        total_events=db.query(func.count(FundraiserEvent.id)).scalar() or 0,
        total_visits=db.query(func.count(Visit.id)).scalar() or 0,
        total_donations=db.query(func.coalesce(func.sum(Visit.donation_amount), 0)).scalar(),
        unmatched_count=db.query(func.count(UnmatchedRecord.id)).filter(
            UnmatchedRecord.status == "pending"
        ).scalar() or 0,
        import_count=db.query(func.count(SourceImport.id)).scalar() or 0,
    )
