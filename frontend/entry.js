/* Enter Visits: an adult records visits for scouts, live on a phone while walking
 * with them, or afterwards from a paper walk sheet.
 *
 * Every save goes into a queue kept on the device (localStorage) and is sent to the
 * server in batches. If the signal drops, saves wait in the queue and send when it
 * comes back. Each save carries a random client_id, so a batch that gets re-sent is
 * only recorded once.
 */

const ENTRY_QUEUE_KEY = "scoutmap_visit_queue";
const ENTRY_STATE_KEY = "scoutmap_entry";

const entry = {
  eventId: "",
  group: "",
  events: [],        // [{id, name, groups: [...]}]
  houses: [],        // from /api/events/{id}/entry
  fields: [],        // active Scout Form fields
  roster: [],        // active scouts
  scouts: [],        // roster ids out today, in walk-sheet key order
  currentScout: "",  // roster id of the scout at the door
  openHouse: "",     // event_house_id whose form is open
  editing: null,     // {visit_id} or {client_id} when changing an existing visit
  form: {},          // answers in the open form
  view: "live",
  lastSaved: null,   // {client_id} for Undo
  sentIds: {},       // client_id -> visit_id once the server has it
};

// ---------------------------------------------------------------------------
// Storage (localStorage can be unavailable; never let that break the page)
// ---------------------------------------------------------------------------
function _readJSON(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
function _writeJSON(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ok */ }
}
function _readQueue() { return _readJSON(ENTRY_QUEUE_KEY, []); }
function _writeQueue(q) { _writeJSON(ENTRY_QUEUE_KEY, q); _updateUnsentBadges(); }

function _saveEntryState() {
  _writeJSON(ENTRY_STATE_KEY, {
    eventId: entry.eventId, group: entry.group, scouts: entry.scouts,
    currentScout: entry.currentScout, view: entry.view, lastSaved: entry.lastSaved,
  });
}

function _newClientId() {
  if (window.crypto?.randomUUID) return crypto.randomUUID().replace(/-/g, "");
  return Date.now().toString(36) + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
}

/** Number of saves still waiting to be sent (used by the logout warning too). */
function unsentVisitCount() { return _readQueue().length; }

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------
async function loadEntryPage() {
  Object.assign(entry, _readJSON(ENTRY_STATE_KEY, {}));
  if (!entry.eventId) entry.eventId = _getWorkingEventId();
  _updateOnlineState();
  try {
    const [evR, rosterR, fieldsR] = await Promise.all([
      authFetch(API + "/api/scout/events"),
      authFetch(API + "/api/scout/roster?active_only=true"),
      authFetch(API + "/api/form-fields"),
    ]);
    if (evR.ok) entry.events = await evR.json();
    if (rosterR.ok) entry.roster = await rosterR.json();
    if (fieldsR.ok) entry.fields = await fieldsR.json();
  } catch {
    _entryMessage("Can't reach the server and nothing is saved on this device yet. Open this page once with a signal first.", "error");
  }
  // Forget scouts who were removed from the roster
  entry.scouts = entry.scouts.filter(id => entry.roster.some(s => s.id === id));
  if (!entry.scouts.includes(entry.currentScout)) entry.currentScout = entry.scouts[0] || "";
  if (!entry.events.some(e => e.id === entry.eventId)) entry.eventId = entry.events[0]?.id || "";

  _renderPickers();
  await loadEntryHouses();
  flushVisitQueue();
}

/** Jump here from elsewhere with an event (and walk group) already picked. */
function openEntryFor(eventId, group) {
  const state = _readJSON(ENTRY_STATE_KEY, {});
  _writeJSON(ENTRY_STATE_KEY, { ...state, eventId, group: group || (state.eventId === eventId ? state.group : "") });
  showPage("entry");
}

