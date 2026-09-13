const $ = (id) => document.getElementById(id);
let session = null;
const pendingFiles = [];
const GAUGE_C = 2 * Math.PI * 32;

const ROLES = [
  ["setup_photo", "Setup photo"],
  ["result_image", "Result / failure photo"],
  ["cad", "CAD / drawing"],
  ["sensor", "Sensor / table"],
  ["log", "Log / console"],
  ["material_doc", "Material cert"],
  ["process_doc", "Process sheet"],
  ["datasheet", "Datasheet"],
  ["other", "Other"],
];

const ROLE_LABEL = Object.fromEntries(ROLES);

function guessRole(name) {
  const n = name.toLowerCase();
  if (/\.(stl|step|stp|iges|dxf)$/.test(n) || /cad|fixture/.test(n)) return "cad";
  if (/\.(csv|tsv|json)$/.test(n) || /sensor|telemetry/.test(n)) return "sensor";
  if (/\.(log|txt|out)$/.test(n)) return "log";
  if (/setup|bench|rig/.test(n)) return "setup_photo";
  if (/crack|fail|gel|burn|corrosion|result/.test(n)) return "result_image";
  if (/\.(png|jpe?g|webp|gif|tif)$/.test(n)) return "setup_photo";
  return "other";
}

function formatBytes(n) {
  if (!Number.isFinite(n)) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

function setBusy(on, message) {
  $("busy").hidden = !on;
  if (message) {
    const title = $("busy").querySelector("strong");
    if (title) title.textContent = message;
  }
}

function updateFileCount() {
  const n = pendingFiles.length;
  $("fileCount").textContent = n === 0 ? "No files yet" : `${n} file${n === 1 ? "" : "s"} ready`;
}

function renderFiles() {
  $("fileList").innerHTML = pendingFiles.map((item, i) => {
    const options = ROLES.map(([id, label]) =>
      `<option value="${id}" ${item.role === id ? "selected" : ""}>${label}</option>`
    ).join("");
    const ext = (item.file.name.split(".").pop() || "file").slice(0, 5);
    const thumb = item.preview
      ? `<img alt="" src="${item.preview}" />`
      : `<div class="thumb">${escapeHtml(ext)}</div>`;
    return `<div class="file-card">
      ${thumb}
      <div class="file-meta">
        <strong title="${escapeHtml(item.file.name)}">${escapeHtml(item.file.name)}</strong>
        <div class="file-size">${formatBytes(item.file.size)}</div>
        <select data-i="${i}" class="role" aria-label="File role">${options}</select>
        <input data-i="${i}" class="cap" placeholder="Caption (what are we looking at?)" value="${escapeHtml(item.caption)}" />
      </div>
      <button type="button" data-remove="${i}" class="ghost">Remove</button>
    </div>`;
  }).join("");
  updateFileCount();

  $("fileList").querySelectorAll(".role").forEach((el) => {
    el.addEventListener("change", (e) => { pendingFiles[Number(e.target.dataset.i)].role = e.target.value; });
  });
  $("fileList").querySelectorAll(".cap").forEach((el) => {
    el.addEventListener("input", (e) => { pendingFiles[Number(e.target.dataset.i)].caption = e.target.value; });
  });
  $("fileList").querySelectorAll("[data-remove]").forEach((el) => {
    el.addEventListener("click", () => {
      const idx = Number(el.dataset.remove);
      if (pendingFiles[idx].preview) URL.revokeObjectURL(pendingFiles[idx].preview);
      pendingFiles.splice(idx, 1);
      renderFiles();
    });
  });
}

function addFiles(fileList) {
  Array.from(fileList || []).forEach((file) => {
    const preview = file.type.startsWith("image/") ? URL.createObjectURL(file) : null;
    pendingFiles.push({ file, role: guessRole(file.name), caption: "", preview });
  });
  renderFiles();
}

function setGauge(conf) {
  const arc = $("confidenceArc");
  const offset = conf == null ? GAUGE_C : GAUGE_C * (1 - Math.max(0, Math.min(1, conf)));
  arc.style.strokeDasharray = String(GAUGE_C);
  arc.style.strokeDashoffset = String(offset);
}

function renderSession(data) {
  session = data;
  $("emptyState").hidden = true;
  setBusy(false);
  $("result").hidden = false;
  clearError();
  const d = data.diagnosis || {};
  const artifacts = data.experiment?.artifacts || [];
  const u = d.uncertainty || {};
  $("caseMeta").textContent = `${data.case_id || "your experiment"} · ${d.engine_mode || "heuristic"} · ${artifacts.length} file(s) · session ${data.session_id}`;
  $("leadTitle").textContent = data.title || "Diagnosis";
  $("leadCause").textContent = d.leading_cause || "No leading cause yet — competing hypotheses are listed below.";
  const conf = u.confidence;
  $("confidenceValue").textContent = conf == null ? "—" : `${Math.round(conf * 100)}%`;
  $("uncertaintyNote").textContent = u.note || "";
  $("entropyValue").textContent = u.entropy == null ? "—" : Number(u.entropy).toFixed(2);
  const activeCount = (d.hypotheses || []).filter((h) => h.status === "active").length;
  $("competingCount").textContent = u.competing_count != null ? String(u.competing_count) : String(activeCount);
  setGauge(conf);

  $("ingested").innerHTML = artifacts.length
    ? artifacts.map((a) => {
      const role = ROLE_LABEL[a.role] || a.role || "file";
      return `<span class="chip"><b>${escapeHtml(role)}</b> · ${escapeHtml(a.filename)}</span>`;
    }).join("")
    : "<span class='muted'>Notes only — no files attached.</span>";

  $("anomalies").innerHTML = (d.anomalies || []).map((a) => `<li>${escapeHtml(a.description)}</li>`).join("") || "<li>None flagged</li>";
  $("missing").innerHTML = (u.missing_information || []).map((m) => `<li>${escapeHtml(m)}</li>`).join("") || "<li>None listed</li>";
  $("chain").innerHTML = (d.leading_causal_chain || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("")
    || "<li class='muted'>No chain reconstructed yet.</li>";
  $("intervention").textContent = d.recommended_intervention?.description || "No intervention yet";
  const predTrue = d.recommended_intervention?.predicted_if_true || "";
  const predFalse = d.recommended_intervention?.predicted_if_false || "";
  $("interventionPred").textContent = [predTrue && `If true: ${predTrue}`, predFalse && `If false: ${predFalse}`].filter(Boolean).join(" ");

  const hyps = d.hypotheses || [];
  const leadId = hyps.find((h) => h.status === "active")?.id;
  $("hypSummary").textContent = hyps.length
    ? `${hyps.length} hypotheses · ${activeCount} still active. The lead stays provisional until evidence closes the rest.`
    : "No hypotheses yet.";

  $("hypotheses").innerHTML = hyps.map((h, i) => {
    const width = Math.round((h.posterior || h.score || 0) * 100);
    const support = (h.supporting_evidence || []).map((e) => `<li class="ok">${escapeHtml(e.statement)}</li>`).join("") || "<li class='muted'>None cited</li>";
    const against = (h.contradictory_evidence || []).map((e) => `<li class="bad">${escapeHtml(e.statement)}</li>`).join("") || "<li class='muted'>None cited</li>";
    const isLead = h.id === leadId && h.status === "active";
    const cls = ["hyp", isLead ? "is-lead" : "", h.status === "rejected" ? "is-rejected" : ""].filter(Boolean).join(" ");
    const statusTag = h.status === "rejected"
      ? `<span class="tag rejected">Rejected</span>`
      : h.status === "confirmed"
        ? `<span class="tag confirmed">Confirmed</span>`
        : isLead
          ? `<span class="tag lead">Lead</span>`
          : `<span class="tag">${escapeHtml(h.status)}</span>`;
    const reason = h.rejected_reason
      ? `<p class="muted">Rejected: ${escapeHtml(h.rejected_reason)}</p>`
      : "";
    return `<div class="${cls}">
      <header>
        <div class="hyp-rank">${i + 1}</div>
        <div>
          <strong>${escapeHtml(h.statement)}</strong>
          <div class="hyp-tags">${statusTag}${h.category ? `<span class="tag">${escapeHtml(h.category)}</span>` : ""}</div>
        </div>
        <div class="hyp-post">${((h.posterior || 0) * 100).toFixed(0)}%</div>
      </header>
      <div class="bar"><span style="width:${width}%"></span></div>
      <div class="ev">
        <div><div class="ev-head ok">Supports</div><ul>${support}</ul></div>
        <div><div class="ev-head bad">Contradicts</div><ul>${against}</ul></div>
      </div>
      ${reason}
    </div>`;
  }).join("");

  $("rejectSelect").innerHTML = hyps
    .filter((h) => h.status === "active")
    .map((h) => `<option value="${escapeHtml(h.id)}">${escapeHtml(h.id)}: ${escapeHtml(h.statement.slice(0, 80))}</option>`)
    .join("");
  if (d.recommended_intervention?.description) {
    $("fuIntervention").value = d.recommended_intervention.description;
  }
}

function showError(message) {
  $("formError").hidden = false;
  $("formError").textContent = message;
  const banner = $("workspaceError");
  banner.hidden = false;
  banner.textContent = message;
}

function clearError() {
  $("formError").hidden = true;
  $("workspaceError").hidden = true;
}

function setWorking(btn, on) {
  if (!btn) return;
  btn.disabled = on;
}

async function diagnoseBundle() {
  clearError();
  const fd = new FormData();
  fd.append("title", $("title").value || "Untitled experiment");
  fd.append("domain", $("domain").value);
  fd.append("objective", $("objective").value);
  fd.append("unexpected_outcome", $("unexpected").value);
  fd.append("setup_description", $("setup").value);
  fd.append("materials", $("materials").value);
  fd.append("processing", $("processing").value);
  fd.append("protocol", $("processing").value);
  fd.append("telemetry", $("telemetry").value);
  fd.append("context", $("context").value);
  fd.append("roles", JSON.stringify(pendingFiles.map((f) => f.role)));
  fd.append("captions", JSON.stringify(pendingFiles.map((f) => f.caption)));
  pendingFiles.forEach((item) => fd.append("files", item.file, item.file.name));
  setBusy(true);
  $("emptyState").hidden = true;
  $("result").hidden = true;
  setWorking($("diagnoseBtn"), true);
  try {
    const data = await api("/api/diagnose-bundle", { method: "POST", body: fd });
    renderSession(data);
  } catch (err) {
    setBusy(false);
    $("emptyState").hidden = false;
    showError(err.message);
  } finally {
    setWorking($("diagnoseBtn"), false);
  }
}

async function diagnoseCase() {
  clearError();
  setBusy(true);
  $("emptyState").hidden = true;
  $("result").hidden = true;
  setWorking($("diagnoseCase"), true);
  try {
    const data = await api("/api/diagnose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ case_id: $("caseSelect").value }),
    });
    renderSession(data);
  } catch (err) {
    setBusy(false);
    $("emptyState").hidden = !session;
    $("result").hidden = !session;
    showError(err.message);
  } finally {
    setWorking($("diagnoseCase"), false);
  }
}

async function rejectHyp() {
  if (!session) return;
  setWorking($("rejectBtn"), true);
  try {
    const data = await api(`/api/sessions/${session.session_id}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        hypothesis_id: $("rejectSelect").value,
        reason: $("rejectReason").value || "Rejected by researcher",
      }),
    });
    $("rejectReason").value = "";
    renderSession(data);
  } finally {
    setWorking($("rejectBtn"), false);
  }
}

async function addInfo() {
  if (!session) return;
  const information = $("addInfo").value.trim();
  if (!information) return;
  setWorking($("addInfoBtn"), true);
  try {
    const data = await api(`/api/sessions/${session.session_id}/add-info`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ information }),
    });
    $("addInfo").value = "";
    renderSession(data);
  } finally {
    setWorking($("addInfoBtn"), false);
  }
}

async function followup() {
  if (!session) return;
  setWorking($("followupBtn"), true);
  try {
    const data = await api(`/api/sessions/${session.session_id}/followup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        intervention: $("fuIntervention").value,
        outcome: $("fuOutcome").value,
      }),
    });
    renderSession(data);
  } finally {
    setWorking($("followupBtn"), false);
  }
}

