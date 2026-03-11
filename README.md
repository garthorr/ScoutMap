# ScoutMap

A door-to-door fundraising management application that uses **public data as the primary source** for address/house records, minimizing manual data entry. Built for neighborhood fundraising campaigns in Dallas, TX.

## Architecture

```
┌─────────────┐     ┌─────────────────┐     ┌────────────┐
│  Admin App   │────▶│  FastAPI Backend │────▶│ PostgreSQL │
│  (/)         │◀────│  (Python)       │◀────│            │
│              │     │  + SQLAlchemy   │     │            │
│  Scout App   │────▶│                 │     │            │
│  (/scout)    │◀────│                 │     │            │
│  + Leaflet   │     │                 │     │            │
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
cd ScoutMap
docker compose up --build

# Admin app:  http://localhost:8000
# Scout app:  http://localhost:8000/scout
```

That's it. The database is created automatically on first startup.

## Two Apps, One System

### Admin App (`/`)

The admin interface for troop leaders to manage the fundraising campaign:

- **Dashboard** — aggregate stats (houses, events, visits, donations)
- **Map** — interactive Leaflet map of all house locations
- **Events** — create fundraising events
- **Walk Groups** — auto-generate walkable groups by street/ZIP
- **Import Data** — pull from ArcGIS or upload Dallas GIS / DCAD files
- **Houses** — search and manually manage the master house list
- **Roster** — manage the scout roster (add individually, import/export CSV)
- **Scout Data** — view, aggregate, and export all field data entered by scouts

All tables (houses, events, walk groups, scout data, roster) are **exportable as CSV**.

Data-modifying operations (walk group generation, imports) require **confirmation before overwriting** existing data.

### Scout App (`/scout`)

A mobile-first field entry app for scouts to use while walking their routes:

1. **Select identity** — pick name from the admin-managed roster dropdown, or choose "Other" to write in
2. **Pick walk group** — select event and assigned group
3. **Record visits** — for each house, enter:
   - Door answer (Yes / No Answer)
   - Donation (Yes / No, with amount field)
   - Former Scout? (Yes / No)
   - Avoid this house (checkbox)
   - Notes (free text)
4. **Progress tracking** — progress bar shows completion within the group
5. **Log out** — clears saved info and returns to setup

Scout name and selection are saved to `localStorage` so scouts don't re-enter info between sessions.

## Scout Roster Import/Export

The roster can be managed individually or in bulk via CSV.

### CSV Format

```csv
name,scout_id
John Smith,12345
Jane Doe,67890
Alex Johnson,
```

| Column | Required | Description |
|---|---|---|
| `name` | Yes | Scout's full name |
| `scout_id` | No | BSA member ID, troop number, or other identifier |

- A **header row** is required
- Extra columns are ignored
- **Duplicate names** (case-insensitive) are skipped during import
- Empty name rows are skipped
- Export produces the same format, so you can export → edit → re-import

### Import

1. Go to the **Roster** page in the admin app
2. Expand the **CSV Format Guide** to see the expected structure
3. Select a `.csv` file and click **Import CSV**
4. The status shows how many were added vs. skipped

### Export

Click **Export Roster CSV** at the bottom of the Roster page. The downloaded file uses the same `name,scout_id` format and can be re-imported.

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
    │       └── visits       Visit outcomes (donation, notes, follow-up, scout data)
    │
    └── unmatched_records    Records that could not be matched (for admin review)

fundraiser_events       Campaign / event definitions

scout_roster            Admin-managed list of scouts (populates scout app dropdown)
```

### Key Tables

| Table | Purpose |
|---|---|
| `source_imports` | Tracks each file upload: source name, batch ID, record count, status |
| `master_houses` | One row per physical address with lat/lng, owner, parcel/appraisal data |
| `house_source_links` | Links a house to its source import with provenance and raw data snapshot |
| `event_houses` | Assigns a master house to a fundraiser event with status tracking |
| `visits` | Records visit outcome, donation, scout info, door answer, former scout flag, avoid flag |
| `unmatched_records` | Stores records that couldn't be matched for admin review |
| `scout_roster` | Admin-managed scout names and IDs for the field app dropdown |

## Workflow

### Admin Workflow

1. **Import public data** — ArcGIS fetch (recommended) or upload Dallas GIS / DCAD files
2. **Create a fundraiser event** (Events page)
3. **Add scouts to the roster** (Roster page) — these appear in the scout app dropdown
4. **Generate walk groups** by ZIP code — houses are auto-grouped by street and sorted by address number
5. **Share the scout app URL** (`/scout`) with scouts
6. **Monitor progress** — Scout Data page shows per-scout stats, all visit records, and CSV export

### Scout Workflow

1. Open `/scout` on a phone
2. Select name from roster (or write in), pick event and walk group
3. Tap "Start Walking"
4. For each house, tap to record: door answer, donation, former scout, avoid, notes
5. Progress bar updates as houses are visited
6. Log out when done

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
2. **ArcGIS fetch** (recommended): enter ZIP codes and click Fetch — pulls data directly
3. **File upload**: select source type, upload CSV/GeoJSON
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

## API Endpoints

### Admin / Core

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/imports/` | Upload and import a source file |
| `GET` | `/api/imports/` | List all imports |
| `DELETE` | `/api/imports/{id}` | Delete import and cascade-remove orphaned houses |
| `GET` | `/api/imports/unmatched/` | List unmatched records |
| `POST` | `/api/arcgis/fetch` | Fetch parcels from Dallas ArcGIS |
| `GET` | `/api/houses/` | Search master houses |
| `GET` | `/api/houses/map` | Get houses within map bounds |
| `POST` | `/api/houses/` | Manually add a house |
| `POST` | `/api/events/` | Create a fundraiser event |
| `GET` | `/api/events/` | List events |
| `POST` | `/api/events/{id}/assign` | Assign houses to event |
| `POST` | `/api/events/{id}/walk-groups` | Generate walk groups by street |
| `GET` | `/api/events/{id}/houses` | List event houses |
| `POST` | `/api/events/{id}/houses/{ehId}/visits` | Record a visit |
| `GET` | `/api/stats/` | Dashboard statistics |

### Scout / Roster

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/scout/roster` | List scout roster |
| `POST` | `/api/scout/roster` | Add scout to roster |
| `POST` | `/api/scout/roster/import` | Import roster from CSV (columns: name, scout_id) |
| `PATCH` | `/api/scout/roster/{id}` | Toggle scout active/inactive |
| `DELETE` | `/api/scout/roster/{id}` | Remove scout from roster |
| `GET` | `/api/scout/events` | List events with walk group labels |
| `GET` | `/api/scout/events/{id}/houses?group=...` | Houses in a walk group |
| `GET` | `/api/scout/data` | All scout visit records (filterable by event) |
| `GET` | `/api/scout/data/summary` | Per-scout aggregated stats |

## Development

```bash
# Run without Docker (requires local PostgreSQL)
export DATABASE_URL=postgresql://user:pass@localhost:5432/scoutmap
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload

# Run with Docker
docker compose up --build
```

## License

MIT