async function loadEntryHouses() {
  const listEl = document.getElementById("entry-list");
  entry.houses = [];
  if (!entry.eventId || !entry.group) {
    listEl.innerHTML = `<p class="help-text">${entry.eventId ? "Pick a walk group." : "Pick an event."}</p>`;
    document.getElementById("entry-sheet").innerHTML = "";
    _renderEntry();
    return;
  }
  listEl.innerHTML = '<div class="loading-bar"></div>';
  try {
    const r = await authFetch(API + `/api/events/${entry.eventId}/entry?group=${encodeURIComponent(entry.group)}`);
    if (r.ok) entry.houses = (await r.json()).houses;
    else _entryMessage("Couldn't load the houses for this group.", "error");
  } catch {
    _entryMessage("No signal, and this group hasn't been opened on this device before.", "error");
  }
  _renderEntry();
}

// ---------------------------------------------------------------------------
// Pickers and scout buttons
// ---------------------------------------------------------------------------
function _renderPickers() {
  const evSel = document.getElementById("entry-event");
  evSel.innerHTML = entry.events.length
    ? entry.events.map(e => `<option value="${esc(e.id)}"${e.id === entry.eventId ? " selected" : ""}>${esc(e.name)}</option>`).join("")
    : '<option value="">No events yet</option>';

  const groups = entry.events.find(e => e.id === entry.eventId)?.groups || [];
  if (!groups.includes(entry.group)) entry.group = "";
  document.getElementById("entry-group").innerHTML = '<option value="">Pick a walk group…</option>' +
    groups.map(g => `<option value="${esc(g)}"${g === entry.group ? " selected" : ""}>${esc(g)}</option>`).join("");

  const addSel = document.getElementById("entry-add-scout");
  addSel.innerHTML = '<option value="">+ Add a scout…</option>' +
    entry.roster.filter(s => !entry.scouts.includes(s.id))
      .map(s => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("");
  _renderChips();
}

document.getElementById("entry-event").onchange = function () {
  entry.eventId = this.value;
  entry.group = "";
  entry.openHouse = "";
  _setWorkingEventId(this.value);
  _saveEntryState();
  _renderPickers();
  loadEntryHouses();
};

document.getElementById("entry-group").onchange = function () {
  entry.group = this.value;
  entry.openHouse = "";
  _saveEntryState();
  loadEntryHouses();
};

document.getElementById("entry-add-scout").onchange = function () {
  if (!this.value) return;
  entry.scouts.push(this.value);
  if (!entry.currentScout) entry.currentScout = this.value;
  _saveEntryState();
  _renderPickers();
  _renderEntry();
};

function _scoutName(id) { return entry.roster.find(s => s.id === id)?.name || "(removed scout)"; }
function _scoutNumber(id) { return entry.scouts.indexOf(id) + 1; }

function _renderChips() {
  const el = document.getElementById("entry-chips");
  if (!entry.scouts.length) {
    el.innerHTML = '<span class="entry-hint">Add the scouts who are out today ↑</span>';
    return;
  }
  el.innerHTML = entry.scouts.map((id, i) => `
    <span class="entry-chip${id === entry.currentScout ? " active" : ""}" data-id="${esc(id)}" onclick="pickEntryScout(this.dataset.id)">
      <span class="entry-chip-num">${i + 1}</span>${esc(_scoutName(id))}
      <button type="button" class="entry-chip-x" aria-label="Remove" onclick="event.stopPropagation(); removeEntryScout(this.parentElement.dataset.id)">&times;</button>
    </span>`).join("");
}

function pickEntryScout(id) {
  entry.currentScout = id;
  _saveEntryState();
  _renderChips();
  const who = document.getElementById("entry-form-scout");
  if (who) who.textContent = _scoutName(id);
}

function removeEntryScout(id) {
  entry.scouts = entry.scouts.filter(s => s !== id);
  if (entry.currentScout === id) entry.currentScout = entry.scouts[0] || "";
  _saveEntryState();
  _renderPickers();
  _renderEntry();
}

// ---------------------------------------------------------------------------
// House state: visits from the server plus saves still waiting to send
// ---------------------------------------------------------------------------
function _houseVisits(h) {
  const queued = _readQueue().filter(q => q.event_house_id === h.event_house_id && q.event_id === entry.eventId);
  const sent = new Set(h.visits.map(v => v.client_id).filter(Boolean));
  return [
    ...h.visits,
    ...queued.filter(q => !sent.has(q.client_id)).map(q => ({
      client_id: q.client_id, roster_id: q.roster_id, scout_name: _scoutName(q.roster_id),
      values: q.values, pending: true, error: q.error,
    })),
  ];
}

function _visitSummary(v) {
  const vals = v.values || {};
  const parts = [v.scout_name || "?"];
  if (vals.door_answer === false) parts.push("No answer");
  if (vals.donation_given) parts.push("$" + (vals.donation_amount || 0));
  else if (vals.door_answer === true) parts.push("Answered");
  if (vals.avoid_house) parts.push("AVOID");
  return parts.join(" · ");
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
function _renderEntry() {
  _renderChips();
  _updateUnsentBadges();
  document.querySelectorAll(".entry-view-toggle button").forEach(b => b.classList.toggle("active", b.dataset.view === entry.view));
  document.getElementById("entry-list").classList.toggle("hidden", entry.view !== "live");
  document.getElementById("entry-sheet").classList.toggle("hidden", entry.view !== "sheet");
  document.getElementById("entry-undo").disabled = !entry.lastSaved;
  if (!entry.eventId || !entry.group) return;
  if (entry.view === "live") _renderLive();
  else _renderSheet();
}

function setEntryView(view) {
  entry.view = view;
  entry.openHouse = "";
  _saveEntryState();
  _renderEntry();
}

function _renderLive() {
  const el = document.getElementById("entry-list");
  if (!entry.houses.length) { el.innerHTML = "<p class='help-text'>No houses in this group.</p>"; return; }
  const done = entry.houses.filter(h => _houseVisits(h).length).length;
  el.innerHTML = `<p class="entry-progress">${done} of ${entry.houses.length} houses visited</p>` +
    entry.houses.map((h, i) => {
      const visits = _houseVisits(h);
      const last = visits[visits.length - 1];
      const isOpen = h.event_house_id === entry.openHouse;
      let state = "";
      if (last?.error) state = '<span class="entry-state error">!</span>';
      else if (last?.pending) state = '<span class="entry-state pending" title="Not sent yet">&#8987;</span>';
      else if (last) state = '<span class="entry-state done">&#10003;</span>';
      const sub = last
        ? esc(_visitSummary(last)) + (last.error ? ` — <span class="entry-err">${esc(last.error)}</span>` : last.pending ? " · not sent yet" : "")
        : (h.owner_name ? esc(h.owner_name) : "Not visited");
      return `<div class="entry-house${last ? " visited" : ""}${isOpen ? " open" : ""}" id="eh-${esc(h.event_house_id)}">
        <button type="button" class="entry-house-head" data-id="${esc(h.event_house_id)}" onclick="openEntryHouse(this.dataset.id)">
          <span class="entry-num">${i + 1}</span>
          <span class="entry-house-text"><span class="entry-addr">${esc(h.address)}</span><span class="entry-sub">${sub}</span></span>
          ${state}
        </button>
        ${isOpen ? _formHtml(h, last) : ""}
      </div>`;
    }).join("");
}

// ---------------------------------------------------------------------------
// Live form
// ---------------------------------------------------------------------------
function openEntryHouse(id) {
  if (entry.openHouse === id) { entry.openHouse = ""; _renderEntry(); return; }
  const h = entry.houses.find(x => x.event_house_id === id);
  const visits = _houseVisits(h);
  const last = visits[visits.length - 1];
  entry.openHouse = id;
  // Visited: open it for editing (with a link to add another visit instead)
  entry.editing = last ? (last.pending ? { client_id: last.client_id } : { visit_id: last.id }) : null;
  entry.form = last ? { ...last.values } : {};
  if (last?.roster_id && entry.scouts.includes(last.roster_id)) entry.currentScout = last.roster_id;
  _renderEntry();
  document.getElementById("eh-" + id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function newEntryVisit() {
  entry.editing = null;
  entry.form = {};
  _renderEntry();
}

function _formHtml(h, last) {
  const editing = !!entry.editing;
  const fields = entry.fields.map(f => {
    const key = esc(f.field_key);
    const val = entry.form[f.field_key];
    const req = f.required ? ' <span class="entry-req">*</span>' : "";
    if (f.field_type === "toggle") {
      return `<div class="entry-field"><span class="entry-label">${esc(f.label)}${req}</span>
        <span class="entry-toggle">
          <button type="button" class="${val === true ? "yes" : ""}" data-key="${key}" onclick="setEntryValue(this.dataset.key, true, this)">Yes</button>
          <button type="button" class="${val === false ? "no" : ""}" data-key="${key}" onclick="setEntryValue(this.dataset.key, false, this)">No</button>
        </span></div>`;
    }
    if (f.field_type === "checkbox") {
      return `<label class="entry-field entry-check"><input type="checkbox" data-key="${key}" ${val ? "checked" : ""}
        onchange="setEntryValue(this.dataset.key, this.checked)" /> ${esc(f.label)}${req}</label>`;
    }
    if (f.field_type === "number") {
      return `<label class="entry-field"><span class="entry-label">${esc(f.label)}${req}</span>
        <input type="number" inputmode="decimal" min="0" step="any" data-key="${key}" value="${val ?? ""}"
          oninput="setEntryValue(this.dataset.key, this.value === '' ? null : parseFloat(this.value))" /></label>`;
    }
    if (f.field_type === "select") {
      return `<label class="entry-field"><span class="entry-label">${esc(f.label)}${req}</span>
        <select data-key="${key}" onchange="setEntryValue(this.dataset.key, this.value || null)"><option value="">—</option>
        ${(f.options || []).map(o => `<option value="${esc(o)}"${val === o ? " selected" : ""}>${esc(o)}</option>`).join("")}</select></label>`;
    }
    const tag = f.field_type === "textarea" ? "textarea" : "input";
    return `<label class="entry-field"><span class="entry-label">${esc(f.label)}${req}</span>
      <${tag} data-key="${key}" ${tag === "input" ? `type="text" value="${esc(val ?? "")}"` : ""}
        oninput="setEntryValue(this.dataset.key, this.value)">${tag === "textarea" ? esc(val ?? "") : ""}${tag === "textarea" ? "</textarea>" : ""}</label>`;
  }).join("");

  return `<div class="entry-form">
    ${last?.error ? `<p class="entry-err">This visit couldn't be saved: ${esc(last.error)}</p>` : ""}
    <p class="entry-who">Scout: <strong id="entry-form-scout">${entry.currentScout ? esc(_scoutName(entry.currentScout)) : "tap a scout above"}</strong></p>
    ${fields}
    <div class="entry-form-actions">
      <button type="button" class="entry-save" onclick="saveEntryForm()">${editing ? "Save changes" : "Save"}</button>
      <button type="button" class="btn-sm btn-quiet" onclick="openEntryHouse(entry.openHouse)">Cancel</button>
    </div>
    ${editing ? `<p class="entry-links"><a href="#" onclick="event.preventDefault(); newEntryVisit()">Record another visit here instead</a>
      ${entry.editing.client_id ? ` · <a href="#" onclick="event.preventDefault(); discardQueued(entry.editing.client_id)">Discard this unsent visit</a>` : ""}</p>` : ""}
  </div>`;
}

function setEntryValue(key, value, btn) {
  entry.form[key] = value;
  if (btn) {  // Yes/No buttons: show which is picked
    btn.parentElement.querySelectorAll("button").forEach(b => b.className = "");
    btn.className = value ? "yes" : "no";
  }
}

/** Problem with the answers, or "" if they're fine. Mirrors the server's check. */
function _checkRequired(values) {
  for (const f of entry.fields) {
    if (!f.required) continue;
    const v = values[f.field_key];
    if (v == null || v === "" || (f.field_type === "checkbox" && v === false)) return `"${f.label}" is required.`;
  }
  return "";
}

async function saveEntryForm() {
  if (!entry.currentScout) { alert("Tap the scout who went to the door first."); return; }
  const problem = _checkRequired(entry.form);
  if (problem) { alert(problem); return; }
  const values = { ...entry.form };
  const houseId = entry.openHouse;

  if (entry.editing?.visit_id) {
    // Changing a visit the server already has: needs a connection
    try {
      const r = await authFetch(API + `/api/events/${entry.eventId}/visits/${entry.editing.visit_id}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ roster_id: entry.currentScout, values }),
      });
      const d = await r.json();
      if (!r.ok) { alert(d.detail || "Couldn't save the change."); return; }
      const h = entry.houses.find(x => x.event_house_id === houseId);
      h.visits = h.visits.map(v => v.id === d.id ? d : v);
    } catch {
      alert("No signal. Changes to a visit that's already sent need a connection. New visits still save offline.");
      return;
    }
  } else if (entry.editing?.client_id) {
    // Still waiting to send: just update it in the queue
    _writeQueue(_readQueue().map(q => q.client_id === entry.editing.client_id
      ? { ...q, roster_id: entry.currentScout, values, error: undefined } : q));
  } else {
    const item = {
      client_id: _newClientId(), event_id: entry.eventId, event_house_id: houseId,
      roster_id: entry.currentScout, visited_at: new Date().toISOString(), values,
    };
    _writeQueue([..._readQueue(), item]);
    entry.lastSaved = { client_id: item.client_id };
    _saveEntryState();
  }

  // Move on to the next house nobody has visited yet
  const idx = entry.houses.findIndex(x => x.event_house_id === houseId);
  const next = entry.houses.slice(idx + 1).find(x => !_houseVisits(x).length);
  entry.openHouse = "";
  entry.editing = null;
  if (next) openEntryHouse(next.event_house_id);
  else _renderEntry();
  flushVisitQueue();
}

