/* global L */
const API = "";
let map, currentEventId, currentEventName;

/** Escape HTML entities to prevent XSS when inserting user data into innerHTML. */
function esc(s) {
  if (s == null) return "";
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// --- Auth ---
let _authToken = localStorage.getItem("scoutmap_token") || "";

function _authHeaders() {
  const h = { "Content-Type": "application/json" };
  if (_authToken) h["Authorization"] = "Bearer " + _authToken;
  return h;
}

/** Authenticated fetch wrapper — injects Bearer token. */
function authFetch(url, opts = {}) {
  opts.headers = opts.headers || {};
  if (_authToken) opts.headers["Authorization"] = "Bearer " + _authToken;
  return fetch(url, opts);
}

// --- Loading indicators ---
function _showMapLoading(msg) {
  const el = document.getElementById("map-loading");
  const txt = document.getElementById("map-loading-text");
  if (el) { txt.textContent = msg || "Loading houses…"; el.classList.add("visible"); }
}
function _hideMapLoading() {
  const el = document.getElementById("map-loading");
  if (el) el.classList.remove("visible");
}
let _opStatusTimer = null;
function _showStatus(msg) {
  const el = document.getElementById("operation-status");
  const txt = document.getElementById("operation-status-text");
  if (!el) return;
  clearTimeout(_opStatusTimer);
  txt.textContent = msg;
  el.classList.add("visible");
}
function _hideStatus() {
  const el = document.getElementById("operation-status");
  if (el) el.classList.remove("visible");
  clearTimeout(_opStatusTimer);
}
function _flashStatus(msg, duration) {
  _showStatus(msg);
  _opStatusTimer = setTimeout(_hideStatus, duration || 2500);
}

async function _checkAuth() {
  if (!_authToken) { _showLogin(); return; }
  try {
    const r = await authFetch(API + "/api/auth/me");
    if (r.ok) {
      const data = await r.json();
      _hideLogin();
      document.getElementById("settings-user-email").textContent = data.email;
    } else {
      _authToken = "";
      localStorage.removeItem("scoutmap_token");
      _showLogin();
    }
  } catch {
    _showLogin();
  }
}

function _showLogin() {
  document.getElementById("login-overlay").classList.remove("hidden");
}
function _hideLogin() {
  document.getElementById("login-overlay").classList.add("hidden");
}

// --- Admin password login ---
async function loginAdminPassword() {
  const pw = document.getElementById("login-admin-pw").value;
  const errEl = document.getElementById("login-admin-error");
  errEl.textContent = "";
  if (!pw) { errEl.textContent = "Enter the admin password."; return; }

  const btn = document.getElementById("login-admin-btn");
  btn.disabled = true; btn.textContent = "Signing in…";
  try {
    const r = await fetch(API + "/api/auth/admin-login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    const data = await r.json();
    if (r.ok && data.token) {
      _authToken = data.token;
      localStorage.setItem("scoutmap_token", _authToken);
      _hideLogin();
      document.getElementById("settings-user-email").textContent = data.email;
      loadDashboard();
    } else {
      errEl.textContent = data.detail || "Incorrect password.";
    }
  } catch (err) {
    errEl.textContent = "Network error: " + err.message;
  }
  btn.disabled = false; btn.textContent = "Sign In";
}

function showEmailLogin() {
  document.getElementById("login-step-admin").style.display = "none";
  document.getElementById("login-step-email").style.display = "";
  document.getElementById("login-step-code").style.display = "none";
}
function showAdminPasswordLogin() {
  document.getElementById("login-step-admin").style.display = "";
  document.getElementById("login-step-email").style.display = "none";
  document.getElementById("login-step-code").style.display = "none";
}

async function loginRequestCode() {
  const email = document.getElementById("login-email").value.trim();
  const errEl = document.getElementById("login-email-error");
  errEl.textContent = "";
  if (!email || !email.includes("@")) { errEl.textContent = "Enter a valid email."; return; }

  const btn = document.getElementById("login-send-btn");
  btn.disabled = true;
  btn.textContent = "Sending…";

  try {
    const r = await fetch(API + "/api/auth/request-code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    const data = await r.json();
    if (r.ok) {
      document.getElementById("login-code-email").textContent = email;
      document.getElementById("login-step-email").style.display = "none";
      document.getElementById("login-step-code").style.display = "";
      document.getElementById("login-code").value = "";
      document.getElementById("login-code").focus();
    } else {
      errEl.textContent = data.detail || "Error sending code.";
    }
  } catch (err) {
    errEl.textContent = "Network error: " + err.message;
  }
  btn.disabled = false;
  btn.textContent = "Send Login Code";
}

async function loginVerifyCode() {
  const email = document.getElementById("login-code-email").textContent;
  const code = document.getElementById("login-code").value.trim();
  const errEl = document.getElementById("login-code-error");
  errEl.textContent = "";
  if (!code || code.length < 6) { errEl.textContent = "Enter the 6-digit code."; return; }

  const btn = document.getElementById("login-verify-btn");
  btn.disabled = true;
  btn.textContent = "Verifying…";

  try {
    const r = await fetch(API + "/api/auth/verify-code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, code }),
    });
    const data = await r.json();
    if (r.ok && data.token) {
      _authToken = data.token;
      localStorage.setItem("scoutmap_token", _authToken);
      _hideLogin();
      document.getElementById("settings-user-email").textContent = data.email;
      loadDashboard();
    } else {
      errEl.textContent = data.detail || "Invalid or expired code.";
    }
  } catch (err) {
    errEl.textContent = "Network error: " + err.message;
  }
  btn.disabled = false;
  btn.textContent = "Verify Code";
}

function loginBackToEmail() {
  document.getElementById("login-step-email").style.display = "";
  document.getElementById("login-step-code").style.display = "none";
  document.getElementById("login-code-error").textContent = "";
}

async function appLogout() {
  try { await authFetch(API + "/api/auth/logout", { method: "POST" }); } catch { /* ok */ }
  _authToken = "";
  localStorage.removeItem("scoutmap_token");
  _showLogin();
  document.getElementById("login-step-admin").style.display = "";
  document.getElementById("login-step-email").style.display = "none";
  document.getElementById("login-step-code").style.display = "none";
  document.getElementById("login-admin-pw").value = "";
  document.getElementById("login-admin-error").textContent = "";
  document.getElementById("login-email").value = "";
  document.getElementById("login-email-error").textContent = "";
}

// Allow Enter key on login inputs
document.getElementById("login-admin-pw").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); loginAdminPassword(); }
});
document.getElementById("login-email").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); loginRequestCode(); }
});
document.getElementById("login-code").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); loginVerifyCode(); }
});

// --- Settings: Allowed Emails ---
async function loadAllowedEmails() {
  const r = await authFetch(API + "/api/auth/allowed-emails");
  const emails = await r.json();
  const el = document.getElementById("allowed-emails-list");
  if (!emails.length) { el.innerHTML = "<p>No allowed emails configured.</p>"; return; }
  el.innerHTML = `<table><tr><th>Email / Pattern</th><th>Added</th><th></th></tr>` +
    emails.map(e => `<tr>
      <td>${esc(e.email)}</td>
      <td>${new Date(e.created_at).toLocaleDateString()}</td>
      <td><button class="btn-sm btn-danger" onclick="removeAllowedEmail('${esc(e.id)}')">Remove</button></td>
    </tr>`).join("") + `</table>`;
}

async function addAllowedEmail(evt) {
  evt.preventDefault();
  const input = document.querySelector('#allowed-email-form [name="email"]');
  const email = input.value.trim();
  if (!email) return;
  const r = await authFetch(API + "/api/auth/allowed-emails", {
    method: "POST",
    headers: _authHeaders(),
    body: JSON.stringify({ email }),
  });
  if (r.ok) {
    input.value = "";
    loadAllowedEmails();
  } else {
    const data = await r.json();
    alert(data.detail || "Error adding email");
  }
}

async function removeAllowedEmail(id) {
  if (!confirm("Remove this email from the allowlist?")) return;
  await authFetch(API + "/api/auth/allowed-emails/" + id, { method: "DELETE" });
  loadAllowedEmails();
}

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
  if (name === "imports") { loadImports(); loadUnmatched(); loadImportEventSelect(); loadArcGISEventSelect(); }
  if (name === "houses") loadHouses();
  if (name === "walk-groups") loadWalkGroupEvents();
  if (name === "roster") loadRoster();
  if (name === "scout-data") { loadScoutDataEvents(); loadScoutData(); }
  if (name === "scout-form") loadFormFields();
  if (name === "settings") loadAllowedEmails();
}

// --- Dashboard ---
function _phaseStat(label, value, page) {
  return `<div class="phase-stat" onclick="showPage('${page}')">
    <span class="phase-stat-label">${label}</span>
    <span class="phase-stat-value">${value}</span>
  </div>`;
}

async function loadDashboard() {
  const r = await authFetch(API + "/api/stats/");
  const s = await r.json();

  // Phase 1: Prepare Data
  document.getElementById("phase-prepare-stats").innerHTML =
    _phaseStat("Events", s.total_events, "events") +
    _phaseStat("Imports", s.import_count, "imports") +
    _phaseStat("Unmatched", s.unmatched_count, "imports") +
    _phaseStat("Scouts", s.total_scouts ?? 0, "roster");

  // Phase 2: Organize
  document.getElementById("phase-organize-stats").innerHTML =
    _phaseStat("Houses", s.total_houses, "houses") +
    _phaseStat("Assigned", s.assigned_houses ?? 0, "events") +
    _phaseStat("Visited", s.houses_visited ?? 0, "scout-data");

  // Phase 3: Collect
  document.getElementById("phase-collect-stats").innerHTML =
    _phaseStat("Visits", s.total_visits, "scout-data") +
    _phaseStat("Donations", "$" + (s.total_donations || 0).toLocaleString(), "scout-data");
}

// --- Map ---
let houseDotLayer, walkRouteLayer, streetHighlightLayer, layerControl;
let _mapStreets = [];      // street data from /api/houses/streets
let _selectedStreets = new Set();

