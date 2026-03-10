"""ArcGIS REST API integration – fetch Dallas Tax Parcels directly.

Field names are auto-discovered by fetching a single sample record with
outFields=* on the first request.
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

ARCGIS_QUERY = (
    "https://services2.arcgis.com/rwnOSbfKSwyTBcwN"
    "/ArcGIS/rest/services/DallasTaxParcels/FeatureServer/0/query"
)

MAX_PAGE_SIZE = 1000

# ---------------------------------------------------------------------------
# Field mapping – maps our canonical names to possible ArcGIS field names.
# ---------------------------------------------------------------------------
FIELD_CANDIDATES = {
    "address":   ["SITEADDRESS", "SiteAddress", "SITUS_ADDRESS", "ADDRESS",
                  "FULL_ADDRESS", "FULLADDR", "PROP_ADDR", "Site_Addr",
                  "SitusAddress", "SITE_ADDR"],
    "owner":     ["OWNERNAME", "OwnerName", "OWNER_NAME", "OWNER", "Owner",
                  "OwnerName1"],
    "parcel":    ["PARCELID", "ParcelId", "PARCEL_ID", "PARCEL", "GEO_ID",
                  "Parcel_ID", "ACCTID", "Account"],
    "zip":       ["ZIPCODE", "ZipCode", "ZIP_CODE", "ZIP", "Zip", "SITUS_ZIP",
                  "SitusZip", "SITUS_ZIPCODE"],
    "city":      ["CITY", "City", "SITUS_CITY", "SitusCity"],
    "state":     ["STATE", "State", "SITUS_STATE", "SitusState"],
    "land_val":  ["LANDVAL", "LandVal", "LAND_VALUE", "Land_Val", "LandValue",
                  "LandMktVal", "LANDMKTVAL"],
    "impr_val":  ["IMPRVAL", "ImprVal", "IMPR_VALUE", "Impr_Val",
                  "ImprovementValue", "ImprMktVal", "IMPRMKTVAL"],
    "total_val": ["TOTALVAL", "TotalVal", "TOTAL_VALUE", "Total_Val",
                  "MARKET_VALUE", "TotalValue", "APPRAISED_VALUE",
                  "TotalMktVal", "TOTALMKTVAL", "MktValTotal"],
    "legal":     ["LEGALDESC", "LegalDesc", "LEGAL_DESC", "Legal_Desc",
                  "LegalDescription", "Legal"],
    "objectid":  ["OBJECTID", "ObjectId", "FID", "objectid"],
    "acct":      ["ACCT", "ACCOUNT_NUM", "ACCOUNT", "AcctNum", "AccountNum"],
}

# Resolved field map – populated by _discover_fields()
_field_map: dict[str, str | None] = {}
_all_field_names: list[str] = []
_discovery_done = False


def _match_fields(real_names: set[str]):
    """Match discovered field names to our canonical names."""
    global _field_map, _all_field_names
    _all_field_names = sorted(real_names)

    # Case-insensitive matching
    real_upper = {name.upper(): name for name in real_names}

    for canonical, candidates in FIELD_CANDIDATES.items():
        _field_map[canonical] = None
        for c in candidates:
            # Exact match first
            if c in real_names:
                _field_map[canonical] = c
                break
            # Case-insensitive match
            if c.upper() in real_upper:
                _field_map[canonical] = real_upper[c.upper()]
                break

    logger.info("ArcGIS field map resolved: %s", _field_map)
    logger.info("All ArcGIS fields: %s", _all_field_names)


async def _discover_fields():
    """Discover field names by fetching one sample record with outFields=*."""
    global _discovery_done
    if _discovery_done:
        return

    logger.info("Discovering ArcGIS field names via sample query...")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(ARCGIS_QUERY, params={
                "where": "1=1",
                "outFields": "*",
                "returnGeometry": "false",
                "resultRecordCount": 1,
                "f": "json",
            })
            logger.info("ArcGIS sample query HTTP %s", resp.status_code)

            if resp.status_code != 200:
                logger.error("ArcGIS sample query failed: HTTP %s", resp.status_code)
                _discovery_done = True
                return

            data = resp.json()
            if "error" in data:
                logger.error("ArcGIS sample query error: %s", data["error"])
                _discovery_done = True
                return

            features = data.get("features", [])
            if not features:
                logger.error("ArcGIS sample query returned 0 features")
                _discovery_done = True
                return

            # Get field names from the first record's attributes
            attrs = features[0].get("attributes", {})
            real_names = set(attrs.keys())
            logger.info("ArcGIS sample record keys: %s", sorted(real_names))
            _match_fields(real_names)

    except Exception as exc:
        logger.error("ArcGIS discovery failed: %s", exc, exc_info=True)

    _discovery_done = True


def _resolved(canonical: str) -> str | None:
    return _field_map.get(canonical)


def _out_fields() -> str:
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
        zf = _resolved("zip")
        if not zf:
            logger.warning("No ZIP field discovered; ignoring zip_codes filter")
        else:
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

            logger.info("ArcGIS query: where=%s offset=%s limit=%s", where, offset, page_size)
            resp = await client.get(ARCGIS_QUERY, params=params)
            if resp.status_code != 200:
                logger.error("ArcGIS query HTTP %s: %s", resp.status_code, resp.text[:500])
                raise HTTPException(502, f"ArcGIS returned HTTP {resp.status_code}")

            data = resp.json()
            if "error" in data:
                logger.error("ArcGIS query error: %s", json.dumps(data["error"]))
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
            return {
                "http_status": resp.status_code,
                "has_error": "error" in raw,
                "error": raw.get("error"),
                "feature_count": len(raw.get("features", [])),
                "sample_attributes": (
                    raw["features"][0].get("attributes", {})
                    if raw.get("features") else None
                ),
                "sample_geometry_keys": (
                    list(raw["features"][0].get("geometry", {}).keys())
                    if raw.get("features") else None
                ),
                "field_map": _field_map,
                "all_discovered_fields": _all_field_names,
                "discovery_done": _discovery_done,
            }
    except Exception as exc:
        return {"error": str(exc), "type": type(exc).__name__}


@router.get("/fields")
async def get_arcgis_fields():
    """Return the discovered field names from the ArcGIS service."""
    await _discover_fields()
    return {
        "field_map": _field_map,
        "all_fields": _all_field_names,
        "discovery_done": _discovery_done,
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
