"""Workflow tests: generated scout passwords, map grouping, walk groups, dashboard checklist."""

import io

from app.models import EventHouse, FundraiserEvent, MasterHouse, ScoutRoster, Visit
from app.routes import auth
from conftest import _session


def _admin(db):
    return _session(db, "admin")


def _event_with_houses(db, houses):
    """houses: list of (address_number, street_name, group_label)."""
    ev = FundraiserEvent(name="Spring")
    db.add(ev)
    db.flush()
    rows = []
    for num, street, label in houses:
        h = MasterHouse(normalized_address=f"{num} {street}", full_address=f"{num} {street}",
                        address_number=num, street_name=street)
        db.add(h)
        db.flush()
        eh = EventHouse(event_id=ev.id, house_id=h.id, assigned_to=label)
        db.add(eh)
        rows.append(eh)
    db.commit()
    return ev, rows


# --- Scout passwords ---------------------------------------------------------

def _login(client, roster_id, password):
    return client.post("/api/auth/scout-login", json={"scout_id": roster_id, "password": password})


def test_new_scout_gets_six_digit_password_that_works(client, db):
    r = client.post("/api/scout/roster", json={"name": "Jane Doe"}, headers=_admin(db))
    scout = r.json()
    assert len(scout["password"]) == 6 and scout["password"].isdigit()
    assert scout["has_password"]
    assert _login(client, scout["id"], scout["password"]).status_code == 200
    # The password is never returned again
    roster = client.get("/api/scout/roster", headers=_admin(db)).json()
    assert roster[0]["password"] is None


def test_regenerate_replaces_password_and_signs_out(client, db):
    headers = _admin(db)
    scout = client.post("/api/scout/roster", json={"name": "Jane Doe"}, headers=headers).json()
    token = _login(client, scout["id"], scout["password"]).json()["token"]

    new = client.post(f"/api/auth/scout-password/{scout['id']}/regenerate", headers=headers).json()
    assert new["password"] != scout["password"] or len(new["password"]) == 6
    assert _login(client, scout["id"], new["password"]).status_code == 200
    if new["password"] != scout["password"]:
        assert _login(client, scout["id"], scout["password"]).status_code == 401
    # Old session no longer works
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_generate_missing_passwords(client, db):
    db.add_all([ScoutRoster(name="No Pass"), ScoutRoster(name="Has Pass", password_hash=auth._hash_password("123456"))])
    db.commit()
    r = client.post("/api/auth/scout-passwords/generate-missing", headers=_admin(db)).json()
    assert [p["name"] for p in r["passwords"]] == ["No Pass"]


def test_csv_import_returns_passwords(client, db):
    csv_file = io.BytesIO(b"name,scout_id,password\nJohn Smith,123,ignored\nAlex Lee,,\n")
    r = client.post("/api/scout/roster/import", files={"file": ("r.csv", csv_file, "text/csv")},
                    headers=_admin(db)).json()
    assert r["added"] == 2
    assert {p["name"] for p in r["passwords"]} == {"John Smith", "Alex Lee"}
    assert all(len(p["password"]) == 6 for p in r["passwords"])


def test_scouts_cannot_regenerate_passwords(client, db):
    s = ScoutRoster(name="Jane Doe")
    db.add(s)
    db.commit()
    r = client.post(f"/api/auth/scout-password/{s.id}/regenerate", headers=_session(db, f"scout:{s.id}"))
    assert r.status_code == 403


def test_short_names_stay_distinguishable():
    names = ["Jack Smith", "Jack Stone", "Jane Doe", "Sam Lee", "Sam Lee"]
    assert auth._short_names(names) == ["Jack Sm.", "Jack St.", "Jane D.", "Sam L. (1)", "Sam L. (2)"]


# --- Map grouping ------------------------------------------------------------