function discardQueued(clientId) {
  if (!confirm("Discard this visit? It hasn't been sent, so it will be gone.")) return;
  _writeQueue(_readQueue().filter(q => q.client_id !== clientId));
  if (entry.lastSaved?.client_id === clientId) { entry.lastSaved = null; _saveEntryState(); }
  entry.openHouse = "";
  entry.editing = null;
  _renderEntry();
}

async function undoLastEntry() {
  const last = entry.lastSaved;
  if (!last) return;
  const queued = _readQueue().find(q => q.client_id === last.client_id);
  const house = entry.houses.find(h => h.event_house_id === queued?.event_house_id)
    || entry.houses.find(h => h.visits.some(v => v.client_id === last.client_id));
  const label = house ? `${house.address} (${_scoutName(queued?.roster_id || house.visits.find(v => v.client_id === last.client_id)?.roster_id)})` : "the last visit";
  if (!confirm(`Undo ${label}?`)) return;

  if (queued) {
    _writeQueue(_readQueue().filter(q => q.client_id !== last.client_id));
  } else {
    const visitId = entry.sentIds[last.client_id] || house?.visits.find(v => v.client_id === last.client_id)?.id;
    if (!visitId) { alert("Couldn't find that visit."); return; }
    try {
      const r = await authFetch(API + `/api/events/${entry.eventId}/visits/${visitId}`, { method: "DELETE" });
      if (!r.ok) { alert("Couldn't undo that visit."); return; }
      if (house) house.visits = house.visits.filter(v => v.id !== visitId);
    } catch {
      alert("No signal. This visit was already sent, so undoing it needs a connection.");
      return;
    }
  }
  entry.lastSaved = null;
  _saveEntryState();
  _renderEntry();
}

