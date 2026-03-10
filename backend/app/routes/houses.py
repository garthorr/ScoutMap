"""House endpoints – browse and manually add master house records."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import MasterHouse
from app.schemas import MasterHouseOut, MasterHouseCreate
from app.address import normalize_address, parse_address_parts

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


@router.get("/{house_id}", response_model=MasterHouseOut)
def get_house(house_id: str, db: Session = Depends(get_db)):
    house = db.query(MasterHouse).get(house_id)
    if not house:
        raise HTTPException(404, "House not found")
    return house


@router.post("/", response_model=MasterHouseOut)
def create_house_manual(body: MasterHouseCreate, db: Session = Depends(get_db)):
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
