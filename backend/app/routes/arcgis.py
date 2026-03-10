"""ArcGIS REST API integration – fetch Dallas Tax Parcels directly."""

import json
import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models import MasterHouse, HouseSourceLink, SourceImport, UnmatchedRecord
from app.address import normalize_address, parse_address_parts

router = APIRouter(prefix="/api/arcgis", tags=["arcgis"])

ARCGIS_BASE = (
    "https://services2.arcgis.com/rwnOSbfKSwyTBcwN"
    "/ArcGIS/rest/services/DallasTaxParcels/FeatureServer/0/query"
)

# Fields we request from the ArcGIS service
OUT_FIELDS = ",".join([
    "OBJECTID", "SITEADDRESS", "OWNERNAME",
    "PARCELID", "ACREAGE", "LANDVAL", "IMPRVAL", "TOTALVAL",
    "LEGALDESC", "ZIPCODE", "CITY", "STATE",
])

MAX_PAGE_SIZE = 1000


class ArcGISFetchRequest(BaseModel):
    zip_codes: Optional[list[str]] = None
    bbox_xmin: Optional[float] = None
    bbox_ymin: Optional[float] = None
    bbox_xmax: Optional[float] = None
    bbox_ymax: Optional[float] = None
    max_records: int = 2000
    notes: Optional[str] = None


def _build_where(req: ArcGISFetchRequest) -> str:
    clauses = []
    if req.zip_codes:
        quoted = ",".join(f"'{z.strip()}'" for z in req.zip_codes)
        clauses.append(f"ZIPCODE IN ({quoted})")
    return " AND ".join(clauses) if clauses else "1=1"


def _float_or_none(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


async def _fetch_all_pages(
    where: str,
    bbox: Optional[dict],
    max_records: int,
) -> list[dict]:
    """Page through ArcGIS results using resultOffset/resultRecordCount."""
    all_features = []
    offset = 0
    page_size = min(MAX_PAGE_SIZE, max_records)

    async with httpx.AsyncClient(timeout=60) as client:
        while len(all_features) < max_records:
            params = {
                "where": where,
                "outFields": OUT_FIELDS,
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "json",
                "resultOffset": offset,
                "resultRecordCount": page_size,
            }
            if bbox:
                params["geometry"] = json.dumps(bbox)
                params["geometryType"] = "esriGeometryEnvelope"
                params["spatialRel"] = "esriSpatialRelIntersects"
                params["inSR"] = "4326"

            resp = await client.get(ARCGIS_BASE, params=params)
            if resp.status_code != 200:
                raise HTTPException(502, f"ArcGIS returned HTTP {resp.status_code}")

            data = resp.json()
            if "error" in data:
                raise HTTPException(502, f"ArcGIS error: {data['error'].get('message', data['error'])}")

            features = data.get("features", [])
            all_features.extend(features)

            if not data.get("exceededTransferLimit", False) or not features:
                break
            offset += len(features)

    return all_features[:max_records]


def _get_attr(attrs: dict, *keys, default=""):
    for k in keys:
        val = attrs.get(k)
        if val not in (None, "", "Null"):
            return str(val).strip()
    return default


@router.post("/fetch")
async def fetch_arcgis_parcels(req: ArcGISFetchRequest, db: Session = Depends(get_db)):
    """Fetch tax parcels from Dallas ArcGIS and import into the database."""
    where = _build_where(req)
    bbox = None
    if all(v is not None for v in [req.bbox_xmin, req.bbox_ymin, req.bbox_xmax, req.bbox_ymax]):
        bbox = {
            "xmin": req.bbox_xmin, "ymin": req.bbox_ymin,
            "xmax": req.bbox_xmax, "ymax": req.bbox_ymax,
        }

    features = await _fetch_all_pages(where, bbox, req.max_records)
    if not features:
        return {"status": "ok", "fetched": 0, "imported": 0, "message": "No parcels found for that query."}

    # Create a SourceImport record
    batch_id = str(uuid.uuid4())
    si = SourceImport(
        source_name="arcgis_parcels",
        file_name=f"arcgis_fetch_{batch_id[:8]}",
        import_batch_id=batch_id,
        status="running",
        started_at=datetime.utcnow(),
        notes=req.notes or f"ArcGIS fetch: {where}",
    )
    db.add(si)
    db.flush()

    imported = 0
    for feat in features:
        attrs = feat.get("attributes", {})
        geom = feat.get("geometry", {})

        full_addr = _get_attr(attrs, "SITEADDRESS")
        if not full_addr:
            continue

        norm = normalize_address(full_addr)
        owner = _get_attr(attrs, "OWNERNAME")
        parcel = _get_attr(attrs, "PARCELID")
        zip_code = _get_attr(attrs, "ZIPCODE")
        city = _get_attr(attrs, "CITY", default="Dallas")
        state = _get_attr(attrs, "STATE", default="TX")
        land_val = _float_or_none(attrs.get("LANDVAL"))
        impr_val = _float_or_none(attrs.get("IMPRVAL"))
        total_val = _float_or_none(attrs.get("TOTALVAL"))
        legal = _get_attr(attrs, "LEGALDESC")
        object_id = str(attrs.get("OBJECTID", uuid.uuid4()))

        lat = geom.get("y")
        lon = geom.get("x")

        if not norm:
            db.add(UnmatchedRecord(
                source_import_id=si.id,
                source_name="arcgis_parcels",
                source_record_id=object_id,
                raw_address=full_addr,
                raw_data=json.dumps(attrs),
            ))
            imported += 1
            continue

        existing = db.query(MasterHouse).filter(
            MasterHouse.normalized_address == norm
        ).first()

        if existing:
            house = existing
            if owner and not house.owner_name:
                house.owner_name = owner
            if parcel and not house.parcel_id:
                house.parcel_id = parcel
            if not house.latitude and lat:
                house.latitude = float(lat)
            if not house.longitude and lon:
                house.longitude = float(lon)
            if legal and not house.legal_description:
                house.legal_description = legal
            if land_val is not None:
                house.land_value = land_val
            if impr_val is not None:
                house.improvement_value = impr_val
            if total_val is not None:
                house.total_appraised_value = total_val
            match_method = "exact"
        else:
            parts = parse_address_parts(full_addr)
            house = MasterHouse(
                full_address=full_addr,
                normalized_address=norm,
                address_number=parts["address_number"],
                street_name=parts["street_name"],
                unit=parts["unit"],
                city=city,
                state=state,
                zip_code=zip_code,
                latitude=float(lat) if lat else None,
                longitude=float(lon) if lon else None,
                owner_name=owner or None,
                parcel_id=parcel or None,
                legal_description=legal or None,
                land_value=land_val,
                improvement_value=impr_val,
                total_appraised_value=total_val,
            )
            db.add(house)
            db.flush()
            match_method = "new"

        link = HouseSourceLink(
            house_id=house.id,
            source_import_id=si.id,
            source_name="arcgis_parcels",
            source_record_id=object_id,
            import_batch_id=batch_id,
            match_method=match_method,
            raw_data=json.dumps(attrs),
        )
        db.add(link)
        imported += 1

    si.record_count = imported
    si.status = "completed"
    si.completed_at = datetime.utcnow()
    db.commit()

    return {
        "status": "ok",
        "fetched": len(features),
        "imported": imported,
        "import_id": str(si.id),
    }