// ---------------------------------------------------------------------------
// Sheet view: type in a paper walk sheet, row by row
// ---------------------------------------------------------------------------
function _renderSheet() {
  const el = document.getElementById("entry-sheet");
  if (!entry.houses.length) { el.innerHTML = "<p class='help-text'>No houses in this group.</p>"; return; }
  if (!entry.scouts.length) {
    el.innerHTML = "<p class='help-text'>Add the scouts above in the same order as the key on your walk sheet (1, 2, 3…).</p>";
    return;
  }
  const scoutOptions = '<option value=""></option>' +
    entry.scouts.map((id, i) => `<option value="${esc(id)}">${i + 1} · ${esc(_scoutName(id))}</option>`).join("");
  const head = entry.fields.map(f => `<th>${esc(f.label)}</th>`).join("");

  el.innerHTML = `<table class="entry-sheet-table">
    <thead><tr><th>#</th><th>Address</th><th>Scout</th>${head}</tr></thead><tbody>` +
    entry.houses.map((h, i) => {
      const visits = _houseVisits(h);
      const last = visits[visits.length - 1];
      if (last) {
        return `<tr class="entry-sheet-done"><td data-label="#">${i + 1}</td><td data-label="Address">${esc(h.address)}</td>
          <td colspan="${entry.fields.length + 1}" data-label="Visit">${last.pending ? "&#8987; " : "&#10003; "}${esc(_visitSummary(last))}
          <button type="button" class="btn-sm btn-quiet" data-id="${esc(h.event_house_id)}" onclick="entry.view='live'; openEntryHouse(this.dataset.id)">Edit</button></td></tr>`;
      }
      const cells = entry.fields.map(f => {
        const key = esc(f.field_key);
        let input;
        if (f.field_type === "toggle") {
          input = `<select data-key="${key}"><option value=""></option><option value="true">Y</option><option value="false">N</option></select>`;
        } else if (f.field_type === "checkbox") {
          input = `<input type="checkbox" data-key="${key}" />`;
        } else if (f.field_type === "number") {
          input = `<input type="number" inputmode="decimal" min="0" step="any" data-key="${key}" />`;
        } else if (f.field_type === "select") {
          input = `<select data-key="${key}"><option value=""></option>${(f.options || []).map(o => `<option value="${esc(o)}">${esc(o)}</option>`).join("")}</select>`;
        } else {
          input = `<input type="text" data-key="${key}" />`;
        }
        return `<td data-label="${esc(f.label)}">${input}</td>`;
      }).join("");
      return `<tr data-id="${esc(h.event_house_id)}"><td data-label="#">${i + 1}</td><td data-label="Address">${esc(h.address)}</td>
        <td data-label="Scout"><select class="entry-sheet-scout">${scoutOptions}</select></td>${cells}</tr>`;
    }).join("") +
    `</tbody></table>
    <div class="entry-sheet-actions"><button type="button" class="entry-save" onclick="saveEntrySheet()">Save all</button>
    <span class="help-text">Only rows with something filled in are saved. In the Scout box, type the number from the sheet.</span></div>`;
}