const GROUP_COLORS = [
  "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
  "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990",
  "#dcbeff", "#9A6324", "#800000", "#aaffc3", "#808000",
  "#000075", "#a9a9a9",
];

function initMap() {
  if (map) { map.invalidateSize(); refreshMapDots(); return; }
  setTimeout(() => {
    map = L.map("map").setView([32.78, -96.80], 12);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map);

    houseDotLayer = L.layerGroup().addTo(map);
    walkRouteLayer = L.layerGroup().addTo(map);
    streetHighlightLayer = L.layerGroup().addTo(map);

    layerControl = L.control.layers(null, {
      "House Pins": houseDotLayer,
      "Walk Group Routes": walkRouteLayer,
      "Selected Streets": streetHighlightLayer,
    }).addTo(map);

    let _mapMoveTimer;
    map.on("moveend", () => { clearTimeout(_mapMoveTimer); _mapMoveTimer = setTimeout(refreshMapDots, 300); });
    map.on("click", _handleMapToolClick);
    map.on("dblclick", (e) => {
      if (_mapTool === "boundary" && !_boundaryClosed && _boundaryPoints.length >= 3) {
        L.DomEvent.stopPropagation(e);
        closeBoundary();
      }
    });
    refreshMapDots();
    _loadMapEventSelect();
    _loadMapZipSelect();
  }, 100);
}

let _refreshAbort = null; // AbortController for in-flight map refresh
async function refreshMapDots() {
  if (!map) return;

  // Cancel any in-flight refresh to avoid stale responses
  if (_refreshAbort) _refreshAbort.abort();
  _refreshAbort = new AbortController();
  const signal = _refreshAbort.signal;

  const b = map.getBounds();
  const zoom = map.getZoom();

  // Determine detail level and limit based on zoom
  // zoom < 13: lightweight dots only (fast), zoom >= 15: full detail with popups
  const useFullDetail = zoom >= 15;
  const limit = zoom >= 17 ? 5000 : zoom >= 15 ? 3000 : zoom >= 13 ? 2000 : 1000;
  const dotRadius = zoom >= 16 ? 5 : zoom >= 14 ? 4 : 3;

  const params = new URLSearchParams({
    min_lat: b.getSouth(), max_lat: b.getNorth(),
    min_lon: b.getWest(), max_lon: b.getEast(),
    limit,
  });

  _showMapLoading(useFullDetail ? "Loading house details…" : "Loading map…");
  houseDotLayer.clearLayers();

  try {
  if (useFullDetail) {
    // Full data with popups for close zoom
    const r = await authFetch(API + "/api/houses/map?" + params, { signal });
    if (!r.ok) throw new Error("Map data request failed");
    const houses = await r.json();
    if (signal.aborted) return;
    houses.forEach(h => {
      if (!h.latitude || !h.longitude) return;
      const dot = L.circleMarker([h.latitude, h.longitude], {
        radius: dotRadius, fillColor: "#003F87", color: "#003F87",
        weight: 1, opacity: 0.8, fillOpacity: 0.6,
      });
      dot._houseId = h.id;
      dot._address = h.full_address;
      dot.bindPopup(`<b>${esc(h.full_address)}</b><br>${esc(h.owner_name || "")}<br>` +
        (h.total_appraised_value ? `Appraised: $${h.total_appraised_value.toLocaleString()}` : ""));
      houseDotLayer.addLayer(dot);
    });
  } else {
    // Lightweight dots — just coordinates, no popups (much faster)
    const r = await authFetch(API + "/api/houses/map/dots?" + params, { signal });
    if (!r.ok) throw new Error("Map dots request failed");
    const dots = await r.json();
    if (signal.aborted) return;
    dots.forEach(d => {
      const dot = L.circleMarker([d.lat, d.lon], {
        radius: dotRadius, fillColor: "#003F87", color: "#003F87",
        weight: 0.5, opacity: 0.6, fillOpacity: 0.4,
      });
      dot._houseId = d.id;
      houseDotLayer.addLayer(dot);
    });
  }
  } catch (err) {
    if (err.name !== "AbortError") console.warn("refreshMapDots error:", err);
  } finally {
    _hideMapLoading();
  }
}

async function _loadMapEventSelect() {
  const sel = document.getElementById("map-event-select");
  try {
    const r = await authFetch(API + "/api/events/");
    const events = await r.json();
    sel.innerHTML = '<option value="">Walk Groups: none</option>' +
      events.map(e => `<option value="${esc(e.id)}">${esc(e.name)} (${e.house_count})</option>`).join("");
  } catch { /* ignore */ }
}

document.getElementById("map-event-select").onchange = function () {
  loadWalkRoutes(this.value);
};

async function loadWalkRoutes(eventId) {
  walkRouteLayer.clearLayers();
  if (!eventId) {
    document.getElementById("map-group-panel").style.display = "none";
    return;
  }
  const r = await authFetch(API + `/api/events/${eventId}/houses`);
  const houses = await r.json();
  if (!houses.length) {
    document.getElementById("map-group-panel").style.display = "none";
    return;
  }

  // Group by assigned_to
  const groups = {};
  houses.forEach(eh => {
    const key = eh.assigned_to || "Unassigned";
    if (!groups[key]) groups[key] = [];
    groups[key].push(eh);
  });

  let colorIdx = 0;
  for (const [label, items] of Object.entries(groups)) {
    const color = GROUP_COLORS[colorIdx % GROUP_COLORS.length];
    colorIdx++;

    const withCoords = items.filter(eh => eh.house?.latitude && eh.house?.longitude);
    if (!withCoords.length) continue;

    // Sub-group by street_name within this walk group
    const byStreet = {};
    withCoords.forEach(eh => {
      const street = (eh.house.street_name || "UNKNOWN").toUpperCase().trim();
      if (!byStreet[street]) byStreet[street] = [];
      byStreet[street].push(eh);
    });

    // Draw one trace per street — a midline down the street
    for (const [street, streetHouses] of Object.entries(byStreet)) {
      streetHouses.sort((a, b) => {
        const an = parseInt(a.house.address_number) || 0;
        const bn = parseInt(b.house.address_number) || 0;
        return an - bn;
      });

      if (streetHouses.length >= 2) {
        // Compute midline: average lat/lon of adjacent pairs sorted by address
        // Group into even/odd sides, then average to get center line
        const midCoords = _computeMidline(streetHouses);
        const line = L.polyline(midCoords, {
          color, weight: 5, opacity: 0.7, lineCap: "round", lineJoin: "round",
        });
        line.bindPopup(`<b>${esc(label)}</b><br>${esc(street)}<br>${streetHouses.length} houses`);
        walkRouteLayer.addLayer(line);
      }
    }

    // Draw dots at each house
    withCoords.forEach((eh, i) => {
      const dot = L.circleMarker([eh.house.latitude, eh.house.longitude], {
        radius: 5, fillColor: color, color: "#fff",
        weight: 2, fillOpacity: 1,
      });
      dot.bindPopup(`<b>${esc(eh.house.full_address)}</b><br>Group: ${esc(label)}<br>Status: ${esc(eh.status)}`);
      walkRouteLayer.addLayer(dot);
    });
  }

  // Fit map to routes
  const allCoords = houses
    .filter(eh => eh.house?.latitude && eh.house?.longitude)
    .map(eh => [eh.house.latitude, eh.house.longitude]);
  if (allCoords.length) map.fitBounds(L.latLngBounds(allCoords).pad(0.1));

  // Render group manipulation panel
  _renderGroupPanel(eventId, groups);
}

function _renderGroupPanel(eventId, groups) {
  const panel = document.getElementById("map-group-panel");
  const listEl = document.getElementById("map-group-list");
  const nameEl = document.getElementById("map-group-event-name");

  const labels = Object.keys(groups);
  if (!labels.length) {
    panel.style.display = "none";
    return;
  }

  panel.style.display = "";
  const sel = document.getElementById("map-event-select");
  nameEl.textContent = "— " + (sel.options[sel.selectedIndex]?.text || "");

  let colorIdx = 0;
  listEl.innerHTML = labels.map(label => {
    const color = GROUP_COLORS[colorIdx % GROUP_COLORS.length];
    colorIdx++;
    const count = groups[label].length;
    return `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;padding:4px 0;border-bottom:1px solid #eee;">
      <input type="checkbox" class="group-merge-cb" value="${esc(label)}" title="Select for merge" />
      <span style="display:inline-block;width:14px;height:14px;border-radius:3px;background:${color};flex-shrink:0;"></span>
      <strong style="min-width:80px;">${esc(label)}</strong>
      <span style="color:var(--sa-gray);font-size:12px;">${count} houses</span>
      <button class="btn-sm" onclick="renameGroup('${esc(eventId)}','${esc(label)}')" style="margin-left:auto;">Rename</button>
      <button class="btn-sm btn-danger" onclick="deleteGroup('${esc(eventId)}','${esc(label)}')">Delete</button>
    </div>`;
  }).join("") +
    `<div style="margin-top:8px;">
      <button class="btn-sm" onclick="mergeSelectedGroups('${esc(eventId)}')">Merge Selected</button>
    </div>`;
}

async function renameGroup(eventId, oldLabel) {
  const newLabel = prompt(`Rename group "${oldLabel}" to:`, oldLabel);
  if (!newLabel || newLabel === oldLabel) return;
  try {
    const r = await authFetch(API + `/api/events/${eventId}/groups/reassign`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old_label: oldLabel, new_label: newLabel.trim() }),
    });
    if (r.ok) {
      loadWalkRoutes(eventId);
      // Refresh walk group list if on that page
      if (document.getElementById("page-walk-groups").classList.contains("active")) loadWalkGroupList();
    } else {
      const data = await r.json().catch(() => ({}));
      alert(data.detail || "Failed to rename group.");
    }
  } catch (err) {
    alert("Error: " + err.message);
  }
}

