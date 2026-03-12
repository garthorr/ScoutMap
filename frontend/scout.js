const API = "";
function esc(s) {
  if (s == null) return "";
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
let scoutName = "";
let scoutIdNum = "";
let selectedEventId = "";
let selectedGroupLabel = "";
let houses = [];
let currentHouseIdx = -1;
let formState = {};
let rosterData = [];
let eventsData = [];
let formFields = []; // dynamic field config from server

// --- Auth ---
let _authToken = localStorage.getItem("scoutmap_token") || "";
let _loginRoster = [];

function authFetch(url, opts = {}) {
  opts.headers = opts.headers || {};
  if (_authToken) opts.headers["Authorization"] = "Bearer " + _authToken;
  return fetch(url, opts);
}

async function _checkAuth() {
  if (!_authToken) { _showLoginOverlay(); return; }
  try {
    const r = await authFetch(API + "/api/auth/me");
    if (r.ok) { _hideLoginOverlay(); }
    else { _authToken = ""; localStorage.removeItem("scoutmap_token"); _showLoginOverlay(); }
  } catch { _showLoginOverlay(); }
}

function _showLoginOverlay() {
  document.getElementById("login-overlay").classList.add("active");
  document.querySelector("header").style.display = "none";
  document.querySelector(".container").style.display = "none";
  _loadLoginRoster();
}
function _hideLoginOverlay() {
  document.getElementById("login-overlay").classList.remove("active");
  document.querySelector("header").style.display = "";
  document.querySelector(".container").style.display = "";
}

async function _loadLoginRoster() {
  const sel = document.getElementById("login-scout-select");
  try {
    const r = await fetch(API + "/api/auth/scout-roster");
    _loginRoster = await r.json();
  } catch {
    _loginRoster = [];
  }
  if (_loginRoster.length) {
    sel.innerHTML = '<option value="">Select your name...</option>' +
      _loginRoster.map(s => `<option value="${esc(s.id)}">${esc(s.name)}${s.scout_id ? " (" + esc(s.scout_id) + ")" : ""}</option>`).join("");
  } else {
    sel.innerHTML = '<option value="">No scouts available</option>';
  }
}

function showAdminLogin() {
  document.getElementById("login-step-scout").style.display = "none";
  document.getElementById("login-step-admin").style.display = "";
}
function showScoutLogin() {
  document.getElementById("login-step-scout").style.display = "";
  document.getElementById("login-step-admin").style.display = "none";
}

async function scoutPasswordLogin() {
  const scoutId = document.getElementById("login-scout-select").value;
  const password = document.getElementById("login-scout-password").value;
  const errEl = document.getElementById("login-scout-error");
  errEl.style.display = "none";

  if (!scoutId) { errEl.textContent = "Select your name."; errEl.style.display = ""; return; }
  if (!password) { errEl.textContent = "Enter your password."; errEl.style.display = ""; return; }

  const btn = document.getElementById("login-scout-btn");
  btn.disabled = true; btn.textContent = "Signing in…";
  try {
    const r = await fetch(API + "/api/auth/scout-login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scout_id: scoutId, password }),
    });
    const data = await r.json();
    if (r.ok && data.token) {
      _authToken = data.token;
      localStorage.setItem("scoutmap_token", _authToken);
      scoutName = data.scout_name;
      scoutIdNum = data.scout_id || "";
      localStorage.setItem("scoutmap_scout", JSON.stringify({
        name: data.scout_name, id: data.scout_id || "", roster_id: data.roster_id
      }));
      _hideLoginOverlay();
      loadRoster(); loadEvents(); loadFormFieldConfig();
    } else {
      errEl.textContent = data.detail || "Invalid credentials."; errEl.style.display = "";
    }
  } catch (err) {
    errEl.textContent = "Network error: " + err.message; errEl.style.display = "";
  }
  btn.disabled = false; btn.textContent = "Sign In";
}

