const $ = (id) => document.getElementById(id);
let session = null;
let evidenceSlots = null;
const pendingFiles = [];
const pendingMore = [];
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

const DEFAULT_SLOTS = [
  { id: "failure", label: "What went wrong", hint: "Unexpected outcome or off-spec measurement" },
  { id: "objective", label: "Intent / objective", hint: "What the run was supposed to produce" },
  { id: "materials", label: "Materials / lots", hint: "Alloy, chemistry, CoA, SDS" },
  { id: "process", label: "Process / protocol", hint: "Feeds, recipe, SOP, G-code" },
  { id: "setup", label: "Setup description", hint: "Fixture, CAD revision, environment" },
  { id: "cad", label: "CAD / drawing", hint: "STEP, STL, DXF, IGES" },
  { id: "sensors", label: "Sensor / table data", hint: "CSV/JSON or typed readings" },
  { id: "logs", label: "Machine / console logs", hint: "Alarms, charger, robot, serial" },
  { id: "photos", label: "Setup or result photos", hint: "Bench, failed part, gel, burn" },
  { id: "context", label: "Background / context", hint: "Operator, humidity, leftover bottle" },
];

const ROLE_TO_SLOT = {
  setup_photo: "photos",
  result_image: "photos",
  cad: "cad",
  sensor: "sensors",
  log: "logs",
  material_doc: "materials",
  process_doc: "process",
  datasheet: "materials",
  other: "setup",
};

const GALLERY_GROUPS = [
  { key: "images", label: "Photos", icon: "photos" },
  { key: "tables", label: "Tables / sensors", icon: "sensors" },
  { key: "logs", label: "Logs", icon: "logs" },
  { key: "cad", label: "CAD", icon: "cad" },
  { key: "documents", label: "Documents", icon: "file" },
  { key: "other", label: "Other", icon: "file" },
];

const BUSY_STAGES = ["ingest", "anomalies", "causes", "evidence", "intervention"];
const BUSY_COPY = {
  ingest: "Parsing notes, files, and coverage…",
  anomalies: "Flagging off-spec readings and log faults…",
  causes: "Keeping competing causes alive…",
  evidence: "Weighing support and contradictions…",
  intervention: "Choosing a discriminating follow-up…",
};

const CHAT_WELCOME = {
  role: "assistant",
  content: "Describe what failed, or dump the record on the left. I'll ask for missing evidence and rank competing causes when there's enough to work with.",
  kind: "message",
};

const ACCEPT_ICON = [
  ["photo", "photos"],
  ["CAD", "cad"],
  ["sensor", "sensors"],
  ["CSV", "sensors"],
  ["log", "logs"],
  ["PDF", "file"],
  ["notebook", "file"],
  ["material", "materials"],
  ["process", "process"],
];

let busyTimer = null;
let busyStage = 0;

const SAMPLE = {
  title: "Housing bore drift / pack venting",
  domain: "machining",
  unexpected: "The CNC bore is 0.18 mm undersize and the surface is smeared. After assembly, the last three 21700 cells in the 6S2P pack vented during a 2C charge.",
  objective: "Machine 6082-T6 housings to drawing REV C, then assemble a 6S2P rover pack.",
  materials: "6082-T6 bar, lot 24-081\nCarbide 12 mm end mill, 4th regrind\n21700 NMC cells, electrolyte bottle already opened\nHysol fixture adhesive",
  processing: "Rough 0.4 mm/rev, finish 0.08 mm/rev, 180 m/min\nFlood coolant reused from yesterday\nCells filled in air, then crimped\n2C CC-CV to 4.20 V",
  protocol: "1. Face and bore housing to REV C\n2. Degrease, assemble pack\n3. Charge 2C to 4.20 V with thermistor on can",
  setup: "Kurt vise, REV C STEP fixture, shop 21 °C / 62% RH\nThermistor taped to pack can, no fixture probe on the bore",
  telemetry: "Bore gauge 11.82 mm vs 12.00 mm drawing\nRa smeared; no chatter marks\nCell can 78 °C at vent",
  logs: "2024-08-12 02:18:44 WARN cell_5 imbalance 42 mV\n2024-08-12 02:19:02 ERROR overheat pack_main",
  context: "New night-shift operator\nElectrolyte bottle left uncapped over the weekend\nSame tool used on the previous aluminium lot",
};