async function deleteGroup(eventId, label) {
  if (!confirm(`Delete group "${label}"? All houses in this group will be unassigned from the event.`)) return;
  try {
    const r = await authFetch(API + `/api/events/${eventId}/groups?label=${encodeURIComponent(label)}`, {
      method: "DELETE",
    });
    if (r.ok) {
      loadWalkRoutes(eventId);
      if (document.getElementById("page-walk-groups").classList.contains("active")) loadWalkGroupList();
    } else {
      const data = await r.json().catch(() => ({}));
      alert(data.detail || "Failed to delete group.");
    }
  } catch (err) {
    alert("Error: " + err.message);
  }
}

async function mergeSelectedGroups(eventId) {
  const checkboxes = document.querySelectorAll(".group-merge-cb:checked");
  if (checkboxes.length < 2) { alert("Select at least 2 groups to merge."); return; }
  const labels = [...checkboxes].map(cb => cb.value);
  const targetLabel = prompt(`Merge ${labels.length} groups into one. Enter the target group name:`, labels[0]);
  if (!targetLabel) return;

  const sourceLabels = labels.filter(l => l !== targetLabel.trim());
  if (!sourceLabels.length) {
    // All selected groups have the same name as target — nothing to merge
    alert("All selected groups already have that name.");
    return;
  }

  try {
    const r = await authFetch(API + `/api/events/${eventId}/groups/merge`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_labels: sourceLabels, target_label: targetLabel.trim() }),
    });
    if (r.ok) {
      const data = await r.json();
      alert(`Merged ${data.updated || sourceLabels.length} group(s) into "${targetLabel.trim()}".`);
      loadWalkRoutes(eventId);
      if (document.getElementById("page-walk-groups").classList.contains("active")) loadWalkGroupList();
    } else {
      const data = await r.json().catch(() => ({}));
      alert(data.detail || "Failed to merge groups.");
    }
  } catch (err) {
    alert("Error: " + err.message);
  }
}

/**
 * Compute a midline trace for a sorted list of house points.
 * Each item needs {lat, lon, address_number|num} (supports both formats).
 * Groups by even/odd address numbers (opposite sides of street),
 * then averages positions pairwise to trace down the center.
 */
function _computeMidline(sortedHouses) {
  const coords = sortedHouses.map(h => ({
    lat: h.lat ?? h.house?.latitude,
    lon: h.lon ?? h.house?.longitude,
    num: parseInt(h.address_number ?? h.house?.address_number) || 0,
  }));

  const even = coords.filter(c => c.num % 2 === 0);
  const odd = coords.filter(c => c.num % 2 === 1);

  if (even.length >= 2 && odd.length >= 2) {
    const midpoints = [];
    const steps = Math.max(Math.max(even.length, odd.length), 3);
    for (let i = 0; i < steps; i++) {
      const t = i / (steps - 1);
      const ei = Math.min(Math.floor(t * (even.length - 1) + 0.5), even.length - 1);
      const oi = Math.min(Math.floor(t * (odd.length - 1) + 0.5), odd.length - 1);
      midpoints.push([
        (even[ei].lat + odd[oi].lat) / 2,
        (even[ei].lon + odd[oi].lon) / 2,
      ]);
    }
    return midpoints;
  }

  if (coords.length <= 2) return coords.map(c => [c.lat, c.lon]);
  const mid = [];
  for (let i = 0; i < coords.length - 1; i++) {
    mid.push([
      (coords[i].lat + coords[i + 1].lat) / 2,
      (coords[i].lon + coords[i + 1].lon) / 2,
    ]);
  }
  return mid;
}

// --- ZIP code multi-select for map ---
async function _loadMapZipSelect() {
  const sel = document.getElementById("map-zip-select");
  if (!sel) return;
  try {
    const r = await authFetch(API + "/api/houses/zip-codes");
    if (!r.ok) return;
    const zips = await r.json();
    sel.innerHTML = zips.map(z =>
      `<option value="${esc(z.zip_code)}">${esc(z.zip_code)} (${z.count} houses)</option>`
    ).join("");
  } catch { sel.innerHTML = '<option value="">Error loading ZIPs</option>'; }
}

// --- Street selection ---
async function loadMapStreets() {
  const sel = document.getElementById("map-zip-select");
  const selectedZips = Array.from(sel.selectedOptions).map(o => o.value).filter(Boolean);
  if (!selectedZips.length) { alert("Select at least one ZIP code."); return; }

  document.getElementById("map-street-count").textContent = "Loading…";
  try {
    // Fetch streets for all selected ZIPs and merge
    const allStreets = [];
    for (const zip of selectedZips) {
      const r = await authFetch(API + `/api/houses/streets?zip_code=${zip}`);
      if (!r.ok) throw new Error("HTTP " + r.status);
      const streets = await r.json();
      allStreets.push(...streets);
    }
    // Merge streets with same name across ZIPs
    const merged = {};
    for (const s of allStreets) {
      if (merged[s.street]) {
        merged[s.street].count += s.count;
        merged[s.street].houses.push(...s.houses);
      } else {
        merged[s.street] = { ...s, houses: [...s.houses] };
      }
    }
    _mapStreets = Object.values(merged).sort((a, b) => a.street.localeCompare(b.street));
  } catch (err) {
    document.getElementById("map-street-count").textContent = "Error: " + err.message;
    return;
  }

  _selectedStreets.clear();
  document.getElementById("map-street-count").textContent =
    `${_mapStreets.length} streets, ${_mapStreets.reduce((s, st) => s + st.count, 0)} houses`;
  document.getElementById("map-street-list").style.display = "";
  document.getElementById("map-street-search").value = "";
  renderStreetCheckboxes();

  // Zoom to houses
  const allPts = _mapStreets.flatMap(s => s.houses.map(h => [h.lat, h.lon]));
  if (allPts.length && map) map.fitBounds(L.latLngBounds(allPts).pad(0.1));
}

function renderStreetCheckboxes() {
  const filter = (document.getElementById("map-street-search").value || "").toUpperCase();
  const el = document.getElementById("map-streets");
  const visible = filter
    ? _mapStreets.filter(s => s.street.includes(filter))
    : _mapStreets;

  document.getElementById("map-street-filter-count").textContent =
    filter ? `(${visible.length} of ${_mapStreets.length})` : `(${_mapStreets.length})`;

  el.innerHTML = visible.map(s => {
    const checked = _selectedStreets.has(s.street) ? " checked" : "";
    return `<label style="display:block;margin-bottom:4px;font-size:13px;cursor:pointer;break-inside:avoid;">` +
      `<input type="checkbox" value="${esc(s.street)}" onchange="toggleStreet(this)"${checked} /> ` +
      `${esc(s.street)} <span style="color:var(--sa-pale-gray);">(${s.count})</span></label>`;
  }).join("");
  _updateSelectionSummary();
}

function toggleStreet(cb) {
  if (cb.checked) _selectedStreets.add(cb.value);
  else _selectedStreets.delete(cb.value);
  _updateSelectionSummary();
  _highlightSelectedStreets();
}

function selectAllVisibleStreets() {
  document.querySelectorAll("#map-streets input[type=checkbox]").forEach(cb => {
    cb.checked = true;
    _selectedStreets.add(cb.value);
  });
  _updateSelectionSummary();
  _highlightSelectedStreets();
}

function clearStreetSelection() {
  _selectedStreets.clear();
  document.querySelectorAll("#map-streets input[type=checkbox]").forEach(cb => cb.checked = false);
  streetHighlightLayer.clearLayers();
  _updateSelectionSummary();
  const selEl = document.getElementById("map-selected-streets");
  selEl.style.display = "none";
}

function _updateSelectionSummary() {
  const count = _selectedStreets.size;
  const houses = _mapStreets
    .filter(s => _selectedStreets.has(s.street))
    .reduce((sum, s) => sum + s.count, 0);
  document.getElementById("map-selection-summary").textContent =
    count ? `${count} street(s) selected, ${houses} houses` : "No streets selected";

  const selEl = document.getElementById("map-selected-streets");
  if (count) {
    selEl.style.display = "";
    selEl.innerHTML = "<strong>Selected:</strong> " + [..._selectedStreets].sort().join(", ");
  } else {
    selEl.style.display = "none";
  }
}

function _highlightSelectedStreets() {
  streetHighlightLayer.clearLayers();
  _mapStreets.forEach(s => {
    if (!_selectedStreets.has(s.street)) return;
    if (s.houses.length < 1) return;

    // Sort by address number
    const sorted = [...s.houses].sort((a, b) => {
      const an = parseInt(a.address_number) || 0;
      const bn = parseInt(b.address_number) || 0;
      return an - bn;
    });

    // Draw midline street trace
    if (sorted.length >= 2) {
      const midCoords = _computeMidline(sorted);
      const line = L.polyline(midCoords, {
        color: "#CE1126", weight: 5, opacity: 0.8,
        lineCap: "round", lineJoin: "round",
      });
      line.bindPopup(`<b>${esc(s.street)}</b><br>${s.count} houses`);
      streetHighlightLayer.addLayer(line);
    }

    // Dots at each house
    s.houses.forEach(h => {
      const dot = L.circleMarker([h.lat, h.lon], {
        radius: 4, fillColor: "#CE1126", color: "#fff",
        weight: 1.5, fillOpacity: 1,
      });
      dot.bindPopup(esc(h.address));
      streetHighlightLayer.addLayer(dot);
    });
  });
}

function copySelectedStreets() {
  if (!_selectedStreets.size) { alert("No streets selected."); return; }
  const streetStr = [..._selectedStreets].sort().join(", ");
  // Fill into the manual assign form street_names field
  const assignStreetInput = document.querySelector('#assign-form [name="street_names"]');
  const assignZipInput = document.querySelector('#assign-form [name="zip_codes"]');
  if (assignStreetInput) assignStreetInput.value = streetStr;
  const selZips = Array.from(document.getElementById("map-zip-select").selectedOptions).map(o => o.value).filter(Boolean);
  if (assignZipInput && selZips.length) {
    assignZipInput.value = selZips.join(", ");
  }
  showPage("events");
  alert(`${_selectedStreets.size} street(s) copied to the assign form. Open an event to assign them.`);
}