async function addMoreFiles() {
  if (!session) return;
  const files = Array.from($("moreFiles").files || []);
  if (!files.length) return;
  const fd = new FormData();
  fd.append("roles", JSON.stringify(files.map((f) => guessRole(f.name))));
  fd.append("captions", JSON.stringify(files.map(() => "")));
  files.forEach((f) => fd.append("files", f, f.name));
  setWorking($("moreFilesBtn"), true);
  try {
    const data = await api(`/api/sessions/${session.session_id}/artifacts`, { method: "POST", body: fd });
    $("moreFiles").value = "";
    renderSession(data);
  } finally {
    setWorking($("moreFilesBtn"), false);
  }
}

async function boot() {
  $("status").classList.add("is-loading");
  const health = await api("/api/health");
  $("status").classList.remove("is-loading");
  $("status").textContent = `${health.cases} catalog cases · ${health.engine_mode} engine`;
  $("accepts").innerHTML = (health.accepts || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const cases = await api("/api/cases");
  $("caseSelect").innerHTML = cases.map((c) =>
    `<option value="${c.id}">${c.id} — ${escapeHtml(c.title)}</option>`
  ).join("");
}

function bindHitlTabs() {
  const tabs = document.querySelectorAll("[data-hitl]");
  const panes = document.querySelectorAll("[data-hitl-pane]");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const key = tab.dataset.hitl;
      tabs.forEach((t) => {
        const on = t === tab;
        t.classList.toggle("is-active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
      });
      panes.forEach((pane) => pane.classList.toggle("is-active", pane.dataset.hitlPane === key));
    });
  });
}

const drop = $("dropzone");
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("drag"); });
drop.addEventListener("dragleave", () => drop.classList.remove("drag"));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  drop.classList.remove("drag");
  addFiles(e.dataTransfer.files);
});
drop.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    $("fileInput").click();
  }
});
$("fileInput").addEventListener("change", (e) => addFiles(e.target.files));

$("diagnoseBtn").addEventListener("click", () => diagnoseBundle().catch((e) => showError(e.message)));
$("diagnoseCase").addEventListener("click", () => diagnoseCase().catch((e) => showError(e.message)));
$("rejectBtn").addEventListener("click", () => rejectHyp().catch((e) => showError(e.message)));
$("addInfoBtn").addEventListener("click", () => addInfo().catch((e) => showError(e.message)));
$("followupBtn").addEventListener("click", () => followup().catch((e) => showError(e.message)));
$("moreFilesBtn").addEventListener("click", () => addMoreFiles().catch((e) => showError(e.message)));
bindHitlTabs();
updateFileCount();
boot().catch((e) => {
  $("status").classList.remove("is-loading");
  $("status").classList.add("is-error");
  $("status").textContent = e.message;
});