function saveEntrySheet() {
  const items = [];
  let problems = 0;
  document.querySelectorAll("#entry-sheet tbody tr[data-id]").forEach(row => {
    row.classList.remove("entry-row-error");
    row.title = "";
    const values = {};
    let filled = false;
    row.querySelectorAll("[data-key]").forEach(input => {
      const f = entry.fields.find(x => x.field_key === input.dataset.key);
      let v;
      if (f.field_type === "toggle") v = input.value === "" ? null : input.value === "true";
      else if (f.field_type === "checkbox") v = input.checked;
      else if (f.field_type === "number") v = input.value === "" ? null : parseFloat(input.value);
      else v = input.value.trim() || null;
      values[f.field_key] = v;
      // Any answer counts, including "No"; an unticked checkbox doesn't
      if (f.field_type === "checkbox" ? v === true : v !== null) filled = true;
    });
    const scout = row.querySelector(".entry-sheet-scout").value;
    if (!filled && !scout) return;  // blank row: skip
    const problem = !scout ? "Pick the scout for this house." : _checkRequired(values);
    if (problem) {
      row.classList.add("entry-row-error");
      row.title = problem;
      problems++;
      return;
    }
    items.push({
      client_id: _newClientId(), event_id: entry.eventId, event_house_id: row.dataset.id,
      roster_id: scout, visited_at: new Date().toISOString(), values,
    });
  });
  if (problems) {
    _entryMessage(`${problems} row(s) need fixing (highlighted). ${items.length ? "The other rows weren't saved yet." : ""}`, "error");
    return;
  }
  if (!items.length) { _entryMessage("Nothing to save — fill in at least one row.", "error"); return; }
  _writeQueue([..._readQueue(), ...items]);
  entry.lastSaved = { client_id: items[items.length - 1].client_id };
  _saveEntryState();
  _entryMessage(`Saved ${items.length} visit(s).`, "ok");
  _renderEntry();
  flushVisitQueue();
}

