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

## Workflow

The **Dashboard** shows a step-by-step checklist for the selected event and highlights the next step.

1. **Create an event** (Events page)
2. **Add houses on the map** — pick the event at the top of the Map page, click **Boundary**, draw around the area to walk, then click **Add to event** (or **Import from ArcGIS** if houses are missing; it adds them to the event too). Type a group name first to put them straight into a walk group.
3. **Make walk groups** — click **Make groups** below the map. Houses are grouped by street and sorted by address number. Houses already in a group keep it unless you tick **Redo all groups**.
4. **Add scouts** (Scouts page) — each scout gets a unique 8-digit sign-in code (shown as `1234 5678`). Codes are listed on the Scouts page; print cut-out sign-in cards or export them to CSV. Click **New code** if one is lost or shared.
5. **Send scouts out** — share the scout app link (`/scout`), then follow progress on **Scout Data**.
   Scouts without a phone? An adult records for them on **Enter Visits** (below).

## Entering Visits for Scouts

Scouts move as a group but each one goes to different doors, so every visit records which scout went.

- **Print walk sheets** (Enter Visits, or an event's house list): one landscape sheet per walk group, houses in walk order, a numbered key for up to 6 scouts, a Scout # column, and a column per Scout Form question.
- **Enter Visits page** (works on a phone):
  - **Live** — add the scouts who are out today, tap the scout who went to the door, tap the house, answer, **Save**. The next unvisited house opens. Tap a visited house to fix it; **Undo** removes the last save.
  - **Sheet** — type in a paper sheet row by row. Add scouts in the same order as the sheet's key, then type each row's scout number. **Save all** saves every filled-in row.
- **Works offline** — saves are kept on the device and sent automatically when the signal returns ("Unsent (N)" shows what's waiting, also on the menu). The page still opens with no signal if it was opened once before. Each save has its own ID, so a save re-sent after a dropped connection is only recorded once.
- **Scout Data** shows **Entered by**: "Scout" for the scout's own entries, or the adult's login.

**Import Data** is for bulk-loading houses into the database (by ZIP code or file). Adding houses to an event always happens on the map.

## Two Apps, One System

### Admin App (`/`)

The admin interface for troop leaders:

| Page | Menu | Purpose |
|------|------|---------|
| **Dashboard** | — | Next-step checklist for an event, overall totals |
| **Events** | Plan | Create, edit, duplicate events; house list and print packet |
| **Map** | Plan | Add houses to the event (boundary, box select), make and edit walk groups |
| **Scouts** | Plan | Manage roster, see/print/export sign-in codes, import/export CSV |
| **Scout Form** | Plan | Customize the form scouts fill in at each house |
| **Enter Visits** | Collect | Adults record visits for scouts — live on a phone (works offline) or from a printed walk sheet |
| **Scout Data** | Collect | View, aggregate, and export field data |
| **Houses** | Data | Search and manage the master house list |
| **Import Data** | Data | Bulk ArcGIS fetch by ZIP, file upload, import history, unmatched records |
| **Settings** | — | Auth, email allowlist |

### Scout App (`/scout`)

A mobile/tablet-optimized field entry app:

1. **Sign in** — type the 8-digit scout code (no name to pick); the app shows the scout's full name
2. **Pick walk group** — select event and assigned group
3. **Record visits** — door answer, donation, former scout, avoid house, custom fields, notes
4. **Progress tracking** — progress bar shows completion within the group
5. **Log out** — clears saved info

## Importing Data

### Three Ways to Import

**1. Polygon Boundary Import (Map — main path)**
- Pick the event at the top of the Map page, click the **Boundary** tool
- Click to draw a polygon following streets/alleys
- Shows both local house count and ArcGIS parcel count for the boundary
- Click **Import from ArcGIS** to fetch all parcels within the polygon — no ZIP code needed. Everything inside the boundary is then added to the selected event (and group, if you typed one)

**2. ArcGIS Fetch by ZIP (Import Data page)**
- Enter ZIP codes; a live count shows how many parcels are available
- Loads houses into the database only; add them to an event on the map

**3. File Upload (Import Data page)**
- Upload CSV/GeoJSON from Dallas GIS or DCAD
- Loads houses into the database only; add them to an event on the map

### Import Pipeline

Each import method runs the same pipeline:
1. Normalize the address
2. Check for existing master house record (dedup by normalized address)
3. Create or enrich the house record
4. Create a `house_source_link` with full provenance
5. Unmatched records go to the review queue

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
- **Event picker** — one choice at the top drives every tool on the page, and is remembered
- **Polygon boundary tool** — draw boundaries, count houses, import from ArcGIS, add to the event (optionally into a named group), delete
- **Box select** — drag to select houses, add them to the event or move them into a named group, or delete them
- **Add tool** — click map to place a new house manually
- **Walk groups** — color-coded routes on the map; make, rename, merge, or remove groups in the panel below it

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
- Each imported scout gets a unique 8-digit sign-in code, shown in the roster

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

Scouts sign in with just a unique 8-digit code generated by the server (100 million possibilities, so guessing one is impractical). There is no public list of scout names. Codes are stored as-is so admins can see, print and export them; **New code** replaces one and signs that scout out. Only wrong codes count toward the sign-in limit (10 per address and 50 overall per 15 minutes), so a troop signing in on one Wi-Fi isn't blocked.

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
| `POST` | `/api/events/{id}/assign` | Add houses to event (by IDs or ZIP/street); with `assigned_to`, also moves houses already in the event into that group |
| `POST` | `/api/events/{id}/walk-groups` | Generate walk groups by street (`keep_existing` defaults to true) |
| `GET` | `/api/events/{id}/houses` | List event houses with details |
| `GET` | `/api/events/{id}/entry?group=` | Houses in walk order with their visits (Enter Visits, walk sheets) |
| `POST` | `/api/events/{id}/visits/batch` | Save visits recorded by an adult; repeats of a `client_id` are ignored |
| `PUT` / `DELETE` | `/api/events/{id}/visits/{visitId}` | Fix or undo a visit |
| `POST` | `/api/events/{id}/houses/{ehId}/visits` | Record a visit |

### Scout & Roster

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/scout/roster` | List scout roster |
| `POST` | `/api/scout/roster` | Add scout to roster (returns their sign-in code) |
| `POST` | `/api/scout/roster/import` | Import roster from CSV (returns the new codes) |
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
| `POST` | `/api/auth/scout-login` | Scout sign-in with `{"code": "12345678"}` (spaces ignored) |
| `POST` | `/api/auth/scout-code/{id}/regenerate` | Give a scout a new sign-in code |
| `GET` | `/api/stats/` | Dashboard statistics |
| `GET` | `/api/stats/checklist?event_id=` | Dashboard checklist numbers for one event |
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
python -m app.startup        # migrations + seeding (run after pulling changes)
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
