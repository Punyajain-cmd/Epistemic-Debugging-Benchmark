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

const SAMPLE = {
  title: "Housing bore drift / pack venting",
  domain: "machining",
  unexpected: "The CNC bore is 0.18 mm undersize and the surface is smeared. After assembly, the last three 21700 cells in the 6S2P pack vented during a 2C charge.",
  objective: "Machine 6082-T6 housings to drawing REV C, then assemble a 6S2P rover pack.",
  materials: "6082-T6 bar, lot 24-081\nCarbide 12 mm end mill, 4th regrind\n21700 NMC cells, electrolyte bottle already opened\nHysol fixture adhesive",
  processing: "Rough 0.4 mm/rev, finish 0.08 mm/rev, 180 m/min\nFlood coolant reused from yesterday\nCells filled in air, then crimped\n2C CC-CV to 4.20 V",
  setup: "Kurt vise, REV C STEP fixture, shop 21 °C / 62% RH\nThermistor taped to pack can, no fixture probe on the bore",
  telemetry: "Bore gauge 11.82 mm vs 12.00 mm drawing\nRa smeared; no chatter marks\nCell can 78 °C at vent; charger log: ERROR overheat pack_main",
  context: "New night-shift operator\nElectrolyte bottle left uncapped over the weekend\nSame tool used on the previous aluminium lot",
};

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

function formatApiError(text) {
  const raw = String(text || "Request failed").trim();
  try {
    const parsed = JSON.parse(raw);
    return parsed.detail || parsed.message || raw;
  } catch {
    return raw.length > 280 ? `${raw.slice(0, 277)}…` : raw;
  }
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(formatApiError(text) || res.statusText);
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
  $("workspace").setAttribute("aria-busy", on ? "true" : "false");
  if (message) $("busyTitle").textContent = message;
  if (!on) $("busyTitle").textContent = "Reading the dossier and ranking hypotheses…";
}