// --- Map tools: erase, add, box-select ---
let _mapTool = "pointer";       // "pointer" | "add" | "select" | "boundary"
let _addMarker = null;          // temp marker for add mode

// Box-select state
let _boxSelectRect = null;      // L.rectangle drawn during drag
let _boxSelectStart = null;     // {lat, lng} of mousedown
let _boxSelectedHouses = [];    // array of {id, lat, lng, address, marker}
let _boxHighlights = [];        // L.circleMarker highlights for selected houses

// Boundary tool state
let _boundaryPoints = [];       // [[lat, lng], ...]
let _boundaryMarkers = [];      // L.circleMarker for each vertex
let _boundaryLines = null;      // L.polyline showing edges
let _boundaryPolygon = null;    // L.polygon when closed
let _boundaryHouseHighlights = []; // L.circleMarker for houses inside
let _boundaryClosed = false;
let _boundaryHouseIds = [];     // house IDs found inside

function setMapTool(tool) {
  // Clean up previous tool state
  if (_mapTool === "add" && tool !== "add" && _addMarker) {
    map.removeLayer(_addMarker); _addMarker = null;
  }
  if (_mapTool === "select" && tool !== "select") clearBoxSelection();
  if (_mapTool === "boundary" && tool !== "boundary") clearBoundary();

  _mapTool = tool;
  document.querySelectorAll(".map-tool").forEach(b => b.classList.remove("active"));
  document.getElementById("map-tool-" + tool).classList.add("active");

  const hint = document.getElementById("map-tool-hint");
  const mapEl = document.getElementById("map");
  if (tool === "add") {
    hint.textContent = "Click on the map to place a new house.";
    mapEl.style.cursor = "crosshair";
    map.dragging.enable();
  } else if (tool === "select") {
    hint.textContent = "Drag a rectangle to select houses. Then assign or delete.";
    mapEl.style.cursor = "crosshair";
    map.dragging.disable();
    _initBoxSelect();
  } else if (tool === "boundary") {
    hint.textContent = "Click to add points. Double-click or click Close to finish.";
    mapEl.style.cursor = "crosshair";
    map.dragging.enable();
    map.doubleClickZoom.disable();
    document.getElementById("map-boundary-ui").style.display = "";
    _boundaryUpdateStatus();
  } else {
    hint.textContent = "";
    mapEl.style.cursor = "";
    map.dragging.enable();
    map.doubleClickZoom.enable();
  }
}

// --- Box select tool ---
function _initBoxSelect() {
  const container = map.getContainer();
  container.addEventListener("mousedown", _boxMouseDown);
  container.addEventListener("touchstart", _boxTouchStart, { passive: false });
}

function _getContainerPoint(e) {
  const rect = map.getContainer().getBoundingClientRect();
  if (e.touches && e.touches.length) {
    return L.point(e.touches[0].clientX - rect.left, e.touches[0].clientY - rect.top);
  }
  if (e.changedTouches && e.changedTouches.length) {
    return L.point(e.changedTouches[0].clientX - rect.left, e.changedTouches[0].clientY - rect.top);
  }
  return L.point(e.clientX - rect.left, e.clientY - rect.top);
}

function _boxMouseDown(e) {
  if (_mapTool !== "select") return;
  if (e.button !== 0) return;
  const latlng = map.containerPointToLatLng(_getContainerPoint(e));
  _boxSelectStart = latlng;
  if (_boxSelectRect) { map.removeLayer(_boxSelectRect); _boxSelectRect = null; }

  document.addEventListener("mousemove", _boxMouseMove);
  document.addEventListener("mouseup", _boxMouseUp);
}

function _boxTouchStart(e) {
  if (_mapTool !== "select") return;
  if (e.touches.length !== 1) return;
  e.preventDefault();
  const latlng = map.containerPointToLatLng(_getContainerPoint(e));
  _boxSelectStart = latlng;
  if (_boxSelectRect) { map.removeLayer(_boxSelectRect); _boxSelectRect = null; }

  document.addEventListener("touchmove", _boxTouchMove, { passive: false });
  document.addEventListener("touchend", _boxTouchEnd);
}

function _boxMouseMove(e) {
  if (!_boxSelectStart || _mapTool !== "select") return;
  const latlng = map.containerPointToLatLng(_getContainerPoint(e));
  const bounds = L.latLngBounds(_boxSelectStart, latlng);
  if (_boxSelectRect) {
    _boxSelectRect.setBounds(bounds);
  } else {
    _boxSelectRect = L.rectangle(bounds, { color: "#003F87", weight: 2, fillOpacity: 0.15, dashArray: "6 3" }).addTo(map);
  }
}

function _boxTouchMove(e) {
  if (!_boxSelectStart || _mapTool !== "select") return;
  e.preventDefault();
  const latlng = map.containerPointToLatLng(_getContainerPoint(e));
  const bounds = L.latLngBounds(_boxSelectStart, latlng);
  if (_boxSelectRect) {
    _boxSelectRect.setBounds(bounds);
  } else {
    _boxSelectRect = L.rectangle(bounds, { color: "#003F87", weight: 2, fillOpacity: 0.15, dashArray: "6 3" }).addTo(map);
  }
}

function _boxMouseUp(e) {
  document.removeEventListener("mousemove", _boxMouseMove);
  document.removeEventListener("mouseup", _boxMouseUp);
  if (!_boxSelectStart || _mapTool !== "select") return;

  const latlng = map.containerPointToLatLng(_getContainerPoint(e));
  const bounds = L.latLngBounds(_boxSelectStart, latlng);
  _boxSelectStart = null;

  // Remove the rectangle visual
  if (_boxSelectRect) { map.removeLayer(_boxSelectRect); _boxSelectRect = null; }

  // Ignore tiny drags (likely just a click)
  const size = map.latLngToContainerPoint(bounds.getNorthEast()).subtract(map.latLngToContainerPoint(bounds.getSouthWest()));
  if (Math.abs(size.x) < 10 && Math.abs(size.y) < 10) return;

  // Find houses inside the bounds
  _selectHousesInBounds(bounds);
}

function _boxTouchEnd(e) {
  document.removeEventListener("touchmove", _boxTouchMove);
  document.removeEventListener("touchend", _boxTouchEnd);
  if (!_boxSelectStart || _mapTool !== "select") return;

  const latlng = map.containerPointToLatLng(_getContainerPoint(e));
  const bounds = L.latLngBounds(_boxSelectStart, latlng);
  _boxSelectStart = null;

  if (_boxSelectRect) { map.removeLayer(_boxSelectRect); _boxSelectRect = null; }

  const size = map.latLngToContainerPoint(bounds.getNorthEast()).subtract(map.latLngToContainerPoint(bounds.getSouthWest()));
  if (Math.abs(size.x) < 10 && Math.abs(size.y) < 10) return;

  _selectHousesInBounds(bounds);
}

function _selectHousesInBounds(bounds) {
  _clearBoxHighlights();
  _boxSelectedHouses = [];
  houseDotLayer.eachLayer(layer => {
    if (layer.getLatLng && bounds.contains(layer.getLatLng()) && layer._houseId) {
      const ll = layer.getLatLng();
      _boxSelectedHouses.push({ id: layer._houseId, lat: ll.lat, lng: ll.lng, address: layer._address || "", _layer: layer });
      const hl = L.circleMarker(ll, { radius: 8, fillColor: "#f59e0b", color: "#fff", weight: 2, fillOpacity: 0.8 }).addTo(map);
      _boxHighlights.push(hl);
    }
  });
  _updateBoxSelectedUI();
}

function _clearBoxHighlights() {
  _boxHighlights.forEach(m => map.removeLayer(m));
  _boxHighlights = [];
}

function _updateBoxSelectedUI() {
  const el = document.getElementById("map-box-selected");
  const countEl = document.getElementById("map-box-count");
  if (_boxSelectedHouses.length > 0) {
    el.style.display = "";
    countEl.textContent = _boxSelectedHouses.length;
  } else {
    el.style.display = "none";
  }
}

function clearBoxSelection() {
  _clearBoxHighlights();
  _boxSelectedHouses = [];
  _updateBoxSelectedUI();
  if (_boxSelectRect && map) { map.removeLayer(_boxSelectRect); _boxSelectRect = null; }
  _boxSelectStart = null;
  // Only remove the listener if we're leaving select mode
  if (_mapTool !== "select" && map) {
    map.getContainer().removeEventListener("mousedown", _boxMouseDown);
    map.getContainer().removeEventListener("touchstart", _boxTouchStart);
  }
}

async function boxDeleteSelected() {
  if (!_boxSelectedHouses.length) return;
  if (!confirm(`Delete ${_boxSelectedHouses.length} selected house(s)? This cannot be undone.`)) return;

  const ids = _boxSelectedHouses.map(h => h.id).filter(Boolean);
  if (!ids.length) { alert("No house IDs found. Zoom in closer and try again."); return; }

  _showStatus(`Deleting ${ids.length} house(s)…`);
  try {
    const r = await authFetch(API + "/api/houses/batch-delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ house_ids: ids }),
    });
    if (r.ok) {
      clearBoxSelection();
      _flashStatus(`Deleted ${ids.length} house(s).`);
      refreshMapDots();
    } else {
      _hideStatus();
      const data = await r.json().catch(() => ({}));
      alert(data.detail || "Error deleting houses.");
    }
  } catch (err) {
    _hideStatus();
    alert("Error: " + err.message);
  }
}

