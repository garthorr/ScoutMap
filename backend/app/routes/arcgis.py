"""ArcGIS REST API integration – fetch Dallas Tax Parcels directly.

The service schema is discovered at startup via the FeatureServer metadata
endpoint, so field name changes won't break the importer.
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/arcgis", tags=["arcgis"])

ARCGIS_SERVICE = (
    "https://services2.arcgis.com/rwnOSbfKSwyTBcwN"
    "/ArcGIS/rest/services/DallasTaxParcels/FeatureServer/0"
)
ARCGIS_QUERY = ARCGIS_SERVICE + "/query"

MAX_PAGE_SIZE = 1000

# ---------------------------------------------------------------------------
# Field mapping – maps our canonical names to possible ArcGIS field names.
# At startup we discover the real schema; until then we try all candidates.
# ---------------------------------------------------------------------------
FIELD_CANDIDATES = {
    "address":   ["SITEADDRESS", "SiteAddress", "SITUS_ADDRESS", "ADDRESS", "FULL_ADDRESS", "FULLADDR", "PROP_ADDR", "Site_Addr"],
    "owner":     ["OWNERNAME", "OwnerName", "OWNER_NAME", "OWNER", "Owner"],
    "parcel":    ["PARCELID", "ParcelId", "PARCEL_ID", "PARCEL", "GEO_ID", "Parcel_ID"],
    "zip":       ["ZIPCODE", "ZipCode", "ZIP_CODE", "ZIP", "Zip", "SITUS_ZIP"],
    "city":      ["CITY", "City", "SITUS_CITY"],
    "state":     ["STATE", "State", "SITUS_STATE"],
    "land_val":  ["LANDVAL", "LandVal", "LAND_VALUE", "Land_Val", "LandValue"],
    "impr_val":  ["IMPRVAL", "ImprVal", "IMPR_VALUE", "Impr_Val", "ImprovementValue"],
    "total_val": ["TOTALVAL", "TotalVal", "TOTAL_VALUE", "Total_Val", "MARKET_VALUE", "TotalValue", "APPRAISED_VALUE"],
    "legal":     ["LEGALDESC", "LegalDesc", "LEGAL_DESC", "Legal_Desc", "LegalDescription"],
    "objectid":  ["OBJECTID", "ObjectId", "FID"],
    "acct":      ["ACCT", "ACCOUNT_NUM", "ACCOUNT", "AcctNum"],
}

# Resolved field map – populated by _discover_fields()
_field_map: dict[str, str | None] = {}
_all_field_names: list[str] = []
_zip_field: str | None = None
_discovery_done = False


async def _discover_fields():
    """Hit the FeatureServer layer metadata to learn the actual field names."""
    global _field_map, _all_field_names, _zip_field, _discovery_done
    if _discovery_done:
        return

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(ARCGIS_SERVICE, params={"f": "json"})
            if resp.status_code != 200:
                logger.warning("ArcGIS metadata returned %s", resp.status_code)
                _discovery_done = True
                return
            meta = resp.json()
    except Exception as exc:
        logger.warning("ArcGIS metadata discovery failed: %s", exc)
        _discovery_done = True
        return

    fields = meta.get("fields", [])
    real_names = {f["name"] for f in fields}
    _all_field_names.clear()
    _all_field_names.extend(sorted(real_names))

    for canonical, candidates in FIELD_CANDIDATES.items():
        _field_map[canonical] = None
        for c in candidates:
            if c in real_names:
                _field_map[canonical] = c
                break

    _zip_field = _field_map.get("zip")
    _discovery_done = True
    logger.info("ArcGIS field map: %s", _field_map)


def _resolved(canonical: str) -> str | None:
    """Return the real ArcGIS field name for a canonical name, or None."""
    return _field_map.get(canonical)


def _out_fields() -> str:
    """Build outFields param from discovered fields (or * if unknown)."""
    resolved = [v for v in _field_map.values() if v]
    return ",".join(resolved) if resolved else "*"


# ---------------------------------------------------------------------------
# Request / helpers
# ---------------------------------------------------------------------------

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
        zf = _zip_field
        if not zf:
            # Fallback: try common names
            for c in FIELD_CANDIDATES["zip"]:
                zf = c
                break
        quoted = ",".join(f"'{z.strip()}'" for z in req.zip_codes)
        clauses.append(f"{zf} IN ({quoted})")
    return " AND ".join(clauses) if clauses else "1=1"


def _float_or_none(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _get_attr(attrs: dict, canonical: str, default=""):
    """Look up a value by canonical name using the resolved field map."""
    real = _resolved(canonical)
    if real:
        val = attrs.get(real)
        if val not in (None, "", "Null"):
            return str(val).strip()
    # Fallback: try all candidates
    for c in FIELD_CANDIDATES.get(canonical, []):
        val = attrs.get(c)
        if val not in (None, "", "Null"):
            return str(val).strip()
    return default


def _get_attr_raw(attrs: dict, canonical: str):
    """Like _get_attr but returns the raw value (for numbers)."""
    real = _resolved(canonical)
    if real:
        val = attrs.get(real)
        if val is not None:
            return val
    for c in FIELD_CANDIDATES.get(canonical, []):
        val = attrs.get(c)
        if val is not None:
            return val
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
                "outFields": _out_fields(),
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

            resp = await client.get(ARCGIS_QUERY, params=params)
            if resp.status_code != 200:
                raise HTTPException(502, f"ArcGIS returned HTTP {resp.status_code}")

            data = resp.json()
            if "error" in data:
                msg = data["error"].get("message", str(data["error"]))
                raise HTTPException(502, f"ArcGIS error: {msg}")

            features = data.get("features", [])
            all_features.extend(features)

            if not data.get("exceededTransferLimit", False) or not features:
                break
            offset += len(features)

    return all_features[:max_records]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/fields")
async def get_arcgis_fields():
    """Return the discovered field names from the ArcGIS service."""
    await _discover_fields()
    return {
        "field_map": _field_map,
        "all_fields": _all_field_names,
        "zip_field": _zip_field,
    }


@router.post("/fetch")
async def fetch_arcgis_parcels(req: ArcGISFetchRequest, db: Session = Depends(get_db)):
    """Fetch tax parcels from Dallas ArcGIS and import into the database."""
    await _discover_fields()

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

        full_addr = _get_attr(attrs, "address")
        if not full_addr:
            continue

        norm = normalize_address(full_addr)
        owner = _get_attr(attrs, "owner")
        parcel = _get_attr(attrs, "parcel")
        zip_code = _get_attr(attrs, "zip")
        city = _get_attr(attrs, "city", default="Dallas")
        state = _get_attr(attrs, "state", default="TX")
        land_val = _float_or_none(_get_attr_raw(attrs, "land_val"))
        impr_val = _float_or_none(_get_attr_raw(attrs, "impr_val"))
        total_val = _float_or_none(_get_attr_raw(attrs, "total_val"))
        legal = _get_attr(attrs, "legal")
        object_id = str(_get_attr_raw(attrs, "objectid") or uuid.uuid4())

        # Geometry may be a point (x/y) or a polygon ring centroid
        lat = lon = None
        if geom:
            if "y" in geom and "x" in geom:
                lat, lon = geom["y"], geom["x"]
            elif "rings" in geom:
                # Compute centroid of first ring
                ring = geom["rings"][0] if geom["rings"] else []
                if ring:
                    lon = sum(p[0] for p in ring) / len(ring)
                    lat = sum(p[1] for p in ring) / len(ring)

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