function restoreWorkspace() {
  setBusy(false);
  if (session) {
    $("emptyState").hidden = true;
    $("result").hidden = false;
  } else {
    $("emptyState").hidden = false;
    $("result").hidden = true;
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
      <button type="button" data-remove="${i}" class="ghost compact">Remove</button>
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

function evidenceList(items) {
  if (items && items.length) {
    return items.map((e) => {
      const text = typeof e === "string" ? e : e.statement;
      return `<li>${escapeHtml(text)}</li>`;
    }).join("");
  }
  return "<li class='muted'>None cited</li>";
}

function pickView(data) {
  const v = data.view || null;
  const d = data.diagnosis || {};
  const u = d.uncertainty || {};
  const artifacts = (v && v.artifacts) || data.experiment?.artifacts || [];
  const hyps = (v && v.hypotheses) || d.hypotheses || [];
  const lead = (v && v.lead) || {};
  const intervention = lead.intervention || d.recommended_intervention || {};
  const confidence = v && v.confidence != null ? v.confidence : u.confidence;
  const confidencePct = v && v.confidence_pct != null
    ? v.confidence_pct
    : (confidence == null ? null : Math.round(Number(confidence) * 100));
  const activeCount = hyps.filter((h) => h.status === "active").length;
  return {
    sessionId: (v && v.session_id) || data.session_id,
    title: (v && v.title) || data.title || "Diagnosis",
    caseId: (v && v.case_id) || data.case_id || "your experiment",
    engineMode: (v && v.engine_mode) || d.engine_mode || "heuristic",
    fileCount: v && v.file_count != null ? v.file_count : artifacts.length,
    confidence,
    confidencePct,
    entropy: v && v.entropy != null ? v.entropy : u.entropy,
    competingCount: v && v.competing_count != null ? v.competing_count : activeCount,
    uncertaintyNote: (v && v.uncertainty_note) || u.note || "",
    leadCause: lead.cause || d.leading_cause || "No leading cause yet — competing hypotheses are listed below.",
    chain: (lead.chain && lead.chain.length ? lead.chain : d.leading_causal_chain) || [],
    intervention,
    anomalies: (v && v.anomalies) || d.anomalies || [],
    missing: (v && v.missing) || u.missing_information || [],
    hyps,
    artifacts,
    history: (v && v.history) || data.history || [],
  };
}

function hypSupport(h) {
  return h.supporting || h.supporting_evidence || [];
}

function hypAgainst(h) {
  return h.contradicting || h.contradictory_evidence || [];
}

function renderSession(data) {
  session = data;
  $("emptyState").hidden = true;
  $("result").hidden = false;
  setBusy(false);
  clearError();
  const view = pickView(data);

  $("caseMeta").textContent = `${view.caseId} · ${view.engineMode} · ${view.fileCount} file(s) · session ${view.sessionId}`;
  $("leadTitle").textContent = view.title;
  $("leadCause").textContent = view.leadCause;
  $("confidenceValue").textContent = view.confidencePct == null ? "—" : `${view.confidencePct}%`;
  $("uncertaintyNote").textContent = view.uncertaintyNote;
  $("entropyValue").textContent = view.entropy == null ? "—" : Number(view.entropy).toFixed(2);
  $("competingCount").textContent = String(view.competingCount);
  setGauge(view.confidence);

  if (!view.artifacts.length) {
    $("ingested").innerHTML = "<span class='muted'>Notes only — no files attached.</span>";
  } else {
    $("ingested").innerHTML = view.artifacts.map((a) => {
      const role = ROLE_LABEL[a.role] || a.role || a.kind || "file";
      const caption = a.caption ? ` — ${a.caption}` : "";
      if (a.preview_url) {
        return `<a class="chip chip-media" href="${escapeHtml(a.preview_url)}" target="_blank" rel="noreferrer">
          <img alt="" src="${escapeHtml(a.preview_url)}" />
          <span><b>${escapeHtml(role)}</b> · ${escapeHtml(a.filename)}${escapeHtml(caption)}</span>
        </a>`;
      }
      return `<span class="chip"><b>${escapeHtml(role)}</b> · ${escapeHtml(a.filename)}${escapeHtml(caption)}</span>`;
    }).join("");
  }

  $("anomalies").innerHTML = view.anomalies.length
    ? view.anomalies.map((a) => `<li>${escapeHtml(a.description || a)}</li>`).join("")
    : "<li>None flagged</li>";
  $("missing").innerHTML = view.missing.length
    ? view.missing.map((m) => `<li>${escapeHtml(m)}</li>`).join("")
    : "<li>None listed</li>";
  $("chain").innerHTML = view.chain.length
    ? view.chain.map((s) => `<li>${escapeHtml(s)}</li>`).join("")
    : "<li class='muted'>No chain reconstructed yet.</li>";

  const plan = view.intervention || {};
  $("intervention").textContent = plan.description || "No intervention yet";
  const predTrue = plan.predicted_if_true || "";
  const predFalse = plan.predicted_if_false || "";
  const power = plan.diagnostic_power != null ? `Diagnostic power ${Number(plan.diagnostic_power).toFixed(2)}.` : "";
  $("interventionPred").textContent = [predTrue && `If true: ${predTrue}`, predFalse && `If false: ${predFalse}`, power].filter(Boolean).join(" ");

  const leadId = view.hyps.find((h) => h.status === "active")?.id;
  $("hypSummary").textContent = view.hyps.length
    ? `${view.hyps.length} hypotheses · ${view.competingCount} still active. The lead stays provisional until evidence closes the rest.`
    : "No hypotheses yet.";

  $("hypotheses").innerHTML = view.hyps.map((h, i) => {
    const pct = h.posterior_pct != null ? h.posterior_pct : Math.round((h.posterior || h.score || 0) * 100);
    const isLead = h.id === leadId && h.status === "active";
    const cls = ["hyp", isLead ? "is-lead" : "", h.status === "rejected" ? "is-rejected" : ""].filter(Boolean).join(" ");
    const statusTag = h.status === "rejected"
      ? `<span class="tag rejected">Rejected</span>`
      : h.status === "confirmed"
        ? `<span class="tag confirmed">Confirmed</span>`
        : isLead
          ? `<span class="tag lead">Lead</span>`
          : `<span class="tag">${escapeHtml(h.status || "active")}</span>`;
    const reason = h.rejected_reason
      ? `<p class="muted">Rejected: ${escapeHtml(h.rejected_reason)}</p>`
      : "";
    return `<div class="${cls}">
      <header>
        <div class="hyp-rank">${i + 1}</div>
        <div class="hyp-copy">
          <strong>${escapeHtml(h.statement)}</strong>
          <div class="hyp-tags">${statusTag}${h.category ? `<span class="tag">${escapeHtml(h.category)}</span>` : ""}</div>
        </div>
        <div class="hyp-post">${pct}%</div>
      </header>
      <div class="bar"><span style="width:${Math.max(0, Math.min(100, pct))}%"></span></div>
      <div class="ev">
        <div><div class="ev-head ok">Supports</div><ul class="ok">${evidenceList(hypSupport(h))}</ul></div>
        <div><div class="ev-head bad">Contradicts</div><ul class="bad">${evidenceList(hypAgainst(h))}</ul></div>
      </div>
      ${reason}
    </div>`;
  }).join("");

  $("rejectSelect").innerHTML = view.hyps
    .filter((h) => h.status === "active")
    .map((h) => `<option value="${escapeHtml(h.id)}">${escapeHtml(h.id)}: ${escapeHtml((h.statement || "").slice(0, 80))}</option>`)
    .join("");
  if (plan.description) $("fuIntervention").value = plan.description;

  const history = view.history || [];
  $("historyCard").hidden = history.length === 0;
  $("history").innerHTML = history.map((entry) => {
    const kind = entry.event || entry.action || entry.kind || "update";
    const detail = entry.information
      || entry.reason
      || entry.outcome
      || (Array.isArray(entry.files) ? entry.files.join(", ") : "")
      || entry.leading
      || entry.summary
      || "";
    return `<li><span class="tag">${escapeHtml(kind)}</span> ${escapeHtml(detail)}</li>`;
  }).join("");
}

function showError(message) {
  $("formError").hidden = false;
  $("formError").textContent = message;
  $("workspaceError").hidden = false;
  $("workspaceErrorText").textContent = message;
}

function clearError() {
  $("formError").hidden = true;
  $("workspaceError").hidden = true;
  $("workspaceErrorText").textContent = "";
}

function setWorking(btn, on) {
  if (!btn) return;
  btn.disabled = on;
}

function fillSample() {
  $("title").value = SAMPLE.title;
  $("domain").value = SAMPLE.domain;
  $("unexpected").value = SAMPLE.unexpected;
  $("objective").value = SAMPLE.objective;
  $("materials").value = SAMPLE.materials;
  $("processing").value = SAMPLE.processing;
  $("setup").value = SAMPLE.setup;
  $("telemetry").value = SAMPLE.telemetry;
  $("context").value = SAMPLE.context;
  document.querySelectorAll(".dossier details.disclose").forEach((el) => { el.open = true; });
  $("unexpected").focus();
}

function dossierFormData() {
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
  return fd;
}

async function diagnoseBundle() {
  clearError();
  setBusy(true, "Reading the dossier and ranking hypotheses…");
  $("emptyState").hidden = true;
  $("result").hidden = true;
  setWorking($("diagnoseBtn"), true);
  try {
    const data = await api("/api/diagnose-bundle", { method: "POST", body: dossierFormData() });
    renderSession(data);
  } catch (err) {
    showError(err.message);
    restoreWorkspace();
  } finally {
    setBusy(false);
    setWorking($("diagnoseBtn"), false);
  }
}

async function diagnoseCase() {
  clearError();
  setBusy(true, "Loading the catalog case and ranking hypotheses…");
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
    showError(err.message);
    restoreWorkspace();
  } finally {
    setBusy(false);
    setWorking($("diagnoseCase"), false);
  }
}

async function runHitl(btn, message, request) {
  if (!session) return;
  clearError();
  setWorking(btn, true);
  setBusy(true, message);
  try {
    const data = await request();
    renderSession(data);
  } catch (err) {
    showError(err.message);
    restoreWorkspace();
  } finally {
    setBusy(false);
    setWorking(btn, false);
  }
}

async function rejectHyp() {
  await runHitl($("rejectBtn"), "Re-ranking after the rejection…", () => api(`/api/sessions/${session.session_id}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      hypothesis_id: $("rejectSelect").value,
      reason: $("rejectReason").value || "Rejected by researcher",
    }),
  }).then((data) => {
    $("rejectReason").value = "";
    return data;
  }));
}

async function addInfo() {
  const information = $("addInfo").value.trim();
  if (!information) return;
  await runHitl($("addInfoBtn"), "Incorporating the new measurement…", () => api(`/api/sessions/${session.session_id}/add-info`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ information }),
  }).then((data) => {
    $("addInfo").value = "";
    return data;
  }));
}

async function followup() {
  await runHitl($("followupBtn"), "Updating posteriors from the follow-up…", () => api(`/api/sessions/${session.session_id}/followup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      intervention: $("fuIntervention").value,
      outcome: $("fuOutcome").value,
    }),
  }));
}