function guessRole(name) {
  const n = name.toLowerCase();
  if (/\.(stl|step|stp|iges|igs|dxf|obj|3mf|ply)$/.test(n) || /cad|fixture|drawing/.test(n)) return "cad";
  if (/\.(nc|gcode|tap|cnc|ngc)$/.test(n) || /protocol|sop|traveler|gcode|recipe/.test(n)) return "process_doc";
  if (/\.(csv|tsv|json|dat)$/.test(n) || /sensor|telemetry|thermistor|cycle/.test(n)) return "sensor";
  if (/\.(log|out|err|serial|ulog|nmea)$/.test(n) || /console|dmesg|syslog|alarm/.test(n)) return "log";
  if (/sds|datasheet|msds|spec/.test(n)) return "datasheet";
  if (/material|alloy|lot|coa|cert|mill|bom/.test(n)) return "material_doc";
  if (/setup|bench|rig|vise/.test(n)) return "setup_photo";
  if (/crack|fail|gel|burn|corrosion|result|vent|scrap|defect/.test(n)) return "result_image";
  if (/\.(png|jpe?g|webp|gif|tif|tiff|bmp|heic)$/.test(n)) return "setup_photo";
  if (/\.(pdf|md|docx)$/.test(n)) return "datasheet";
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

function formatChatHtml(value) {
  return escapeHtml(value).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

function chatRoleLabel(role) {
  if (role === "user") return "You";
  if (role === "system-tool") return "Engine";
  return "EpiDebug";
}

function renderChat(messages) {
  const root = $("chatLog");
  if (!root) return;
  const panel = $("chatPanel");
  const hasThread = (messages || []).some((msg) => msg && msg.role === "user");
  if (panel) panel.classList.toggle("has-thread", hasThread);

  if (!messages || !messages.length) {
    root.classList.add("is-idle");
    root.innerHTML = `<p class="chat-idle-hint">${escapeHtml(CHAT_WELCOME.content)}</p>`;
    return;
  }

  root.classList.remove("is-idle");
  root.innerHTML = messages.map((msg) => {
    const role = msg.role || "assistant";
    return `<div class="chat-bubble ${escapeHtml(role)}" data-role="${escapeHtml(role)}">
      <small>${escapeHtml(chatRoleLabel(role))}</small>
      <div>${formatChatHtml(msg.content || "")}</div>
    </div>`;
  }).join("");
  root.scrollTop = root.scrollHeight;
}

function setChatMode(mode) {
  const el = $("chatMode");
  if (!el) return;
  const label = mode || "heuristic";
  el.textContent = label;
  el.classList.toggle("is-llm", label !== "heuristic");
}

function syncDiagnoseLabel() {
  const btn = $("diagnoseBtn");
  if (!btn) return;
  const span = btn.querySelector("span");
  const small = btn.querySelector("small");
  if (session) {
    if (span) span.textContent = "Force re-diagnose";
    if (small) small.textContent = "Re-rank this thread from the current dossier";
  } else {
    if (span) span.textContent = "Run epistemic debugging";
    if (small) small.textContent = "Rank competing causes from this dossier";
  }
}

function slotIcon(id) {
  const key = id && document.querySelector(`#ico-${id}`) ? id : "file";
  return `<svg class="ico" aria-hidden="true"><use href="#ico-${key}"></use></svg>`;
}

function acceptIcon(text) {
  const hit = ACCEPT_ICON.find(([needle]) => text.toLowerCase().includes(needle.toLowerCase()));
  return slotIcon(hit ? hit[1] : "file");
}

function setBusyStage(index) {
  busyStage = index;
  const stage = BUSY_STAGES[index] || BUSY_STAGES[0];
  const sub = $("busySub");
  if (sub) sub.textContent = BUSY_COPY[stage] || "";
  document.querySelectorAll("#busyPipeline li").forEach((el, i) => {
    el.classList.toggle("is-active", i === index);
    el.classList.toggle("is-done", i < index);
  });
}

function stopBusyStages() {
  if (busyTimer) {
    clearInterval(busyTimer);
    busyTimer = null;
  }
  busyStage = 0;
}

function startBusyStages() {
  stopBusyStages();
  setBusyStage(0);
  busyTimer = setInterval(() => {
    const next = Math.min(busyStage + 1, BUSY_STAGES.length - 1);
    setBusyStage(next);
    if (next >= BUSY_STAGES.length - 1) stopBusyStages();
  }, 720);
}

function setBusy(on, message) {
  $("busy").hidden = !on;
  $("workspace").setAttribute("aria-busy", on ? "true" : "false");
  if (message) $("busyTitle").textContent = message;
  if (on) startBusyStages();
  else {
    stopBusyStages();
    setBusyStage(0);
    $("busyTitle").textContent = "Reading the dossier and ranking hypotheses…";
    const sub = $("busySub");
    if (sub) sub.textContent = "Ingest → anomalies → causes → evidence → intervention";
  }
}

function restoreWorkspace() {
  setBusy(false);
  if (session) {
    const hasDx = !!(session.diagnosis && (session.diagnosis.hypotheses || []).length);
    $("emptyState").hidden = hasDx;
    $("result").hidden = !hasDx;
    renderChat(pickView(session).messages);
  } else {
    $("emptyState").hidden = false;
    $("result").hidden = true;
    renderChat([]);
  }
  syncDiagnoseLabel();
}

function roleOptions(selected) {
  return ROLES.map(([id, label]) =>
    `<option value="${id}" ${selected === id ? "selected" : ""}>${label}</option>`
  ).join("");
}

function fileThumb(item) {
  const ext = (item.file.name.split(".").pop() || "file").slice(0, 5);
  const slot = ROLE_TO_SLOT[item.role] || "file";
  if (item.preview && item.file.size > 200) return `<img alt="" src="${item.preview}" />`;
  return `<div class="thumb thumb-ico">${slotIcon(slot)}<span>${escapeHtml(ext)}</span></div>`;
}

function fileCard(item, i, prefix) {
  return `<div class="file-card">
    ${fileThumb(item)}
    <div class="file-meta">
      <strong title="${escapeHtml(item.file.name)}">${escapeHtml(item.file.name)}</strong>
      <div class="file-size">${formatBytes(item.file.size)} · ${escapeHtml(ROLE_LABEL[item.role] || item.role)}</div>
      <select data-i="${i}" data-prefix="${prefix}" class="role" aria-label="File role">${roleOptions(item.role)}</select>
      <input data-i="${i}" data-prefix="${prefix}" class="cap" placeholder="Caption (what are we looking at?)" value="${escapeHtml(item.caption)}" />
    </div>
    <button type="button" data-remove="${i}" data-prefix="${prefix}" class="ghost compact">Remove</button>
  </div>`;
}

function bindFileList(root, bucket) {
  root.querySelectorAll(".role").forEach((el) => {
    el.addEventListener("change", (e) => {
      bucket[Number(e.target.dataset.i)].role = e.target.value;
      if (bucket === pendingFiles) renderFiles();
      else renderMoreFiles();
    });
  });
  root.querySelectorAll(".cap").forEach((el) => {
    el.addEventListener("input", (e) => {
      bucket[Number(e.target.dataset.i)].caption = e.target.value;
    });
  });
  root.querySelectorAll("[data-remove]").forEach((el) => {
    el.addEventListener("click", () => {
      const idx = Number(el.dataset.remove);
      if (bucket[idx].preview) URL.revokeObjectURL(bucket[idx].preview);
      bucket.splice(idx, 1);
      if (bucket === pendingFiles) renderFiles();
      else renderMoreFiles();
    });
  });
}

function updateFileCount() {
  const n = pendingFiles.length;
  $("fileCount").textContent = n === 0 ? "No files yet" : `${n} file${n === 1 ? "" : "s"} ready`;
}

function renderFiles() {
  const groups = {};
  pendingFiles.forEach((item, i) => {
    (groups[item.role] ||= []).push({ item, i });
  });
  const order = ROLES.map(([id]) => id).filter((id) => groups[id]);
  Object.keys(groups).forEach((id) => {
    if (!order.includes(id)) order.push(id);
  });
  $("fileList").innerHTML = order.map((role) => {
    const rows = groups[role].map(({ item, i }) => fileCard(item, i, "pending")).join("");
    return `<section class="file-group">
      <h4>${escapeHtml(ROLE_LABEL[role] || role)} <span>${groups[role].length}</span></h4>
      ${rows}
    </section>`;
  }).join("");
  updateFileCount();
  bindFileList($("fileList"), pendingFiles);
  renderCoverage();
}

function renderMoreFiles() {
  const root = $("moreFileList");
  if (!root) return;
  root.innerHTML = pendingMore.map((item, i) => fileCard(item, i, "more")).join("");
  bindFileList(root, pendingMore);
}

function addFiles(fileList, role) {
  Array.from(fileList || []).forEach((file) => {
    const preview = file.type.startsWith("image/") ? URL.createObjectURL(file) : null;
    pendingFiles.push({ file, role: role || guessRole(file.name), caption: "", preview });
  });
  renderFiles();
}

function addMorePending(fileList) {
  Array.from(fileList || []).forEach((file) => {
    const preview = file.type.startsWith("image/") ? URL.createObjectURL(file) : null;
    pendingMore.push({ file, role: guessRole(file.name), caption: "", preview });
  });
  renderMoreFiles();
}

function fieldFilled(id) {
  const el = $(id);
  return !!(el && el.value.trim());
}

function hasRole(...roles) {
  return pendingFiles.some((item) => roles.includes(item.role));
}

function hasKind(pred) {
  return pendingFiles.some((item) => pred(item.file.name.toLowerCase()));
}

function clientCoverage() {
  const slots = evidenceSlots || DEFAULT_SLOTS;
  const present = {
    failure: fieldFilled("unexpected"),
    objective: fieldFilled("objective"),
    materials: fieldFilled("materials") || hasRole("material_doc", "datasheet"),
    process: fieldFilled("processing") || fieldFilled("protocol") || hasRole("process_doc"),
    setup: fieldFilled("setup"),
    cad: hasRole("cad") || hasKind((n) => /\.(step|stp|stl|iges|igs|dxf)$/.test(n)),
    sensors: fieldFilled("telemetry") || hasRole("sensor"),
    logs: fieldFilled("logs") || hasRole("log"),
    photos: hasRole("setup_photo", "result_image") || hasKind((n) => /\.(png|jpe?g|webp|gif|tif|tiff|bmp|heic)$/.test(n)),
    context: fieldFilled("context"),
  };
  return slots.map((slot) => ({ ...slot, present: !!present[slot.id] }));
}

function renderCoverageList(target, items, emptyText) {
  if (!target) return;
  if (!items.length) {
    target.innerHTML = emptyText ? `<div class="cov-tile is-missing"><span class="cov-label">${escapeHtml(emptyText)}</span></div>` : "";
    return;
  }
  target.innerHTML = items.map((slot) => `
    <div class="cov-tile ${slot.present ? "is-present" : "is-missing"}" role="listitem" title="${escapeHtml(slot.hint || "")}">
      <span class="cov-ico" aria-hidden="true">${slotIcon(slot.id)}</span>
      <span class="cov-label">${escapeHtml(slot.label)}</span>
      <span class="cov-state">${slot.present ? "in dump" : "missing"}</span>
    </div>
  `).join("");
}

function renderCoverage() {
  const items = clientCoverage();
  const ready = items.filter((s) => s.present).length;
  const pct = items.length ? Math.round((ready / items.length) * 100) : 0;
  $("coverageScore").textContent = `${ready} / ${items.length}`;
  const panel = $("coveragePanel");
  if (panel) panel.style.setProperty("--coverage-pct", String(pct));
  const sub = $("coverageSub");
  if (sub) sub.textContent = pct === 100 ? "Complete dump — every slot has a signal" : "Evidence slots the engine can actually use";
  renderCoverageList($("coverageList"), items);
  const missing = items.filter((s) => !s.present).map((s) => s.label);
  $("coverageHint").textContent = missing.length
    ? `Still missing: ${missing.join(", ")}. Attach them if they exist — the engine will use the summaries.`
    : "Complete dump. Run epistemic debugging when you are ready.";
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
    gallery: (v && v.gallery) || {},
    ingest: (v && v.ingest) || null,
    history: (v && v.history) || data.history || [],
    messages: (v && v.messages) || data.messages || [],
    chat: (v && v.chat) || {},
  };
}

function hypSupport(h) {
  return h.supporting || h.supporting_evidence || [];
}

function hypAgainst(h) {
  return h.contradicting || h.contradictory_evidence || [];
}

function galleryCard(a) {
  const role = ROLE_LABEL[a.role] || a.role || a.kind || "file";
  const caption = a.caption ? `<em>${escapeHtml(a.caption)}</em>` : "";
  const summary = a.summary ? `<p>${escapeHtml(a.summary)}</p>` : "";
  const flags = (a.flags || []).slice(0, 3)
    .map((f) => `<li>${escapeHtml(f)}</li>`).join("");
  const preview = a.extracted_preview && !a.preview_url
    ? `<pre>${escapeHtml(a.extracted_preview)}</pre>` : "";
  const isStubImage = a.kind === "image" && (!a.size_bytes || a.size_bytes < 200);
  const isImage = (a.kind === "image" || !!a.preview_url) && !isStubImage;
  const icon = ROLE_TO_SLOT[a.role] || (a.kind === "sensor" ? "sensors" : a.kind === "log" ? "logs" : a.kind === "cad" ? "cad" : "file");
  const media = a.preview_url && !isStubImage
    ? `<a href="${escapeHtml(a.preview_url)}" target="_blank" rel="noreferrer"><img alt="" src="${escapeHtml(a.preview_url)}" /></a>`
    : `<div class="thumb thumb-ico">${slotIcon(icon)}<span>${escapeHtml((a.filename.split(".").pop() || a.kind || "file").slice(0, 5))}</span></div>`;
  return `<article class="gallery-card kind-${escapeHtml(a.kind || "other")}${isImage ? " is-image" : ""}">
    ${media}
    <div>
      <strong title="${escapeHtml(a.filename)}">${escapeHtml(a.filename)}</strong>
      <div class="file-size"><span class="kind-badge">${slotIcon(icon)}${escapeHtml(role)}</span>${a.size_bytes ? ` · ${formatBytes(a.size_bytes)}` : ""}</div>
      ${caption}
      ${summary}
      ${flags ? `<ul class="gallery-flags">${flags}</ul>` : ""}
      ${preview}
    </div>
  </article>`;
}

function galleryEmpty() {
  return `<div class="gallery-empty">
    <svg viewBox="0 0 72 56" aria-hidden="true">
      <rect x="8" y="12" width="56" height="36" rx="4" />
      <path d="M18 32l10-9 8 7 8-6 12 10" />
      <circle cx="24" cy="22" r="3" />
    </svg>
    <p>Notes only — no files attached. The engine ranked from typed fields.</p>
  </div>`;
}

function renderHighlights(ingest) {
  const root = $("ingestHighlights");
  if (!root) return;
  const hits = (ingest && ingest.anomaly_highlights) || [];
  if (!hits.length) {
    root.hidden = true;
    root.innerHTML = "";
    return;
  }
  root.hidden = false;
  root.innerHTML = hits.slice(0, 8).map((h) => `
    <span class="highlight-chip">
      <b>${escapeHtml(h.kind || "flag")} · ${escapeHtml(h.filename || "")}</b>
      ${escapeHtml(h.text || "")}
    </span>
  `).join("");
}

function renderGallery(view) {
  const artifacts = view.artifacts || [];
  const ingest = view.ingest;
  const gallery = view.gallery || {};
  if (ingest && Array.isArray(ingest.coverage)) {
    renderCoverageList($("ingestCoverage"), ingest.coverage);
    const pct = ingest.completeness == null ? "" : ` · ${Math.round(ingest.completeness * 100)}% complete`;
    $("ingestMeta").textContent = `${view.fileCount} file(s)${pct}. Summaries below are what the engine actually read.`;
  } else {
    $("ingestCoverage").innerHTML = "";
    $("ingestMeta").textContent = `${view.fileCount} file(s) attached.`;
  }
  renderHighlights(ingest);
  if (!artifacts.length) {
    $("gallery").innerHTML = galleryEmpty();
    $("ingested").innerHTML = "<span class='muted'>Notes only — no files attached.</span>";
    return;
  }
  const grouped = GALLERY_GROUPS
    .map((group) => ({ ...group, items: gallery[group.key] || [] }))
    .filter((group) => group.items.length);
  if (grouped.length) {
    $("gallery").innerHTML = grouped.map((group) => `
      <section class="gallery-group">
        <h4>${slotIcon(group.icon)}${escapeHtml(group.label)} <span class="gallery-count">${group.items.length}</span></h4>
        <div class="gallery-mosaic">${group.items.map(galleryCard).join("")}</div>
      </section>
    `).join("");
  } else {
    $("gallery").innerHTML = `<div class="gallery-mosaic">${artifacts.map(galleryCard).join("")}</div>`;
  }
  $("ingested").innerHTML = artifacts.map((a) => {
    const role = ROLE_LABEL[a.role] || a.role || a.kind || "file";
    return `<span class="chip"><b>${escapeHtml(role)}</b> · ${escapeHtml(a.filename)}</span>`;
  }).join("");
}

function renderSession(data) {
  session = data;
  $("emptyState").hidden = true;
  $("result").hidden = false;
  setBusy(false);
  clearError();
  const view = pickView(data);
  syncDiagnoseLabel();
  setChatMode(view.chat.mode || view.engineMode);
  renderChat(view.messages);

  $("caseMeta").textContent = `${view.caseId} · ${view.engineMode} · ${view.fileCount} file(s) · session ${view.sessionId}`;
  $("leadTitle").textContent = view.title;
  $("leadCause").textContent = view.leadCause;
  $("confidenceValue").textContent = view.confidencePct == null ? "—" : `${view.confidencePct}%`;
  $("uncertaintyNote").textContent = view.uncertaintyNote;
  $("entropyValue").textContent = view.entropy == null ? "—" : Number(view.entropy).toFixed(2);
  $("competingCount").textContent = String(view.competingCount);
  setGauge(view.confidence);
  renderGallery(view);

  $("anomalies").innerHTML = view.anomalies.length
    ? view.anomalies.map((a) => {
      const sev = (a.severity || a.source || "flag").toString();
      return `<li class="flag-row"><span class="sev ${escapeHtml(sev)}">${escapeHtml(sev)}</span><span>${escapeHtml(a.description || a)}</span></li>`;
    }).join("")
    : "<li class='flag-row'><span class='sev'>none</span><span>None flagged</span></li>";
  $("missing").innerHTML = view.missing.length
    ? view.missing.map((m) => `<li class="flag-row"><span class="sev">gap</span><span>${escapeHtml(m)}</span></li>`).join("")
    : "<li class='flag-row'><span class='sev low'>ok</span><span>None listed</span></li>";
  $("chain").innerHTML = view.chain.length
    ? view.chain.map((s, i) => `<li><span class="tl-index">${String(i + 1).padStart(2, "0")}</span><span class="tl-body">${escapeHtml(s)}</span></li>`).join("")
    : "<li><span class='tl-index'>—</span><span class='tl-body muted'>No chain reconstructed yet.</span></li>";

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

function makeNamedFile(name, parts, type) {
  const mime = type || "text/plain";
  try {
    return new File(parts, name, { type: mime });
  } catch {
    const blob = new Blob(parts, { type: mime });
    try {
      return new File([blob], name, { type: mime });
    } catch {
      blob.name = name;
      return blob;
    }
  }
}

function makeTextFile(name, body, type) {
  return makeNamedFile(name, [body], type || "text/plain");
}

function tinyPngFile(name) {
  // 1x1 PNG — built as bytes so sample-fill does not depend on atob.
  const bytes = new Uint8Array([
    137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82, 0, 0, 0, 1,
    0, 0, 0, 1, 8, 2, 0, 0, 0, 144, 119, 83, 222, 0, 0, 0, 10, 73, 68, 65, 84,
    120, 156, 99, 248, 15, 0, 1, 1, 1, 0, 24, 221, 141, 180, 0, 0, 0, 0, 73,
    69, 78, 68, 174, 66, 96, 130,
  ]);
  return makeNamedFile(name, [bytes], "image/png");
}

function pushSampleFile(file, role, caption) {
  if (pendingFiles.some((item) => item.file.name === file.name)) return;
  let preview = null;
  try {
    if (file.type.startsWith("image/")) preview = URL.createObjectURL(file);
  } catch {
    preview = null;
  }
  pendingFiles.push({ file, role, caption, preview });
}

const SAMPLE_FIXTURES = [
  { path: "/mock_data/dump_demo/pack_temp.csv", role: "sensor", caption: "Thermistor on pack can", type: "text/csv" },
  { path: "/mock_data/dump_demo/charger.log", role: "log", caption: "Charger console", type: "text/plain" },
  { path: "/mock_data/dump_demo/housing_revC.step", role: "cad", caption: "REV C fixture", type: "application/step" },
  { path: "/mock_data/dump_demo/bore_finish.nc", role: "process_doc", caption: "Finish bore program", type: "text/plain" },
  { path: "/mock_data/dump_demo/setup_vise.png", role: "setup_photo", caption: "Kurt vise setup", type: "image/png" },
  { path: "/mock_data/dump_demo/vented_cells_result.png", role: "result_image", caption: "Vented 21700 cans", type: "image/png" },
  { path: "/mock_data/dump_demo/mill_cert_lot24081.txt", role: "material_doc", caption: "Lot 24-081 mill cert", type: "text/plain" },
];

async function addFixtureFiles() {
  for (const spec of SAMPLE_FIXTURES) {
    try {
      const res = await fetch(spec.path);
      if (!res.ok) continue;
      const blob = await res.blob();
      const name = spec.path.split("/").pop();
      const file = makeNamedFile(name, [blob], spec.type || blob.type || "application/octet-stream");
      pushSampleFile(file, spec.role, spec.caption);
    } catch (err) {
      console.warn("fixture skipped", spec.path, err);
    }
  }
}

function addSampleDumpFiles() {
  const makers = [
    () => [
      makeTextFile("pack_temp.csv", "time_s,temp_C,current_A\n0,25.1,1.02\n1,48.8,3.4\n2,71.2,3.5\n3,94.0,3.6\n", "text/csv"),
      "sensor",
      "Thermistor on pack can",
    ],
    () => [
      makeTextFile("charger.log", "2024-08-12 02:14:01 INFO charger ready\n2024-08-12 02:18:44 WARN cell_5 imbalance 42 mV\n2024-08-12 02:19:02 ERROR overheat pack_main\n"),
      "log",
      "Charger console",
    ],
    () => [
      makeTextFile(
        "housing_revC.step",
        "ISO-10303-21;\nHEADER;\nFILE_NAME('housing.step','2024-08-01T10:00:00');\nFILE_DESCRIPTION(('REV C bore fixture'),'2;1');\nFILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\nENDSEC;\nDATA;\n#1=PRODUCT('6082-T6-housing','part','');\nENDSEC;\n",
        "application/step",
      ),
      "cad",
      "REV C fixture",
    ],
    () => [
      makeTextFile("bore_finish.nc", "(REV C bore finish)\nT4 M6\nS4200 M3\nG1 Z-12.0 F180\nM30\n"),
      "process_doc",
      "Finish bore program",
    ],
    () => [tinyPngFile("setup_vise.png"), "setup_photo", "Kurt vise setup"],
    () => [tinyPngFile("vented_cells_result.png"), "result_image", "Vented 21700 cans"],
    () => [
      makeTextFile("mill_cert_lot24081.txt", "Mill certificate / CoA\nAlloy: 6082-T6\nLot: 24-081\n"),
      "material_doc",
      "Lot 24-081 mill cert",
    ],
  ];
  makers.forEach((make) => {
    try {
      const spec = make();
      pushSampleFile(spec[0], spec[1], spec[2]);
    } catch (err) {
      console.warn("Sample file skipped", err);
    }
  });
}

async function fillSample() {
  $("title").value = SAMPLE.title;
  $("domain").value = SAMPLE.domain;
  $("unexpected").value = SAMPLE.unexpected;
  $("objective").value = SAMPLE.objective;
  $("materials").value = SAMPLE.materials;
  $("processing").value = SAMPLE.processing;
  $("protocol").value = SAMPLE.protocol;
  $("setup").value = SAMPLE.setup;
  $("telemetry").value = SAMPLE.telemetry;
  $("logs").value = SAMPLE.logs;
  $("context").value = SAMPLE.context;
  document.querySelectorAll(".dossier details.disclose").forEach((el) => { el.open = true; });
  const before = pendingFiles.length;
  try {
    await addFixtureFiles();
  } catch (err) {
    console.warn("Fixture fetch failed", err);
  }
  if (pendingFiles.length === before) {
    try {
      addSampleDumpFiles();
    } catch (err) {
      console.warn("Sample dump files failed", err);
    }
  }
  renderFiles();
  renderCoverage();
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
  fd.append("protocol", $("protocol").value || $("processing").value);
  fd.append("telemetry", $("telemetry").value);
  fd.append("logs", $("logs").value);
  fd.append("context", $("context").value);
  fd.append("roles", JSON.stringify(pendingFiles.map((f) => f.role)));
  fd.append("captions", JSON.stringify(pendingFiles.map((f) => f.caption)));
  if (session && session.session_id) fd.append("session_id", session.session_id);
  pendingFiles.forEach((item) => fd.append("files", item.file, item.file.name));
  return fd;
}

async function diagnoseBundle() {
  clearError();
  setBusy(true, session ? "Re-ranking this thread from the current dossier…" : "Reading the dossier and ranking hypotheses…");
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
  if (!pendingMore.length) return;
  const fd = new FormData();
  fd.append("roles", JSON.stringify(pendingMore.map((f) => f.role)));
  fd.append("captions", JSON.stringify(pendingMore.map((f) => f.caption)));
  pendingMore.forEach((item) => fd.append("files", item.file, item.file.name));
  await runHitl($("moreFilesBtn"), "Adding files to this diagnosis…", () => api(`/api/sessions/${session.session_id}/artifacts`, {
    method: "POST",
    body: fd,
  }).then((data) => {
    pendingMore.splice(0).forEach((item) => {
      if (item.preview) URL.revokeObjectURL(item.preview);
    });
    $("moreFiles").value = "";
    renderMoreFiles();
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
  if (Array.isArray(health.evidence_slots) && health.evidence_slots.length) {
    evidenceSlots = health.evidence_slots;
  }
  const contract = health.contract_version ? ` · v${health.contract_version}` : "";
  $("status").classList.remove("is-loading");
  $("status").textContent = `${health.cases} catalog cases · ${health.engine_mode} engine${contract}`;
  setChatMode(health.engine_mode || health.llm_provider || "heuristic");
  renderChat([]);
  $("accepts").innerHTML = (health.accepts || []).map((item) => `<li><span class="accept-ico">${acceptIcon(item)}</span>${escapeHtml(item)}</li>`).join("");
  const cases = await api("/api/cases");
  $("caseSelect").innerHTML = cases.map((c) => {
    const label = c.failure_category_label ? ` · ${c.failure_category_label}` : "";
    return `<option value="${escapeHtml(c.id)}" data-preview="${escapeHtml(c.objective_preview || "")}" data-category="${escapeHtml(c.failure_category_label || "")}" data-regime="${escapeHtml(c.information_regime || "")}">${escapeHtml(c.id)} — ${escapeHtml(c.title)}${escapeHtml(label)}</option>`;
  }).join("");
  updateCasePreview();
  renderCoverage();
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

document.querySelectorAll(".attach-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const input = $(`attach-${btn.dataset.attach}`);
    if (input) input.click();
  });
});
document.querySelectorAll(".role-file").forEach((input) => {
  input.addEventListener("change", (e) => {
    addFiles(e.target.files, e.target.dataset.role);
    e.target.value = "";
  });
});

["unexpected", "objective", "materials", "processing", "protocol", "setup", "telemetry", "logs", "context"].forEach((id) => {
  const el = $(id);
  if (el) el.addEventListener("input", renderCoverage);
});

$("diagnoseBtn").addEventListener("click", () => diagnoseBundle().catch((e) => { showError(e.message); restoreWorkspace(); }));
$("diagnoseCase").addEventListener("click", () => diagnoseCase().catch((e) => { showError(e.message); restoreWorkspace(); }));
$("rejectBtn").addEventListener("click", () => rejectHyp().catch((e) => { showError(e.message); restoreWorkspace(); }));
$("addInfoBtn").addEventListener("click", () => addInfo().catch((e) => { showError(e.message); restoreWorkspace(); }));
$("followupBtn").addEventListener("click", () => followup().catch((e) => { showError(e.message); restoreWorkspace(); }));
$("moreFilesBtn").addEventListener("click", () => addMoreFiles().catch((e) => { showError(e.message); restoreWorkspace(); }));
$("moreFiles").addEventListener("change", (e) => addMorePending(e.target.files));
$("sampleFill").addEventListener("click", fillSample);
$("sampleFillEmpty").addEventListener("click", () => {
  fillSample();
  $("unexpected").scrollIntoView({ behavior: "smooth", block: "center" });
});
$("dismissError").addEventListener("click", clearError);

function applyChatSession(data) {
  const view = pickView(data);
  const hasDx = !!(data.diagnosis && (data.diagnosis.hypotheses || []).length);
  if (hasDx) {
    renderSession(data);
    return;
  }
  session = data;
  syncDiagnoseLabel();
  setChatMode(view.chat.mode || view.engineMode || "heuristic");
  renderChat(view.messages);
}

function isComposerExpanded() {
  const form = $("chatForm");
  return !!(form && form.classList.contains("is-expanded"));
}

function expandComposer() {
  const form = $("chatForm");
  const input = $("chatInput");
  const collapse = $("chatCollapse");
  const panel = $("chatPanel");
  if (!form || !input) return;
  form.classList.add("is-expanded");
  form.classList.remove("is-compact");
  form.setAttribute("aria-expanded", "true");
  input.rows = 8;
  if (panel) panel.classList.add("is-composing");
  if (collapse) {
    collapse.hidden = false;
    collapse.setAttribute("aria-label", "Collapse to one line");
    collapse.setAttribute("title", "Collapse to one line");
  }
}

function collapseComposer() {
  const form = $("chatForm");
  const input = $("chatInput");
  const collapse = $("chatCollapse");
  const panel = $("chatPanel");
  if (!form || !input) return;
  form.classList.remove("is-expanded");
  form.classList.add("is-compact");
  form.setAttribute("aria-expanded", "false");
  input.rows = 1;
  input.style.height = "";
  input.scrollTop = 0;
  if (panel) panel.classList.remove("is-composing");
  if (collapse) {
    collapse.hidden = false;
    collapse.setAttribute("aria-label", "Expand compose box");
    collapse.setAttribute("title", "Expand compose box");
  }
}

function insertChatNewline() {
  const input = $("chatInput");
  if (!input) return;
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? input.value.length;
  const value = input.value;
  input.value = `${value.slice(0, start)}\n${value.slice(end)}`;
  input.selectionStart = input.selectionEnd = start + 1;
}

function maybeExpandComposerFromContent() {
  const input = $("chatInput");
  if (!input || isComposerExpanded()) return;
  if (input.value.includes("\n") || input.scrollHeight > input.clientHeight + 2) {
    expandComposer();
  }
}

async function sendChat() {
  const input = $("chatInput");
  const text = (input.value || "").trim();
  if (!text) return;
  clearError();
  input.value = "";
  collapseComposer();
  const pending = [];
  if (session) {
    const existing = (pickView(session).messages || []).slice();
    existing.push({ role: "user", content: text });
    existing.push({ role: "assistant", content: "Thinking…", kind: "pending" });
    pending.push(...existing);
  } else {
    pending.push({ role: "user", content: text });
    pending.push({ role: "assistant", content: "Thinking…", kind: "pending" });
  }
  renderChat(pending);
  const last = $("chatLog") && $("chatLog").lastElementChild;
  if (last) last.classList.add("chat-pending");
  setWorking($("chatSend"), true);
  try {
    const path = session
      ? `/api/sessions/${session.session_id}/chat`
      : "/api/chat";
    const data = await api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });
    applyChatSession(data);
  } catch (err) {
    showError(err.message);
    if (session) renderChat(pickView(session).messages);
    else renderChat([]);
  } finally {
    setWorking($("chatSend"), false);
  }
}

$("chatForm").addEventListener("submit", (e) => {
  e.preventDefault();
  sendChat().catch((err) => { showError(err.message); restoreWorkspace(); });
});
$("chatInput").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.isComposing || e.keyCode === 229) return;
  if (e.metaKey || e.ctrlKey) {
    e.preventDefault();
    sendChat().catch((err) => { showError(err.message); restoreWorkspace(); });
    return;
  }
  if (!e.shiftKey && !isComposerExpanded()) {
    e.preventDefault();
    if (!(e.target.value || "").trim()) return;
    expandComposer();
    insertChatNewline();
  }
});
$("chatInput").addEventListener("input", maybeExpandComposerFromContent);
$("chatCollapse").addEventListener("click", () => {
  if (isComposerExpanded()) collapseComposer();
  else expandComposer();
  const input = $("chatInput");
  if (input) input.focus();
});

bindHitlTabs();
updateFileCount();
renderCoverage();
boot().catch((e) => {
  setBusy(false);
  $("status").classList.remove("is-loading");
  $("status").classList.add("is-error");
  $("status").textContent = "Engine unreachable";
  $("accepts").innerHTML = "<li>Could not load accepted file types</li>";
  showError(e.message || "Could not reach the EpiDebug API.");
  restoreWorkspace();
});
