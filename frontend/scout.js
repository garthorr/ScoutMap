const API = "";
let scoutName = "";
let scoutIdNum = "";
let selectedEventId = "";
let selectedGroupLabel = "";
let houses = [];
let currentHouseIdx = -1;
let formState = { door: null, donation: null, former: null };

// --- Screen management ---
function showScreen(id) {
  document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
  document.getElementById(id).classList.add("active");
}

// --- Restore saved scout info ---
function restoreScoutInfo() {
  const saved = localStorage.getItem("scoutmap_scout");
  if (saved) {
    try {
      const data = JSON.parse(saved);
      document.getElementById("scout-name").value = data.name || "";
      document.getElementById("scout-id-num").value = data.id || "";
    } catch {}  // eslint-disable-line no-empty
  }
}

function saveScoutInfo() {
  scoutName = document.getElementById("scout-name").value.trim();
  scoutIdNum = document.getElementById("scout-id-num").value.trim();
  localStorage.setItem("scoutmap_scout", JSON.stringify({ name: scoutName, id: scoutIdNum }));
  const badge = document.getElementById("scout-info-badge");
  badge.textContent = scoutName || "";
}

// --- Load events and groups ---
async function loadEvents() {
  const sel = document.getElementById("event-select");
  try {
    const r = await fetch(API + "/api/scout/events");
    const events = await r.json();
    if (!events.length) {
      sel.innerHTML = '<option value="">No events available</option>';
      return;
    }
    sel.innerHTML = '<option value="">Select an event...</option>' +
      events.map(ev => `<option value="${ev.id}" data-groups='${JSON.stringify(ev.groups)}'>${ev.name}</option>`).join("");
  } catch {
    sel.innerHTML = '<option value="">Failed to load</option>';
  }
}

document.getElementById("event-select").onchange = (e) => {
  const opt = e.target.options[e.target.selectedIndex];
  selectedEventId = e.target.value;
  const groupSel = document.getElementById("group-select");

  if (!selectedEventId) {
    groupSel.innerHTML = '<option value="">Select event first</option>';
    checkReady();
    return;
  }

  const groups = JSON.parse(opt.dataset.groups || "[]");
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
  const name = document.getElementById("scout-name").value.trim();
  const group = document.getElementById("group-select").value;
  document.getElementById("start-btn").disabled = !(name && group);
}
document.getElementById("scout-name").oninput = checkReady;
document.getElementById("scout-id-num").oninput = checkReady;

// --- Start walking ---
document.getElementById("start-btn").onclick = async () => {
  saveScoutInfo();
  selectedGroupLabel = document.getElementById("group-select").value;

  const r = await fetch(API + `/api/scout/events/${selectedEventId}/groups/${encodeURIComponent(selectedGroupLabel)}/houses`);
  houses = await r.json();

  document.getElementById("group-title").textContent = selectedGroupLabel;
  renderHouseList();
  showScreen("house-list-screen");
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

  // Reset form state
  formState = { door: null, donation: null, former: null };
  document.getElementById("donation-amount").value = "";
  document.getElementById("avoid-house").checked = false;
  document.getElementById("visit-notes").value = "";
  document.getElementById("donation-amount-wrap").classList.remove("visible");

  // Pre-fill if previously visited
  if (h.last_visit) {
    if (h.last_visit.door_answer != null) setToggle("door", h.last_visit.door_answer);
    if (h.last_visit.donation_given != null) setToggle("donation", h.last_visit.donation_given);
    if (h.last_visit.former_scout != null) setToggle("former", h.last_visit.former_scout);
    if (h.last_visit.donation_amount) document.getElementById("donation-amount").value = h.last_visit.donation_amount;
    if (h.last_visit.avoid_house) document.getElementById("avoid-house").checked = true;
    if (h.last_visit.notes) document.getElementById("visit-notes").value = h.last_visit.notes;
  }

  // Clear toggle visuals for untouched fields
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
      // Update local state
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
loadEvents();