// ---------------------------------------------------------------------------
// Sending the queue
// ---------------------------------------------------------------------------
let _flushing = false;

async function flushVisitQueue() {
  _updateOnlineState();
  const waiting = _readQueue().filter(q => !q.error);
  if (_flushing || !waiting.length || !_signedIn || navigator.onLine === false) return;
  _flushing = true;
  let sentAny = false;
  try {
    const byEvent = {};
    waiting.forEach(q => (byEvent[q.event_id] = byEvent[q.event_id] || []).push(q));
    for (const [eventId, items] of Object.entries(byEvent)) {
      for (let i = 0; i < items.length; i += 100) {
        const chunk = items.slice(i, i + 100);
        const r = await authFetch(API + `/api/events/${eventId}/visits/batch`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ visits: chunk.map(({ client_id, event_house_id, roster_id, visited_at, values }) =>
            ({ client_id, event_house_id, roster_id, visited_at, values })) }),
        });
        if (r.status === 401) {
          _entryMessage("Your sign-in expired. Sign in again and the waiting visits will send.", "error");
          return;
        }
        if (!r.ok) return;  // server trouble: try again later
        const { results } = await r.json();
        const byId = Object.fromEntries(results.map(x => [x.client_id, x]));
        _writeQueue(_readQueue().flatMap(q => {
          const res = byId[q.client_id];
          if (!res) return [q];
          if (res.status === "error") return [{ ...q, error: res.error }];  // shown on the house to fix
          entry.sentIds[q.client_id] = res.visit_id;
          _markSent(q, res.visit_id);
          sentAny = true;
          return [];
        }));
      }
    }
  } catch {
    /* no signal: everything stays queued */
  } finally {
    _flushing = false;
    _updateUnsentBadges();
  }
  // Pick up the server's copy (with ids) so edits and Undo work on sent visits
  if (sentAny && document.getElementById("page-entry").classList.contains("active") && !entry.openHouse) {
    await loadEntryHouses();
  } else if (document.getElementById("page-entry").classList.contains("active")) {
    _renderEntry();
  }
}

