# Fundraising App

A door-to-door fundraising management application that uses **public data as the primary source** for address/house records, minimizing manual data entry. Built for neighborhood fundraising campaigns in Dallas, TX.

## Architecture

```
┌─────────────┐     ┌─────────────────┐     ┌────────────┐
│  Frontend    │────▶│  FastAPI Backend │────▶│ PostgreSQL │
│  (HTML/JS)   │◀────│  (Python)       │◀────│            │
│  + Leaflet   │     │  + SQLAlchemy   │     │            │
└─────────────┘     └─────────────────┘     └────────────┘
```

- **Backend**: Python / FastAPI / SQLAlchemy
- **Frontend**: Vanilla HTML/CSS/JS with Leaflet maps
- **Database**: PostgreSQL 16
- **Containerization**: Docker Compose

## Quick Start

```bash
# Clone and start
git clone <repo-url>
cd Fundraising
docker compose up --build

# App is available at http://localhost:8000
```

That's it. The database is created automatically on first startup.

## Data Model

```
source_imports          One row per uploaded file / import batch
    │
    ▼
master_houses           Canonical house records (one per physical address)
    │
    ├── house_source_links   Provenance: which import produced this record
    │
    ├── event_houses         Junction: house assigned to a fundraiser event
    │       │
    │       └── visits       Visit outcomes (donation, notes, follow-up)
    │
    └── unmatched_records    Records that could not be matched (for admin review)

fundraiser_events       Campaign / event definitions
```

### Key Tables

| Table | Purpose |
|---|---|
| `source_imports` | Tracks each file upload: source name, batch ID, record count, status |
| `master_houses` | One row per physical address with lat/lng, owner, parcel/appraisal data |
| `house_source_links` | Links a house to its source import with `source_name`, `source_record_id`, `source_last_updated`, `import_batch_id`, and raw data snapshot |
| `event_houses` | Assigns a master house to a fundraiser event with status tracking |
| `visits` | Records visit outcome, donation amount, tickets, notes, follow-up flag |
| `unmatched_records` | Stores records that couldn't be matched for admin review |

## Import Workflow

### 1. Prepare Source Files

The app accepts CSV (and GeoJSON for GIS data) files from two built-in sources:

**City of Dallas GIS Address Points**
- Expected columns: `FULLADDR`, `LAT`, `LON`, `CITY`, `STATE`, `ZIP`, `OBJECTID`
- Provides: addresses with geographic coordinates

**Dallas Central Appraisal District (DCAD)**
- Expected columns: `SITUS_ADDRESS`, `OWNER_NAME`, `ACCOUNT_NUM`, `PARCEL_ID`, `LAND_VALUE`, `IMPR_VALUE`, `TOTAL_VALUE`, `LEGAL_DESC`
- Provides: owner names, parcel IDs, appraised values

### 2. Import via Admin UI

1. Go to **Import Data** in the nav bar
2. Select the source type (Dallas GIS or DCAD)
3. Upload the CSV/GeoJSON file
4. The import pipeline processes each row:
   - Normalizes the address
   - Checks for existing master house record (dedup)
   - Creates or enriches the house record
   - Creates a `house_source_link` with full provenance
   - Unmatched records go to the review queue

### 3. Review Unmatched Records

After import, check the **Unmatched Records** section on the Import Data page. These are records whose addresses could not be normalized or matched.

## Address Matching Logic

The matching pipeline uses a two-tier approach:

### Exact Normalized Match (Primary)

1. **Normalize** both addresses using the same rules:
   - Convert to uppercase
   - Strip punctuation (periods, commas)
   - Replace directional words → abbreviations (North → N, Southwest → SW)
   - Replace street suffixes → USPS abbreviations (Street → ST, Boulevard → BLVD)
   - Strip unit/apartment designators
   - Collapse whitespace

2. **Compare** normalized strings for exact equality

Example:
```
"1000 North Elm Street, Apt 2" → "1000 N ELM ST"
"1000 N. Elm St."              → "1000 N ELM ST"
→ MATCH
```

### Fallback: Unmatched Queue

Records that cannot be normalized (empty address, unparseable format) are stored in `unmatched_records` for manual admin review.

### Cross-Source Enrichment

When DCAD data is imported after GIS data:
- GIS provides address + lat/lng
- DCAD provides owner name, parcel ID, appraisal values
- Records are matched by normalized address and the master house is enriched with data from both sources

## Pluggable Importer Architecture

To add a new public data source:

1. Create a new module in `backend/app/importers/` (e.g., `my_source.py`)
2. Implement a function with signature:
   ```python
   def import_my_source(db: Session, file_path: str, source_import_id: str) -> int:
       # Process file, create/update MasterHouse records
       # Return count of records processed
   ```
3. Register it:
   ```python
   from app.importers import register_importer
   register_importer("my_source", import_my_source)
   ```
4. Import it in `backend/app/routes/imports.py` so it's registered at startup
5. Add an `<option>` to the source dropdown in `frontend/index.html`

## Event Workflow

1. **Import public data** (GIS addresses, DCAD appraisals)
2. **Create a fundraiser event** (Events page)
3. **Assign houses** to the event by ZIP code, street name, or limit
4. **Print packets** for volunteers (use the Print Packet button)
5. **Record visits** — outcomes, donations, tickets, notes, follow-up flags
6. **View dashboard** for aggregate stats

## Manual Entry

Manual house creation is intentionally limited. The primary workflow is:

- **Automated**: Import public data → houses are created automatically
- **Manual**: Only when a house is missing from source data, admin can add it from the Houses page
- **Always manual**: Visit outcomes, donation details, notes, follow-up flags

## Sample Data

The `sample_data/` directory contains sample CSV files for testing:

```bash
# With the app running:
# 1. Go to Import Data
# 2. Select "City of Dallas GIS Address Points"
# 3. Upload sample_data/dallas_gis_sample.csv
# 4. Then select "DCAD Appraisal / Parcel Data"
# 5. Upload sample_data/dcad_sample.csv
```

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/imports/` | Upload and import a source file |
| `GET` | `/api/imports/` | List all imports |
| `GET` | `/api/imports/unmatched/` | List unmatched records |
| `GET` | `/api/houses/` | Search master houses |
| `GET` | `/api/houses/map` | Get houses within map bounds |
| `POST` | `/api/houses/` | Manually add a house |
| `POST` | `/api/events/` | Create a fundraiser event |
| `GET` | `/api/events/` | List events |
| `POST` | `/api/events/{id}/assign` | Assign houses to event |
| `GET` | `/api/events/{id}/houses` | List event houses |
| `POST` | `/api/events/{id}/houses/{ehId}/visits` | Record a visit |
| `GET` | `/api/stats/` | Dashboard statistics |

## Development

```bash
# Run without Docker (requires local PostgreSQL)
export DATABASE_URL=postgresql://user:pass@localhost:5432/fundraiser
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload

# Run with Docker
docker compose up --build
```

## License

MIT
