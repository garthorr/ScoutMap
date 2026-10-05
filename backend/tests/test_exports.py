"""CSV exports and paged admin lists."""

import csv
import io
import json
import uuid
from datetime import datetime, timedelta

import anyio
import pytest

from app.csv_export import csv_response, safe_cell
from app.models import (
    EventHouse, FundraiserEvent, MasterHouse, ScoutFormField, SourceImport, UnmatchedRecord, Visit,
)
from conftest import _session

EXPORTS = ["/api/scout/data.csv", "/api/scout/data/summary.csv", "/api/houses/export.csv"]


def _rows(r):
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "text/csv; charset=utf-8"
    assert r.headers["cache-control"] == "no-store"
    text = r.content.decode("utf-8")
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:], newline="")))


def _visits(db):
    """Two events; Jane has two visits in Spring, Sam one in Fall."""
    spring, fall = FundraiserEvent(name="Spring"), FundraiserEvent(name="Fall")
    db.add_all([spring, fall])
    db.flush()
    houses = []
    for ev, addr in ((spring, "1 Main St"), (spring, "2 Main St"), (fall, "3 Oak St")):
        h = MasterHouse(normalized_address=addr.upper(), full_address=addr, zip_code="75201")
        db.add(h)
        db.flush()
        eh = EventHouse(event_id=ev.id, house_id=h.id, assigned_to="Group 1")
        db.add(eh)
        db.flush()
        houses.append(eh)
    t = datetime(2026, 5, 1, 10, 0, 0)
    db.add_all([
        Visit(event_house_id=houses[0].id, scout_name="Jane Doe", scout_id="77", visited_at=t,
              door_answer=True, donation_given=True, donation_amount=20.0, avoid_house=False,
              notes='=HYPERLINK("http://evil.example","click")', outcome="donated",
              custom_data=json.dumps({"door_answer": True, "shirt_size": "M", "old_key": "x"})),
        Visit(event_house_id=houses[1].id, scout_name="Jane Doe", scout_id="77",
              visited_at=t + timedelta(hours=1), door_answer=False, avoid_house=True,
              outcome="not_home", entered_by="leader@troop.org", custom_data="{not json"),
        Visit(event_house_id=houses[2].id, scout_name="Sam Lee", visited_at=t + timedelta(hours=2),
              door_answer=True, donation_given=True, donation_amount=5.5, former_scout=True),
        # No scout name: not scout data
        Visit(event_house_id=houses[2].id, visited_at=t),
    ])
    db.commit()
    return spring, fall


@pytest.fixture
def shirt_field(db):
    f = ScoutFormField(field_key="shirt_size", label="Shirt Size", field_type="text",
                       position=99, active=False)
    db.add(f)
    db.commit()
    yield f
    db.query(ScoutFormField).filter(ScoutFormField.id == f.id).delete()
    db.commit()


def test_safe_cell():
    assert safe_cell(None) == ""
    assert safe_cell(True) == "Yes" and safe_cell(False) == "No"
    assert safe_cell(-96.8) == "-96.8" and safe_cell(-3) == "-3"
    assert safe_cell(datetime(2026, 1, 2, 3, 4)) == "2026-01-02T03:04:00"
    assert safe_cell({"a": 1}) == '{"a": 1}'
    for bad in ("=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx"):
        assert safe_cell(bad) == "'" + bad
    assert safe_cell("Main St") == "Main St"


def test_csv_response_closes_rows_on_disconnect():
    state = {"closed": False}

    def rows():
        try:
            for i in range(100_000):
                yield [i]
        finally:
            state["closed"] = True

    async def run():
        sent, chunks = anyio.Event(), []

        async def receive():
            await sent.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            chunks.append(message)
            if len(chunks) == 3:  # start, header, first block of rows
                sent.set()

        scope = {"type": "http", "asgi": {"spec_version": "2.3"}}
        await csv_response("x.csv", ["n"], rows())(scope, receive, send)
        assert chunks[-1]["more_body"]  # stream was cut off, not finished
        assert state["closed"]

    anyio.run(run)


@pytest.mark.parametrize("path", EXPORTS)
def test_exports_need_admin(client, db, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers=_session(db, f"scout:{uuid.uuid4()}")).status_code == 403


def test_scout_data_csv(client, db, shirt_field):
    _visits(db)
    r = client.get("/api/scout/data.csv", headers=_session(db, "admin"))
    rows = _rows(r)
    assert r.headers["content-disposition"] == 'attachment; filename="scout-data.csv"'
    assert r.content.decode("utf-8").splitlines()[0].endswith("Other Fields")
    assert b"\r\n" in r.content

    header = rows[0]
    assert header[:15] == ["Time", "Scout", "Scout ID", "Event", "Group", "Address", "ZIP",
                           "Door Answer", "Donation", "Amount", "Former Scout", "Avoid House",
                           "Notes", "Outcome", "Entered By"]
    assert header[-2:] == ["Shirt Size", "Other Fields"]
    # Built-in form fields don't get a second column
    assert "Door Answer" not in header[15:]

    data = [dict(zip(header, row)) for row in rows[1:]]
    assert [d["Scout"] for d in data] == ["Sam Lee", "Jane Doe", "Jane Doe"]  # newest first
    sam, jane_late, jane = data
    assert sam["Event"] == "Fall" and sam["Former Scout"] == "Yes" and sam["Amount"] == "5.5"
    assert sam["Entered By"] == "Scout" and sam["Other Fields"] == ""

    assert jane_late["Entered By"] == "leader@troop.org"
    assert jane_late["Door Answer"] == "No" and jane_late["Avoid House"] == "Yes"
    assert jane_late["Shirt Size"] == "" and jane_late["Other Fields"] == ""  # bad JSON

    assert jane["Time"] == "2026-05-01T10:00:00"
    assert jane["Address"] == "1 Main St" and jane["ZIP"] == "75201" and jane["Group"] == "Group 1"
    assert jane["Door Answer"] == "Yes" and jane["Donation"] == "Yes" and jane["Amount"] == "20.0"
    assert jane["Former Scout"] == "" and jane["Avoid House"] == "No"
    assert jane["Notes"] == '\'=HYPERLINK("http://evil.example","click")'
    assert jane["Shirt Size"] == "M"
    assert json.loads(jane["Other Fields"]) == {"old_key": "x"}


