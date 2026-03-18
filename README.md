# ScoutMap

A door-to-door fundraising management application that uses **public data as the primary source** for address/house records, minimizing manual data entry. Built for neighborhood fundraising campaigns in Dallas, TX. Branded with **Scouting America** identity.

## Architecture

```
┌─────────────┐     ┌─────────────────┐     ┌────────────┐
│  Admin App  │────▶│ FastAPI Backend │────▶│ PostgreSQL │
│  (/)        │◀────│  (Python)       │◀────│            │
│             │     │  + SQLAlchemy   │     │            │
│  Scout App  │────▶│                 │     │            │
│  (/scout)   │◀────│                 │     │            │
│  + Leaflet  │     │                 │     │            │
└─────────────┘     └─────────────────┘     └────────────┘
                          │
                          ▼
                    ┌─────────────┐
                    │ Dallas      │
                    │ ArcGIS REST │
                    │ Service     │
                    └─────────────┘
```

- **Backend**: Python / FastAPI / SQLAlchemy
- **Frontend**: Vanilla HTML/CSS/JS with Leaflet maps
- **Database**: PostgreSQL 16
- **Containerization**: Docker Compose
- **External**: Dallas ArcGIS REST API (tax parcels)

## Quick Start

```bash
# Clone and start
git clone <repo-url>
cd ScoutMap
docker compose up --build

# Admin app:  http://localhost:8000
# Scout app:  http://localhost:8000/scout
```

The database is created automatically on first startup.

## Three-Phase Workflow

The app organizes work into three phases:

### Phase 1: Prepare

1. **Create a fundraiser event** (Events page)
2. **Import house data** using any of these methods:
   - **ArcGIS fetch** — enter ZIP codes, see record count preview, fetch directly from Dallas ArcGIS
   - **Polygon boundary** — draw a polygon on the map, import all ArcGIS parcels within it (no ZIP needed)
   - **File upload** — upload Dallas GIS or DCAD CSV files
3. **Assign houses to an event** — can be done during import (select event in the import form) or later via the Events page
4. **Add scouts to the roster** — individually or via CSV import
5. **Use the map** — view imported houses, draw boundaries, select by box or street

### Phase 2: Organize

1. **Generate walk groups** — houses auto-grouped by street, sorted by address number
2. **Review houses** — search, filter, manage the master house list

### Phase 3: Collect

1. **Share the scout app URL** (`/scout`) with scouts
2. **Monitor progress** — Scout Data page shows per-scout stats and all visit records
3. **Export data** — all tables exportable as CSV

## Two Apps, One System

### Admin App (`/`)

The admin interface for troop leaders:

| Page | Phase | Purpose |
|------|-------|---------|
| **Dashboard** | — | Aggregate stats, phase progress, quick links |
| **Events** | Prepare | Create events, assign houses |
| **Import Data** | Prepare | ArcGIS fetch, file upload, import history, unmatched records |
| **Map** | Prepare | Interactive map with tools: pointer, box select, add house, polygon boundary |
| **Scouts** | Prepare | Manage roster, import/export CSV, set passwords |
| **Houses** | Organize | Search and manage master house list |
| **Walk Groups** | Organize | Auto-generate walkable groups by street |
| **Scout Data** | Collect | View, aggregate, and export field data |
| **Scout Form** | Collect | Customize dynamic form fields for scouts |
| **Settings** | — | Auth, email allowlist, configuration |

### Scout App (`/scout`)

A mobile/tablet-optimized field entry app:

1. **Select identity** — pick name from roster dropdown or write in
2. **Pick walk group** — select event and assigned group
3. **Record visits** — door answer, donation, former scout, avoid house, custom fields, notes
4. **Progress tracking** — progress bar shows completion within the group
5. **Log out** — clears saved info

## Importing Data

### Three Ways to Import

**1. ArcGIS Fetch (Recommended)**
- Enter ZIP codes in the Import Data page
- Live record count preview shows how many parcels are available before fetching
- Optionally select an event to auto-assign imported houses
- Pulls addresses, owner names, parcel IDs, and coordinates directly from Dallas ArcGIS

**2. Polygon Boundary Import (Map-based)**
- Open the Map page, click **Boundary** tool
- Click to draw a polygon following streets/alleys
- Shows both local house count and ArcGIS parcel count for the boundary
- Click **Import from ArcGIS** to fetch all parcels within the polygon — no ZIP code needed
- Delete boundary houses directly from the boundary panel
- Optionally assign to an event and group label in one step

**3. File Upload**
- Upload CSV/GeoJSON from Dallas GIS or DCAD
- Select source type and optionally an event
- Imported houses are automatically assigned to the selected event

### Import Pipeline

Each import method runs the same pipeline:
1. Normalize the address
2. Check for existing master house record (dedup by normalized address)
3. Create or enrich the house record
4. Create a `house_source_link` with full provenance
5. Unmatched records go to the review queue
6. If an event was selected, create `event_house` assignments

### Supported Sources

**City of Dallas GIS Address Points**
- Columns: `FULLADDR`, `LAT`, `LON`, `CITY`, `STATE`, `ZIP`, `OBJECTID`

**Dallas Central Appraisal District (DCAD)**
- Columns: `SITUS_ADDRESS`, `OWNER_NAME`, `ACCOUNT_NUM`, `PARCEL_ID`, `LAND_VALUE`, `IMPR_VALUE`, `TOTAL_VALUE`, `LEGAL_DESC`

