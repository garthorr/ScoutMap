"""Import endpoints – upload public data files and trigger import pipelines."""

import uuid
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import SourceImport, UnmatchedRecord, HouseSourceLink, MasterHouse, EventHouse, FundraiserEvent
from app.schemas import SourceImportOut, UnmatchedRecordOut
from app.routes.auth import require_admin
from app.importers import get_importer

# Ensure importers are registered
import app.importers.dallas_gis  # noqa: F401
import app.importers.dcad  # noqa: F401

router = APIRouter(prefix="/api/imports", tags=["imports"])

UPLOAD_DIR = Path("/tmp/scoutmap_uploads")
UPLOAD_DIR.mkdir(exist_ok=True)


@router.post("/", response_model=SourceImportOut)
async def create_import(
    source_name: str = Form(...),
    file: UploadFile = File(...),
    notes: str = Form(None),
    event_id: str = Form(None),
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    importer = get_importer(source_name)
    if not importer:
        raise HTTPException(400, f"Unknown source: {source_name}. Available: dallas_gis, dcad")

    # Validate event_id if provided
    event = None
    if event_id:
        event = db.query(FundraiserEvent).filter(FundraiserEvent.id == event_id).first()
        if not event:
            raise HTTPException(400, f"Event not found: {event_id}")

    batch_id = str(uuid.uuid4())
    si = SourceImport(
        source_name=source_name,
        file_name=file.filename,
        import_batch_id=batch_id,
        status="running",
        started_at=datetime.utcnow(),
        notes=notes,
    )
    db.add(si)
    db.flush()

    # Save uploaded file
    dest = UPLOAD_DIR / f"{si.id}_{file.filename}"
    with open(dest, "wb") as f_out:
        shutil.copyfileobj(file.file, f_out)

    try:
        count = importer(db, str(dest), str(si.id))
        si.record_count = count
        si.status = "completed"
        si.completed_at = datetime.utcnow()
    except Exception as e:
        si.status = "failed"
        si.notes = (si.notes or "") + f"\nError: {str(e)}"
        db.commit()
        raise HTTPException(500, f"Import failed: {e}")

    # Auto-assign imported houses to event if event_id was provided
    assigned = 0
    if event:
        imported_house_ids = [
            row[0] for row in
            db.query(HouseSourceLink.house_id)
            .filter(HouseSourceLink.source_import_id == str(si.id))
            .all()
        ]
        # Only assign houses that have coordinates (needed for map/walk groups)
        houses_with_coords = set(
            row[0] for row in
            db.query(MasterHouse.id)
            .filter(
                MasterHouse.id.in_(imported_house_ids),
                MasterHouse.latitude.isnot(None),
                MasterHouse.longitude.isnot(None),
            )
            .all()
        ) if imported_house_ids else set()
        # Skip houses already assigned to this event
        already_assigned = set(
            row[0] for row in
            db.query(EventHouse.house_id)
            .filter(
                EventHouse.event_id == event.id,
                EventHouse.house_id.in_(list(houses_with_coords)),
            )
            .all()
        ) if houses_with_coords else set()
        for house_id in houses_with_coords:
            if house_id not in already_assigned:
                db.add(EventHouse(event_id=event.id, house_id=house_id))
                assigned += 1
        if assigned:
            si.notes = (si.notes or "") + f"\nAuto-assigned {assigned} houses to event: {event.name}"

    db.commit()
    return si


@router.get("/", response_model=list[SourceImportOut])
def list_imports(db: Session = Depends(get_db)):
    return db.query(SourceImport).order_by(SourceImport.created_at.desc()).all()


@router.get("/{import_id}", response_model=SourceImportOut)
def get_import(import_id: str, db: Session = Depends(get_db)):
    si = db.query(SourceImport).filter(SourceImport.id == import_id).first()
    if not si:
        raise HTTPException(404, "Import not found")
    return si


@router.delete("/{import_id}")
def delete_import(import_id: str, _admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    """Delete an import and its associated records.

    Houses that were ONLY linked to this import (and aren't manually created
    or assigned to any event) are also removed.
    """
    si = db.query(SourceImport).filter(SourceImport.id == import_id).first()
    if not si:
        raise HTTPException(404, "Import not found")

    # Find house IDs linked to this import
    linked_house_ids = [
        row[0] for row in
        db.query(HouseSourceLink.house_id)
        .filter(HouseSourceLink.source_import_id == si.id)
        .all()
    ]

    # Delete source links for this import
    db.query(HouseSourceLink).filter(
        HouseSourceLink.source_import_id == si.id
    ).delete(synchronize_session=False)

    # Delete unmatched records for this import
    db.query(UnmatchedRecord).filter(
        UnmatchedRecord.source_import_id == si.id
    ).delete(synchronize_session=False)

    # Remove houses that have no remaining source links, aren't manually
    # created, and aren't assigned to any event
    houses_deleted = 0
    if linked_house_ids:
        for house_id in linked_house_ids:
            # Skip if house still has other source links
            other_links = db.query(HouseSourceLink.id).filter(
                HouseSourceLink.house_id == house_id
            ).first()
            if other_links:
                continue

            house = db.query(MasterHouse).filter(MasterHouse.id == house_id).first()
            if not house:
                continue

            # Skip manually created houses
            if house.manually_created:
                continue

            # Skip houses assigned to events
            event_link = db.query(EventHouse.id).filter(
                EventHouse.house_id == house_id
            ).first()
            if event_link:
                continue

            db.delete(house)
            houses_deleted += 1

    # Delete the import record itself
    db.delete(si)
    db.commit()

    return {
        "status": "ok",
        "deleted_import": import_id,
        "houses_removed": houses_deleted,
        "houses_kept": len(linked_house_ids) - houses_deleted,
    }


@router.get("/unmatched/", response_model=list[UnmatchedRecordOut])
def list_unmatched(status: str = "pending", db: Session = Depends(get_db)):
    q = db.query(UnmatchedRecord)
    if status:
        q = q.filter(UnmatchedRecord.status == status)
    return q.order_by(UnmatchedRecord.created_at.desc()).limit(200).all()