async function scoutAdminLogin() {
  const pw = document.getElementById("login-admin-pw").value;
  const errEl = document.getElementById("login-admin-error");
  errEl.style.display = "none";
  if (!pw) { errEl.textContent = "Enter the admin password."; errEl.style.display = ""; return; }

  const btn = document.getElementById("login-admin-btn");
  btn.disabled = true; btn.textContent = "Signing in…";
  try {
    const r = await fetch(API + "/api/auth/admin-login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    const data = await r.json();
    if (r.ok && data.token) {
      _authToken = data.token;
      localStorage.setItem("scoutmap_token", _authToken);
      _hideLoginOverlay();
      loadRoster(); loadEvents(); loadFormFieldConfig();
    } else {
      errEl.textContent = data.detail || "Incorrect password."; errEl.style.display = "";
    }
  } catch (err) {
    errEl.textContent = "Network error: " + err.message; errEl.style.display = "";
  }
  btn.disabled = false; btn.textContent = "Sign In";
}

document.getElementById("login-scout-password").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); scoutPasswordLogin(); }
});
document.getElementById("login-admin-pw").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); scoutAdminLogin(); }
});

// --- Screen management ---
function showScreen(id) {
  document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
  document.getElementById(id).classList.add("active");
}

// --- Restore saved scout selection ---
function restoreScoutInfo() {
  const saved = localStorage.getItem("scoutmap_scout");
  if (saved) {
    try {
      const data = JSON.parse(saved);
      scoutName = data.name || "";
      scoutIdNum = data.id || "";
    } catch {}  // eslint-disable-line no-empty
  }
  updateBadge();
}

function updateBadge() {
  const badge = document.getElementById("scout-info-badge");
  const logoutBtn = document.getElementById("logout-btn");
  badge.textContent = scoutName || "";
  logoutBtn.style.display = scoutName ? "" : "none";
}

function saveScoutInfo() {
  const sel = document.getElementById("scout-select");
  const val = sel.value;

  if (val === "__other__") {
    scoutName = document.getElementById("other-name").value.trim();
    scoutIdNum = document.getElementById("other-id").value.trim();
  } else if (val) {
    const scout = rosterData.find(s => s.id === val);
    if (scout) {
      scoutName = scout.name;
      scoutIdNum = scout.scout_id || "";
    }
  }

  localStorage.setItem("scoutmap_scout", JSON.stringify({ name: scoutName, id: scoutIdNum, roster_id: val }));
  updateBadge();
}

// --- Logout ---
function scoutLogout() {
  try { authFetch(API + "/api/auth/logout", { method: "POST" }); } catch { /* ok */ }
  _authToken = "";
  localStorage.removeItem("scoutmap_token");
  localStorage.removeItem("scoutmap_scout");
  scoutName = "";
  scoutIdNum = "";
  selectedEventId = "";
  selectedGroupLabel = "";
  houses = [];
  document.getElementById("scout-select").value = "";
  document.getElementById("other-name-wrap").classList.remove("visible");
  document.getElementById("other-name").value = "";
  document.getElementById("other-id").value = "";
  document.getElementById("event-select").value = "";
  document.getElementById("group-select").innerHTML = '<option value="">Select event first</option>';
  updateBadge();
  checkReady();
  _showLoginOverlay();
}

function backToSetup() {
  showScreen("setup-screen");
}

// --- Load roster for dropdown ---
async function loadRoster() {
  const sel = document.getElementById("scout-select");
  try {
    const r = await authFetch(API + "/api/scout/roster?active_only=true");
    rosterData = await r.json();
  } catch {
    rosterData = [];
  }

  sel.innerHTML = '<option value="">Select your name...</option>' +
    rosterData.map(s => `<option value="${esc(s.id)}">${esc(s.name)}${s.scout_id ? " (" + esc(s.scout_id) + ")" : ""}</option>`).join("") +
    '<option value="__other__">Other (write in)</option>';

  const saved = localStorage.getItem("scoutmap_scout");
  if (saved) {
    try {
      const data = JSON.parse(saved);
      if (data.roster_id && data.roster_id !== "__other__") {
        sel.value = data.roster_id;
      } else if (data.name) {
        sel.value = "__other__";
        document.getElementById("other-name-wrap").classList.add("visible");
        document.getElementById("other-name").value = data.name || "";
        document.getElementById("other-id").value = data.id || "";
      }
    } catch {}  // eslint-disable-line no-empty
  }
  checkReady();
}

