"""Workflow tests: scout login codes, map grouping, walk groups, dashboard checklist."""

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


# --- Scout login codes -------------------------------------------------------

def _login(client, code):
    return client.post("/api/auth/scout-login", json={"code": code})


def test_new_scout_gets_code_and_signs_in_with_it_alone(client, db):
    scout = client.post("/api/scout/roster", json={"name": "Jane Doe"}, headers=_admin(db)).json()
    code = scout["login_code"]
    assert len(code) == 8 and code.isdigit()
    r = _login(client, code)
    assert r.status_code == 200 and r.json()["scout_name"] == "Jane Doe"
    # The scout app learns the full name from /me
    me = client.get("/api/auth/me", headers={"Authorization": "Bearer " + r.json()["token"]}).json()
    assert me == {"email": f"scout:{scout['id']}", "is_scout": True, "name": "Jane Doe"}
    # Admins can always see the code again
    roster = client.get("/api/scout/roster", headers=_admin(db)).json()
    assert roster[0]["login_code"] == code


def test_wrong_or_inactive_code_fails(client, db):
    db.add_all([ScoutRoster(name="Active", login_code="11111111"),
                ScoutRoster(name="Gone", login_code="22222222", active=False)])
    db.commit()
    assert _login(client, "99999999").status_code == 401
    assert _login(client, "22222222").status_code == 401
    assert _login(client, "12").status_code == 401
    assert _login(client, "111111").status_code == 401  # old 6-digit style is not enough
    assert _login(client, " 1111 1111 ").status_code == 200  # spaces are ignored


def test_regenerate_replaces_code_and_signs_out(client, db):
    headers = _admin(db)
    scout = client.post("/api/scout/roster", json={"name": "Jane Doe"}, headers=headers).json()
    token = _login(client, scout["login_code"]).json()["token"]

    new = client.post(f"/api/auth/scout-code/{scout['id']}/regenerate", headers=headers).json()
    assert new["login_code"] != scout["login_code"]
    assert _login(client, new["login_code"]).status_code == 200
    assert _login(client, scout["login_code"]).status_code == 401
    # Old session no longer works
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_codes_are_unique(client, db):
    lines = "name\n" + "\n".join(f"Scout {i}" for i in range(200))
    r = client.post("/api/scout/roster/import", files={"file": ("r.csv", io.BytesIO(lines.encode()), "text/csv")},
                    headers=_admin(db)).json()
    codes = [s["login_code"] for s in r["scouts"]]
    assert r["added"] == 200 and len(set(codes)) == 200


def test_csv_import_returns_codes(client, db):
    csv_file = io.BytesIO(b"name,scout_id\nJohn Smith,123\nAlex Lee,\n")
    r = client.post("/api/scout/roster/import", files={"file": ("r.csv", csv_file, "text/csv")},
                    headers=_admin(db)).json()
    assert {s["name"] for s in r["scouts"]} == {"John Smith", "Alex Lee"}
    assert all(len(s["login_code"]) == 8 for s in r["scouts"])


def test_scouts_cannot_regenerate_codes(client, db):
    s = ScoutRoster(name="Jane Doe", login_code="12345678")
    db.add(s)
    db.commit()
    r = client.post(f"/api/auth/scout-code/{s.id}/regenerate", headers=_session(db, f"scout:{s.id}"))
    assert r.status_code == 403


def test_many_correct_logins_from_one_wifi_are_fine(client, db):
    db.add(ScoutRoster(name="Jane", login_code="12345678"))
    db.commit()
    assert all(_login(client, "12345678").status_code == 200 for _ in range(25))


def test_guessing_codes_gets_blocked(client, db):
    db.add(ScoutRoster(name="Jane", login_code="12345678"))
    db.commit()
    for i in range(10):
        assert _login(client, f"{i:08d}").status_code == 401
    # Blocked now, even with the right code
    assert _login(client, "12345678").status_code == 429


def test_guessing_from_many_addresses_hits_overall_cap(client, db):
    # Simulate failures already recorded from many different addresses
    import time
    auth._failed_scout_logins["all"] = [time.time()] * auth._SCOUT_FAIL_MAX_TOTAL
    assert _login(client, "00000000").status_code == 429


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
    db.add_all([ScoutRoster(name="Ready", login_code="11111111"), ScoutRoster(name="Gone", login_code="22222222", active=False)])
    db.commit()
    c = client.get(f"/api/stats/checklist?event_id={ev.id}", headers=_admin(db)).json()
    assert c["houses"] == 3 and c["grouped"] == 2 and c["groups"] == 1
    assert c["visits"] == 1 and c["houses_visited"] == 1 and c["donations"] == 20
    assert c["scouts_ready"] == 1


# --- Caching -----------------------------------------------------------------

def test_frontend_files_are_always_revalidated(client):
    """Stops a browser from running an old app.js against a new index.html."""
    for path in ("/", "/scout", "/sw.js", "/static/app.js", "/static/entry.js", "/static/scout.js", "/static/style.css"):
        assert client.get(path).headers.get("cache-control") == "no-cache", path


def test_offline_helper_is_served_from_site_root(client):
    r = client.get("/sw.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