async function boxAssignSelected() {
  if (!_boxSelectedHouses.length) return;
  const ids = _boxSelectedHouses.map(h => h.id).filter(Boolean);
  if (!ids.length) { alert("No house IDs found. Zoom in closer and try again."); return; }

  // Prompt for event selection
  let eventsData;
  try {
    const r = await authFetch(API + "/api/events/");
    eventsData = await r.json();
  } catch { alert("Could not load events."); return; }

  if (!eventsData.length) { alert("No events exist. Create one first."); return; }

  const eventName = prompt("Enter event name to assign to:\n\n" + eventsData.map(e => "  " + e.name).join("\n"));
  if (!eventName) return;
  const ev = eventsData.find(e => e.name.toLowerCase() === eventName.trim().toLowerCase());
  if (!ev) { alert("Event not found. Enter the exact name."); return; }

  const groupLabel = prompt("Group label (optional):", "");

  _showStatus(`Assigning ${ids.length} house(s)…`);
  try {
    const body = {
      house_ids: ids,
      assigned_to: groupLabel || undefined,
    };
    const ar = await authFetch(API + `/api/events/${ev.id}/assign`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (ar.ok) {
      const data = await ar.json();
      _flashStatus(`Assigned ${data.assigned || ids.length} house(s) to "${ev.name}".`);
      clearBoxSelection();
    } else {
      _hideStatus();
      const data = await ar.json().catch(() => ({}));
      alert(data.detail || "Error assigning houses.");
    }
  } catch (err) {
    _hideStatus();
    alert("Error: " + err.message);
  }
}

function _handleMapToolClick(e) {
  if (_mapTool === "add") {
    _placeAddMarker(e.latlng);
  } else if (_mapTool === "boundary" && !_boundaryClosed) {
    _addBoundaryPoint(e.latlng);
  }
}

// --- Boundary tool ---
function _addBoundaryPoint(latlng) {
  const pt = [latlng.lat, latlng.lng];

  // If clicking near the first point and we have 3+ points, close the polygon
  if (_boundaryPoints.length >= 3) {
    const first = _boundaryPoints[0];
    const dist = map.latLngToContainerPoint(latlng).distanceTo(
      map.latLngToContainerPoint(L.latLng(first[0], first[1]))
    );
    if (dist < 25) { closeBoundary(); return; }
  }

  _boundaryPoints.push(pt);

  // Add vertex marker (larger for touch usability)
  const marker = L.circleMarker(latlng, {
    radius: 8, color: "#003F87", fillColor: "#fff", fillOpacity: 1, weight: 2,
  }).addTo(map);
  _boundaryMarkers.push(marker);

  // Update polyline
  _updateBoundaryLine();
  _boundaryUpdateStatus();
}

function _updateBoundaryLine() {
  if (_boundaryLines) map.removeLayer(_boundaryLines);
  if (_boundaryPoints.length >= 2) {
    _boundaryLines = L.polyline(_boundaryPoints, {
      color: "#003F87", weight: 2, dashArray: "6 4", opacity: 0.8,
    }).addTo(map);
  }
}

function _boundaryUpdateStatus() {
  const el = document.getElementById("map-boundary-status");
  if (el) el.textContent = `${_boundaryPoints.length} points`;
}

function undoBoundaryPoint() {
  if (_boundaryClosed) return;
  if (!_boundaryPoints.length) return;
  _boundaryPoints.pop();
  const m = _boundaryMarkers.pop();
  if (m) map.removeLayer(m);
  _updateBoundaryLine();
  _boundaryUpdateStatus();
}

async function closeBoundary() {
  if (_boundaryPoints.length < 3) { alert("Need at least 3 points to close a polygon."); return; }
  _boundaryClosed = true;

  // Remove polyline, draw filled polygon
  if (_boundaryLines) { map.removeLayer(_boundaryLines); _boundaryLines = null; }
  _boundaryPolygon = L.polygon(_boundaryPoints, {
    color: "#003F87", weight: 2, fillColor: "#003F87", fillOpacity: 0.1,
  }).addTo(map);

  // Hide drawing UI, show result UI
  document.getElementById("map-boundary-ui").style.display = "none";
  document.getElementById("map-boundary-result").style.display = "";
  document.getElementById("map-boundary-count").textContent = "counting…";
  document.getElementById("map-tool-hint").textContent = "";

  // Load event dropdown
  try {
    const r = await authFetch(API + "/api/events/");
    if (r.ok) {
      const events = await r.json();
      const sel = document.getElementById("map-boundary-event");
      sel.innerHTML = '<option value="">Select event…</option>' +
        events.map(ev => `<option value="${esc(ev.id)}">${esc(ev.name)}</option>`).join("");
    }
  } catch {}

  // Query house count
  try {
    const r = await authFetch(API + "/api/houses/in-polygon", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ polygon: _boundaryPoints, count_only: true }),
    });
    const data = await r.json();
    if (r.ok) {
      document.getElementById("map-boundary-count").textContent =
        (data.count || 0).toLocaleString();
    } else {
      document.getElementById("map-boundary-count").textContent = "error";
    }
  } catch (err) {
    document.getElementById("map-boundary-count").textContent = "error";
  }

  // Also query ArcGIS for how many parcels exist in this boundary
  document.getElementById("map-boundary-arcgis-count").textContent = "(checking ArcGIS…)";
  try {
    const r = await authFetch(API + "/api/arcgis/count", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ polygon: _boundaryPoints }),
    });
    const data = await r.json();
    if (data.count !== null && data.count !== undefined) {
      document.getElementById("map-boundary-arcgis-count").textContent =
        `(${data.count.toLocaleString()} in ArcGIS)`;
    } else {
      document.getElementById("map-boundary-arcgis-count").textContent = "";
    }
  } catch { document.getElementById("map-boundary-arcgis-count").textContent = ""; }
}

async function boundaryImportFromArcGIS() {
  if (!_boundaryPoints.length) return;
  const arcgisCountText = document.getElementById("map-boundary-arcgis-count").textContent;
  const eventId = document.getElementById("map-boundary-event").value || undefined;
  const groupLabel = document.getElementById("map-boundary-group").value.trim() || undefined;

  let msg = `Import all ArcGIS parcels within this boundary ${arcgisCountText}?`;
  if (eventId) msg += "\n\nImported houses will also be assigned to the selected event.";
  if (!confirm(msg)) return;

  document.getElementById("map-boundary-arcgis-count").textContent = "(importing…)";
  _showStatus("Importing parcels from ArcGIS…");
  try {
    const body = {
      polygon: _boundaryPoints,
      max_records: 10000,
      notes: "Boundary import from map",
    };
    if (eventId) body.event_id = eventId;
    const r = await authFetch(API + "/api/arcgis/fetch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      _hideStatus();
      alert("Error: " + (data.detail || `Server returned ${r.status}`));
      document.getElementById("map-boundary-arcgis-count").textContent = arcgisCountText;
      return;
    }
    const data = await r.json();
    let result = `Fetched ${data.fetched} parcels, imported ${data.imported} records.`;
    if (data.assigned) result += ` ${data.assigned} assigned to "${data.event_name}".`;
    document.getElementById("map-boundary-arcgis-count").textContent = `(${data.imported} imported)`;
    _flashStatus(`Imported ${data.imported} parcels from ArcGIS.`);
  } catch (err) {
    _hideStatus();
    alert("Import error: " + err.message);
    document.getElementById("map-boundary-arcgis-count").textContent = arcgisCountText;
    return;
  }

  // Refresh map and re-count independently — errors here should not mask import success
  refreshMapDots();
  try {
    const cr = await authFetch(API + "/api/houses/in-polygon", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ polygon: _boundaryPoints, count_only: true }),
    });
    if (cr.ok) {
      const cd = await cr.json();
      document.getElementById("map-boundary-count").textContent = (cd.count || 0).toLocaleString();
    }
  } catch {}
}

async function boundaryAssignToEvent() {
  const eventId = document.getElementById("map-boundary-event").value;
  if (!eventId) { alert("Select an event first."); return; }
  const groupLabel = document.getElementById("map-boundary-group").value.trim() || undefined;

  const countText = document.getElementById("map-boundary-count").textContent;
  if (!confirm(`Assign ${countText} houses in this boundary to the selected event?`)) return;

  document.getElementById("map-boundary-count").textContent = "assigning…";
  try {
    const r = await authFetch(API + "/api/houses/in-polygon", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        polygon: _boundaryPoints,
        event_id: eventId,
        assigned_to: groupLabel,
      }),
    });
    const data = await r.json();
    if (r.ok) {
      const msg = `${data.assigned} houses assigned (${data.count} total in boundary)`;
      document.getElementById("map-boundary-count").textContent = data.count.toLocaleString();
      alert(msg);
      // Highlight assigned houses on map
      _showBoundaryHouses(data.house_ids);
    } else {
      alert("Error: " + (data.detail || JSON.stringify(data)));
      document.getElementById("map-boundary-count").textContent = countText;
    }
  } catch (err) {
    alert("Network error: " + err.message);
    document.getElementById("map-boundary-count").textContent = countText;
  }
}

function _showBoundaryHouses(houseIds) {
  // We already have the polygon on the map; no need to re-highlight
  // but we update the count to reflect assigned count
}

function clearBoundary() {
  _boundaryPoints = [];
  _boundaryClosed = false;
  _boundaryHouseIds = [];
  _boundaryMarkers.forEach(m => map.removeLayer(m));
  _boundaryMarkers = [];
  if (_boundaryLines) { map.removeLayer(_boundaryLines); _boundaryLines = null; }
  if (_boundaryPolygon) { map.removeLayer(_boundaryPolygon); _boundaryPolygon = null; }
  _boundaryHouseHighlights.forEach(m => map.removeLayer(m));
  _boundaryHouseHighlights = [];
  document.getElementById("map-boundary-ui").style.display = "none";
  document.getElementById("map-boundary-result").style.display = "none";
}