document.getElementById("scout-select").onchange = (e) => {
  const wrap = document.getElementById("other-name-wrap");
  if (e.target.value === "__other__") {
    wrap.classList.add("visible");
  } else {
    wrap.classList.remove("visible");
  }
  checkReady();
};
document.getElementById("other-name").oninput = checkReady;

// --- Load events and groups ---
async function loadEvents() {
  const sel = document.getElementById("event-select");
  try {
    const r = await authFetch(API + "/api/scout/events");
    eventsData = await r.json();
    if (!eventsData.length) {
      sel.innerHTML = '<option value="">No events available</option>';
      return;
    }
    sel.innerHTML = '<option value="">Select an event...</option>' +
      eventsData.map(ev => `<option value="${esc(ev.id)}">${esc(ev.name)}</option>`).join("");
  } catch {
    sel.innerHTML = '<option value="">Failed to load</option>';
  }
}

document.getElementById("event-select").onchange = (e) => {
  selectedEventId = e.target.value;
  const groupSel = document.getElementById("group-select");

  if (!selectedEventId) {
    groupSel.innerHTML = '<option value="">Select event first</option>';
    checkReady();
    return;
  }

  const ev = eventsData.find(ev => ev.id === selectedEventId);
  const groups = ev ? ev.groups : [];
  if (!groups.length) {
    groupSel.innerHTML = '<option value="">No walk groups</option>';
  } else {
    groupSel.innerHTML = '<option value="">Select a group...</option>' +
      groups.map(g => `<option value="${g}">${g}</option>`).join("");
  }
  checkReady();
};

document.getElementById("group-select").onchange = () => {
  selectedGroupLabel = document.getElementById("group-select").value;
  checkReady();
};

function checkReady() {
  const sel = document.getElementById("scout-select").value;
  let nameOk = false;
  if (sel === "__other__") {
    nameOk = !!document.getElementById("other-name").value.trim();
  } else {
    nameOk = !!sel;
  }
  const group = document.getElementById("group-select").value;
  document.getElementById("start-btn").disabled = !(nameOk && group);
  document.getElementById("start-error").style.display = "none";
}

// --- Load form field config ---
async function loadFormFieldConfig() {
  try {
    const r = await authFetch(API + "/api/form-fields");
    if (r.ok) formFields = await r.json();
  } catch {
    formFields = [];
  }
}

// --- Start walking ---
document.getElementById("start-btn").onclick = async () => {
  saveScoutInfo();
  selectedGroupLabel = document.getElementById("group-select").value;
  const errEl = document.getElementById("start-error");
  const btn = document.getElementById("start-btn");

  if (!scoutName) {
    errEl.textContent = "Please enter your name.";
    errEl.style.display = "block";
    return;
  }
  if (!selectedEventId || !selectedGroupLabel) {
    errEl.textContent = "Please select an event and walk group.";
    errEl.style.display = "block";
    return;
  }

  btn.disabled = true;
  btn.textContent = "Loading...";
  errEl.style.display = "none";

  try {
    const params = new URLSearchParams({ group: selectedGroupLabel });
    const r = await authFetch(API + `/api/scout/events/${selectedEventId}/houses?${params}`);
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(data.detail || `Server error (${r.status})`);
    }
    houses = await r.json();
    if (!houses.length) {
      errEl.textContent = "No houses found in this group.";
      errEl.style.display = "block";
      btn.disabled = false;
      btn.textContent = "Start Walking";
      return;
    }

    document.getElementById("group-title").textContent = selectedGroupLabel;
    renderHouseList();
    showScreen("house-list-screen");
  } catch (err) {
    errEl.textContent = "Error: " + err.message;
    errEl.style.display = "block";
  }

  btn.disabled = false;
  btn.textContent = "Start Walking";
};

