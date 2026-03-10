"""Importer for City of Dallas GIS address-point data.

Expected input: CSV or GeoJSON file containing at minimum:
  - FULLADDR or ADDRESS  (full street address)
  - LAT / Y              (latitude)
  - LON / LONG / X       (longitude)

Optional fields: CITY, STATE, ZIP, OBJECTID / GIS_ID
"""

import csv
import json
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import MasterHouse, HouseSourceLink, SourceImport, UnmatchedRecord
from app.address import normalize_address, parse_address_parts
from app.importers import register_importer


def _read_records(file_path: str) -> list[dict]:
    """Read CSV or GeoJSON and return a list of dicts."""
    p = Path(file_path)
    if p.suffix.lower() in (".geojson", ".json"):
        with open(p) as f:
            data = json.load(f)
        features = data.get("features", data if isinstance(data, list) else [])
        return [
            {**feat.get("properties", {}),
             **({"LAT": feat["geometry"]["coordinates"][1],
                 "LON": feat["geometry"]["coordinates"][0]}
                if feat.get("geometry") else {})}
            for feat in features
        ]
    else:
        with open(p, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))


def _get(row: dict, *keys, default=""):
    for k in keys:
        for rk in row:
            if rk.upper() == k.upper():
                val = row[rk]
                if val not in (None, ""):
                    return str(val).strip()
    return default


def import_dallas_gis(db: Session, file_path: str, source_import_id: str) -> int:
    records = _read_records(file_path)
    si = db.query(SourceImport).get(source_import_id)
    batch_id = si.import_batch_id if si else str(uuid.uuid4())
    imported = 0

    for row in records:
        full_addr = _get(row, "FULLADDR", "FULL_ADDR", "ADDRESS", "SITEADDR")
        if not full_addr:
            continue

        lat = _get(row, "LAT", "LATITUDE", "Y")
        lon = _get(row, "LON", "LONG", "LONGITUDE", "X")
        source_id = _get(row, "OBJECTID", "GIS_ID", "FID")
        city = _get(row, "CITY", default="Dallas")
        state = _get(row, "STATE", default="TX")
        zip_code = _get(row, "ZIP", "ZIPCODE", "ZIP_CODE")

        norm = normalize_address(full_addr)
        if not norm:
            continue

        # Check for existing house by normalized address
        existing = db.query(MasterHouse).filter(
            MasterHouse.normalized_address == norm
        ).first()

        if existing:
            house = existing
            # Update coords if missing
            if not house.latitude and lat:
                house.latitude = float(lat)
            if not house.longitude and lon:
                house.longitude = float(lon)
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
            )
            db.add(house)
            db.flush()

        # Create source link
        link = HouseSourceLink(
            house_id=house.id,
            source_import_id=source_import_id,
            source_name="dallas_gis",
            source_record_id=source_id or str(uuid.uuid4()),
            import_batch_id=batch_id,
            match_method="exact" if existing else "new",
            raw_data=json.dumps(row),
        )
        db.add(link)
        imported += 1

    db.commit()
    return imported


register_importer("dallas_gis", import_dallas_gis)