// --- Add tool ---
function _placeAddMarker(latlng) {
  if (_addMarker) map.removeLayer(_addMarker);
  _addMarker = L.marker(latlng, {
    icon: L.divIcon({
      className: "",
      html: '<div style="color:#059669;font-size:26px;font-weight:900;text-align:center;line-height:26px;text-shadow:0 0 3px #fff,0 0 3px #fff;">+</div>',
      iconSize: [26, 26],
      iconAnchor: [13, 13],
    }),
  }).addTo(map);

  const popupHtml = `
    <div style="min-width:220px;">
      <div style="margin-bottom:6px;"><strong>Add House</strong></div>
      <div style="margin-bottom:4px;">
        <input id="add-house-address" placeholder="Full address" style="width:100%;padding:4px 6px;font-size:13px;border:1px solid #c5c5c5;border-radius:4px;" />
      </div>
      <div style="margin-bottom:4px;">
        <input id="add-house-zip" placeholder="ZIP code" style="width:80px;padding:4px 6px;font-size:13px;border:1px solid #c5c5c5;border-radius:4px;" value="${(Array.from(document.getElementById("map-zip-select").selectedOptions).map(o => o.value)[0]) || ""}" />
      </div>
      <div style="font-size:11px;color:#888;margin-bottom:6px;">
        ${latlng.lat.toFixed(6)}, ${latlng.lng.toFixed(6)}
      </div>
      <button onclick="saveAddHouse(${latlng.lat}, ${latlng.lng})" style="padding:4px 12px;font-size:13px;background:#059669;color:#fff;border:none;border-radius:4px;cursor:pointer;">Save</button>
      <button onclick="cancelAddHouse()" style="padding:4px 12px;font-size:13px;background:#ccc;color:#333;border:none;border-radius:4px;cursor:pointer;margin-left:4px;">Cancel</button>
    </div>
  `;
  _addMarker.bindPopup(popupHtml, { closeOnClick: false, autoClose: false }).openPopup();
}

async function saveAddHouse(lat, lng) {
  const address = document.getElementById("add-house-address").value.trim();
  const zip = document.getElementById("add-house-zip").value.trim();
  if (!address) { alert("Enter an address."); return; }

  try {
    const r = await authFetch(API + "/api/houses/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        full_address: address,
        zip_code: zip || null,
        latitude: lat,
        longitude: lng,
      }),
    });
    const data = await r.json();
    if (r.ok) {
      if (_addMarker) { map.removeLayer(_addMarker); _addMarker = null; }
      refreshMapDots();
    } else {
      alert(data.detail || "Error adding house.");
    }
  } catch (err) {
    alert("Network error: " + err.message);
  }
}

function cancelAddHouse() {
  if (_addMarker) { map.removeLayer(_addMarker); _addMarker = null; }
}

// --- Events ---
async function loadEvents() {
  document.getElementById("events-list").innerHTML = '<div class="loading-bar"></div>';
  const r = await authFetch(API + "/api/events/");
  const events = await r.json();
  document.getElementById("events-list").innerHTML = events.length
    ? `<table><tr><th>Name</th><th>Description</th><th>Date</th><th>Houses</th><th></th></tr>` +
      events.map(e => `<tr>
        <td>${esc(e.name)}</td>
        <td>${esc(e.description || "")}</td>
        <td>${e.event_date ? new Date(e.event_date).toLocaleDateString() : "—"}</td>
        <td>${e.house_count}</td>
        <td>
          <button class="btn-sm" onclick="openEvent('${esc(e.id)}','${esc(e.name)}')">Open</button>
          <button class="btn-sm" onclick="editEvent('${esc(e.id)}')" style="margin-left:4px;">Edit</button>
          <button class="btn-sm" onclick="duplicateEvent('${esc(e.id)}')" style="margin-left:4px;">Duplicate</button>
          <button class="btn-sm btn-danger" onclick="deleteEvent('${esc(e.id)}','${esc(e.name)}')" style="margin-left:4px;">Delete</button>
        </td>
      </tr>`).join("") + `</table>`
    : "<p>No events yet.</p>";
}

async function deleteEvent(id, name) {
  if (!confirm(`Delete event "${name}"? This will remove all assigned houses and visit records for this event.`)) return;
  _showStatus("Deleting event…");
  try {
    const r = await authFetch(API + `/api/events/${id}`, { method: "DELETE" });
    if (r.ok) {
      _flashStatus(`Deleted event "${name}".`);
      loadEvents();
      // If we were viewing this event's detail, go back
      if (currentEventId === id) {
        currentEventId = null;
        currentEventName = null;
        showPage("events");
      }
    } else {
      _hideStatus();
      const data = await r.json().catch(() => ({}));
      alert(data.detail || "Failed to delete event.");
    }
  } catch (err) {
    _hideStatus();
    alert("Error: " + err.message);
  }
}