// --- Render house list ---
function renderHouseList() {
  const el = document.getElementById("house-list");
  const visited = houses.filter(h => h.visited).length;
  const total = houses.length;

  document.getElementById("progress-fill").style.width = total ? (visited / total * 100) + "%" : "0%";
  document.getElementById("progress-text").textContent = `${visited} / ${total} visited`;

  el.innerHTML = houses.map((h, idx) => {
    const numClass = h.last_visit?.avoid_house ? "avoid" : h.visited ? "done" : "";
    let statusText = "Tap to record visit";
    if (h.visited && h.last_visit) {
      const parts = [];
      if (h.last_visit.door_answer === false) parts.push("No answer");
      if (h.last_visit.donation_given) parts.push("$" + (h.last_visit.donation_amount || 0));
      if (h.last_visit.avoid_house) parts.push("AVOID");
      statusText = parts.length ? parts.join(" | ") : "Visited";
    }
    return `<div class="house-item" onclick="openHouseForm(${idx})">
      <div class="house-num ${numClass}">${idx + 1}</div>
      <div class="house-details">
        <div class="house-addr">${esc(h.address)}</div>
        <div class="house-status">${esc(statusText)}</div>
      </div>
      <div class="house-arrow">&rsaquo;</div>
    </div>`;
  }).join("");
}

// --- Dynamic form rendering ---
function renderDynamicForm(lastVisit) {
  const container = document.getElementById("dynamic-form-fields");
  formState = {};

  if (!formFields.length) {
    container.innerHTML = '<p style="color:var(--sa-pale-gray);">No form fields configured.</p>';
    return;
  }

  let html = "";
  for (const f of formFields) {
    const key = f.field_key;
    const reqMark = f.required ? ' <span style="color:var(--sa-red);">*</span>' : "";

    // Get previous value from last_visit (check legacy columns first, then custom_data)
    let prevVal = null;
    if (lastVisit) {
      if (key in lastVisit) {
        prevVal = lastVisit[key];
      } else if (lastVisit.custom_data && key in lastVisit.custom_data) {
        prevVal = lastVisit.custom_data[key];
      }
    }

    if (f.field_type === "toggle") {
      formState[key] = prevVal != null ? prevVal : null;
      html += `<div class="field">
        <label>${esc(f.label)}${reqMark}</label>
        <div class="toggle-group" id="toggle-${esc(key)}">
          <button type="button" class="toggle-btn" data-val="true" onclick="setToggle('${esc(key)}',true)">Yes</button>
          <button type="button" class="toggle-btn" data-val="false" onclick="setToggle('${esc(key)}',false)">No</button>
        </div>
      </div>`;
    } else if (f.field_type === "checkbox") {
      const checked = prevVal ? "checked" : "";
      formState[key] = !!prevVal;
      html += `<div class="checkbox-field">
        <input type="checkbox" id="field-${esc(key)}" ${checked} onchange="formState['${esc(key)}']=this.checked" />
        <label for="field-${esc(key)}">${esc(f.label)}${reqMark}</label>
      </div>`;
    } else if (f.field_type === "number") {
      formState[key] = prevVal || null;
      html += `<div class="field">
        <label for="field-${esc(key)}">${esc(f.label)}${reqMark}</label>
        <input id="field-${esc(key)}" type="number" step="any" min="0" placeholder="${esc(f.label)}" value="${prevVal != null ? esc(prevVal) : ""}" oninput="formState['${esc(key)}']=this.value?parseFloat(this.value):null" />
      </div>`;
    } else if (f.field_type === "text") {
      formState[key] = prevVal || "";
      html += `<div class="field">
        <label for="field-${esc(key)}">${esc(f.label)}${reqMark}</label>
        <input id="field-${esc(key)}" type="text" placeholder="${esc(f.label)}" value="${esc(prevVal || "")}" oninput="formState['${esc(key)}']=this.value" />
      </div>`;
    } else if (f.field_type === "textarea") {
      formState[key] = prevVal || "";
      html += `<div class="field">
        <label for="field-${esc(key)}">${esc(f.label)}${reqMark}</label>
        <textarea id="field-${esc(key)}" placeholder="${esc(f.label)}" oninput="formState['${esc(key)}']=this.value">${esc(prevVal || "")}</textarea>
      </div>`;
    } else if (f.field_type === "select") {
      formState[key] = prevVal || "";
      const opts = f.options || [];
      html += `<div class="field">
        <label for="field-${esc(key)}">${esc(f.label)}${reqMark}</label>
        <select id="field-${esc(key)}" onchange="formState['${esc(key)}']=this.value">
          <option value="">—</option>
          ${opts.map(o => `<option value="${esc(o)}" ${prevVal === o ? "selected" : ""}>${esc(o)}</option>`).join("")}
        </select>
      </div>`;
    }
  }

  container.innerHTML = html;

  // Highlight toggle buttons for pre-filled values
  for (const f of formFields) {
    if (f.field_type === "toggle" && formState[f.field_key] != null) {
      highlightToggle(f.field_key, formState[f.field_key]);
    }
  }
}

