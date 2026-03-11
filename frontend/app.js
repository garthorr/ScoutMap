/* global L */
const API = "";
let map, markersLayer, currentEventId, currentEventName;

// --- CSV Export Utility ---
function exportCSV(filename, headers, rows) {
  const escape = (v) => {
    const s = String(v ?? "");
    return s.includes(",") || s.includes('"') || s.includes("\n")
      ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const lines = [headers.map(escape).join(",")];
  rows.forEach(r => lines.push(r.map(escape).join(",")));
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

// --- Navigation ---
function showPage(name, evt) {
  document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
  document.querySelectorAll(".nav-links a").forEach(a => a.classList.remove("active"));
  const el = document.getElementById("page-" + name);
  if (el) el.classList.add("active");
  if (evt?.target?.tagName === "A") evt.target.classList.add("active");

  if (name === "dashboard") loadDashboard();
  if (name === "map") initMap();
  if (name === "events") loadEvents();
  if (name === "imports") { loadImports(); loadUnmatched(); }
  if (name === "houses") loadHouses();
  if (name === "walk-groups") loadWalkGroupEvents();
  if (name === "roster") loadRoster();
  if (name === "scout-data") { loadScoutDataEvents(); loadScoutData(); }
}

// --- Dashboard ---
async function loadDashboard() {
  const r = await fetch(API + "/api/stats/");
  const s = await r.json();
  document.getElementById("stats-grid").innerHTML = [
    stat("Houses", s.total_houses),
    stat("Events", s.total_events),
    stat("Visits", s.total_visits),
    stat("Donations", "$" + (s.total_donations || 0).toLocaleString()),
    stat("Unmatched", s.unmatched_count),
    stat("Imports", s.import_count),
  ].join("");
}
function stat(label, value) {
  return `<div class="stat-card"><div class="value">${value}</div><div class="label">${label}</div></div>`;
}

// --- Map ---
function initMap() {
  if (map) { map.invalidateSize(); refreshMapMarkers(); return; }
  setTimeout(() => {
    map = L.map("map").setView([32.78, -96.80], 12);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map);
    markersLayer = L.layerGroup().addTo(map);
    map.on("moveend", refreshMapMarkers);
    refreshMapMarkers();
  }, 100);
}
async function refreshMapMarkers() {
  if (!map) return;
  const b = map.getBounds();
  const params = new URLSearchParams({
    min_lat: b.getSouth(), max_lat: b.getNorth(),
    min_lon: b.getWest(), max_lon: b.getEast(),
    limit: 500,
  });
  const r = await fetch(API + "/api/houses/map?" + params);
  const houses = await r.json();
  markersLayer.clearLayers();
  houses.forEach(h => {
    if (!h.latitude || !h.longitude) return;
    const m = L.marker([h.latitude, h.longitude]);
    m.bindPopup(`<b>${h.full_address}</b><br>${h.owner_name || ""}<br>` +
      (h.total_appraised_value ? `Appraised: $${h.total_appraised_value.toLocaleString()}` : ""));
    markersLayer.addLayer(m);
  });
}

// --- Events ---
async function loadEvents() {
  const r = await fetch(API + "/api/events/");
  const events = await r.json();
  document.getElementById("events-list").innerHTML = events.length
    ? `<table><tr><th>Name</th><th>Date</th><th>Houses</th><th></th></tr>` +
      events.map(e => `<tr>
        <td>${e.name}</td>
        <td>${e.event_date ? new Date(e.event_date).toLocaleDateString() : "—"}</td>
        <td>${e.house_count}</td>
        <td><button class="btn-sm" onclick="openEvent('${e.id}','${e.name}')">Open</button></td>
      </tr>`).join("") + `</table>`
    : "<p>No events yet.</p>";
}
async function exportEventsCSV() {
  const r = await fetch(API + "/api/events/");
  const events = await r.json();
  if (!events.length) { alert("No events to export."); return; }
  exportCSV("events.csv",
    ["Name", "Description", "Date", "Houses"],
    events.map(e => [
      e.name, e.description || "", e.event_date || "", e.house_count,
    ])
  );
}

document.getElementById("event-form").onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = Object.fromEntries(fd);
  if (body.event_date) body.event_date = new Date(body.event_date).toISOString();
  else delete body.event_date;
  await fetch(API + "/api/events/", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  e.target.reset();
  loadEvents();
};

async function openEvent(id, name) {
  currentEventId = id;
  currentEventName = name;
  document.getElementById("event-detail-title").textContent = name;
  document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
  document.getElementById("page-event-detail").classList.add("active");
  loadEventHouses();
}

async function loadEventHouses() {
  const r = await fetch(API + `/api/events/${currentEventId}/houses`);
  const houses = await r.json();
  const el = document.getElementById("event-houses-list");
  if (!houses.length) { el.innerHTML = "<p>No houses assigned. Use Walk Groups or Manual Assign to add houses.</p>"; return; }

  // Group by assigned_to label
  const groups = {};
  houses.forEach(eh => {
    const key = eh.assigned_to || "Unassigned";
    if (!groups[key]) groups[key] = [];
    groups[key].push(eh);
  });

  let html = "";
  for (const [label, items] of Object.entries(groups)) {
    const visited = items.filter(eh => eh.status === "visited").length;
    html += `<details open><summary><strong>${label}</strong> — ${items.length} houses, ${visited} visited</summary>`;
    html += `<table><tr><th>#</th><th>Address</th><th>Owner</th><th>Status</th><th></th></tr>`;
    html += items.map((eh, idx) => `<tr>
      <td>${idx + 1}</td>
      <td>${eh.house.full_address}</td>
      <td>${eh.house.owner_name || "—"}</td>
      <td><span class="badge badge-${eh.status}">${eh.status}</span></td>
      <td><button class="btn-sm" onclick="openVisitModal('${currentEventId}','${eh.id}')">Visit</button></td>
    </tr>`).join("");
    html += `</table></details>`;
  }
  el.innerHTML = html;
}

document.getElementById("assign-form").onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = {};
  const zips = fd.get("zip_codes");
  if (zips) body.zip_codes = zips.split(",").map(s => s.trim());
  const streets = fd.get("street_names");
  if (streets) body.street_names = streets.split(",").map(s => s.trim());
  const limit = fd.get("limit");
  if (limit) body.limit = parseInt(limit);
  const assigned = fd.get("assigned_to");
  if (assigned) body.assigned_to = assigned;
  await fetch(API + `/api/events/${currentEventId}/assign`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  e.target.reset();
  loadEventHouses();
};

// --- Walk Groups ---
async function loadWalkGroupEvents() {
  const sel = document.getElementById("wg-event-select");
  try {
    const r = await fetch(API + "/api/events/");
    if (!r.ok) { sel.innerHTML = '<option value="">Failed to load events</option>'; return; }
    const events = await r.json();
    if (!events.length) {
      sel.innerHTML = '<option value="">No events — create one first</option>';
      return;
    }
    sel.innerHTML = '<option value="">Select an event…</option>' +
      events.map(ev => {
        const selected = String(ev.id) === String(currentEventId) ? " selected" : "";
        return `<option value="${ev.id}"${selected}>${ev.name} (${ev.house_count} houses)</option>`;
      }).join("");
    if (currentEventId) loadWalkGroupList();
  } catch (err) {
    sel.innerHTML = '<option value="">Error loading events</option>';
  }
}

document.getElementById("wg-event-select").onchange = (e) => {
  currentEventId = e.target.value;
  const opt = e.target.options[e.target.selectedIndex];
  currentEventName = opt.textContent;
  if (currentEventId) loadWalkGroupList();
  else document.getElementById("wg-groups-list").innerHTML = "";
};

document.getElementById("walk-group-form").onsubmit = async (e) => {
  e.preventDefault();
  if (!currentEventId) { alert("Select an event first."); return; }
  // Check if event already has houses assigned — warn before overwriting
  const existing = await fetch(API + `/api/events/${currentEventId}/houses`);
  const existingHouses = await existing.json();
  if (existingHouses.length > 0) {
    if (!confirm(`This event already has ${existingHouses.length} houses assigned.\n\nGenerating walk groups will reassign group labels for overlapping houses.\n\nContinue?`)) return;
  }
  const fd = new FormData(e.target);
  const body = {
    zip_code: fd.get("zip_code"),
    group_size: parseInt(fd.get("group_size") || "20"),
  };
  const streets = fd.get("street_names");
  if (streets) body.street_names = streets.split(",").map(s => s.trim()).filter(Boolean);
  const resultEl = document.getElementById("walk-group-result");
  resultEl.classList.remove("hidden");
  resultEl.textContent = "Generating groups…";
  try {
    const r = await fetch(API + `/api/events/${currentEventId}/walk-groups`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (r.ok && data.groups?.length) {
      resultEl.innerHTML = `<strong>${data.groups.length} groups created (${data.total_assigned} new houses assigned)</strong>` +
        `<ul>` + data.groups.map(g => `<li>${g.label} — ${g.houses} houses</li>`).join("") + `</ul>`;
      loadWalkGroupList();
    } else if (r.ok) {
      resultEl.textContent = data.message || "No houses found.";
    } else {
      resultEl.textContent = "Error: " + (data.detail || JSON.stringify(data));
    }
  } catch (err) {
    resultEl.textContent = "Network error: " + err.message;
  }
};

async function loadWalkGroupList() {
  if (!currentEventId) return;
  const el = document.getElementById("wg-groups-list");
  el.innerHTML = '<p class="loading-text">Loading groups…</p>';
  try {
    const r = await fetch(API + `/api/events/${currentEventId}/houses`);
    if (!r.ok) {
      el.innerHTML = `<p>Failed to load groups (HTTP ${r.status}).</p>`;
      return;
    }
    const houses = await r.json();
    if (!Array.isArray(houses) || !houses.length) {
      el.innerHTML = "<p>No groups yet. Generate walk groups above.</p>";
      return;
    }

    const groups = {};
    houses.forEach(eh => {
      const key = eh.assigned_to || "Unassigned";
      if (!groups[key]) groups[key] = [];
      groups[key].push(eh);
    });

    let html = "";
    for (const [label, items] of Object.entries(groups)) {
      const visited = items.filter(eh => eh.status === "visited").length;
      html += `<details><summary><strong>${label}</strong> — ${items.length} houses, ${visited} visited</summary>`;
      html += `<table><tr><th>#</th><th>Address</th><th>Owner</th></tr>`;
      html += items.map((eh, idx) => `<tr>
        <td>${idx + 1}</td>
        <td>${eh.house.full_address}</td>
        <td>${eh.house.owner_name || "—"}</td>
      </tr>`).join("");
      html += `</table></details>`;
    }
    el.innerHTML = html;
  } catch (err) {
    el.innerHTML = `<p>Error loading groups: ${err.message}</p>`;
  }
}

async function exportWalkGroupsCSV() {
  if (!currentEventId) { alert("Select an event first."); return; }
  const r = await fetch(API + `/api/events/${currentEventId}/houses`);
  const houses = await r.json();
  if (!houses.length) { alert("No walk groups to export."); return; }
  exportCSV("walk-groups.csv",
    ["Group", "Address", "Owner", "ZIP", "Status"],
    houses.map(eh => [
      eh.assigned_to || "Unassigned", eh.house.full_address,
      eh.house.owner_name || "", eh.house.zip_code || "", eh.status,
    ])
  );
}

async function exportEventHousesCSV() {
  if (!currentEventId) return;
  const r = await fetch(API + `/api/events/${currentEventId}/houses`);
  const houses = await r.json();
  if (!houses.length) { alert("No houses to export."); return; }
  exportCSV(`event-${currentEventName || "houses"}.csv`,
    ["Group", "Address", "Owner", "ZIP", "Status", "Latitude", "Longitude"],
    houses.map(eh => [
      eh.assigned_to || "Unassigned", eh.house.full_address,
      eh.house.owner_name || "", eh.house.zip_code || "", eh.status,
      eh.house.latitude || "", eh.house.longitude || "",
    ])
  );
}

// --- Visits ---
function openVisitModal(eventId, eventHouseId) {
  document.querySelector('[name="event_id"]').value = eventId;
  document.querySelector('[name="event_house_id"]').value = eventHouseId;
  document.getElementById("visit-modal").classList.remove("hidden");
}
function closeVisitModal() {
  document.getElementById("visit-modal").classList.add("hidden");
}
document.getElementById("visit-form").onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const eventId = fd.get("event_id");
  const ehId = fd.get("event_house_id");
  const body = {
    outcome: fd.get("outcome"),
    donation_amount: fd.get("donation_amount") ? parseFloat(fd.get("donation_amount")) : null,
    tickets_purchased: parseInt(fd.get("tickets_purchased") || "0"),
    notes: fd.get("notes") || null,
    follow_up: !!fd.get("follow_up"),
    volunteer_name: fd.get("volunteer_name") || null,
  };
  await fetch(API + `/api/events/${eventId}/houses/${ehId}/visits`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  closeVisitModal();
  loadEventHouses();
};

// --- Print packet ---
function printPacket() { window.print(); }

// --- ArcGIS Fetch ---
document.getElementById("arcgis-form").onsubmit = async (e) => {
  e.preventDefault();
  if (!confirm("This will fetch public data and add/update houses in the database.\n\nExisting house data will not be overwritten, only missing fields will be filled in.\n\nContinue?")) return;
  const fd = new FormData(e.target);
  const zips = fd.get("zip_codes");
  const body = {
    max_records: parseInt(fd.get("max_records") || "2000"),
    notes: fd.get("notes") || undefined,
  };
  if (zips) body.zip_codes = zips.split(",").map(s => s.trim()).filter(Boolean);
  document.getElementById("arcgis-progress").classList.remove("hidden");
  document.getElementById("arcgis-status").textContent = "connecting…";
  try {
    const r = await fetch(API + "/api/arcgis/fetch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (r.ok) {
      document.getElementById("arcgis-status").textContent =
        `Done! Fetched ${data.fetched} parcels, imported ${data.imported} records.`;
    } else {
      document.getElementById("arcgis-status").textContent =
        `Error: ${data.detail || "unknown"}`;
    }
  } catch (err) {
    document.getElementById("arcgis-status").textContent = "Network error: " + err.message;
  }
  e.target.reset();
  loadImports();
  loadUnmatched();
};

// --- Imports ---
document.getElementById("import-form").onsubmit = async (e) => {
  e.preventDefault();
  if (!confirm("This will import addresses and add/update houses in the database.\n\nExisting house data will not be overwritten.\n\nContinue?")) return;
  const fd = new FormData(e.target);
  document.getElementById("import-progress").classList.remove("hidden");
  document.getElementById("import-status").textContent = "uploading…";
  try {
    const r = await fetch(API + "/api/imports/", { method: "POST", body: fd });
    const data = await r.json();
    if (r.ok) {
      document.getElementById("import-status").textContent =
        `Done! ${data.record_count} records imported.`;
    } else {
      document.getElementById("import-status").textContent = `Error: ${data.detail || "unknown"}`;
    }
  } catch (err) {
    document.getElementById("import-status").textContent = "Network error: " + err.message;
  }
  e.target.reset();
  loadImports();
  loadUnmatched();
};

function importTitle(i) {
  // Extract zip codes from notes like "ArcGIS fetch: TAXPAZIP LIKE '75252%'"
  const zips = (i.notes || "").match(/\d{5}/g);
  const zipLabel = zips ? "ZIP " + [...new Set(zips)].join(", ") : "";
  const src = i.source_name === "arcgis_parcels" ? "ArcGIS" : i.source_name;
  return [src, zipLabel, i.file_name || ""].filter(Boolean).join(" — ");
}

async function loadImports() {
  const r = await fetch(API + "/api/imports/");
  const imports = await r.json();
  document.getElementById("imports-list").innerHTML = imports.length
    ? `<table><tr><th>Import</th><th>Records</th><th>Status</th><th>Date</th><th></th></tr>` +
      imports.map(i => `<tr>
        <td>${importTitle(i)}</td>
        <td>${i.record_count}</td>
        <td><span class="badge badge-${i.status}">${i.status}</span></td>
        <td>${new Date(i.created_at).toLocaleString()}</td>
        <td><button class="btn-sm btn-danger" onclick="deleteImport('${i.id}')">Delete</button></td>
      </tr>`).join("") + `</table>`
    : "<p>No imports yet.</p>";
}

async function deleteImport(id) {
  if (!confirm("Delete this import and its records?")) return;
  const r = await fetch(API + "/api/imports/" + id, { method: "DELETE" });
  const data = await r.json();
  if (r.ok) {
    alert(`Deleted. ${data.houses_removed} house(s) removed, ${data.houses_kept} kept.`);
    loadImports();
    loadUnmatched();
  } else {
    alert("Delete failed: " + (data.detail || JSON.stringify(data)));
  }
}

async function loadUnmatched() {
  const r = await fetch(API + "/api/imports/unmatched/");
  const records = await r.json();
  document.getElementById("unmatched-list").innerHTML = records.length
    ? `<table><tr><th>Source</th><th>Address</th><th>Status</th></tr>` +
      records.map(u => `<tr>
        <td>${u.source_name}</td>
        <td>${u.raw_address || "—"}</td>
        <td><span class="badge badge-${u.status}">${u.status}</span></td>
      </tr>`).join("") + `</table>`
    : "<p>No unmatched records.</p>";
}

// --- Houses ---
async function loadHouses() {
  const search = document.getElementById("house-search")?.value || "";
  const zip = document.getElementById("house-zip")?.value || "";
  const params = new URLSearchParams();
  if (search) params.set("search", search);
  if (zip) params.set("zip_code", zip);
  const r = await fetch(API + "/api/houses/?" + params);
  const houses = await r.json();
  document.getElementById("houses-list").innerHTML = houses.length
    ? `<table><tr><th>Address</th><th>City</th><th>ZIP</th><th>Owner</th><th>Source</th></tr>` +
      houses.map(h => `<tr>
        <td>${h.full_address}</td>
        <td>${h.city || ""}</td>
        <td>${h.zip_code || ""}</td>
        <td>${h.owner_name || "—"}</td>
        <td>${h.manually_created ? "Manual" : "Imported"}</td>
      </tr>`).join("") + `</table>`
    : "<p>No houses found.</p>";
}

async function exportHousesCSV() {
  const search = document.getElementById("house-search")?.value || "";
  const zip = document.getElementById("house-zip")?.value || "";
  const params = new URLSearchParams();
  if (search) params.set("search", search);
  if (zip) params.set("zip_code", zip);
  const r = await fetch(API + "/api/houses/?" + params);
  const houses = await r.json();
  if (!houses.length) { alert("No houses to export."); return; }
  exportCSV("houses.csv",
    ["Address", "City", "ZIP", "Owner", "Appraised Value", "Latitude", "Longitude", "Source"],
    houses.map(h => [
      h.full_address, h.city || "", h.zip_code || "", h.owner_name || "",
      h.total_appraised_value || "", h.latitude || "", h.longitude || "",
      h.manually_created ? "Manual" : "Imported",
    ])
  );
}

document.getElementById("manual-house-form").onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = {
    full_address: fd.get("full_address"),
    zip_code: fd.get("zip_code") || undefined,
    latitude: fd.get("latitude") ? parseFloat(fd.get("latitude")) : undefined,
    longitude: fd.get("longitude") ? parseFloat(fd.get("longitude")) : undefined,
  };
  const r = await fetch(API + "/api/houses/", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (r.ok) { e.target.reset(); loadHouses(); }
  else { const d = await r.json(); alert(d.detail || "Error"); }
};

// --- Roster ---
async function loadRoster() {
  const r = await fetch(API + "/api/scout/roster");
  const roster = await r.json();
  document.getElementById("roster-list").innerHTML = roster.length
    ? `<table><tr><th>Name</th><th>Scout ID</th><th>Status</th><th></th></tr>` +
      roster.map(s => `<tr>
        <td>${s.name}</td>
        <td>${s.scout_id || "—"}</td>
        <td><span class="badge badge-${s.active ? "completed" : "pending"}">${s.active ? "Active" : "Inactive"}</span></td>
        <td>
          <button class="btn-sm" onclick="toggleRosterScout('${s.id}')">${s.active ? "Deactivate" : "Activate"}</button>
          <button class="btn-sm btn-danger" onclick="deleteRosterScout('${s.id}')">Delete</button>
        </td>
      </tr>`).join("") + `</table>`
    : "<p>No scouts in roster. Add scouts above.</p>";
}
document.getElementById("roster-form").onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  await fetch(API + "/api/scout/roster", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: fd.get("name"), scout_id: fd.get("scout_id") || null }),
  });
  e.target.reset();
  loadRoster();
};
async function toggleRosterScout(id) {
  await fetch(API + "/api/scout/roster/" + id, { method: "PATCH" });
  loadRoster();
}
async function deleteRosterScout(id) {
  if (!confirm("Remove this scout from the roster?")) return;
  await fetch(API + "/api/scout/roster/" + id, { method: "DELETE" });
  loadRoster();
}

