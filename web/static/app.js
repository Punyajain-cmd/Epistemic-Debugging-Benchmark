const $ = (id) => document.getElementById(id);
let session = null;
const pendingFiles = [];

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

function renderFiles() {
  $("fileList").innerHTML = pendingFiles.map((item, i) => {
    const options = ROLES.map(([id, label]) =>
      `<option value="${id}" ${item.role === id ? "selected" : ""}>${label}</option>`
    ).join("");
    const thumb = item.preview
      ? `<img alt="" src="${item.preview}" />`
      : `<div class="thumb">${escapeHtml(item.file.name.split(".").pop() || "file")}</div>`;
    return `<div class="file-card">
      ${thumb}
      <div>
        <strong>${escapeHtml(item.file.name)}</strong>
        <select data-i="${i}" class="role">${options}</select>
        <input data-i="${i}" class="cap" placeholder="Caption (what are we looking at?)" value="${escapeHtml(item.caption)}" />
      </div>
      <button type="button" data-remove="${i}" class="ghost">Remove</button>
    </div>`;
  }).join("");

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

function renderSession(data) {
  session = data;
  $("emptyState").hidden = true;
  $("busy").hidden = true;
  $("result").hidden = false;
  const d = data.diagnosis || {};
  const artifacts = data.experiment?.artifacts || [];
  $("caseMeta").textContent = `${data.case_id || "your experiment"} · ${d.engine_mode || "heuristic"} · ${artifacts.length} file(s) · session ${data.session_id}`;
  $("leadTitle").textContent = data.title || "Diagnosis";
  $("leadCause").textContent = d.leading_cause || "No leading cause yet — competing hypotheses are listed below.";
  const conf = d.uncertainty?.confidence;
  $("confidenceValue").textContent = conf == null ? "—" : conf.toFixed(2);
  $("uncertaintyNote").textContent = d.uncertainty?.note || "";

  $("ingested").innerHTML = artifacts.length
    ? artifacts.map((a) => `<span class="chip">${escapeHtml(a.filename)} · ${escapeHtml(a.role)}</span>`).join("")
    : "<span class='muted'>Notes only — no files attached.</span>";

  $("anomalies").innerHTML = (d.anomalies || []).map((a) => `<li>${escapeHtml(a.description)}</li>`).join("") || "<li>None flagged</li>";
  $("missing").innerHTML = (d.uncertainty?.missing_information || []).map((m) => `<li>${escapeHtml(m)}</li>`).join("") || "<li>None listed</li>";
  $("chain").innerHTML = (d.leading_causal_chain || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("");
  $("intervention").textContent = d.recommended_intervention?.description || "No intervention yet";
  const predTrue = d.recommended_intervention?.predicted_if_true || "";
  const predFalse = d.recommended_intervention?.predicted_if_false || "";
  $("interventionPred").textContent = [predTrue && `If true: ${predTrue}`, predFalse && `If false: ${predFalse}`].filter(Boolean).join(" ");

  $("hypotheses").innerHTML = (d.hypotheses || []).map((h) => {
    const width = Math.round((h.posterior || h.score || 0) * 100);
    const support = (h.supporting_evidence || []).map((e) => `<li class="ok">${escapeHtml(e.statement)}</li>`).join("") || "<li class='muted'>None cited</li>";
    const against = (h.contradictory_evidence || []).map((e) => `<li class="bad">${escapeHtml(e.statement)}</li>`).join("") || "<li class='muted'>None cited</li>";
    return `<div class="hyp">
      <header>
        <div><strong>${escapeHtml(h.statement)}</strong><div class="muted">${escapeHtml(h.category || "")} · ${escapeHtml(h.status)}</div></div>
        <div>${((h.posterior || 0) * 100).toFixed(0)}%</div>
      </header>
      <div class="bar"><span style="width:${width}%"></span></div>
      <div class="ev">
        <div><div class="ok">Supports</div><ul>${support}</ul></div>
        <div><div class="bad">Contradicts</div><ul>${against}</ul></div>
      </div>
    </div>`;
  }).join("");

  $("rejectSelect").innerHTML = (d.hypotheses || [])
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
}

function clearError() {
  $("formError").hidden = true;
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
  $("busy").hidden = false;
  $("emptyState").hidden = true;
  $("result").hidden = true;
  try {
    const data = await api("/api/diagnose-bundle", { method: "POST", body: fd });
    renderSession(data);
  } catch (err) {
    $("busy").hidden = true;
    $("emptyState").hidden = false;
    showError(err.message);
  }
}

async function diagnoseCase() {
  clearError();
  $("busy").hidden = false;
  const data = await api("/api/diagnose", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ case_id: $("caseSelect").value }),
  });
  renderSession(data);
}

async function rejectHyp() {
  if (!session) return;
  const data = await api(`/api/sessions/${session.session_id}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      hypothesis_id: $("rejectSelect").value,
      reason: $("rejectReason").value || "Rejected by researcher",
    }),
  });
  renderSession(data);
}

async function addInfo() {
  if (!session) return;
  const information = $("addInfo").value.trim();
  if (!information) return;
  const data = await api(`/api/sessions/${session.session_id}/add-info`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ information }),
  });
  $("addInfo").value = "";
  renderSession(data);
}

async function followup() {
  if (!session) return;
  const data = await api(`/api/sessions/${session.session_id}/followup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      intervention: $("fuIntervention").value,
      outcome: $("fuOutcome").value,
    }),
  });
  renderSession(data);
}

async function addMoreFiles() {
  if (!session) return;
  const files = Array.from($("moreFiles").files || []);
  if (!files.length) return;
  const fd = new FormData();
  fd.append("roles", JSON.stringify(files.map((f) => guessRole(f.name))));
  fd.append("captions", JSON.stringify(files.map(() => "")));
  files.forEach((f) => fd.append("files", f, f.name));
  const data = await api(`/api/sessions/${session.session_id}/artifacts`, { method: "POST", body: fd });
  $("moreFiles").value = "";
  renderSession(data);
}

async function boot() {
  const health = await api("/api/health");
  $("status").textContent = `${health.cases} catalog cases · ${health.engine_mode} engine`;
  $("accepts").innerHTML = (health.accepts || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const cases = await api("/api/cases");
  $("caseSelect").innerHTML = cases.map((c) =>
    `<option value="${c.id}">${c.id} — ${escapeHtml(c.title)}</option>`
  ).join("");
}

const drop = $("dropzone");
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("drag"); });
drop.addEventListener("dragleave", () => drop.classList.remove("drag"));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  drop.classList.remove("drag");
  addFiles(e.dataTransfer.files);
});
$("fileInput").addEventListener("change", (e) => addFiles(e.target.files));

$("diagnoseBtn").addEventListener("click", () => diagnoseBundle().catch((e) => showError(e.message)));
$("diagnoseCase").addEventListener("click", () => diagnoseCase().catch((e) => showError(e.message)));
$("rejectBtn").addEventListener("click", () => rejectHyp().catch((e) => showError(e.message)));
$("addInfoBtn").addEventListener("click", () => addInfo().catch((e) => showError(e.message)));
$("followupBtn").addEventListener("click", () => followup().catch((e) => showError(e.message)));
$("moreFilesBtn").addEventListener("click", () => addMoreFiles().catch((e) => showError(e.message)));
boot().catch((e) => { $("status").textContent = e.message; });