/** Show a sent visit on its house right away (the full reload happens when no form is open). */
function _markSent(q, visitId) {
  if (q.event_id !== entry.eventId) return;
  const h = entry.houses.find(x => x.event_house_id === q.event_house_id);
  if (!h || h.visits.some(v => v.client_id === q.client_id)) return;
  h.visits.push({ id: visitId, client_id: q.client_id, roster_id: q.roster_id,
                  scout_name: _scoutName(q.roster_id), values: q.values });
}

function _updateUnsentBadges() {
  const q = _readQueue();
  const failed = q.filter(x => x.error).length;
  const text = q.length ? `Unsent (${q.length})` + (failed ? ` · ${failed} need fixing` : "") : "";
  for (const id of ["entry-unsent", "nav-unsent"]) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.textContent = id === "nav-unsent" ? (q.length || "") : text;
    el.classList.toggle("hidden", !q.length);
    el.classList.toggle("has-errors", failed > 0);
  }
}

function _updateOnlineState() {
  document.getElementById("entry-offline")?.classList.toggle("hidden", navigator.onLine !== false);
}

let _msgTimer = null;
function _entryMessage(text, kind) {
  const el = document.getElementById("entry-msg");
  el.textContent = text;
  el.className = "entry-msg " + (kind || "");
  clearTimeout(_msgTimer);
  if (kind === "ok") _msgTimer = setTimeout(() => el.classList.add("hidden"), 4000);
}

window.addEventListener("online", flushVisitQueue);
window.addEventListener("offline", _updateOnlineState);
setInterval(flushVisitQueue, 20000);
setTimeout(flushVisitQueue, 1500);  // anything left over from last time
_updateUnsentBadges();