def test_scout_data_csv_event_filter(client, db):
    spring, fall = _visits(db)
    admin = _session(db, "admin")
    rows = _rows(client.get(f"/api/scout/data.csv?event_id={spring.id}", headers=admin))
    assert {row[3] for row in rows[1:]} == {"Spring"} and len(rows) == 3

    for bad in (uuid.uuid4(), "not-a-uuid"):
        r = client.get(f"/api/scout/data.csv?event_id={bad}", headers=admin)
        assert r.status_code == 404 and r.json()["detail"] == "Event not found"


def test_scout_summary_csv_and_json(client, db):
    spring, _ = _visits(db)
    admin = _session(db, "admin")

    assert client.get("/api/scout/data/summary", headers=admin).json() == {
        "total_visits": 3,
        "total_donations": 25.5,
        "scouts": [
            {"scout_name": "Jane Doe", "scout_id": "77", "total_visits": 2, "doors_answered": 1,
             "donations": 1, "donation_total": 20.0, "former_scouts": 0, "avoid_houses": 1},
            {"scout_name": "Sam Lee", "scout_id": None, "total_visits": 1, "doors_answered": 1,
             "donations": 1, "donation_total": 5.5, "former_scouts": 1, "avoid_houses": 0},
        ],
    }

    r = client.get("/api/scout/data/summary.csv", headers=admin)
    assert r.headers["content-disposition"] == 'attachment; filename="scout-summary.csv"'
    assert _rows(r) == [
        ["Scout", "Scout ID", "Visits", "Doors Answered", "Donations", "Donation Total",
         "Former Scouts", "Avoid Houses"],
        ["Jane Doe", "77", "2", "1", "1", "20.0", "0", "1"],
        ["Sam Lee", "", "1", "1", "1", "5.5", "1", "0"],
        ["TOTAL", "", "3", "2", "2", "25.5", "1", "1"],
    ]

    rows = _rows(client.get(f"/api/scout/data/summary.csv?event_id={spring.id}", headers=admin))
    assert [row[0] for row in rows[1:]] == ["Jane Doe", "TOTAL"]


def _houses(db, n=150):
    db.add_all([
        MasterHouse(normalized_address=f"{i:03d} ELM ST", full_address=f"{i} Elm St", city="Dallas",
                    zip_code="75201" if i < 120 else "75202", owner_name="@Owner" if i == 0 else "Pat",
                    total_appraised_value=250000.0, latitude=32.78, longitude=-96.8,
                    manually_created=(i == 1))
        for i in range(n)
    ])
    db.commit()


def test_houses_export_csv(client, db):
    _houses(db)
    admin = _session(db, "admin")
    r = client.get("/api/houses/export.csv", headers=admin)
    rows = _rows(r)
    assert r.headers["content-disposition"] == 'attachment; filename="houses.csv"'
    assert rows[0] == ["Address", "City", "ZIP", "Owner", "Appraised Value", "Latitude", "Longitude", "Source"]
    assert len(rows) == 151
    assert rows[1] == ["0 Elm St", "Dallas", "75201", "'@Owner", "250000.0", "32.78", "-96.8", "Imported"]
    assert rows[2][-1] == "Manual"

    rows = _rows(client.get("/api/houses/export.csv?zip_code=75202", headers=admin))
    assert len(rows) == 31 and {row[2] for row in rows[1:]} == {"75202"}


def test_houses_paging(client, db):
    _houses(db)
    admin = _session(db, "admin")
    r = client.get("/api/houses/?zip_code=75201&limit=50&offset=100", headers=admin)
    assert r.headers["x-total-count"] == "120"
    page = r.json()
    assert len(page) == 20 and page[0]["full_address"] == "100 Elm St"

    r = client.get("/api/houses/", headers=admin)
    assert r.headers["x-total-count"] == "150" and len(r.json()) == 100
    assert client.get("/api/houses/?limit=501", headers=admin).status_code == 422
    assert client.get("/api/houses/?offset=-1", headers=admin).status_code == 422


def test_unmatched_paging(client, db):
    si = SourceImport(source_name="dcad", import_batch_id="b1", status="completed")
    db.add(si)
    db.flush()
    t = datetime(2026, 5, 1)
    db.add_all([
        UnmatchedRecord(source_import_id=si.id, source_name="dcad", raw_address=f"{i} Nowhere",
                        created_at=t + timedelta(minutes=i))
        for i in range(5)
    ] + [UnmatchedRecord(source_import_id=si.id, source_name="dcad", status="resolved", created_at=t)])
    db.commit()
    admin = _session(db, "admin")

    pages = []
    for offset in (0, 2, 4):
        r = client.get(f"/api/imports/unmatched/?limit=2&offset={offset}", headers=admin)
        assert r.headers["x-total-count"] == "5"
        pages.append([u["raw_address"] for u in r.json()])
    assert pages == [["4 Nowhere", "3 Nowhere"], ["2 Nowhere", "1 Nowhere"], ["0 Nowhere"]]

    r = client.get("/api/imports/unmatched/?status=resolved", headers=admin)
    assert r.headers["x-total-count"] == "1" and len(r.json()) == 1
