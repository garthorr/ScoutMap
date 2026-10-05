"""Admin visit entry: adults recording visits for scouts (live or from a walk sheet)."""

import uuid

from app.models import EventHouse, FundraiserEvent, MasterHouse, ScoutFormField, ScoutRoster, Visit
from conftest import _session


def _setup(db):
    """An event with 3 houses in one group, 2 scouts, and the default form fields."""
    ev = FundraiserEvent(name="Spring")
    db.add(ev)
    db.flush()
    rows = []
    for num in ("10", "9", "11"):
        h = MasterHouse(normalized_address=f"{num} ELM", full_address=f"{num} Elm St",
                        address_number=num, street_name="ELM")
        db.add(h)
        db.flush()
        eh = EventHouse(event_id=ev.id, house_id=h.id, assigned_to="Group 2")
        db.add(eh)
        rows.append(eh)
    jane = ScoutRoster(name="Jane Doe", scout_id="77", login_code="11112222")
    sam = ScoutRoster(name="Sam Lee", login_code="33334444")
    db.add_all([jane, sam])
    db.commit()
    return ev, rows, jane, sam


def _visit(eh, scout, client_id=None, **values):
    return {"client_id": client_id or uuid.uuid4().hex, "event_house_id": str(eh.id),
            "roster_id": str(scout.id), "values": values}


def test_entry_lists_houses_in_walk_order(client, db):
    ev, rows, jane, _ = _setup(db)
    data = client.get(f"/api/events/{ev.id}/entry?group=Group 2", headers=_session(db, "admin")).json()
    assert [h["address"] for h in data["houses"]] == ["9 Elm St", "10 Elm St", "11 Elm St"]
    assert data["groups"] == ["Group 2"]


def test_batch_save_credits_roster_scout_and_adult(client, db):
    ev, rows, jane, sam = _setup(db)
    body = {"visits": [
        _visit(rows[0], jane, door_answer=True, donation_given=True, donation_amount="20"),
        _visit(rows[1], sam, door_answer=False),
    ]}
    r = client.post(f"/api/events/{ev.id}/visits/batch", json=body, headers=_session(db, "leader@troop.org")).json()
    assert [x["status"] for x in r["results"]] == ["saved", "saved"]

    db.expire_all()
    v = db.query(Visit).filter(Visit.scout_name == "Jane Doe").one()
    assert v.scout_roster_id == jane.id and v.scout_id == "77"
    assert v.entered_by == "leader@troop.org"
    assert v.donation_amount == 20.0 and v.outcome == "donated"
    assert db.query(EventHouse).filter(EventHouse.id == rows[0].id).one().status == "visited"

    # Shows up in Scout Data and the per-scout summary
    admin = _session(db, "admin")
    data = client.get("/api/scout/data", headers=admin).json()
    assert {d["scout_name"] for d in data} == {"Jane Doe", "Sam Lee"}
    assert all(d["entered_by"] == "leader@troop.org" for d in data)
    summary = client.get("/api/scout/data/summary", headers=admin).json()
    assert summary["total_donations"] == 20.0


def test_resending_a_batch_saves_once(client, db):
    ev, rows, jane, _ = _setup(db)
    body = {"visits": [_visit(rows[0], jane, client_id="phone-abc-123", door_answer=True)]}
    headers = _session(db, "admin")
    first = client.post(f"/api/events/{ev.id}/visits/batch", json=body, headers=headers).json()
    again = client.post(f"/api/events/{ev.id}/visits/batch", json=body, headers=headers).json()
    assert first["results"][0]["status"] == "saved"
    assert again["results"][0] == {"client_id": "phone-abc-123", "status": "duplicate",
                                   "visit_id": first["results"][0]["visit_id"]}
    assert db.query(Visit).count() == 1


def test_bad_rows_are_reported_and_good_rows_still_save(client, db):
    ev, rows, jane, _ = _setup(db)
    db.query(ScoutFormField).filter(ScoutFormField.field_key == "door_answer").update({"required": True})
    db.commit()
    other_event = FundraiserEvent(name="Other")
    db.add(other_event)
    db.commit()
    body = {"visits": [
        _visit(rows[0], jane, door_answer=False),                       # "No" is a valid answer
        _visit(rows[1], jane),                                          # required answer missing
        {**_visit(rows[2], jane, door_answer=True), "roster_id": str(uuid.uuid4())},  # unknown scout
        _visit(rows[2], jane, door_answer=True, donation_amount="lots"),  # not a number
    ]}
    r = client.post(f"/api/events/{ev.id}/visits/batch", json=body, headers=_session(db, "admin")).json()
    statuses = [x["status"] for x in r["results"]]
    assert statuses == ["saved", "error", "error", "error"]
    assert "required" in r["results"][1]["error"]
    assert "roster" in r["results"][2]["error"]
    assert "number" in r["results"][3]["error"]
    # A house from another event is rejected
    r = client.post(f"/api/events/{other_event.id}/visits/batch", json={"visits": [_visit(rows[0], jane)]},
                    headers=_session(db, "admin")).json()
    assert r["results"][0]["status"] == "error"


def test_edit_and_undo(client, db):
    ev, rows, jane, sam = _setup(db)
    headers = _session(db, "admin")
    r = client.post(f"/api/events/{ev.id}/visits/batch", json={"visits": [_visit(rows[0], jane, door_answer=True)]},
                    headers=headers).json()
    visit_id = r["results"][0]["visit_id"]

    fixed = client.put(f"/api/events/{ev.id}/visits/{visit_id}",
                       json={"roster_id": str(sam.id), "values": {"door_answer": False}}, headers=headers).json()
    assert fixed["scout_name"] == "Sam Lee" and fixed["values"]["door_answer"] is False

    assert client.delete(f"/api/events/{ev.id}/visits/{visit_id}", headers=headers).json() == {"ok": True}
    db.expire_all()
    assert db.query(Visit).count() == 0
    assert db.query(EventHouse).filter(EventHouse.id == rows[0].id).one().status == "pending"


def test_scouts_cannot_use_admin_entry(client, db):
    ev, rows, jane, _ = _setup(db)
    headers = _session(db, f"scout:{jane.id}")
    assert client.get(f"/api/events/{ev.id}/entry", headers=headers).status_code == 403
    r = client.post(f"/api/events/{ev.id}/visits/batch", json={"visits": [_visit(rows[0], jane)]}, headers=headers)
    assert r.status_code == 403


def test_scout_app_records_roster_scout(client, db):
    ev, rows, jane, sam = _setup(db)
    # A scout recording their own visit
    client.post(f"/api/events/{ev.id}/houses/{rows[0].id}/visits", json={"scout_name": "ignored"},
                headers=_session(db, f"scout:{jane.id}"))
    # An admin in the scout app recording for a scout
    client.post(f"/api/events/{ev.id}/houses/{rows[1].id}/visits", json={"roster_id": str(sam.id)},
                headers=_session(db, "admin"))
    db.expire_all()
    by_name = {v.scout_name: v for v in db.query(Visit).all()}
    assert by_name["Jane Doe"].scout_roster_id == jane.id and by_name["Jane Doe"].entered_by is None
    assert by_name["Sam Lee"].scout_roster_id == sam.id and by_name["Sam Lee"].entered_by == "admin"