// ---------------------------------------------------------------------------
// Printable walk sheets
// ---------------------------------------------------------------------------
async function printWalkSheets(eventId, group) {
  if (!eventId) { alert("Pick an event first."); return; }
  const w = window.open("", "_blank");  // open now, before waiting, so pop-up blockers allow it
  if (!w) { alert("Allow pop-ups to print walk sheets."); return; }
  w.document.write("<p style='font-family:sans-serif'>Preparing walk sheets…</p>");

  let data, fields;
  try {
    const q = group ? `?group=${encodeURIComponent(group)}` : "";
    const [r, fr] = await Promise.all([
      authFetch(API + `/api/events/${eventId}/entry${q}`),
      authFetch(API + "/api/form-fields"),
    ]);
    data = await r.json();
    fields = fr.ok ? await fr.json() : [];
    if (!r.ok) throw new Error(data.detail || "error");
  } catch (err) {
    w.document.body.innerHTML = "<p>Couldn't load the walk sheets: " + esc(err.message) + "</p>";
    return;
  }

  const groups = [];
  data.houses.forEach(h => {
    const label = h.group || "Not in a group";
    let g = groups.find(x => x.label === label);
    if (!g) groups.push(g = { label, houses: [] });
    g.houses.push(h);
  });
  if (!groups.length) { w.document.body.innerHTML = "<p>No houses to print.</p>"; return; }

  const box = '<span class="box"></span>';
  const cell = f => {
    if (f.field_type === "toggle") return `Y ${box} N ${box}`;
    if (f.field_type === "checkbox") return box;
    if (f.field_type === "number" && /amount|\$|donat/i.test(f.label)) return "$";
    if (f.field_type === "select") return `<span class="opts">${(f.options || []).map(esc).join(" / ")}</span>`;
    return "";
  };
  const notesLike = f => f.field_type === "textarea";
  const head = fields.map(f => `<th class="${notesLike(f) ? "notes" : ""}">${esc(f.label)}</th>`).join("");
  const blankCells = fields.map(f => `<td class="${notesLike(f) ? "notes" : ""}">${cell(f)}</td>`).join("");

  const sheets = groups.map(g => `<section class="sheet">
    <h1>${esc(data.event.name)} — ${esc(g.label)}</h1>
    <div class="meta">Date: ______________ &nbsp; Adult: ______________________</div>
    <div class="key"><b>Scouts</b> (write their number in the Scout column):
      ${[1, 2, 3, 4, 5, 6].map(n => `<span>${n}. ________________</span>`).join("")}</div>
    <table><thead><tr><th>#</th><th class="addr">Address</th><th>Scout #</th>${head}</tr></thead><tbody>
      ${g.houses.map((h, i) => `<tr><td>${i + 1}</td><td class="addr">${esc(h.address)}</td><td></td>${blankCells}</tr>`).join("")}
      ${[1, 2, 3].map(() => `<tr><td></td><td class="addr">Other: </td><td></td>${blankCells}</tr>`).join("")}
    </tbody></table>
  </section>`).join("");

  w.document.open();
  w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>Walk sheets — ${esc(data.event.name)}</title>
    <style>
      @page { size: landscape; margin: 10mm; }
      body { font-family: sans-serif; margin: 12px; color: #000; }
      .sheet { page-break-after: always; }
      .sheet:last-child { page-break-after: auto; }
      h1 { font-size: 18px; margin: 0 0 4px; }
      .meta { font-size: 13px; margin-bottom: 6px; }
      .key { font-size: 13px; margin-bottom: 8px; display: flex; flex-wrap: wrap; gap: 4px 16px; }
      table { width: 100%; border-collapse: collapse; font-size: 13px; }
      th, td { border: 1px solid #555; padding: 6px 5px; text-align: left; vertical-align: middle; }
      th { background: #eee; font-size: 11px; }
      tr { height: 30px; page-break-inside: avoid; }
      td.addr { min-width: 170px; }
      th.notes, td.notes { width: 22%; }
      .box { display: inline-block; width: 13px; height: 13px; border: 1.5px solid #000; vertical-align: middle; margin-right: 4px; }
      .opts { font-size: 10px; color: #444; }
    </style></head><body>${sheets}</body></html>`);
  w.document.close();
  w.focus();
  w.print();
}
