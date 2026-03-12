"""ArcGIS REST API integration – fetch Dallas Tax Parcels directly.

Schema (from /FeatureServer/0 sample):
  ST_NUM, ST_NAME, ST_TYPE, ST_DIR  → composed into full address
  TAXPANAME1                        → owner name
  ACCT / GIS_ACCT                   → account / parcel ID
  TAXPAZIP                          → 9-digit ZIP+4 (truncate to 5)
  CITY                              → city
  LEGAL_1..LEGAL_5                  → legal description parts
  PROP_CL, SPTBCODE, ResCom         → property classification
  geometry.rings                    → polygon (compute centroid)
"""

import json
import logging
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
from app.routes.auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/arcgis", tags=["arcgis"])

ARCGIS_QUERY = (
    "https://services2.arcgis.com/rwnOSbfKSwyTBcwN"
    "/ArcGIS/rest/services/DallasTaxParcels/FeatureServer/0/query"
)

# Only request the fields we actually use
OUT_FIELDS = ",".join([
    "OBJECTID", "ST_NUM", "ST_NAME", "ST_TYPE", "ST_DIR", "UNITID",
    "TAXPANAME1", "ACCT", "GIS_ACCT",
    "TAXPAZIP", "CITY", "COUNTY",
    "LEGAL_1", "LEGAL_2", "LEGAL_3",
    "PROP_CL", "ResCom",
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
    """Build ArcGIS WHERE clause. TAXPAZIP is 9 digits, so use LIKE for 5-digit input."""
    import re
    clauses = []
    if req.zip_codes:
        parts = []
        for z in req.zip_codes:
            z = re.sub(r"[^0-9]", "", z.strip())  # sanitize: digits only
            if not z:
                continue
            if len(z) == 5:
                parts.append(f"TAXPAZIP LIKE '{z}%'")
            else:
                parts.append(f"TAXPAZIP = '{z}'")
        if len(parts) == 1:
            clauses.append(parts[0])
        elif parts:
            clauses.append("(" + " OR ".join(parts) + ")")
    return " AND ".join(clauses) if clauses else "1=1"


def _compose_address(attrs: dict) -> str:
    """Build a full street address from component fields."""
    num = str(attrs.get("ST_NUM") or "").strip()
    direction = str(attrs.get("ST_DIR") or "").strip()
    name = str(attrs.get("ST_NAME") or "").strip()
    stype = str(attrs.get("ST_TYPE") or "").strip()
    unit = str(attrs.get("UNITID") or "").strip()

    if not num or not name:
        return ""

    parts = [num]
    if direction:
        parts.append(direction)
    parts.append(name)
    if stype:
        parts.append(stype)
    addr = " ".join(parts)
    if unit:
        addr += f" #{unit}"
    return addr


def _get_zip5(attrs: dict) -> str:
    """Extract 5-digit ZIP from TAXPAZIP (which may be 9-digit ZIP+4)."""
    raw = str(attrs.get("TAXPAZIP") or "").strip()
    if len(raw) >= 5:
        return raw[:5]
    return raw


def _get_legal(attrs: dict) -> str:
    """Concatenate LEGAL_1 through LEGAL_5."""
    parts = []
    for i in range(1, 6):
        val = attrs.get(f"LEGAL_{i}")
        if val and str(val).strip():
            parts.append(str(val).strip())
    return " / ".join(parts) if parts else ""


def _centroid(geom: dict) -> tuple[Optional[float], Optional[float]]:
    """Compute centroid from polygon rings or point geometry."""
    if not geom:
        return None, None
    if "y" in geom and "x" in geom:
        return geom["y"], geom["x"]
    if "rings" in geom:
        ring = geom["rings"][0] if geom["rings"] else []
        if ring:
            lon = sum(p[0] for p in ring) / len(ring)
            lat = sum(p[1] for p in ring) / len(ring)
            return lat, lon
    return None, None


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
    all_features: list[dict] = []
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

            logger.info("ArcGIS query: where=%s offset=%s limit=%s", where, offset, page_size)
            resp = await client.get(ARCGIS_QUERY, params=params)

            if resp.status_code != 200:
                logger.error("ArcGIS HTTP %s: %s", resp.status_code, resp.text[:500])
                raise HTTPException(502, f"ArcGIS returned HTTP {resp.status_code}")

            data = resp.json()
            if "error" in data:
                logger.error("ArcGIS error: %s", json.dumps(data["error"]))
                msg = data["error"].get("message", str(data["error"]))
                details = data["error"].get("details", [])
                full_msg = f"{msg}. Details: {details}" if details else msg
                raise HTTPException(502, f"ArcGIS error: {full_msg}")

            features = data.get("features", [])
            logger.info("ArcGIS returned %d features (offset=%d)", len(features), offset)
            all_features.extend(features)

            if not data.get("exceededTransferLimit", False) or not features:
                break
            offset += len(features)

    return all_features[:max_records]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/test")
async def test_arcgis_connection():
    """Diagnostic: fetch 1 record from ArcGIS and return the raw response."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(ARCGIS_QUERY, params={
                "where": "1=1",
                "outFields": "*",
                "returnGeometry": "true",
                "resultRecordCount": 1,
                "f": "json",
            })
            raw = resp.json()
            sample_attrs = (
                raw["features"][0].get("attributes", {})
                if raw.get("features") else None
            )
            # Show what address we'd compose
            composed_addr = _compose_address(sample_attrs) if sample_attrs else None
            return {
                "http_status": resp.status_code,
                "has_error": "error" in raw,
                "error": raw.get("error"),
                "feature_count": len(raw.get("features", [])),
                "composed_address": composed_addr,
                "sample_attributes": sample_attrs,
                "sample_geometry_keys": (
                    list(raw["features"][0].get("geometry", {}).keys())
                    if raw.get("features") else None
                ),
            }
    except Exception as exc:
        return {"error": str(exc), "type": type(exc).__name__}


@router.post("/fetch")
async def fetch_arcgis_parcels(req: ArcGISFetchRequest, _admin: str = Depends(require_admin), db: Session = Depends(get_db)):
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

        full_addr = _compose_address(attrs)
        if not full_addr:
            continue

        norm = normalize_address(full_addr)
        owner = str(attrs.get("TAXPANAME1") or "").strip() or None
        acct = str(attrs.get("ACCT") or attrs.get("GIS_ACCT") or "").strip() or None
        zip_code = _get_zip5(attrs)
        city = str(attrs.get("CITY") or "").strip() or "Dallas"
        legal = _get_legal(attrs)
        object_id = str(attrs.get("OBJECTID", uuid.uuid4()))

        lat, lon = _centroid(geom)

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
            if acct and not house.account_number:
                house.account_number = acct
            if not house.latitude and lat:
                house.latitude = float(lat)
            if not house.longitude and lon:
                house.longitude = float(lon)
            if legal and not house.legal_description:
                house.legal_description = legal
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
                state="TX",
                zip_code=zip_code,
                latitude=float(lat) if lat else None,
                longitude=float(lon) if lon else None,
                owner_name=owner,
                account_number=acct,
                legal_description=legal or None,
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