// --- Open house form ---
function openHouseForm(idx) {
  currentHouseIdx = idx;
  const h = houses[idx];
  document.getElementById("form-house-addr").textContent = h.address;
  renderDynamicForm(h.last_visit);
  showScreen("house-form-screen");
}

// --- Toggle buttons ---
function setToggle(key, val) {
  formState[key] = val;
  highlightToggle(key, val);
}

function highlightToggle(key, val) {
  const group = document.getElementById("toggle-" + key);
  if (!group) return;
  group.querySelectorAll(".toggle-btn").forEach(btn => {
    btn.classList.remove("active-yes", "active-no");
    const btnVal = btn.dataset.val === "true";
    if (btnVal === val) {
      btn.classList.add(val ? "active-yes" : "active-no");
    }
  });
}

function cancelForm() {
  showScreen("house-list-screen");
}

// --- Save visit ---
async function saveVisit() {
  // Validate required fields
  for (const f of formFields) {
    if (f.required) {
      const val = formState[f.field_key];
      if (val == null || val === "" || val === false) {
        alert(`"${f.label}" is required.`);
        return;
      }
    }
  }

  const h = houses[currentHouseIdx];
  const btn = document.getElementById("save-visit-btn");
  btn.disabled = true;
  btn.textContent = "Saving...";

  // Map known legacy columns from formState
  const legacyKeys = ["door_answer", "donation_given", "donation_amount", "former_scout", "avoid_house", "notes"];
  const body = {
    scout_name: scoutName,
    scout_id: scoutIdNum || null,
    custom_data: {},
  };

  // Populate legacy columns and custom_data
  for (const f of formFields) {
    const val = formState[f.field_key];
    if (legacyKeys.includes(f.field_key)) {
      body[f.field_key] = val;
    }
    // Always store in custom_data for consistency
    body.custom_data[f.field_key] = val;
  }

  // Derive outcome from known fields
  if (body.door_answer === false) body.outcome = "not_home";
  else if (body.donation_given) body.outcome = "donated";
  else body.outcome = "other";

  try {
    const r = await authFetch(API + `/api/events/${h.event_id}/houses/${h.event_house_id}/visits`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (r.ok) {
      h.visited = true;
      h.status = "visited";
      // Reconstruct last_visit from formState for display
      h.last_visit = { ...body, custom_data: body.custom_data };
      renderHouseList();
      showScreen("house-list-screen");
    } else {
      alert("Failed to save. Please try again.");
    }
  } catch {
    alert("Network error. Check your connection and try again.");
  }

  btn.disabled = false;
  btn.textContent = "Save";
}

// --- Init ---
_checkAuth().then(() => {
  if (_authToken) {
    restoreScoutInfo();
    loadRoster();
    loadEvents();
    loadFormFieldConfig();
  }
});