def test_assign_with_group_name_moves_houses_already_in_event(client, db):
    ev, rows = _event_with_houses(db, [("1", "ELM", None), ("2", "ELM", "Old")])
    house_ids = [str(eh.house_id) for eh in rows]
    r = client.post(f"/api/events/{ev.id}/assign", json={"house_ids": house_ids, "assigned_to": "Elm A"},
                    headers=_admin(db)).json()
    assert r["assigned"] == 0 and r["regrouped"] == 2
    db.expire_all()
    assert {eh.assigned_to for eh in db.query(EventHouse).all()} == {"Elm A"}


def test_assign_without_group_name_leaves_groups_alone(client, db):
    ev, rows = _event_with_houses(db, [("1", "ELM", "Old")])
    r = client.post(f"/api/events/{ev.id}/assign", json={"house_ids": [str(rows[0].house_id)]},
                    headers=_admin(db)).json()
    assert r["regrouped"] == 0
    db.expire_all()
    assert db.query(EventHouse).one().assigned_to == "Old"


# --- Walk group generation ---------------------------------------------------

def test_make_groups_keeps_existing_and_reports_skipped(client, db):
    ev, _ = _event_with_houses(db, [
        ("1", "ELM", "Group 3 — 1 ELM"),   # already grouped: must be kept
        ("2", "OAK", None), ("4", "OAK", None),
        ("9", None, None),                  # no street: skipped
    ])
    r = client.post(f"/api/events/{ev.id}/walk-groups", json={"group_size": 20}, headers=_admin(db)).json()
    assert r["kept"] == 1 and r["skipped_no_street"] == 1 and r["total_assigned"] == 2
    # Numbering continues after the kept group
    assert r["groups"][0]["label"].startswith("Group 4")
    db.expire_all()
    labels = {eh.house.address_number: eh.assigned_to for eh in db.query(EventHouse).all()}
    assert labels["1"] == "Group 3 — 1 ELM"


def test_redo_all_regroups_everything(client, db):
    ev, _ = _event_with_houses(db, [("1", "ELM", "Custom"), ("3", "ELM", None)])
    r = client.post(f"/api/events/{ev.id}/walk-groups", json={"group_size": 20, "keep_existing": False},
                    headers=_admin(db)).json()
    assert r["kept"] == 0 and r["total_assigned"] == 2
    assert r["groups"][0]["label"] == "Group 1 — 1-3 ELM"


def test_scout_group_list_sorts_numbers_naturally(client, db):
    ev, _ = _event_with_houses(db, [("1", "A", "Group 10"), ("2", "B", "Group 2"), ("3", "C", "Group 1")])
    events = client.get("/api/scout/events", headers=_session(db, "scout:x")).json()
    assert events[0]["groups"] == ["Group 1", "Group 2", "Group 10"]


# --- Dashboard checklist -----------------------------------------------------

def test_checklist_counts(client, db):
    ev, rows = _event_with_houses(db, [("1", "ELM", "G1"), ("2", "ELM", "G1"), ("3", "OAK", None)])
    db.add(Visit(event_house_id=rows[0].id, scout_name="Jane", donation_amount=20))
    db.add_all([ScoutRoster(name="Ready", password_hash="x"), ScoutRoster(name="NoPass")])
    db.commit()
    c = client.get(f"/api/stats/checklist?event_id={ev.id}", headers=_admin(db)).json()
    assert c["houses"] == 3 and c["grouped"] == 2 and c["groups"] == 1
    assert c["visits"] == 1 and c["houses_visited"] == 1 and c["donations"] == 20
    assert c["scouts_ready"] == 1 and c["scouts_no_password"] == 1


# --- Caching -----------------------------------------------------------------

def test_frontend_files_are_always_revalidated(client):
    """Stops a browser from running an old app.js against a new index.html."""
    for path in ("/", "/scout", "/static/app.js", "/static/scout.js", "/static/style.css"):
        assert client.get(path).headers.get("cache-control") == "no-cache", path
