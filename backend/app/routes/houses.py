"""House endpoints – browse, add, and remove master house records."""

from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import MasterHouse, HouseSourceLink, EventHouse, Visit, UnmatchedRecord, FundraiserEvent
from app.schemas import MasterHouseOut, MasterHouseCreate
from app.address import normalize_address, parse_address_parts
from app.routes.auth import require_admin

router = APIRouter(prefix="/api/houses", tags=["houses"])


@router.get("/", response_model=list[MasterHouseOut])
def list_houses(
    search: str = Query(None),
    zip_code: str = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
    db: Session = Depends(get_db),
):
    q = db.query(MasterHouse)
    if search:
        pattern = f"%{search.upper()}%"
        q = q.filter(MasterHouse.normalized_address.ilike(pattern))
    if zip_code:
        q = q.filter(MasterHouse.zip_code == zip_code)
    return q.order_by(MasterHouse.normalized_address).offset(offset).limit(limit).all()


@router.get("/map", response_model=list[MasterHouseOut])
def houses_for_map(
    min_lat: float = Query(...),
    min_lon: float = Query(...),
    max_lat: float = Query(...),
    max_lon: float = Query(...),
    limit: int = Query(500, le=2000),
    db: Session = Depends(get_db),
):
    return (
        db.query(MasterHouse)
        .filter(
            MasterHouse.latitude.isnot(None),
            MasterHouse.longitude.isnot(None),
            MasterHouse.latitude.between(min_lat, max_lat),
            MasterHouse.longitude.between(min_lon, max_lon),
        )
        .limit(limit)
        .all()
    )


@router.get("/streets")
def list_streets(
    zip_code: str = Query(...),
    db: Session = Depends(get_db),
):
    """Return streets in a ZIP with their house coordinates for map display."""
    houses = (
        db.query(MasterHouse)
        .filter(
            MasterHouse.zip_code == zip_code.strip(),
            MasterHouse.street_name.isnot(None),
            MasterHouse.latitude.isnot(None),
            MasterHouse.longitude.isnot(None),
        )
        .order_by(MasterHouse.street_name, MasterHouse.address_number)
        .all()
    )
    by_street = defaultdict(list)
    for h in houses:
        street = (h.street_name or "").upper().strip()
        by_street[street].append({
            "id": str(h.id),
            "lat": h.latitude,
            "lon": h.longitude,
            "address": h.full_address,
            "address_number": h.address_number,
        })
    return [
        {"street": street, "count": len(pts), "houses": pts}
        for street, pts in sorted(by_street.items())
    ]


@router.get("/zip-codes")
def list_zip_codes(db: Session = Depends(get_db)):
    """Return all distinct ZIP codes that have houses with coordinates."""
    rows = (
        db.query(MasterHouse.zip_code, func.count(MasterHouse.id))
        .filter(
            MasterHouse.zip_code.isnot(None),
            MasterHouse.zip_code != "",
            MasterHouse.latitude.isnot(None),
            MasterHouse.longitude.isnot(None),
        )
        .group_by(MasterHouse.zip_code)
        .order_by(MasterHouse.zip_code)
        .all()
    )
    return [{"zip_code": z, "count": c} for z, c in rows]


class PolygonQueryRequest(BaseModel):
    polygon: list[list[float]]  # [[lat, lng], [lat, lng], ...]
    event_id: Optional[str] = None
    assigned_to: Optional[str] = None
    count_only: bool = False


