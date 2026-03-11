const API = "";
let scoutName = "";
let scoutIdNum = "";
let selectedEventId = "";
let selectedGroupLabel = "";
let houses = [];
let currentHouseIdx = -1;
let formState = { door: null, donation: null, former: null };
let rosterData = [];
let eventsData = [];

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
      // Selection restored after roster loads
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
  showScreen("setup-screen");
}

function backToSetup() {
  showScreen("setup-screen");
}

// --- Load roster for dropdown ---
async function loadRoster() {
  const sel = document.getElementById("scout-select");
  try {
    const r = await fetch(API + "/api/scout/roster?active_only=true");
    rosterData = await r.json();
  } catch {
    rosterData = [];
  }

  sel.innerHTML = '<option value="">Select your name...</option>' +
    rosterData.map(s => `<option value="${s.id}">${s.name}${s.scout_id ? " (" + s.scout_id + ")" : ""}</option>`).join("") +
    '<option value="__other__">Other (write in)</option>';

  // Restore previous selection
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

// Handle scout dropdown change
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
    const r = await fetch(API + "/api/scout/events");
    eventsData = await r.json();
    if (!eventsData.length) {
      sel.innerHTML = '<option value="">No events available</option>';
      return;
    }
    sel.innerHTML = '<option value="">Select an event...</option>' +
      eventsData.map(ev => `<option value="${ev.id}">${ev.name}</option>`).join("");
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
    const r = await fetch(API + `/api/scout/events/${selectedEventId}/houses?${params}`);
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
        <div class="house-addr">${h.address}</div>
        <div class="house-status">${statusText}</div>
      </div>
      <div class="house-arrow">&rsaquo;</div>
    </div>`;
  }).join("");
}

// --- Open house form ---
function openHouseForm(idx) {
  currentHouseIdx = idx;
  const h = houses[idx];
  document.getElementById("form-house-addr").textContent = h.address;

  formState = { door: null, donation: null, former: null };
  document.getElementById("donation-amount").value = "";
  document.getElementById("avoid-house").checked = false;
  document.getElementById("visit-notes").value = "";
  document.getElementById("donation-amount-wrap").classList.remove("visible");

  if (h.last_visit) {
    if (h.last_visit.door_answer != null) setToggle("door", h.last_visit.door_answer);
    if (h.last_visit.donation_given != null) setToggle("donation", h.last_visit.donation_given);
    if (h.last_visit.former_scout != null) setToggle("former", h.last_visit.former_scout);
    if (h.last_visit.donation_amount) document.getElementById("donation-amount").value = h.last_visit.donation_amount;
    if (h.last_visit.avoid_house) document.getElementById("avoid-house").checked = true;
    if (h.last_visit.notes) document.getElementById("visit-notes").value = h.last_visit.notes;
  }

  resetToggleUI();
  showScreen("house-form-screen");
}

function resetToggleUI() {
  ["door", "donation", "former"].forEach(key => {
    const group = document.getElementById("toggle-" + key);
    group.querySelectorAll(".toggle-btn").forEach(btn => {
      btn.classList.remove("active-yes", "active-no");
    });
    if (formState[key] != null) {
      highlightToggle(key, formState[key]);
    }
  });
}

// --- Toggle buttons ---
function setToggle(key, val) {
  formState[key] = val;
  highlightToggle(key, val);
  if (key === "donation") {
    document.getElementById("donation-amount-wrap").classList.toggle("visible", val === true);
    if (!val) document.getElementById("donation-amount").value = "";
  }
}

function highlightToggle(key, val) {
  const group = document.getElementById("toggle-" + key);
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
  const h = houses[currentHouseIdx];
  const btn = document.getElementById("save-visit-btn");
  btn.disabled = true;
  btn.textContent = "Saving...";

  const body = {
    outcome: formState.door === false ? "not_home" : (formState.donation ? "donated" : "other"),
    door_answer: formState.door,
    donation_given: formState.donation,
    donation_amount: formState.donation ? (parseFloat(document.getElementById("donation-amount").value) || null) : null,
    former_scout: formState.former,
    avoid_house: document.getElementById("avoid-house").checked,
    notes: document.getElementById("visit-notes").value.trim() || null,
    scout_name: scoutName,
    scout_id: scoutIdNum || null,
  };

  try {
    const r = await fetch(API + `/api/events/${h.event_id}/houses/${h.event_house_id}/visits`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (r.ok) {
      h.visited = true;
      h.status = "visited";
      h.last_visit = {
        door_answer: body.door_answer,
        donation_given: body.donation_given,
        donation_amount: body.donation_amount,
        former_scout: body.former_scout,
        avoid_house: body.avoid_house,
        notes: body.notes,
      };
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
restoreScoutInfo();
loadRoster();
loadEvents();