async function exportRosterCSV() {
  const r = await fetch(API + "/api/scout/roster");
  const roster = await r.json();
  if (!roster.length) { alert("No scouts to export."); return; }
  exportCSV("scout-roster.csv",
    ["name", "scout_id"],
    roster.map(s => [s.name, s.scout_id || ""])
  );
}

document.getElementById("roster-import-form").onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const statusEl = document.getElementById("roster-import-status");
  statusEl.classList.remove("hidden");
  statusEl.textContent = "Importing…";
  try {
    const r = await fetch(API + "/api/scout/roster/import", { method: "POST", body: fd });
    const data = await r.json();
    if (r.ok) {
      statusEl.textContent = `Done! ${data.added} scout(s) added, ${data.skipped} skipped (duplicates or empty).`;
      e.target.reset();
      loadRoster();
    } else {
      statusEl.textContent = "Error: " + (data.detail || JSON.stringify(data));
    }
  } catch (err) {
    statusEl.textContent = "Network error: " + err.message;
  }
};

// --- Scout Data ---
async function loadScoutDataEvents() {
  const sel = document.getElementById("sd-event-filter");
  const r = await fetch(API + "/api/events/");
  const events = await r.json();
  sel.innerHTML = '<option value="">All Events</option>' +
    events.map(e => `<option value="${e.id}">${e.name}</option>`).join("");
}