async function addMoreFiles() {
  const files = Array.from($("moreFiles").files || []);
  if (!files.length) return;
  const fd = new FormData();
  fd.append("roles", JSON.stringify(files.map((f) => guessRole(f.name))));
  fd.append("captions", JSON.stringify(files.map(() => "")));
  files.forEach((f) => fd.append("files", f, f.name));
  await runHitl($("moreFilesBtn"), "Adding files to this diagnosis…", () => api(`/api/sessions/${session.session_id}/artifacts`, {
    method: "POST",
    body: fd,
  }).then((data) => {
    $("moreFiles").value = "";
    return data;
  }));
}

function updateCasePreview() {
  const select = $("caseSelect");
  const option = select.selectedOptions[0];
  const preview = $("casePreview");
  if (!option || !option.dataset.preview) {
    preview.hidden = true;
    preview.textContent = "";
    return;
  }
  preview.hidden = false;
  preview.textContent = [
    option.dataset.category,
    option.dataset.regime,
    option.dataset.preview,
  ].filter(Boolean).join(" · ");
}

async function boot() {
  $("status").classList.add("is-loading");
  $("status").classList.remove("is-error");
  const health = await api("/api/health");
  const contract = health.contract_version ? ` · v${health.contract_version}` : "";
  $("status").classList.remove("is-loading");
  $("status").textContent = `${health.cases} catalog cases · ${health.engine_mode} engine${contract}`;
  $("accepts").innerHTML = (health.accepts || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const cases = await api("/api/cases");
  $("caseSelect").innerHTML = cases.map((c) => {
    const label = c.failure_category_label ? ` · ${c.failure_category_label}` : "";
    return `<option value="${escapeHtml(c.id)}" data-preview="${escapeHtml(c.objective_preview || "")}" data-category="${escapeHtml(c.failure_category_label || "")}" data-regime="${escapeHtml(c.information_regime || "")}">${escapeHtml(c.id)} — ${escapeHtml(c.title)}${escapeHtml(label)}</option>`;
  }).join("");
  updateCasePreview();
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
$("caseSelect").addEventListener("change", updateCasePreview);

$("diagnoseBtn").addEventListener("click", () => diagnoseBundle().catch((e) => { showError(e.message); restoreWorkspace(); }));
$("diagnoseCase").addEventListener("click", () => diagnoseCase().catch((e) => { showError(e.message); restoreWorkspace(); }));
$("rejectBtn").addEventListener("click", () => rejectHyp().catch((e) => { showError(e.message); restoreWorkspace(); }));
$("addInfoBtn").addEventListener("click", () => addInfo().catch((e) => { showError(e.message); restoreWorkspace(); }));
$("followupBtn").addEventListener("click", () => followup().catch((e) => { showError(e.message); restoreWorkspace(); }));
$("moreFilesBtn").addEventListener("click", () => addMoreFiles().catch((e) => { showError(e.message); restoreWorkspace(); }));
$("sampleFill").addEventListener("click", fillSample);
$("sampleFillEmpty").addEventListener("click", () => {
  fillSample();
  $("unexpected").scrollIntoView({ behavior: "smooth", block: "center" });
});
$("dismissError").addEventListener("click", clearError);

bindHitlTabs();
updateFileCount();
boot().catch((e) => {
  setBusy(false);
  $("status").classList.remove("is-loading");
  $("status").classList.add("is-error");
  $("status").textContent = "Engine unreachable";
  $("accepts").innerHTML = "<li>Could not load accepted file types</li>";
  showError(e.message || "Could not reach the EpiDebug API.");
  restoreWorkspace();
});