**ArcGIS Tax Parcels (via API)**
- Fields: `ST_NUM`, `ST_NAME`, `ST_TYPE`, `ST_DIR`, `TAXPANAME1`, `ACCT`, `TAXPAZIP`
- Supports ZIP code filter, bounding box, and polygon geometry queries
- Automatically filtered to residential property types (single family residences and duplexes)

### Cross-Source Enrichment

When multiple sources cover the same address, records are matched by normalized address and enriched:
- GIS provides address + coordinates
- DCAD provides owner name, parcel ID, appraisal values
- ArcGIS provides all of the above

## Map Features

The interactive map (Leaflet) includes:

- **Zoom-based loading** — lightweight dots at low zoom (fast rendering of thousands of houses), full detail with popups at high zoom
- **Polygon boundary tool** — draw boundaries, count houses, import from ArcGIS, assign to events, delete
- **Box select** — drag to select houses, assign to events or delete selected
- **Add tool** — click map to place a new house manually
- **Walk group overlay** — select event to see color-coded walk routes

Touch/tablet support: all tools work with touch events, minimum 44px tap targets.

## Scout Roster

### CSV Format

```csv
name,scout_id
John Smith,12345
Jane Doe,67890
Alex Johnson,
```

| Column | Required | Description |
|--------|----------|-------------|
| `name` | Yes | Scout's full name |
| `scout_id` | No | BSA member ID or other identifier |

- Header row required, extra columns ignored
- Duplicate names (case-insensitive) skipped during import
- Passwords are set via the admin UI only (never from CSV)

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
    │       └── visits       Visit outcomes (donation, notes, scout data, custom fields)
    │
    └── unmatched_records    Records that could not be matched (for admin review)

fundraiser_events       Campaign / event definitions

scout_roster            Admin-managed list of scouts
scout_form_fields       Admin-configurable dynamic form fields

allowed_emails          Email allowlist for admin access
auth_sessions           Active authentication sessions
auth_codes              One-time login codes
```

## Authentication

The app supports two authentication methods:

- **Admin password** — set via `ADMIN_PASSWORD` env var, provides full admin access
- **Email OTP** — admin configures allowed emails/patterns (e.g., `*@troop123.org`), users receive a 6-digit code via SMTP

Scout accounts use passwords set by the admin (minimum 6 characters).

## API Endpoints

### Import & Data

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/imports/` | Upload and import a source file (optional `event_id`) |
| `GET` | `/api/imports/` | List all imports |
| `DELETE` | `/api/imports/{id}` | Delete import and cascade-remove orphaned houses |
| `GET` | `/api/imports/unmatched/` | List unmatched records |
| `POST` | `/api/arcgis/fetch` | Fetch parcels from ArcGIS (ZIP, bbox, or polygon) |
| `POST` | `/api/arcgis/count` | Preview record count before fetching |

### Houses & Map

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/houses/` | Search master houses |
| `GET` | `/api/houses/map` | Houses within map bounds (full detail) |
| `GET` | `/api/houses/map/dots` | Houses within bounds (lightweight: id, lat, lon only) |
| `GET` | `/api/houses/streets` | Streets and houses by ZIP code |
| `GET` | `/api/houses/zip-codes` | All imported ZIP codes with counts |
| `POST` | `/api/houses/in-polygon` | Find/assign houses inside a polygon boundary |
| `POST` | `/api/houses/` | Manually add a house |
| `POST` | `/api/houses/batch-delete` | Delete multiple houses |

### Events & Walk Groups

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/events/` | Create a fundraiser event |
| `GET` | `/api/events/` | List events with house counts |
| `POST` | `/api/events/{id}/assign` | Assign houses to event by ZIP/street filter |
| `POST` | `/api/events/{id}/walk-groups` | Generate walk groups by street |
| `GET` | `/api/events/{id}/houses` | List event houses with details |
| `POST` | `/api/events/{id}/houses/{ehId}/visits` | Record a visit |

### Scout & Roster

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/scout/roster` | List scout roster |
| `POST` | `/api/scout/roster` | Add scout to roster |
| `POST` | `/api/scout/roster/import` | Import roster from CSV |
| `GET` | `/api/scout/events` | List events with walk group labels |
| `GET` | `/api/scout/events/{id}/houses` | Houses in a walk group |
| `GET` | `/api/scout/data` | All scout visit records |
| `GET` | `/api/scout/data/summary` | Per-scout aggregated stats |

### Auth & Settings

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/auth/admin-password` | Login with admin password |
| `POST` | `/api/auth/request-code` | Request email OTP |
| `POST` | `/api/auth/verify-code` | Verify OTP and create session |
| `POST` | `/api/auth/scout-login` | Scout password login |
| `GET` | `/api/stats/` | Dashboard statistics |
| `GET/POST` | `/api/form-fields/` | Manage custom scout form fields |

## Pluggable Importer Architecture

To add a new public data source:

1. Create `backend/app/importers/my_source.py`:
   ```python
   def import_my_source(db: Session, file_path: str, source_import_id: str) -> int:
       # Process file, create/update MasterHouse records
       # Return count of records processed
   ```
2. Register it:
   ```python
   from app.importers import register_importer
   register_importer("my_source", import_my_source)
   ```
3. Import the module in `backend/app/routes/imports.py`
4. Add an `<option>` to the source dropdown in `frontend/index.html`

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

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `ADMIN_PASSWORD` | No | Master admin password for login |
| `SMTP_HOST` | No | SMTP server for email OTP |
| `SMTP_PORT` | No | SMTP port (default 587) |
| `SMTP_USER` | No | SMTP username |
| `SMTP_PASSWORD` | No | SMTP password |
| `SMTP_FROM` | No | From address for OTP emails |

## License

MIT