let _scoutDataCache = [];
async function loadScoutData() {
  const eventId = document.getElementById("sd-event-filter").value;
  const params = eventId ? "?event_id=" + eventId : "";

  const [dataR, summaryR] = await Promise.all([
    fetch(API + "/api/scout/data" + params),
    fetch(API + "/api/scout/data/summary" + params),
  ]);
  const data = await dataR.json();
  const summary = await summaryR.json();
  _scoutDataCache = data;

  // Summary table
  const sumEl = document.getElementById("scout-summary");
  if (summary.scouts.length) {
    sumEl.innerHTML =
      `<p style="margin-bottom:8px"><strong>${summary.total_visits}</strong> total visits &middot; <strong>$${summary.total_donations.toLocaleString()}</strong> donated</p>` +
      `<table><tr><th>Scout</th><th>ID</th><th>Visits</th><th>Doors</th><th>Donations</th><th>$ Total</th><th>Former</th><th>Avoid</th></tr>` +
      summary.scouts.map(s => `<tr>
        <td>${s.scout_name}</td>
        <td>${s.scout_id || "—"}</td>
        <td>${s.total_visits}</td>
        <td>${s.doors_answered}</td>
        <td>${s.donations}</td>
        <td>$${s.donation_total.toLocaleString()}</td>
        <td>${s.former_scouts}</td>
        <td>${s.avoid_houses}</td>
      </tr>`).join("") + `</table>`;
  } else {
    sumEl.innerHTML = "<p>No scout data yet.</p>";
  }

  // Detail table
  const listEl = document.getElementById("scout-data-list");
  if (data.length) {
    listEl.innerHTML = `<table><tr><th>Time</th><th>Scout</th><th>Address</th><th>Group</th><th>Door</th><th>Donation</th><th>Amount</th><th>Former</th><th>Avoid</th><th>Notes</th></tr>` +
      data.map(v => `<tr>
        <td>${v.visited_at ? new Date(v.visited_at).toLocaleString() : "—"}</td>
        <td>${v.scout_name}</td>
        <td>${v.address}</td>
        <td>${v.group_label || "—"}</td>
        <td>${v.door_answer == null ? "—" : v.door_answer ? "Yes" : "No"}</td>
        <td>${v.donation_given == null ? "—" : v.donation_given ? "Yes" : "No"}</td>
        <td>${v.donation_amount ? "$" + v.donation_amount : "—"}</td>
        <td>${v.former_scout == null ? "—" : v.former_scout ? "Yes" : "No"}</td>
        <td>${v.avoid_house ? "YES" : "—"}</td>
        <td>${v.notes || ""}</td>
      </tr>`).join("") + `</table>`;
  } else {
    listEl.innerHTML = "<p>No visit data yet. Scouts record data at <a href='/scout' target='_blank'>/scout</a>.</p>";
  }
}

function exportScoutDataCSV() {
  if (!_scoutDataCache.length) { alert("No data to export."); return; }
  exportCSV("scout-data.csv",
    ["Time", "Scout", "Scout ID", "Event", "Group", "Address", "ZIP", "Door Answer", "Donation", "Amount", "Former Scout", "Avoid House", "Notes"],
    _scoutDataCache.map(v => [
      v.visited_at || "", v.scout_name || "", v.scout_id || "", v.event_name || "",
      v.group_label || "", v.address || "", v.zip_code || "",
      v.door_answer == null ? "" : v.door_answer ? "Yes" : "No",
      v.donation_given == null ? "" : v.donation_given ? "Yes" : "No",
      v.donation_amount || "", v.former_scout == null ? "" : v.former_scout ? "Yes" : "No",
      v.avoid_house ? "Yes" : "No", v.notes || "",
    ])
  );
}

// --- Init ---
loadDashboard();
