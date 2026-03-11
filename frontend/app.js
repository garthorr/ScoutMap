/* global L */
const API = "";
let map, markersLayer, currentEventId, currentEventName;

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
  const r = await fetch(API + `/api/events/${currentEventId}/houses`);
  const houses = await r.json();
  const el = document.getElementById("wg-groups-list");
  if (!houses.length) { el.innerHTML = "<p>No groups yet. Generate walk groups above.</p>"; return; }

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

// --- Init ---
loadDashboard();
