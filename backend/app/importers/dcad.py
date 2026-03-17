"""Importer for Dallas Central Appraisal District (DCAD) data.

Expected input: CSV with fields such as:
  - ACCOUNT_NUM / ACCT       (appraisal account number)
  - SITUS_ADDRESS / ADDRESS  (property address)
  - OWNER_NAME / OWNER       (owner name)
  - LEGAL_DESC               (legal description)
  - LAND_VALUE               (land appraised value)
  - IMPR_VALUE / IMPROVEMENT_VALUE
  - TOTAL_VALUE / MARKET_VALUE
  - PARCEL_ID / GEO_ID

Latitude/longitude may not be present in DCAD exports; matching to GIS
records fills in coordinates.
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


def _get(row: dict, *keys, default=""):
    for k in keys:
        for rk in row:
            if rk.upper() == k.upper():
                val = row[rk]
                if val not in (None, ""):
                    return str(val).strip()
    return default


def _float_or_none(val: str):
    if not val:
        return None
    try:
        return float(val.replace(",", "").replace("$", ""))
    except ValueError:
        return None


def import_dcad(db: Session, file_path: str, source_import_id: str) -> int:
    p = Path(file_path)
    with open(p, newline="", encoding="utf-8-sig") as f:
        records = list(csv.DictReader(f))

    si = db.query(SourceImport).get(source_import_id)
    batch_id = si.import_batch_id if si else str(uuid.uuid4())
    imported = 0

    for row in records:
        full_addr = _get(row, "SITUS_ADDRESS", "ADDRESS", "SITEADDR", "PROP_ADDR")
        if not full_addr:
            continue

        norm = normalize_address(full_addr)
        account = _get(row, "ACCOUNT_NUM", "ACCT", "ACCOUNT")
        parcel = _get(row, "PARCEL_ID", "GEO_ID", "PARCEL")
        owner = _get(row, "OWNER_NAME", "OWNER")
        legal = _get(row, "LEGAL_DESC", "LEGAL_DESCRIPTION")
        land_val = _float_or_none(_get(row, "LAND_VALUE"))
        impr_val = _float_or_none(_get(row, "IMPR_VALUE", "IMPROVEMENT_VALUE"))
        total_val = _float_or_none(_get(row, "TOTAL_VALUE", "MARKET_VALUE", "APPRAISED_VALUE"))
        prop_type = _get(row, "PROP_CL", "PROP_TYPE", "PROPERTY_TYPE", "STATE_CD")
        source_id = account or parcel or str(uuid.uuid4())

        # Try exact normalized match first
        existing = db.query(MasterHouse).filter(
            MasterHouse.normalized_address == norm
        ).first() if norm else None

        if existing:
            house = existing
            match_method = "exact"
            # Enrich with DCAD data
            if owner and not house.owner_name:
                house.owner_name = owner
            if parcel and not house.parcel_id:
                house.parcel_id = parcel
            if account and not house.account_number:
                house.account_number = account
            if legal and not house.legal_description:
                house.legal_description = legal
            if land_val is not None:
                house.land_value = land_val
            if impr_val is not None:
                house.improvement_value = impr_val
            if total_val is not None:
                house.total_appraised_value = total_val
            if prop_type and not house.property_type:
                house.property_type = prop_type
        elif norm:
            parts = parse_address_parts(full_addr)
            house = MasterHouse(
                full_address=full_addr,
                normalized_address=norm,
                address_number=parts["address_number"],
                street_name=parts["street_name"],
                unit=parts["unit"],
                city="Dallas",
                state="TX",
                owner_name=owner or None,
                parcel_id=parcel or None,
                account_number=account or None,
                legal_description=legal or None,
                land_value=land_val,
                improvement_value=impr_val,
                total_appraised_value=total_val,
                property_type=prop_type or None,
            )
            db.add(house)
            db.flush()
            match_method = "new"
        else:
            # Cannot normalize – save for review
            db.add(UnmatchedRecord(
                source_import_id=source_import_id,
                source_name="dcad",
                source_record_id=source_id,
                raw_address=full_addr,
                raw_data=json.dumps(row),
            ))
            imported += 1
            continue

        link = HouseSourceLink(
            house_id=house.id,
            source_import_id=source_import_id,
            source_name="dcad",
            source_record_id=source_id,
            import_batch_id=batch_id,
            match_method=match_method,
            raw_data=json.dumps(row),
        )
        db.add(link)
        imported += 1

    db.commit()
    return imported


register_importer("dcad", import_dcad)