async function editEvent(id) {
  // Fetch current event data
  let ev;
  try {
    const r = await authFetch(API + `/api/events/${id}`);
    if (!r.ok) { alert("Failed to load event."); return; }
    ev = await r.json();
  } catch (err) { alert("Error: " + err.message); return; }

  const newName = prompt("Event name:", ev.name);
  if (newName === null) return; // cancelled
  const newDesc = prompt("Description:", ev.description || "");
  if (newDesc === null) return;
  const currentDate = ev.event_date ? ev.event_date.split("T")[0] : "";
  const newDate = prompt("Date (YYYY-MM-DD):", currentDate);
  if (newDate === null) return;

  const body = { name: newName.trim() || ev.name };
  body.description = newDesc.trim();
  body.event_date = newDate.trim() ? new Date(newDate.trim()).toISOString() : "";

  try {
    const r = await authFetch(API + `/api/events/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (r.ok) {
      loadEvents();
      // Update detail title if viewing this event
      if (currentEventId === id) {
        currentEventName = body.name;
        document.getElementById("event-detail-title").textContent = body.name;
      }
    } else {
      const data = await r.json().catch(() => ({}));
      alert(data.detail || "Failed to update event.");
    }
  } catch (err) {
    alert("Error: " + err.message);
  }
}
async function duplicateEvent(id) {
  if (!confirm("Duplicate this event with all house assignments (visits will not be copied)?")) return;
  try {
    const r = await authFetch(API + `/api/events/${id}/duplicate`, { method: "POST" });
    if (r.ok) {
      const data = await r.json();
      alert(`Event duplicated: "${data.name}" with ${data.house_count ?? 0} houses.`);
      loadEvents();
    } else {
      const data = await r.json().catch(() => ({}));
      alert(data.detail || "Failed to duplicate event.");
    }
  } catch (err) {
    alert("Error: " + err.message);
  }
}

async function exportEventsCSV() {
  const r = await authFetch(API + "/api/events/");
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
  await authFetch(API + "/api/events/", {
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

function _renderGroupedHouses(houses, { openByDefault = false } = {}) {
  const groups = {};
  houses.forEach(eh => {
    const key = eh.assigned_to || "Unassigned";
    if (!groups[key]) groups[key] = [];
    groups[key].push(eh);
  });
  let html = "";
  for (const [label, items] of Object.entries(groups)) {
    const visited = items.filter(eh => eh.status === "visited").length;
    html += `<details${openByDefault ? " open" : ""}><summary><strong>${esc(label)}</strong> — ${items.length} houses, ${visited} visited</summary>`;
    html += `<table><tr><th>#</th><th>Address</th><th>Owner</th><th>Status</th><th></th></tr>`;
    html += items.map((eh, idx) => `<tr>
      <td>${idx + 1}</td>
      <td>${esc(eh.house.full_address)}</td>
      <td>${esc(eh.house.owner_name) || "—"}</td>
      <td><span class="badge badge-${esc(eh.status)}">${esc(eh.status)}</span></td>
      <td><button class="btn-sm" onclick="openVisitModal('${esc(currentEventId)}','${esc(eh.id)}','${esc(eh.house.full_address)}')">Visit</button></td>
    </tr>`).join("");
    html += `</table></details>`;
  }
  return html;
}

let _eventHousesCache = [];
async function loadEventHouses() {
  document.getElementById("event-houses-list").innerHTML = '<div class="loading-bar"></div>';
  const r = await authFetch(API + `/api/events/${currentEventId}/houses`);
  const houses = await r.json();
  _eventHousesCache = houses;
  const el = document.getElementById("event-houses-list");
  if (!houses.length) { el.innerHTML = "<p>No houses assigned. Use Walk Groups or Manual Assign to add houses.</p>"; return; }
  el.innerHTML = _renderGroupedHouses(houses, { openByDefault: true });
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
  await authFetch(API + `/api/events/${currentEventId}/assign`, {
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
    const r = await authFetch(API + "/api/events/");
    if (!r.ok) { sel.innerHTML = '<option value="">Failed to load events</option>'; return; }
    const events = await r.json();
    if (!events.length) {
      sel.innerHTML = '<option value="">No events — create one first</option>';
      return;
    }
    sel.innerHTML = '<option value="">Select an event…</option>' +
      events.map(ev => {
        const selected = String(ev.id) === String(currentEventId) ? " selected" : "";
        return `<option value="${esc(ev.id)}"${selected}>${esc(ev.name)} (${ev.house_count} houses)</option>`;
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
  if (!confirm("This will organize all houses assigned to this event into walk groups by street.\n\nExisting group labels will be overwritten.\n\nContinue?")) return;
  const fd = new FormData(e.target);
  const body = {
    group_size: parseInt(fd.get("group_size") || "20"),
  };
  const resultEl = document.getElementById("walk-group-result");
  resultEl.classList.remove("hidden");
  resultEl.textContent = "Generating groups…";
  try {
    const r = await authFetch(API + `/api/events/${currentEventId}/walk-groups`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (r.ok && data.groups?.length) {
      resultEl.innerHTML = `<strong>${data.groups.length} groups created (${data.total_assigned} houses)</strong>` +
        `<ul>` + data.groups.map(g => `<li>${g.label} — ${g.houses} houses</li>`).join("") + `</ul>`;
      loadWalkGroupList();
    } else if (r.ok) {
      resultEl.textContent = data.message || "No houses assigned to this event yet.";
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
    const r = await authFetch(API + `/api/events/${currentEventId}/houses`);
    if (!r.ok) {
      el.innerHTML = `<p>Failed to load groups (HTTP ${r.status}).</p>`;
      return;
    }
    const houses = await r.json();
    _eventHousesCache = houses;
    if (!Array.isArray(houses) || !houses.length) {
      el.innerHTML = "<p>No groups yet. Generate walk groups above.</p>";
      return;
    }
    el.innerHTML = _renderGroupedHouses(houses);
  } catch (err) {
    el.innerHTML = `<p>Error loading groups: ${err.message}</p>`;
  }
}

function exportWalkGroupsCSV() {
  if (!currentEventId) { alert("Select an event first."); return; }
  if (!_eventHousesCache.length) { alert("No walk groups to export."); return; }
  exportCSV("walk-groups.csv",
    ["Group", "Address", "Owner", "ZIP", "Status"],
    _eventHousesCache.map(eh => [
      eh.assigned_to || "Unassigned", eh.house.full_address,
      eh.house.owner_name || "", eh.house.zip_code || "", eh.status,
    ])
  );
}

function exportEventHousesCSV() {
  if (!currentEventId) return;
  if (!_eventHousesCache.length) { alert("No houses to export."); return; }
  exportCSV(`event-${currentEventName || "houses"}.csv`,
    ["Group", "Address", "Owner", "ZIP", "Status", "Latitude", "Longitude"],
    _eventHousesCache.map(eh => [
      eh.assigned_to || "Unassigned", eh.house.full_address,
      eh.house.owner_name || "", eh.house.zip_code || "", eh.status,
      eh.house.latitude || "", eh.house.longitude || "",
    ])
  );
}

// --- Visits ---
let _visitRosterLoaded = false;
async function loadVisitRoster() {
  const sel = document.getElementById("visit-volunteer-select");
  try {
    const r = await authFetch(API + "/api/scout/roster?active_only=true");
    const roster = await r.json();
    sel.innerHTML = '<option value="">Select volunteer...</option>' +
      roster.map(s => `<option value="${esc(s.name)}">${esc(s.name)}${s.scout_id ? " (" + esc(s.scout_id) + ")" : ""}</option>`).join("") +
      '<option value="__other__">Other (write in)</option>';
  } catch {
    sel.innerHTML = '<option value="">Select volunteer...</option><option value="__other__">Other (write in)</option>';
  }
  _visitRosterLoaded = true;
}
document.getElementById("visit-volunteer-select").onchange = (e) => {
  document.getElementById("visit-volunteer-other-wrap").style.display =
    e.target.value === "__other__" ? "" : "none";
  if (e.target.value !== "__other__") {
    document.querySelector('#visit-form [name="volunteer_name"]').value = "";
  }
};
document.getElementById("visit-donation-select").onchange = (e) => {
  document.getElementById("visit-donation-amount-wrap").style.display =
    e.target.value === "true" ? "" : "none";
};
async function openVisitModal(eventId, eventHouseId, address) {
  const form = document.getElementById("visit-form");
  form.reset();
  form.querySelector('[name="event_id"]').value = eventId;
  form.querySelector('[name="event_house_id"]').value = eventHouseId;
  document.getElementById("visit-modal-title").textContent =
    address ? "Record Visit — " + address : "Record Visit";
  document.getElementById("visit-volunteer-other-wrap").style.display = "none";
  document.getElementById("visit-donation-amount-wrap").style.display = "none";
  if (!_visitRosterLoaded) await loadVisitRoster();
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
  const volSelect = fd.get("volunteer_select");
  const volunteerName = volSelect === "__other__"
    ? (fd.get("volunteer_name") || null)
    : (volSelect || null);

  const toBool = (v) => v === "true" ? true : v === "false" ? false : null;

  const body = {
    outcome: fd.get("outcome") || null,
    donation_amount: fd.get("donation_amount") ? parseFloat(fd.get("donation_amount")) : null,
    tickets_purchased: parseInt(fd.get("tickets_purchased") || "0"),
    notes: fd.get("notes") || null,
    follow_up: !!fd.get("follow_up"),
    volunteer_name: volunteerName,
    door_answer: toBool(fd.get("door_answer")),
    donation_given: toBool(fd.get("donation_given")),
    former_scout: toBool(fd.get("former_scout")),
    avoid_house: !!fd.get("avoid_house"),
  };
  await authFetch(API + `/api/events/${eventId}/houses/${ehId}/visits`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  closeVisitModal();
  // Refresh whichever list is active
  if (document.getElementById("page-event-detail").classList.contains("active")) loadEventHouses();
  if (document.getElementById("page-walk-groups").classList.contains("active")) loadWalkGroupList();
};

// --- Print packet ---
function printPacket() { window.print(); }

// --- ArcGIS Fetch ---
let _arcgisCountTimer = null;
document.getElementById("arcgis-zip-input").addEventListener("input", () => {
  clearTimeout(_arcgisCountTimer);
  _arcgisCountTimer = setTimeout(checkArcGISCount, 600);
});

async function checkArcGISCount() {
  const el = document.getElementById("arcgis-record-count");
  const zips = document.getElementById("arcgis-zip-input").value.trim();
  if (!zips) { el.textContent = ""; return; }
  const zip_codes = zips.split(",").map(s => s.trim()).filter(Boolean);
  if (!zip_codes.length) { el.textContent = ""; return; }
  el.textContent = "checking…";
  try {
    const r = await authFetch(API + "/api/arcgis/count", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ zip_codes }),
    });
    const data = await r.json();
    if (data.count !== null && data.count !== undefined) {
      el.textContent = `${data.count.toLocaleString()} records available`;
    } else {
      el.textContent = data.error || "count unavailable";
    }
  } catch { el.textContent = ""; }
}

async function loadArcGISEventSelect() {
  const sel = document.getElementById("arcgis-event-select");
  if (!sel) return;
  try {
    const r = await authFetch(API + "/api/events/");
    if (!r.ok) return;
    const events = await r.json();
    sel.innerHTML = '<option value="">No event (import only)</option>' +
      events.map(ev => `<option value="${esc(ev.id)}">${esc(ev.name)}</option>`).join("");
  } catch (_) { /* keep default */ }
}

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
  const eventId = fd.get("event_id");
  if (eventId) body.event_id = eventId;
  document.getElementById("arcgis-progress").classList.remove("hidden");
  document.getElementById("arcgis-status").textContent = "connecting…";
  _showStatus("Fetching parcels from ArcGIS…");
  try {
    const r = await authFetch(API + "/api/arcgis/fetch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (r.ok) {
      let msg = `Done! Fetched ${data.fetched} parcels, imported ${data.imported} records.`;
      if (data.assigned) msg += ` ${data.assigned} houses assigned to "${data.event_name}" — ready for walk groups.`;
      document.getElementById("arcgis-status").textContent = msg;
      _flashStatus(`Imported ${data.imported} records from ArcGIS.`);
    } else {
      document.getElementById("arcgis-status").textContent =
        `Error: ${data.detail || "unknown"}`;
    }
  } catch (err) {
    document.getElementById("arcgis-status").textContent = "Network error: " + err.message;
  }
  document.getElementById("arcgis-record-count").textContent = "";
  e.target.reset();
  loadImports();
  loadUnmatched();
};

// --- Imports ---
async function loadImportEventSelect() {
  const sel = document.getElementById("import-event-select");
  if (!sel) return;
  try {
    const r = await authFetch(API + "/api/events/");
    if (!r.ok) return;
    const events = await r.json();
    sel.innerHTML = '<option value="">No event (import only)</option>' +
      events.map(ev => `<option value="${esc(ev.id)}">${esc(ev.name)}</option>`).join("");
  } catch (_) { /* keep default option */ }
}

document.getElementById("import-form").onsubmit = async (e) => {
  e.preventDefault();
  if (!confirm("This will import addresses and add/update houses in the database.\n\nExisting house data will not be overwritten.\n\nContinue?")) return;
  const fd = new FormData(e.target);
  document.getElementById("import-progress").classList.remove("hidden");
  document.getElementById("import-status").textContent = "uploading…";
  _showStatus("Importing file…");
  try {
    const r = await authFetch(API + "/api/imports/", { method: "POST", body: fd });
    const data = await r.json();
    if (r.ok) {
      let msg = `Done! ${data.record_count} records imported.`;
      if (data.notes && data.notes.includes("Auto-assigned")) {
        const match = data.notes.match(/Auto-assigned (\d+) houses to event: (.+)/);
        if (match) msg += ` ${match[1]} houses assigned to "${match[2]}" — you can now create walk groups.`;
      }
      document.getElementById("import-status").textContent = msg;
      _flashStatus(`Imported ${data.record_count} records.`);
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
  document.getElementById("imports-list").innerHTML = '<div class="loading-bar"></div>';
  const r = await authFetch(API + "/api/imports/");
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
  _showStatus("Deleting import…");
  const r = await authFetch(API + "/api/imports/" + id, { method: "DELETE" });
  const data = await r.json();
  if (r.ok) {
    _flashStatus(`Deleted. ${data.houses_removed} house(s) removed, ${data.houses_kept} kept.`);
    loadImports();
    loadUnmatched();
  } else {
    _hideStatus();
    alert("Delete failed: " + (data.detail || JSON.stringify(data)));
  }
}

async function loadUnmatched() {
  const r = await authFetch(API + "/api/imports/unmatched/");
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
  document.getElementById("houses-list").innerHTML = '<div class="loading-bar"></div>';
  const search = document.getElementById("house-search")?.value || "";
  const zip = document.getElementById("house-zip")?.value || "";
  const params = new URLSearchParams();
  if (search) params.set("search", search);
  if (zip) params.set("zip_code", zip);
  const r = await authFetch(API + "/api/houses/?" + params);
  const houses = await r.json();
  document.getElementById("houses-list").innerHTML = houses.length
    ? `<table><tr><th>Address</th><th>City</th><th>ZIP</th><th>Owner</th><th>Source</th></tr>` +
      houses.map(h => `<tr>
        <td>${esc(h.full_address)}</td>
        <td>${esc(h.city)}</td>
        <td>${esc(h.zip_code)}</td>
        <td>${esc(h.owner_name) || "—"}</td>
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
  const r = await authFetch(API + "/api/houses/?" + params);
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
  const r = await authFetch(API + "/api/houses/", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (r.ok) { e.target.reset(); loadHouses(); }
  else { const d = await r.json(); alert(d.detail || "Error"); }
};

// --- Roster ---
async function loadRoster() {
  document.getElementById("roster-list").innerHTML = '<div class="loading-bar"></div>';
  const r = await authFetch(API + "/api/scout/roster");
  const roster = await r.json();
  document.getElementById("roster-list").innerHTML = roster.length
    ? `<table><tr><th>Name</th><th>Scout ID</th><th>Status</th><th>Password</th><th></th></tr>` +
      roster.map(s => `<tr>
        <td>${esc(s.name)}</td>
        <td>${esc(s.scout_id) || "—"}</td>
        <td><span class="badge badge-${s.active ? "completed" : "pending"}">${s.active ? "Active" : "Inactive"}</span></td>
        <td>${s.has_password
          ? '<span class="badge badge-completed">Set</span> <button class="btn-sm" onclick="clearScoutPassword(\'' + esc(s.id) + '\')">Clear</button>'
          : '<span class="badge badge-pending">None</span>'
        }</td>
        <td>
          <button class="btn-sm" onclick="promptScoutPassword('${esc(s.id)}', '${esc(s.name)}')">Set Password</button>
          <button class="btn-sm" onclick="toggleRosterScout('${esc(s.id)}')">${s.active ? "Deactivate" : "Activate"}</button>
          <button class="btn-sm btn-danger" onclick="deleteRosterScout('${esc(s.id)}')">Delete</button>
        </td>
      </tr>`).join("") + `</table>`
    : "<p>No scouts in roster. Add scouts above.</p>";
}
async function promptScoutPassword(rosterId, name) {
  const pw = prompt("Set password for " + name + " (min 4 characters):");
  if (!pw) return;
  if (pw.length < 4) { alert("Password must be at least 4 characters."); return; }
  try {
    const r = await authFetch(API + "/api/auth/scout-password/" + rosterId, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    if (r.ok) { loadRoster(); }
    else { const d = await r.json(); alert(d.detail || "Error setting password."); }
  } catch (err) { alert("Network error: " + err.message); }
}
async function clearScoutPassword(rosterId) {
  if (!confirm("Clear this scout's password? They won't be able to log in until a new one is set.")) return;
  try {
    const r = await authFetch(API + "/api/auth/scout-password/" + rosterId, { method: "DELETE" });
    if (r.ok) { loadRoster(); }
    else { const d = await r.json(); alert(d.detail || "Error clearing password."); }
  } catch (err) { alert("Network error: " + err.message); }
}
document.getElementById("roster-form").onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  await authFetch(API + "/api/scout/roster", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: fd.get("name"), scout_id: fd.get("scout_id") || null }),
  });
  e.target.reset();
  loadRoster();
  _visitRosterLoaded = false;
};
async function toggleRosterScout(id) {
  await authFetch(API + "/api/scout/roster/" + id, { method: "PATCH" });
  loadRoster();
  _visitRosterLoaded = false;
}
async function deleteRosterScout(id) {
  if (!confirm("Remove this scout from the roster?")) return;
  await authFetch(API + "/api/scout/roster/" + id, { method: "DELETE" });
  loadRoster();
  _visitRosterLoaded = false;
}

async function exportRosterCSV() {
  const r = await authFetch(API + "/api/scout/roster");
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
    const r = await authFetch(API + "/api/scout/roster/import", { method: "POST", body: fd });
    const data = await r.json();
    if (r.ok) {
      statusEl.textContent = `Done! ${data.added} scout(s) added, ${data.skipped} skipped (duplicates or empty).`;
      e.target.reset();
      loadRoster();
      _visitRosterLoaded = false;
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
  const r = await authFetch(API + "/api/events/");
  const events = await r.json();
  sel.innerHTML = '<option value="">All Events</option>' +
    events.map(e => `<option value="${esc(e.id)}">${esc(e.name)}</option>`).join("");
}

let _scoutDataCache = [];
async function loadScoutData() {
  document.getElementById("scout-summary").innerHTML = '<div class="loading-bar"></div>';
  document.getElementById("scout-data-list").innerHTML = '<div class="loading-bar"></div>';
  const eventId = document.getElementById("sd-event-filter").value;
  const params = eventId ? "?event_id=" + eventId : "";

  const [dataR, summaryR] = await Promise.all([
    authFetch(API + "/api/scout/data" + params),
    authFetch(API + "/api/scout/data/summary" + params),
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
        <td>${esc(s.scout_name)}</td>
        <td>${esc(s.scout_id) || "—"}</td>
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
        <td>${esc(v.scout_name)}</td>
        <td>${esc(v.address)}</td>
        <td>${esc(v.group_label) || "—"}</td>
        <td>${v.door_answer == null ? "—" : v.door_answer ? "Yes" : "No"}</td>
        <td>${v.donation_given == null ? "—" : v.donation_given ? "Yes" : "No"}</td>
        <td>${v.donation_amount ? "$" + v.donation_amount : "—"}</td>
        <td>${v.former_scout == null ? "—" : v.former_scout ? "Yes" : "No"}</td>
        <td>${v.avoid_house ? "YES" : "—"}</td>
        <td>${esc(v.notes)}</td>
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

// --- Scout Form Fields ---
let _formFields = [];

async function loadFormFields() {
  const el = document.getElementById("form-fields-list");
  el.innerHTML = '<p class="loading-text">Loading fields...</p>';
  try {
    const r = await authFetch(API + "/api/form-fields?include_inactive=true");
    if (!r.ok) { el.innerHTML = "<p>Failed to load fields.</p>"; return; }
    _formFields = await r.json();
    renderFormFields();
  } catch (err) {
    el.innerHTML = `<p>Error: ${esc(err.message)}</p>`;
  }
}

function renderFormFields() {
  const el = document.getElementById("form-fields-list");
  if (!_formFields.length) {
    el.innerHTML = "<p>No fields configured. Add one above.</p>";
    return;
  }

  const typeLabels = {
    toggle: "Toggle (Yes/No)", checkbox: "Checkbox", text: "Text",
    number: "Number", textarea: "Text Area", select: "Dropdown",
  };

  el.innerHTML = `<table>
    <thead><tr><th style="width:30px;"></th><th>Label</th><th>Key</th><th>Type</th><th>Required</th><th>Options</th><th></th></tr></thead>
    <tbody>${_formFields.map((f, i) => `<tr draggable="true" data-idx="${i}" data-id="${esc(f.id)}">
      <td style="cursor:grab;color:var(--sa-pale-gray);">&#x2630;</td>
      <td><strong>${esc(f.label)}</strong>${!f.active ? ' <span style="color:var(--sa-pale-gray);">(inactive)</span>' : ""}</td>
      <td style="font-size:12px;color:var(--sa-gray);font-family:monospace;">${esc(f.field_key)}</td>
      <td>${esc(typeLabels[f.field_type] || f.field_type)}</td>
      <td>${f.required ? "Yes" : "No"}</td>
      <td style="font-size:12px;">${f.options ? esc(f.options.join(", ")) : ""}</td>
      <td>
        <button class="btn-sm" onclick="toggleFormFieldRequired('${esc(f.id)}', ${!f.required})">${f.required ? "Optional" : "Required"}</button>
        ${f.active
          ? `<button class="btn-sm" onclick="toggleFormFieldActive('${esc(f.id)}', false)" style="margin-left:4px;">Disable</button>`
          : `<button class="btn-sm" onclick="toggleFormFieldActive('${esc(f.id)}', true)" style="margin-left:4px;">Enable</button>`}
        <button class="btn-sm btn-danger" onclick="deleteFormField('${esc(f.id)}', '${esc(f.label)}')" style="margin-left:4px;">Remove</button>
      </td>
    </tr>`).join("")}</tbody>
  </table>`;

  // Drag-and-drop reorder
  const tbody = el.querySelector("tbody");
  let dragRow = null;
  tbody.querySelectorAll("tr").forEach(row => {
    row.addEventListener("dragstart", (e) => { dragRow = row; row.style.opacity = ".4"; });
    row.addEventListener("dragend", () => { row.style.opacity = ""; });
    row.addEventListener("dragover", (e) => { e.preventDefault(); });
    row.addEventListener("drop", (e) => {
      e.preventDefault();
      if (dragRow && dragRow !== row) {
        const allRows = [...tbody.querySelectorAll("tr")];
        const fromIdx = allRows.indexOf(dragRow);
        const toIdx = allRows.indexOf(row);
        if (fromIdx < toIdx) row.after(dragRow);
        else row.before(dragRow);
        saveFieldOrder();
      }
    });
  });
}

async function saveFieldOrder() {
  const rows = document.querySelectorAll("#form-fields-list tbody tr");
  const ids = [...rows].map(r => r.dataset.id);
  try {
    await authFetch(API + "/api/form-fields/reorder/batch", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ field_ids: ids }),
    });
  } catch { /* silent */ }
}

async function toggleFormFieldRequired(id, required) {
  await authFetch(API + `/api/form-fields/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ required }),
  });
  loadFormFields();
}

async function toggleFormFieldActive(id, active) {
  await authFetch(API + `/api/form-fields/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ active }),
  });
  loadFormFields();
}

async function deleteFormField(id, label) {
  if (!confirm(`Remove "${label}"? Previously collected data for this field will be preserved.`)) return;
  await authFetch(API + `/api/form-fields/${id}`, { method: "DELETE" });
  loadFormFields();
}

document.getElementById("form-field-create").onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const label = fd.get("label").trim();
  const field_type = fd.get("field_type");
  const required = !!fd.get("required");
  const optionsRaw = fd.get("options").trim();
  const options = field_type === "select" && optionsRaw
    ? optionsRaw.split(",").map(o => o.trim()).filter(Boolean)
    : null;

  if (!label) return;
  if (field_type === "select" && (!options || !options.length)) {
    alert("Dropdown fields require at least one option (comma-separated).");
    return;
  }

  try {
    const r = await authFetch(API + "/api/form-fields", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label, field_type, required, options }),
    });
    if (r.ok) {
      e.target.reset();
      loadFormFields();
    } else {
      const data = await r.json().catch(() => ({}));
      alert(data.detail || "Failed to create field.");
    }
  } catch (err) {
    alert("Error: " + err.message);
  }
};

// --- Init ---
_checkAuth().then(() => {
  if (_authToken) loadDashboard();
});