def _point_in_polygon(lat: float, lng: float, polygon: list[list[float]]) -> bool:
    """Ray-casting algorithm for point-in-polygon test."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i]
        yj, xj = polygon[j]
        if ((yi > lat) != (yj > lat)) and (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


@router.post("/in-polygon")
def houses_in_polygon(body: PolygonQueryRequest, _admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    """Find all houses inside a polygon boundary and optionally assign to an event.

    Uses bounding-box pre-filter in SQL then ray-casting in Python.
    Handles tens of thousands of houses efficiently.
    """
    if len(body.polygon) < 3:
        raise HTTPException(400, "Polygon must have at least 3 points")

    # Compute bounding box for fast SQL pre-filter
    lats = [p[0] for p in body.polygon]
    lngs = [p[1] for p in body.polygon]
    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)

    # Query all houses in the bounding box (no limit - support tens of thousands)
    candidates = (
        db.query(MasterHouse.id, MasterHouse.latitude, MasterHouse.longitude)
        .filter(
            MasterHouse.latitude.isnot(None),
            MasterHouse.longitude.isnot(None),
            MasterHouse.latitude.between(min_lat, max_lat),
            MasterHouse.longitude.between(min_lng, max_lng),
        )
        .all()
    )

    # Ray-casting filter
    inside_ids = []
    for hid, hlat, hlng in candidates:
        if _point_in_polygon(hlat, hlng, body.polygon):
            inside_ids.append(hid)

    if body.count_only:
        return {"count": len(inside_ids)}

    # Optionally assign to event
    assigned = 0
    if body.event_id and inside_ids:
        event = db.query(FundraiserEvent).filter(FundraiserEvent.id == body.event_id).first()
        if not event:
            raise HTTPException(400, "Event not found")

        # Batch check existing assignments
        already = set()
        # Process in chunks to avoid SQL parameter limits
        for i in range(0, len(inside_ids), 500):
            chunk = inside_ids[i:i + 500]
            already.update(
                row[0] for row in
                db.query(EventHouse.house_id)
                .filter(EventHouse.event_id == event.id, EventHouse.house_id.in_(chunk))
                .all()
            )

        for hid in inside_ids:
            if hid not in already:
                db.add(EventHouse(
                    event_id=event.id,
                    house_id=hid,
                    assigned_to=body.assigned_to,
                ))
                assigned += 1
        db.commit()

    return {
        "count": len(inside_ids),
        "house_ids": [str(hid) for hid in inside_ids],
        "assigned": assigned,
    }


@router.get("/{house_id}", response_model=MasterHouseOut)
def get_house(house_id: str, db: Session = Depends(get_db)):
    house = db.query(MasterHouse).filter(MasterHouse.id == house_id).first()
    if not house:
        raise HTTPException(404, "House not found")
    return house


@router.post("/", response_model=MasterHouseOut)
def create_house_manual(body: MasterHouseCreate, _admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    """Admin manually adds a house that is missing from public data."""
    norm = normalize_address(body.full_address)
    existing = db.query(MasterHouse).filter(MasterHouse.normalized_address == norm).first()
    if existing:
        raise HTTPException(409, f"House already exists: {existing.id}")

    parts = parse_address_parts(body.full_address)
    house = MasterHouse(
        full_address=body.full_address,
        normalized_address=norm,
        address_number=parts["address_number"],
        street_name=parts["street_name"],
        unit=body.unit or parts["unit"],
        city=body.city,
        state=body.state,
        zip_code=body.zip_code,
        latitude=body.latitude,
        longitude=body.longitude,
        owner_name=body.owner_name,
        manually_created=True,
    )
    db.add(house)
    db.commit()
    db.refresh(house)
    return house


def _delete_house(house_id: str, db: Session):
    """Remove a house and all dependent records (source links, event assignments, visits)."""
    house = db.query(MasterHouse).filter(MasterHouse.id == house_id).first()
    if not house:
        return False

    # Delete visits for event_houses linked to this house
    event_house_ids = [
        eh.id for eh in db.query(EventHouse).filter(EventHouse.house_id == house_id).all()
    ]
    if event_house_ids:
        db.query(Visit).filter(Visit.event_house_id.in_(event_house_ids)).delete(synchronize_session=False)
    db.query(EventHouse).filter(EventHouse.house_id == house_id).delete(synchronize_session=False)
    db.query(HouseSourceLink).filter(HouseSourceLink.house_id == house_id).delete(synchronize_session=False)
    db.query(UnmatchedRecord).filter(UnmatchedRecord.resolved_house_id == house_id).update(
        {"resolved_house_id": None, "status": "pending"}, synchronize_session=False
    )
    db.delete(house)
    return True


@router.delete("/{house_id}")
def delete_house(house_id: str, _admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    """Delete a house and all related records."""
    if not _delete_house(house_id, db):
        raise HTTPException(404, "House not found")
    db.commit()
    return {"ok": True}


class BatchDeleteBody(BaseModel):
    house_ids: list[str]


@router.post("/batch-delete")
def batch_delete_houses(body: BatchDeleteBody, _admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    """Delete multiple houses at once (used by map erase tool)."""
    deleted = 0
    for hid in body.house_ids:
        if _delete_house(hid, db):
            deleted += 1
    db.commit()
    return {"ok": True, "deleted": deleted}
