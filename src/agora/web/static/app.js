const MODE_CONFIG = {
  "compact-adversarial": {
    label: "Compact Adversarial",
    subtitle: "Short debate setup",
    style: "ein-mdp",
    min: 4,
    max: 5,
    defaultRounds: 5,
    defaultConvergence: "agree-marker",
    roundsLabel: "Round Cap",
    unit: "rounds",
    autoVerdict: true,
    workflow: "compact-debate",
    roles: [
      { key: "lead_a", label: "Lead A", kinds: ["claude-code-new", "anthropic-code", "chatgpt-web", "claude-web", "gemini-web", "codex", "gemini-cli"] },
      { key: "lead_b", label: "Lead B", kinds: ["gemini-cli", "codex", "chatgpt-web", "claude-code-new", "claude-web", "gemini-web", "anthropic-code"] },
      { key: "lead_c", label: "Lead C", kinds: ["codex", "chatgpt-web", "claude-web", "gemini-web", "claude-code-new", "gemini-cli", "anthropic-code", "openclaw"] },
    ],
  },
  "critic-review": {
    label: "Critic Review",
    subtitle: "Longer adversarial review",
    style: "critic-terminate",
    min: 4,
    max: 15,
    defaultRounds: 8,
    defaultConvergence: "terminate-majority",
    roundsLabel: "Round Cap",
    unit: "rounds",
    autoVerdict: true,
    workflow: "compact-debate",
    roles: [
      { key: "author", label: "Author", kinds: ["claude-code-new", "anthropic-code", "chatgpt-web", "claude-web", "gemini-web", "codex", "gemini-cli"] },
      { key: "critic", label: "Critic", kinds: ["gemini-cli", "codex", "claude-code-new", "chatgpt-web", "claude-web", "gemini-web", "anthropic-code"] },
      { key: "reviewer", label: "Reviewer", kinds: ["codex", "chatgpt-web", "claude-web", "gemini-web", "claude-code-new", "gemini-cli", "anthropic-code", "openclaw"] },
    ],
  },
  "primary-pair": {
    label: "Primary Pair",
    subtitle: "Persistent two-column convergence",
    style: "primary-pair",
    min: 4,
    max: 15,
    defaultRounds: 6,
    defaultConvergence: "terminate-majority",
    roundsLabel: "Convergence Cap",
    unit: "rounds",
    autoVerdict: true,
    workflow: "pi-monitor",
    roles: [
      { key: "primary_a", label: "LLM1", kinds: ["codex", "claude-code-new", "gemini-cli"], defaultModel: "gpt-5.4-mini", defaultEffort: "low" },
      { key: "primary_b", label: "LLM2", kinds: ["gemini-cli", "codex", "claude-code-new"], defaultModel: "gemini-2.5-flash-lite" },
      { key: "secondary", label: "LLM3", kinds: ["claude-code-new", "codex", "gemini-cli"], defaultModel: "MiniMax-M2.7-highspeed" },
    ],
  },
  "code-audit-loop": {
    label: "Code Audit Loop",
    subtitle: "Fix, audit, validate",
    style: "exhaustion-loop",
    min: 4,
    max: 50,
    defaultRounds: 20,
    defaultConvergence: "adversarial-exhaustion",
    roundsLabel: "Cycle Cap",
    unit: "cycles",
    autoVerdict: false,
    workflow: "classic-war-room",
    roles: [
      { key: "fixer", label: "Fixer", kinds: ["claude-code-new"] },
      { key: "auditor", label: "Auditor", kinds: ["gemini-cli"] },
      { key: "validator", label: "Validator", kinds: ["codex"] },
    ],
  },
};

const DRIVER_MODEL_OPTIONS = {
  "claude-code-new": ["MiniMax-M2.7-highspeed", "MiniMax-M2.7", "claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"],
  "anthropic-code": ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"],
  codex: ["gpt-5.4", "gpt-5.5", "gpt-5.4-mini"],
  "gemini-cli": [
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
  ],
  "chatgpt-web": ["chatgpt-latest"],
  "claude-web": ["claude-web-default"],
  "gemini-web": ["gemini-web-default"],
  openclaw: ["agent-default"],
};

const EFFORT_OPTIONS = ["Low", "Medium", "High"];
const PI_SECONDARY_HEIGHT_KEY = "agora.piSecondaryHeight.v4";

const LEGACY_MODE_MAP = {
  "ein-mdp": "compact-adversarial",
  "critic-terminate": "critic-review",
  "exhaustion-loop": "code-audit-loop",
};

const PHASE_LABELS = {
  positions: "Opening views",
  contrarian: "Objections",
  critic: "Critic review",
  debate: "Debate",
  synthesis: "Synthesis",
  verdict: "Verdict",
  fix: "Fix",
  audit_gemini: "Gemini audit",
  audit_codex: "Codex validation",
};

const KIND_LABELS = {
  "claude-code-new": "Claude MiniMax",
  "anthropic-code": "Anthropic Code",
  codex: "Codex CLI",
  "gemini-cli": "Gemini CLI",
  "chatgpt-web": "ChatGPT Web",
  "claude-web": "Claude Web",
  "gemini-web": "Gemini Web",
  openclaw: "OpenClaw",
  fake: "Fake",
};

const EXHAUSTION_ACTIVE_ROLE = {
  fix: "fixer",
  audit_gemini: "auditor",
  audit_codex: "validator",
};

const ACTIVE_CLI_KINDS = ["claude-code-new", "gemini-cli", "codex"];
const EFFORT_CAPABLE_KINDS = ["codex"];

const KIND_CLASS = {
  "claude-code-new": "kind-claude-code-new",
  "claude-code-resume": "kind-claude-code-new",
  codex: "kind-codex",
  "gemini-cli": "kind-gemini-cli",
  openclaw: "kind-openclaw",
  fake: "kind-fake",
};

const state = {
  rooms: new Map(),
  activeRoomId: null,
  drivers: [],
  ops: null,
  wsState: "connecting",
  currentTab: "debates",
  roomsCollapsed: false,
  thinking: new Map(),
  explorer: { targetInputId: null, isDirOnly: false, currentPath: "." },
  recorder: { mediaRecorder: null, chunks: [], stream: null },
  piSecondaryResize: { active: false, startY: 0, startHeight: 0 },
};

const el = {
  body: document.body,
  layout: document.querySelector("main.layout"),
  roomsActive: document.getElementById("roomsActive"),
  roomsArchived: document.getElementById("roomsArchived"),
  roomsCount: document.getElementById("roomsCount"),
  transcript: document.getElementById("transcript"),
  transcriptMeta: document.getElementById("transcriptMeta"),
  participantLegend: document.getElementById("participantLegend"),
  transcriptHeader: document.getElementById("transcriptHeader"),
  commandForm: document.getElementById("commandForm"),
  commandInput: document.getElementById("commandInput"),
  commandScope: document.getElementById("commandScope"),
  commandHint: document.getElementById("commandHint"),
  status: document.getElementById("status"),
  footerStatus: document.getElementById("footerStatus"),
  wsState: document.getElementById("wsState"),
  activeRoomState: document.getElementById("activeRoomState"),
  lastEventState: document.getElementById("lastEventState"),
  statusDot: document.getElementById("setStatus"),
  newDebateModal: document.getElementById("newDebateModal"),
  newDebateForm: document.getElementById("newDebateForm"),
  openNewDebateBtn: document.getElementById("openNewDebateBtn"),
  toggleRoomsBtn: document.getElementById("toggleRoomsBtn"),
  cancelNewDebateBtn: document.getElementById("cancelNewDebateBtn"),
  topicInput: document.getElementById("topicInput"),
  modeInput: document.getElementById("modeInput"),
  roundsInput: document.getElementById("roundsInput"),
  roundsRow: document.getElementById("roundsRow"),
  roundsLabel: document.getElementById("roundsLabel"),
  roundsFieldLabel: document.getElementById("roundsFieldLabel"),
  roleCards: document.getElementById("roleCards"),
  participantsList: document.getElementById("participantsList"),
  autoVerdictInput: document.getElementById("autoVerdictInput"),
  workflowFields: document.getElementById("workflowFields"),
  targetFileInput: document.getElementById("targetFileInput"),
  dodFileInput: document.getElementById("dodFileInput"),
  workflowNotesInput: document.getElementById("workflowNotesInput"),
  pickTargetBtn: document.getElementById("pickTargetBtn"),
  pickDodBtn: document.getElementById("pickDodBtn"),
  modalModeLabel: document.getElementById("modalModeLabel"),
  explorerModal: document.getElementById("explorerModal"),
  explorerList: document.getElementById("explorerList"),
  explorerPath: document.getElementById("explorerCurrentPath"),
  cancelExplorerBtn: document.getElementById("cancelExplorerBtn"),
  warRoomPlaceholder: document.getElementById("warRoomPlaceholder"),
  warRoomContent: document.getElementById("warRoomContent"),
  warRoomOverview: document.getElementById("warRoomOverview"),
  warRoomNoMonitor: document.getElementById("warRoomNoMonitor"),
  warRoomClassic: document.getElementById("warRoomClassic"),
  warRoomPi: document.getElementById("warRoomPi"),
  cockpitTarget: document.getElementById("cockpitTarget"),
  cockpitDod: document.getElementById("cockpitDod"),
  cockpitControls: document.getElementById("cockpitControls"),
  cockpitFindings: document.getElementById("cockpitFindings"),
  cockpitLog: document.getElementById("cockpitLog"),
  stabilityBar: document.getElementById("stabilityBar"),
  cycleCount: document.getElementById("cycleCount"),
  piModeLabel: document.getElementById("piModeLabel"),
  piWorkflowLabel: document.getElementById("piWorkflowLabel"),
  piControls: document.getElementById("piControls"),
  piSecondaryTitle: document.getElementById("piSecondaryTitle"),
  piSecondaryMeta: document.getElementById("piSecondaryMeta"),
  piSecondaryBody: document.getElementById("piSecondaryBody"),
  piSecondaryPanel: document.getElementById("piSecondaryPanel"),
  piSecondaryResize: document.getElementById("piSecondaryResize"),
  piPrimaryATitle: document.getElementById("piPrimaryATitle"),
  piPrimaryAMeta: document.getElementById("piPrimaryAMeta"),
  piPrimaryABody: document.getElementById("piPrimaryABody"),
  piPrimaryBTitle: document.getElementById("piPrimaryBTitle"),
  piPrimaryBMeta: document.getElementById("piPrimaryBMeta"),
  piPrimaryBBody: document.getElementById("piPrimaryBBody"),
  opsTranscript: document.getElementById("opsTranscript"),
  opsForm: document.getElementById("opsForm"),
  opsInput: document.getElementById("opsInput"),
  opsModelSelect: document.getElementById("opsModelSelect"),
  opsMicBtn: document.getElementById("opsMicBtn"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function compactId(value) {
  return String(value || "").replace(/-1$/, "");
}

function canonicalMode(value) {
  const key = String(value || "").trim();
  if (MODE_CONFIG[key]) return key;
  return LEGACY_MODE_MAP[key] || "compact-adversarial";
}

function roomMode(room) {
  return canonicalMode(room?.room_config?.ui_mode || room?.style || "compact-adversarial");
}

function styleLabel(style) {
  if (MODE_CONFIG[style]) return MODE_CONFIG[style].label;
  if (style === "ein-mdp") return "Compact Adversarial";
  if (style === "critic-terminate") return "Critic Review";
  if (style === "exhaustion-loop") return "Code Audit Loop";
  return "Debate";
}

function modeConfig(mode) {
  return MODE_CONFIG[canonicalMode(mode)];
}

function phaseLabel(phase) {
  return PHASE_LABELS[phase] || String(phase || "phase");
}

function primaryPairArtifactMeta(artifactId) {
  const text = String(artifactId || "");
  const match = text.match(/^([ABS])(\d+)$/);
  if (!match) return { label: text || "turn", round: 0 };
  const family = match[1];
  const ordinal = Number(match[2]);
  if (family === "S") return { label: "LLM3 Seed", round: 1 };
  if (ordinal === 0) return { label: family === "A" ? "LLM1 Seed" : "LLM2 Seed", round: 1 };
  if (ordinal === 1) return { label: family === "A" ? "LLM1 First Document" : "LLM2 First Document", round: 2 };
  return {
    label: family === "A" ? `LLM1 Revision ${ordinal - 1}` : `LLM2 Revision ${ordinal - 1}`,
    round: ordinal + 1,
  };
}

function primaryPairEntryMeta(entry) {
  const info = primaryPairArtifactMeta(entry?.phase);
  const round = info.round || Number(entry?.round || 0);
  return {
    label: info.label || String(entry?.phase || "turn"),
    round,
    time: formatTime(entry?.ts),
  };
}

function kindLabel(kind) {
  return KIND_LABELS[kind] || String(kind || "driver");
}

function driverLabel(driver) {
  if (!driver) return "";
  if (driver.kind === "claude-code-new") return "Claude MiniMax";
  return driver.display_name || driver.id;
}

function supportsEffort(driverOrKind) {
  const kind = typeof driverOrKind === "string" ? driverOrKind : driverOrKind?.kind;
  return EFFORT_CAPABLE_KINDS.includes(kind);
}

function driverById(driverId) {
  return state.drivers.find((item) => item.id === driverId) || null;
}

function sortedRooms() {
  return [...state.rooms.values()].sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
}

function roomNumber(roomOrId) {
  const id = typeof roomOrId === "string" ? roomOrId : roomOrId?.id;
  const index = sortedRooms().findIndex((room) => room.id === id);
  return index >= 0 ? index + 1 : null;
}

function participantById(room, participantId) {
  return (room?.participants || []).find((item) => item.id === participantId) || null;
}

function roleAssignments(room) {
  const assignments = room?.room_config?.role_assignments;
  return Array.isArray(assignments) ? assignments : [];
}

function assignmentByDriver(room, driverId) {
  return roleAssignments(room).find((item) => item.driver_id === driverId) || null;
}

function assignmentByRole(room, roleKey) {
  return roleAssignments(room).find((item) => item.role_key === roleKey) || null;
}

function visibleRoleLabel(assignment, fallback = "LLM") {
  const key = assignment?.role_key;
  if (key === "primary_a") return "LLM1";
  if (key === "primary_b") return "LLM2";
  if (key === "secondary") return "LLM3";
  return assignment?.label || assignment?.role_key || fallback;
}

function visibleParticipantLabel(room, participant) {
  const assignment = assignmentByDriver(room, participant?.id);
  return assignment ? visibleRoleLabel(assignment) : (participant?.display_name || participant?.id || "Participant");
}

function modelOptionsForKind(kind, currentModel = null) {
  const seen = new Set();
  return [currentModel, ...(DRIVER_MODEL_OPTIONS[kind] || ["local-default"])]
    .filter(Boolean)
    .filter((value) => {
      const key = String(value);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function effectiveModel(assignment, driver, participant) {
  const requested = String(assignment?.requested_model || "").trim();
  if (requested && requested !== "Default") return requested;
  const personaModel = String(participant?.persona || "").match(/model=([^/]+)/)?.[1]?.trim();
  if (personaModel && personaModel !== "Default") return personaModel;
  return driver?.current_model || modelOptionsForKind(driver?.kind || "", null)[0] || "local-default";
}

function effortLabel(value) {
  const raw = String(value || "medium").trim().toLowerCase();
  if (!raw || raw === "none") return "N/A";
  if (raw === "high") return "High";
  if (raw === "low") return "Low";
  return "Medium";
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function kindClass(kind) {
  return KIND_CLASS[kind] || "kind-fake";
}

function markerClass(status) {
  if (status === "running") return "ok";
  if (status === "paused") return "warn";
  if (status === "failed" || status === "error") return "danger";
  return "";
}

function roleStatus(room, assignment) {
  if (!room || !assignment) return "waiting";
  const pp = room.room_config?.primary_pair || {};
  if (room.style === "primary-pair" || room.room_config?.ui_mode === "primary-pair") {
    if (pp.active_participant_id === assignment.driver_id && room.status === "running") return "thinking";
    if (room.status === "done") return "done";
    return "waiting";
  }
  if (isThinking(room.id, assignment.driver_id)) return "thinking";
  if (room.next_up === "all") return "active";
  if (room.next_up === assignment.driver_id) return "active";
  if (EXHAUSTION_ACTIVE_ROLE[room.current_phase] === assignment.role_key) return "current";
  if (room.status === "done") return "done";
  return "waiting";
}

function thinkingKey(roomId, participantId) {
  return `${roomId || ""}:${participantId || ""}`;
}

function isThinking(roomId, participantId) {
  return state.thinking.get(thinkingKey(roomId, participantId)) === true;
}

function setThinking(roomId, participantId, inFlight) {
  if (!roomId || !participantId) return;
  state.thinking.set(thinkingKey(roomId, participantId), Boolean(inFlight));
}

function activeAssignment(room) {
  if (!room) return null;
  const pp = room.room_config?.primary_pair || {};
  if (room.status === "running" && pp.active_participant_id) return assignmentByDriver(room, pp.active_participant_id);
  if (room.next_up && room.next_up !== "all") return assignmentByDriver(room, room.next_up);
  const phaseRole = EXHAUSTION_ACTIVE_ROLE[room.current_phase];
  if (phaseRole) return assignmentByRole(room, phaseRole);
  const assignments = roleAssignments(room);
  return assignments[0] || null;
}

function progressForRoom(room, config) {
  if (!room) return { percent: 0, label: "No room" };
  if (room.status === "done") return { percent: 100, label: "Complete" };
  if (room.style === "primary-pair" || room.room_config?.ui_mode === "primary-pair") {
    const pp = room.room_config?.primary_pair || {};
    const turns = Number(pp.turns || (room.transcript || []).length || 0);
    const expected = Math.max(5, Number(room.max_total_rounds || config.defaultRounds || 6) + 1);
    const percent = Math.min(98, Math.round((turns / expected) * 100));
    return { percent, label: pp.current_artifact ? `${pp.current_artifact} / ${pp.current_event || "running"}` : `${turns}/${expected} turns` };
  }
  if (config.style === "exhaustion-loop") {
    const cycle = Number(room.exhaustion_cycle || 0);
    const max = Math.max(1, Number(room.max_total_rounds || config.defaultRounds || 1));
    const phaseOffset = { fix: 0.12, audit_gemini: 0.55, audit_codex: 0.82 }[room.current_phase] || 0.08;
    const percent = Math.min(98, Math.round(((cycle + phaseOffset) / max) * 100));
    return { percent, label: `${cycle}/${max} cycles` };
  }
  const phaseCount = Math.max(1, (room.phase_sequence || []).length || 1);
  const phaseIndex = Math.max(0, Number(room.current_phase_index || 0));
  const maxRounds = Math.max(1, Number(room.max_total_rounds || config.defaultRounds || 1));
  const round = Math.max(1, Number(room.current_round || 1));
  const percent = Math.min(98, Math.round(((phaseIndex / phaseCount) + (round / maxRounds / phaseCount)) * 100));
  return { percent, label: `round ${round}/${maxRounds}` };
}

function roomOverviewHtml(room, config) {
  const assignments = roleAssignments(room);
  const progress = progressForRoom(room, config);
  const active = activeAssignment(room);
  const activeParticipant = active ? participantById(room, active.driver_id) : null;
  const activeDriver = active ? driverById(active.driver_id) : null;
  const activeLabel = room.next_up === "all"
    ? "All participants"
    : active
      ? `${visibleRoleLabel(active)} / ${driverLabel(activeDriver) || active.driver_id}`
      : room.status === "done"
        ? "Complete"
        : "Waiting";
  const activeModel = active ? effectiveModel(active, activeDriver, activeParticipant) : "";

  const roleCards = assignments.map((assignment) => {
    const participant = participantById(room, assignment.driver_id);
    const driver = driverById(assignment.driver_id);
    const status = roleStatus(room, assignment);
    return `
      <article class="overview-role-card ${status === "active" || status === "current" || status === "thinking" ? "active" : ""} ${status === "thinking" ? "thinking" : ""}">
        <div class="overview-role-head">
          <strong>${escapeHtml(visibleRoleLabel(assignment, "Role"))}</strong>
          <span class="pill ${status === "active" || status === "current" ? "ok" : status === "thinking" ? "pulse" : status === "done" ? "" : "warn"}">${escapeHtml(status)}</span>
        </div>
        <div class="overview-role-line">${escapeHtml(driverLabel(driver) || participant?.display_name || assignment.driver_id)}</div>
        <div class="overview-role-meta">
          <span>${escapeHtml(kindLabel(driver?.kind || participant?.kind))}</span>
          <span>${escapeHtml(effectiveModel(assignment, driver, participant))}</span>
          ${supportsEffort(driver || participant?.kind) ? `<span>${escapeHtml(effortLabel(assignment.effort))}</span>` : ""}
        </div>
      </article>
    `;
  }).join("");

  return `
    <div class="overview-main">
      <div>
        <div class="overview-kicker">Active Mode</div>
        <h2>${escapeHtml(config.label)}</h2>
        <p>${escapeHtml(config.subtitle)}</p>
      </div>
      <div class="overview-status">
        <span class="pill ${markerClass(room.status)}">${escapeHtml(room.status || "idle")}</span>
        <span class="pill">${escapeHtml(phaseLabel(room.current_phase))}</span>
        <span class="pill">${escapeHtml(progress.label)}</span>
      </div>
    </div>
    <div class="overview-metrics">
      <div class="overview-metric wide">
        <span class="metric-label">Current / Next LLM</span>
        <strong>${escapeHtml(activeLabel)}</strong>
        <small>${escapeHtml(activeModel || "No model selected")}</small>
      </div>
      <div class="overview-metric">
        <span class="metric-label">Progress</span>
        <strong>${escapeHtml(`${progress.percent}%`)}</strong>
        <div class="progress-bar"><div class="progress-fill" style="width:${progress.percent}%"></div></div>
      </div>
      <div class="overview-metric">
        <span class="metric-label">Messages</span>
        <strong>${escapeHtml((room.transcript || []).length)}</strong>
        <small>${escapeHtml(room.next_up === "all" ? "parallel turn" : "serial turn")}</small>
      </div>
    </div>
    <div class="overview-role-grid">${roleCards || `<div class="empty-text">No role assignments saved.</div>`}</div>
    <div class="overview-ledger">
      <span class="metric-label">Backend Ledger</span>
      <code>${escapeHtml(`${room.room_dir || "(room dir unavailable)"}\\turn-ledger.jsonl`)}</code>
    </div>
  `;
}

function setStatus(text, type = "info") {
  if (el.status) {
    el.status.textContent = text;
    el.status.className = `status status-${type}`;
  }
  if (el.footerStatus) el.footerStatus.textContent = text;
}

function setLastEvent(text) {
  if (el.lastEventState) el.lastEventState.textContent = `Last event: ${text}`;
}

function updateTopStatus() {
  if (el.wsState) el.wsState.textContent = `WS ${state.wsState}`;
  if (el.activeRoomState) el.activeRoomState.textContent = state.activeRoomId ? `Room ${compactId(state.activeRoomId)}` : "No room";
  if (el.statusDot) {
    el.statusDot.classList.toggle("ok", state.wsState === "connected");
    el.statusDot.classList.toggle("error", state.wsState === "disconnected");
  }
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
  }
  if (!response.ok) {
    const detail = data?.detail || data?.error || response.statusText;
    throw new Error(detail);
  }
  return data;
}

async function refreshDrivers() {
  const data = await fetchJson("/api/drivers");
  state.drivers = data.drivers || [];
}

async function refreshRooms() {
  const data = await fetchJson("/api/rooms");
  state.rooms.clear();
  for (const room of data || []) state.rooms.set(String(room.id), room);
  if (state.activeRoomId && !state.rooms.has(state.activeRoomId)) state.activeRoomId = null;
  if (!state.activeRoomId) {
    const first = [...state.rooms.values()].find((room) => !room.archived);
    if (first) state.activeRoomId = String(first.id);
  }
}

async function refreshRoom(roomId) {
  const room = await fetchJson(`/api/rooms/${encodeURIComponent(roomId)}`);
  state.rooms.set(String(room.id), room);
}

async function refreshOps() {
  try {
    state.ops = await fetchJson("/api/ops");
  } catch (err) {
    state.ops = { transcript: [], allowed_models: [], error: String(err.message || err) };
  }
}

function renderRooms() {
  if (!el.roomsActive || !el.roomsArchived) return;
  const rooms = sortedRooms();
  const active = rooms.filter((room) => !room.archived);
  const archived = rooms.filter((room) => room.archived);

  if (el.roomsCount) el.roomsCount.textContent = `${active.length} active`;

  const draw = (list, target) => {
    target.innerHTML = "";
    if (!list.length) {
      target.innerHTML = `<div class="empty-state"><div class="empty-text">No rooms.</div></div>`;
      return;
    }
    for (const room of list) {
      const number = roomNumber(room);
      const button = document.createElement("button");
      button.type = "button";
      button.className = `room-item status-${escapeHtml(room.status)} ${room.id === state.activeRoomId ? "active" : ""}`;
      button.innerHTML = `
        <div>
          <div class="room-title-line"><span class="room-number">#${escapeHtml(number || "?")}</span><div class="room-topic">${escapeHtml(room.topic || "Untitled")}</div></div>
          <div class="room-meta">
            <span class="pill ${markerClass(room.status)}">${escapeHtml(room.status || "idle")}</span>
            <span class="pill">${escapeHtml(styleLabel(roomMode(room)))}</span>
            <span class="pill">${escapeHtml(room.current_phase || "phase")}</span>
          </div>
        </div>
        <span class="pill">${escapeHtml(formatTime(room.updated_at))}</span>
      `;
      button.addEventListener("click", () => {
        state.activeRoomId = String(room.id);
        renderAll();
      });
      target.appendChild(button);
    }
  };

  draw(active, el.roomsActive);
  draw(archived, el.roomsArchived);
}

function renderLegend(room) {
  if (!el.participantLegend) return;
  el.participantLegend.innerHTML = "";
  for (const participant of room?.participants || []) {
    const chip = document.createElement("span");
    chip.className = `legend-chip ${kindClass(participant.kind)}`;
    chip.innerHTML = `<span class="legend-dot"></span><span>${escapeHtml(compactId(participant.id))}</span>`;
    el.participantLegend.appendChild(chip);
  }
}

function renderHeader(room) {
  if (!el.transcriptHeader) return;
  if (!room) {
    el.transcriptHeader.classList.add("hidden");
    return;
  }

  const isDone = room.status === "done";
  const canPause = room.status === "running";
  const canResume = room.status === "paused" || room.status === "idle";
  const config = modeConfig(roomMode(room));
  const number = roomNumber(room);
  const active = activeAssignment(room);
  const activeParticipant = active ? participantById(room, active.driver_id) : null;
  const activeDriver = active ? driverById(active.driver_id) : null;
  const activeText = room.next_up === "all"
    ? "All participants"
    : active
      ? `${visibleRoleLabel(active)} / ${driverLabel(activeDriver) || active.driver_id} / ${effectiveModel(active, activeDriver, activeParticipant)}${supportsEffort(activeDriver) ? ` / ${effortLabel(active.effort)}` : ""}`
      : room.status === "done"
        ? "Complete"
        : "Waiting";
  const participantsOptions = (room.participants || [])
    .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(visibleParticipantLabel(room, p))}</option>`)
    .join("");

  el.transcriptHeader.classList.remove("hidden");
  el.transcriptHeader.innerHTML = `
    <div class="header-topic">
      <strong>${number ? `#${escapeHtml(number)} ` : ""}${escapeHtml(room.topic || "Untitled")}</strong>
      <span>ID: ${escapeHtml(compactId(room.id))} | Mode: ${escapeHtml(config.label)} | Phase: ${escapeHtml(phaseLabel(room.current_phase))} | Active: ${escapeHtml(activeText)}</span>
    </div>
    <div class="header-actions">
      <button class="ghost-btn" data-command="/pause" ${canPause ? "" : "disabled"}>Pause</button>
      <button class="ghost-btn" data-command="/resume" ${canResume ? "" : "disabled"}>Resume</button>
      <button class="ghost-btn" data-command="/stop" ${isDone ? "disabled" : ""}>Stop</button>
      <button class="ghost-btn" data-archive="true">Archive</button>
      ${isDone ? '<button class="ghost-btn" data-regenerate="true">Regenerate verdict</button>' : ""}
    </div>
    ${isDone ? `
    <div class="follow-up-composer">
      <select class="follow-up-select">${participantsOptions}</select>
      <input type="text" class="follow-up-input" placeholder="Ask a follow-up..." />
      <button class="primary-btn" data-followup="true">Send</button>
    </div>
    ` : ""}
  `;

  if (room.warning_detail) {
    const warning = document.createElement("div");
    warning.className = "debate-warning";
    warning.textContent = room.warning_detail;
    el.transcriptHeader.appendChild(warning);
  }

  el.transcriptHeader.querySelectorAll("[data-command]").forEach((button) => {
    button.addEventListener("click", () => runRoomCommand(button.dataset.command));
  });
  el.transcriptHeader.querySelector("[data-archive]")?.addEventListener("click", archiveActiveRoom);
  el.transcriptHeader.querySelector("[data-regenerate]")?.addEventListener("click", regenerateVerdict);
  el.transcriptHeader.querySelector("[data-followup]")?.addEventListener("click", sendFollowUp);
}

function renderTranscript(room) {
  if (!el.transcript || !el.transcriptMeta) return;
  el.transcript.innerHTML = "";

  if (!room) {
    el.transcriptMeta.textContent = "No room selected";
    el.transcript.innerHTML = `<div class="empty-state"><div class="empty-title">No room selected</div><div class="empty-text">Create or choose a room.</div></div>`;
    return;
  }

  const entries = room.transcript || [];
  el.transcriptMeta.textContent = `${entries.length} messages`;

  const summary = document.createElement("section");
  summary.className = "summary-bar";
  summary.innerHTML = `
    <span class="pill ${markerClass(room.status)}">${escapeHtml(room.status)}</span>
    <span class="pill">${escapeHtml(styleLabel(roomMode(room)))}</span>
    <span class="pill">${escapeHtml(room.current_phase || "phase")}</span>
    <span class="pill">${escapeHtml(room.agree_count || 0)} AGREE</span>
    <span class="pill">${escapeHtml(room.terminate_count || 0)} TERMINATE</span>
  `;
  el.transcript.appendChild(summary);

  if (room.verdict_text) {
    const verdict = document.createElement("section");
    verdict.className = "verdict-panel";
    verdict.innerHTML = `
      <div class="verdict-head">
        <span>Verdict</span>
        <span>${escapeHtml(room.verdict_author || "participant")}</span>
      </div>
      <div class="verdict-body">${escapeHtml(room.verdict_text)}</div>
    `;
    el.transcript.appendChild(verdict);
  }

  if (!entries.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `<div class="empty-title">No transcript yet</div><div class="empty-text">${escapeHtml(room.status || "idle")}</div>`;
    el.transcript.appendChild(empty);
    return;
  }

  for (const entry of entries) {
    const participantMeta = (room.participants || []).find((item) => item.id === entry.participant_id);
    const speakerName = participantMeta?.display_name || compactId(entry.participant_id || entry.role || "system");
    const card = document.createElement("article");
    card.className = `msg-card role-${escapeHtml(entry.role || "participant")} ${kindClass(entry.participant_kind)} ${entry.error ? "has-error" : ""}`;
    const content = entry.error ? `ERROR: ${entry.error}` : entry.content;
    card.innerHTML = `
        <header class="msg-header">
        <div class="msg-speaker">
          <span class="speaker-dot"></span>
          <span>${escapeHtml(speakerName)}</span>
        </div>
        <div class="msg-meta">${escapeHtml(entry.phase || "")} / r${escapeHtml(entry.round || 0)} / ${escapeHtml(formatTime(entry.ts))}</div>
      </header>
      <div class="msg-content">${escapeHtml(content || "")}</div>
    `;
    el.transcript.appendChild(card);
  }
}

function renderCommand(room) {
  if (el.commandScope) el.commandScope.textContent = room ? compactId(room.id) : "No room selected";
}

function renderLayoutState() {
  el.layout?.classList.toggle("rooms-collapsed", state.roomsCollapsed);
  if (el.toggleRoomsBtn) el.toggleRoomsBtn.textContent = state.roomsCollapsed ? "Rooms" : "Hide";
}

function resetNeuralLink() {
  for (const id of ["thoughtClaude", "thoughtGemini", "thoughtCodex"]) {
    const target = document.getElementById(id);
    if (target) target.textContent = "Waiting.";
  }
  document.querySelectorAll(".neural-slot").forEach((slot) => slot.classList.remove("active"));
}

function renderWarRoom(room) {
  if (!el.warRoomPlaceholder || !el.warRoomContent) return;
  const mode = roomMode(room);
  const config = modeConfig(mode);
  const showClassic = config.workflow === "classic-war-room";
  const showPi = config.workflow === "pi-monitor";
  const hasRoom = Boolean(room);
  const hasMonitor = hasRoom && (showClassic || showPi);
  el.warRoomPlaceholder.classList.toggle("hidden", hasRoom);
  el.warRoomContent.classList.toggle("hidden", !hasRoom);
  el.warRoomNoMonitor?.classList.toggle("hidden", hasMonitor);
  el.warRoomClassic?.classList.toggle("hidden", !hasMonitor || !showClassic);
  el.warRoomPi?.classList.toggle("hidden", !hasMonitor || !showPi);
  if (!hasRoom) return;
  if (el.warRoomOverview) el.warRoomOverview.innerHTML = roomOverviewHtml(room, config);
  if (!hasMonitor) {
    return;
  }

  if (showPi) {
    renderPiWarRoom(room, config);
    return;
  }
  el.cockpitTarget.textContent = room.target_file || "--";
  el.cockpitDod.textContent = room.dod_file || "--";
  el.cockpitControls.innerHTML = `
    <button class="ghost-btn" data-command="/pause">Pause</button>
    <button class="ghost-btn" data-command="/resume">Resume</button>
    <button class="secondary-btn" data-command="/stop">Stop</button>
  `;
  el.cockpitControls.querySelectorAll("[data-command]").forEach((button) => {
    button.addEventListener("click", () => runRoomCommand(button.dataset.command));
  });

  document.querySelectorAll(".chain-node").forEach((node) => {
    node.classList.toggle("active", node.dataset.phase === room.current_phase);
  });

  const entries = room.transcript || [];
  const lastGemini = [...entries].reverse().find((entry) => entry.phase === "audit_gemini");
  const lastCodex = [...entries].reverse().find((entry) => entry.phase === "audit_codex");
  document.getElementById("arrowGeminiGap")?.classList.toggle("hidden", !lastGemini || String(lastGemini.content).includes("ZERO FINDINGS"));
  document.getElementById("arrowCodexGap")?.classList.toggle("hidden", !lastCodex || String(lastCodex.content).includes("ZERO FINDINGS"));

  const lastAudit = [...entries].reverse().find((entry) => String(entry.phase || "").includes("audit"));
  el.cockpitFindings.textContent = lastAudit ? `[${lastAudit.participant_id}]\n\n${lastAudit.content || lastAudit.error || ""}` : "Waiting for audit.";

  el.cockpitLog.innerHTML = "";
  if (!entries.length) {
    el.cockpitLog.textContent = "No events.";
  } else {
    for (const entry of entries) {
      const row = document.createElement("div");
      row.className = `log-entry ${entry.error ? "findings" : ""}`;
      row.textContent = `[${formatTime(entry.ts)}] ${entry.phase} / ${compactId(entry.participant_id)} / ${entry.error ? "error" : "complete"}`;
      el.cockpitLog.appendChild(row);
    }
  }

  const progress = room.status === "done" ? 100 : ({ fix: 18, audit_gemini: 52, audit_codex: 82 }[room.current_phase] || 5);
  el.stabilityBar.style.width = `${progress}%`;
  el.cycleCount.textContent = room.exhaustion_cycle || 0;
}

function roleMap(room) {
  const assignments = room?.room_config?.role_assignments || [];
  const map = new Map();
  for (const item of assignments) {
    if (item?.driver_id) map.set(item.driver_id, item);
  }
  return map;
}

function livePromptHtml(room, roleMeta) {
  const pp = room?.room_config?.primary_pair || {};
  if (!pp.prompt_preview || pp.active_participant_id !== roleMeta?.driver_id || room?.status !== "running") return "";
  const artifactInfo = primaryPairArtifactMeta(pp.current_artifact || pp.current_phase || "turn");
  return `
    <article class="pi-stream-turn live">
      <div class="pi-stream-meta">${escapeHtml(artifactInfo.label)} / r${escapeHtml(artifactInfo.round || 0)} / input being sent</div>
      <div class="pi-stream-text">${escapeHtml(pp.prompt_preview || "")}</div>
    </article>
  `;
}

function renderPiThread(room, entries, roleMeta, targetBody, targetMeta) {
  if (!targetBody || !targetMeta) return;
  const thinking = room?.status === "running" && (
    isThinking(state.activeRoomId, roleMeta?.driver_id)
    || room?.room_config?.primary_pair?.active_participant_id === roleMeta?.driver_id
  );
  const livePrompt = livePromptHtml(room, roleMeta);
  targetMeta.textContent = thinking ? "Thinking..." : entries.length ? `${entries.length} turns` : "Waiting";
  if (!entries.length) {
    targetBody.innerHTML = thinking
      ? `<div class="pi-live-status"><span class="thinking-dot"></span>${escapeHtml(visibleRoleLabel(roleMeta, "Participant"))} is thinking...</div>${livePrompt}`
      : "No turns yet.";
    return;
  }
  targetBody.innerHTML = `
    ${thinking ? `<div class="pi-live-status"><span class="thinking-dot"></span>${escapeHtml(visibleRoleLabel(roleMeta, "Participant"))} is thinking...</div>` : ""}
    ${livePrompt}
    ${entries.map((entry) => {
      const meta = primaryPairEntryMeta(entry);
      return `
      <article class="pi-stream-turn">
        <div class="pi-stream-meta">${escapeHtml(meta.label)} / r${escapeHtml(meta.round || 0)} / ${escapeHtml(meta.time)}</div>
        <div class="pi-stream-text">${escapeHtml(entry.error ? `ERROR: ${entry.error}` : entry.content || "")}</div>
      </article>
    `;
    }).join("")}
  `;
  targetBody.scrollTop = targetBody.scrollHeight;
}

function renderPiWarRoom(room, config) {
  const assignments = room?.room_config?.role_assignments || [];
  const byRole = new Map(assignments.map((item) => [item.role_key, item]));
  const byDriver = roleMap(room);
  const entries = room.transcript || [];
  const secondary = byRole.get("secondary");
  const primaryA = byRole.get("primary_a") || byRole.get("lead_a") || byRole.get("author") || byRole.get("fixer");
  const primaryB = byRole.get("primary_b") || byRole.get("lead_b") || byRole.get("critic") || byRole.get("auditor");

  el.piModeLabel.textContent = config.label;
  el.piWorkflowLabel.textContent = room.room_config?.workflow_notes || "Persistent per-role sessions";
  el.piControls.innerHTML = `
    <button class="ghost-btn" data-command="/pause">Pause</button>
    <button class="ghost-btn" data-command="/resume">Resume</button>
    <button class="secondary-btn" data-command="/stop">Stop</button>
  `;
  el.piControls.querySelectorAll("[data-command]").forEach((button) => {
    button.addEventListener("click", () => runRoomCommand(button.dataset.command));
  });

  el.piSecondaryTitle.textContent = visibleRoleLabel(secondary, "LLM3");
  el.piPrimaryATitle.textContent = visibleRoleLabel(primaryA, "LLM1");
  el.piPrimaryBTitle.textContent = visibleRoleLabel(primaryB, "LLM2");

  const secondaryEntries = secondary ? entries.filter((entry) => entry.participant_id === secondary.driver_id) : [];
  const secondaryEntry = secondaryEntries[secondaryEntries.length - 1];
  const secondaryThinking = room.status === "running" && (
    isThinking(room.id, secondary?.driver_id)
    || room.room_config?.primary_pair?.active_participant_id === secondary?.driver_id
  );
  const secondaryLivePrompt = livePromptHtml(room, secondary);
  el.piSecondaryMeta.textContent = secondaryThinking ? "Thinking..." : secondaryEntry ? `${secondary.driver_id} / ${formatTime(secondaryEntry.ts)}` : "Waiting";
  const secondaryEntryMeta = primaryPairEntryMeta(secondaryEntry);
  el.piSecondaryBody.innerHTML = secondaryThinking
    ? `<div class="pi-live-status"><span class="thinking-dot"></span>${escapeHtml(visibleRoleLabel(secondary, "LLM3"))} is thinking...</div>${secondaryLivePrompt}`
    : secondaryEntry
      ? `<article class="pi-stream-turn"><div class="pi-stream-meta">${escapeHtml(secondaryEntryMeta.label)} / r${escapeHtml(secondaryEntryMeta.round || 0)} / ${escapeHtml(secondaryEntryMeta.time)}</div><div class="pi-stream-text">${escapeHtml(secondaryEntry.error ? `ERROR: ${secondaryEntry.error}` : secondaryEntry.content || "")}</div></article>`
      : "No LLM3 turn yet.";

  const primaryAEntries = primaryA ? entries.filter((entry) => entry.participant_id === primaryA.driver_id) : [];
  const primaryBEntries = primaryB ? entries.filter((entry) => entry.participant_id === primaryB.driver_id) : [];
  renderPiThread(room, primaryAEntries, primaryA || byDriver.get(primaryA?.driver_id), el.piPrimaryABody, el.piPrimaryAMeta);
  renderPiThread(room, primaryBEntries, primaryB || byDriver.get(primaryB?.driver_id), el.piPrimaryBBody, el.piPrimaryBMeta);
}

function renderOps() {
  if (!el.opsTranscript || !el.opsModelSelect) return;
  const ops = state.ops || { transcript: [], allowed_models: [] };

  const currentOptions = [...el.opsModelSelect.options].map((option) => option.value).join("|");
  const nextOptions = (ops.allowed_models || []).join("|");
  if (currentOptions !== nextOptions) {
    el.opsModelSelect.innerHTML = "";
    for (const model of ops.allowed_models || []) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      el.opsModelSelect.appendChild(option);
    }
  }
  if (ops.model) el.opsModelSelect.value = ops.model;

  el.opsTranscript.innerHTML = "";
  if (ops.error) {
    el.opsTranscript.innerHTML = `<div class="empty-state"><div class="empty-title">Ops unavailable</div><div class="empty-text">${escapeHtml(ops.error)}</div></div>`;
    return;
  }

  const messages = ops.transcript || [];
  if (!messages.length) {
    el.opsTranscript.innerHTML = `<div class="empty-state"><div class="empty-title">Ops is idle</div><div class="empty-text">Send a message.</div></div>`;
    return;
  }

  for (const message of messages) {
    const card = document.createElement("article");
    card.className = `ops-message ${escapeHtml(message.role || "system")}`;
    card.innerHTML = `
      <span class="msg-meta">${escapeHtml(message.role || "system")} / ${escapeHtml(formatTime(message.ts))}</span>
      <div class="ops-body">${escapeHtml(message.content || "")}</div>
    `;
    el.opsTranscript.appendChild(card);
  }
  el.opsTranscript.scrollTop = el.opsTranscript.scrollHeight;
}

function renderAll() {
  const room = state.activeRoomId ? state.rooms.get(state.activeRoomId) : null;
  renderRooms();
  renderLegend(room);
  renderHeader(room);
  renderTranscript(room);
  renderCommand(room);
  renderWarRoom(room);
  renderOps();
  renderLayoutState();
  updateTopStatus();
}

function switchTab(tab) {
  state.currentTab = tab;
  document.querySelectorAll(".tab-btn").forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
  el.body.dataset.view = tab === "ops" ? "ops" : "debates";
  el.layout.dataset.tab = tab;
  if (tab === "ops") refreshOps().then(renderOps).catch((err) => setStatus(err.message, "error"));
  if (tab === "war-room") resetNeuralLink();
}

async function runRoomCommand(command) {
  const roomId = state.activeRoomId;
  if (!roomId) {
    setStatus("Select a room first.", "error");
    return;
  }
  try {
    await fetchJson(`/api/rooms/${encodeURIComponent(roomId)}/command`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: command }),
    });
    await refreshRoom(roomId);
    setStatus(`Ran ${command}`, "ok");
    renderAll();
  } catch (err) {
    setStatus(err.message, "error");
  }
}

async function archiveActiveRoom() {
  const roomId = state.activeRoomId;
  if (!roomId) return;
  try {
    await fetchJson(`/api/rooms/${encodeURIComponent(roomId)}/archive`, { method: "POST" });
    await refreshRooms();
    setStatus("Archived room.", "ok");
    renderAll();
  } catch (err) {
    setStatus(err.message, "error");
  }
}

async function regenerateVerdict() {
  const roomId = state.activeRoomId;
  if (!roomId) return;
  // TODO: Implement participant selection dropdown as per spec
  try {
    await fetchJson(`/api/rooms/${encodeURIComponent(roomId)}/regenerate-verdict`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ participant_id: null }), // Use default selection for now
    });
    await refreshRoom(roomId);
    setStatus("Verdict regenerated.", "ok");
    renderAll();
  } catch (err) {
    setStatus(err.message, "error");
  }
}

async function sendFollowUp() {
  const roomId = state.activeRoomId;
  if (!roomId) return;
  const input = el.transcriptHeader.querySelector(".follow-up-input");
  const select = el.transcriptHeader.querySelector(".follow-up-select");
  const text = input.value.trim();
  const participantId = select.value;

  if (!text || !participantId) {
    setStatus("Participant and text are required for follow-up.", "error");
    return;
  }

  try {
    await fetchJson(`/api/rooms/${encodeURIComponent(roomId)}/follow-up`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ participant_id: participantId, text: text }),
    });
    input.value = "";
    await refreshRoom(roomId);
    setStatus("Follow-up sent.", "ok");
    renderAll();
  } catch (err) {
    setStatus(err.message, "error");
  }
}

function driversForKinds(kinds = []) {
  const kindSet = new Set(kinds.filter((kind) => ACTIVE_CLI_KINDS.includes(kind)));
  return state.drivers.filter((driver) => kindSet.has(driver.kind));
}

function preferredDriverId(kinds = [], used = new Set()) {
  const candidates = kinds
    .flatMap((kind) => state.drivers.filter((driver) => driver.kind === kind && ACTIVE_CLI_KINDS.includes(driver.kind)));
  const healthy = candidates.filter((driver) => driver.health?.ok !== false);
  const ordered = healthy.length ? healthy : candidates;
  const fresh = ordered.find((driver) => !used.has(driver.id));
  return fresh?.id || ordered[0]?.id || "";
}

function modelOptionsForDriver(driverId) {
  const driver = state.drivers.find((item) => item.id === driverId);
  return modelOptionsForKind(driver?.kind || "", driver?.current_model);
}

function modelOptionsMarkup(driverId, selectedValue = "") {
  const options = modelOptionsForDriver(driverId);
  const selected = selectedValue && options.includes(selectedValue) ? selectedValue : options[0];
  return options.map((value) => `<option value="${escapeHtml(value)}" ${value === selected ? "selected" : ""}>${escapeHtml(value)}</option>`).join("");
}

function renderParticipantPicker() {
  if (!el.participantsList) return;
  el.participantsList.innerHTML = "";
  for (const driver of state.drivers) {
    const label = document.createElement("label");
    label.className = "participant-option";
    const health = driver.health?.ok === false ? "offline" : driver.kind;
    label.innerHTML = `
      <input type="checkbox" value="${escapeHtml(driver.id)}" />
      <span>${escapeHtml(driver.display_name || driver.id)} (${escapeHtml(health)})</span>
    `;
    el.participantsList.appendChild(label);
  }
}

function renderRoleCards() {
  if (!el.roleCards) return;
  const config = modeConfig(el.modeInput.value);
  const used = new Set();
  el.roleCards.innerHTML = "";
  for (const role of config.roles) {
    const defaultDriverId = preferredDriverId(role.kinds, used);
    if (defaultDriverId) used.add(defaultDriverId);
    const driverOptions = driversForKinds(role.kinds).map((driver) => {
      const status = driver.health?.ok === false ? "offline" : "ready";
      const selected = driver.id === defaultDriverId ? "selected" : "";
      return `<option value="${escapeHtml(driver.id)}" ${selected}>${escapeHtml(driverLabel(driver))} (${escapeHtml(status)})</option>`;
    }).join("");
    const defaultDriver = driverById(defaultDriverId);
    const hasEffort = supportsEffort(defaultDriver);
    const defaultModel = role.defaultModel || modelOptionsForDriver(defaultDriverId)[0] || "";
    const modelOptions = modelOptionsMarkup(defaultDriverId, defaultModel);
    const defaultEffort = role.defaultEffort || "medium";
    const card = document.createElement("section");
    card.className = "role-card";
    card.dataset.roleKey = role.key;
    card.innerHTML = `
      <div class="role-card-head">
        <div>
          <strong>${escapeHtml(role.label)}</strong>
          <div class="role-card-sub">${escapeHtml(config.label)}</div>
        </div>
      </div>
      <div class="role-card-grid ${hasEffort ? "" : "no-effort"}">
        <label class="form-group compact-field">
          <span class="form-label">Driver</span>
          <select class="role-driver-select">${driverOptions}</select>
        </label>
        <label class="form-group compact-field">
          <span class="form-label">Model</span>
          <select class="role-model-select">${modelOptions}</select>
        </label>
        <label class="form-group compact-field effort-field ${hasEffort ? "" : "hidden"}">
          <span class="form-label">Effort</span>
          <select class="role-effort-select" ${hasEffort ? "" : "disabled"}>${EFFORT_OPTIONS.map((value) => `<option value="${value.toLowerCase()}" ${value.toLowerCase() === defaultEffort ? "selected" : ""}>${escapeHtml(value)}</option>`).join("")}</select>
        </label>
      </div>
    `;
    const driverSelect = card.querySelector(".role-driver-select");
    const modelSelect = card.querySelector(".role-model-select");
    const effortField = card.querySelector(".effort-field");
    const effortSelect = card.querySelector(".role-effort-select");
    const grid = card.querySelector(".role-card-grid");
    driverSelect?.addEventListener("change", () => {
      const nextDriver = driverById(driverSelect.value);
      modelSelect.innerHTML = modelOptionsMarkup(driverSelect.value);
      const nextHasEffort = supportsEffort(nextDriver);
      effortField?.classList.toggle("hidden", !nextHasEffort);
      grid?.classList.toggle("no-effort", !nextHasEffort);
      if (effortSelect) effortSelect.disabled = !nextHasEffort;
    });
    el.roleCards.appendChild(card);
  }
}

function applyModeDefaults() {
  const config = modeConfig(el.modeInput.value);
  const isAudit = config.style === "exhaustion-loop";
  const hideEditableLimit = isAudit;

  el.roundsInput.min = config.min;
  el.roundsInput.max = config.max;
  el.roundsInput.value = config.defaultRounds;
  el.roundsFieldLabel.textContent = config.roundsLabel;
  el.roundsRow?.classList.toggle("hidden", hideEditableLimit);
  el.workflowFields.classList.toggle("hidden", !isAudit);
  el.autoVerdictInput.checked = config.autoVerdict;
  el.autoVerdictInput.disabled = isAudit;
  el.modalModeLabel.textContent = config.subtitle;
  renderRoleCards();
  renderParticipantPicker();
  renderRoundsLabel();
}

function renderRoundsLabel() {
  const config = modeConfig(el.modeInput.value);
  const value = Number(el.roundsInput.value);
  el.roundsLabel.textContent = `${value} ${config.unit}`;
}

function openNewRoomModal() {
  const isWarRoom = state.currentTab === "war-room";
  el.topicInput.value = "";
  el.targetFileInput.value = "";
  el.dodFileInput.value = "";
  if (el.workflowNotesInput) el.workflowNotesInput.value = "";
  el.modeInput.value = isWarRoom ? "code-audit-loop" : "primary-pair";
  applyModeDefaults();
  el.newDebateModal.showModal();
  requestAnimationFrame(() => el.topicInput.focus());
}

function collectRoleAssignments() {
  return [...el.roleCards.querySelectorAll(".role-card")].map((card) => {
    const roleKey = card.dataset.roleKey;
    const label = card.querySelector(".role-card-head strong")?.textContent?.trim() || roleKey;
    const driverId = card.querySelector(".role-driver-select")?.value || "";
    const requestedModel = card.querySelector(".role-model-select")?.value || "";
    const driver = driverById(driverId);
    const effort = supportsEffort(driver) ? (card.querySelector(".role-effort-select")?.value || "medium") : "";
    return { role_key: roleKey, label, driver_id: driverId, requested_model: requestedModel, effort };
  }).filter((item) => item.driver_id);
}

async function startRoom(event) {
  event.preventDefault();
  const mode = el.modeInput.value;
  const config = modeConfig(mode);
  const roleAssignments = collectRoleAssignments();
  const selected = roleAssignments.map((item) => item.driver_id);
  const duplicates = selected.filter((value, index) => selected.indexOf(value) !== index);
  const body = {
    topic: el.topicInput.value.trim(),
    participants: selected,
    max_total_rounds: Number(el.roundsInput.value),
    convergence: config.defaultConvergence,
    style: config.style,
    ui_mode: mode,
    role_assignments: roleAssignments,
    workflow_notes: el.workflowNotesInput?.value.trim() || "",
    auto_verdict: Boolean(el.autoVerdictInput.checked),
    target_file: el.targetFileInput.value.trim() || null,
    dod_file: el.dodFileInput.value.trim() || null,
  };

  if (!body.topic) {
    setStatus("Brief is required.", "error");
    return;
  }
  if (roleAssignments.length !== config.roles.length) {
    setStatus("Every role needs a driver.", "error");
    return;
  }
  if (duplicates.length) {
    setStatus("Each role needs a different driver.", "error");
    return;
  }

  try {
    const data = await fetchJson("/api/rooms/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    state.activeRoomId = String(data.room_id);
    el.newDebateModal.close();
    await refreshRooms();
    setStatus("Room started.", "ok");
    renderAll();
  } catch (err) {
    setStatus(err.message, "error");
  }
}

async function renderExplorer() {
  const data = await fetchJson(`/api/fs/ls?path=${encodeURIComponent(state.explorer.currentPath)}`);
  el.explorerPath.textContent = data.current_path || ".";
  el.explorerList.innerHTML = "";

  if (data.current_path && data.current_path !== ".") {
    const parent = document.createElement("button");
    parent.type = "button";
    parent.className = "explorer-item is-dir";
    parent.textContent = "..";
    parent.addEventListener("click", () => {
      const parts = String(state.explorer.currentPath).split("/").filter(Boolean);
      parts.pop();
      state.explorer.currentPath = parts.join("/") || ".";
      renderExplorer().catch((err) => setStatus(err.message, "error"));
    });
    el.explorerList.appendChild(parent);
  }

  for (const item of data.items || []) {
    const row = document.createElement("div");
    row.className = `explorer-item ${item.is_dir ? "is-dir" : ""}`;
    row.innerHTML = `<span>${escapeHtml(item.name)}</span>`;
    row.addEventListener("click", () => {
      if (item.is_dir) {
        state.explorer.currentPath = item.rel_path;
        renderExplorer().catch((err) => setStatus(err.message, "error"));
        return;
      }
      if (!item.is_dir && !state.explorer.isDirOnly) {
        document.getElementById(state.explorer.targetInputId).value = item.rel_path;
        el.explorerModal.close();
      }
    });
    if (item.is_dir && state.explorer.isDirOnly) {
      const select = document.createElement("button");
      select.type = "button";
      select.className = "ghost-btn";
      select.textContent = "Select";
      select.addEventListener("click", (event) => {
        event.stopPropagation();
        document.getElementById(state.explorer.targetInputId).value = item.rel_path;
        el.explorerModal.close();
      });
      row.appendChild(select);
    }
    el.explorerList.appendChild(row);
  }
}

async function openExplorer(targetInputId, isDirOnly) {
  state.explorer = { targetInputId, isDirOnly, currentPath: "." };
  await renderExplorer();
  el.explorerModal.showModal();
}

async function sendOpsMessage(event) {
  event.preventDefault();
  const text = el.opsInput.value.trim();
  if (!text) return;
  try {
    await fetchJson("/api/ops/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    el.opsInput.value = "";
    await refreshOps();
    renderOps();
    setStatus("Ops message sent.", "ok");
  } catch (err) {
    setStatus(err.message, "error");
  }
}

async function setOpsModel() {
  const model = el.opsModelSelect.value;
  if (!model) return;
  try {
    await fetchJson("/api/ops/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
    });
    await refreshOps();
    renderOps();
    setStatus(`Ops model: ${model}`, "ok");
  } catch (err) {
    setStatus(err.message, "error");
  }
}

async function toggleRecording() {
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
    setStatus("Browser recording is unavailable.", "error");
    return;
  }

  if (state.recorder.mediaRecorder?.state === "recording") {
    state.recorder.mediaRecorder.stop();
    el.opsMicBtn.textContent = "Mic";
    el.opsMicBtn.classList.remove("recording");
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const recorder = new MediaRecorder(stream);
    state.recorder = { mediaRecorder: recorder, chunks: [], stream };
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data?.size) state.recorder.chunks.push(event.data);
    });
    recorder.addEventListener("stop", async () => {
      try {
        const blob = new Blob(state.recorder.chunks, { type: "audio/webm" });
        const form = new FormData();
        form.append("audio", blob, "ops.webm");
        const data = await fetchJson("/api/ops/transcribe", { method: "POST", body: form });
        el.opsInput.value = data.text || "";
        el.opsInput.focus();
        setStatus("Transcribed audio.", "ok");
      } catch (err) {
        setStatus(err.message, "error");
      } finally {
        state.recorder.stream?.getTracks().forEach((track) => track.stop());
        state.recorder = { mediaRecorder: null, chunks: [], stream: null };
      }
    });
    recorder.start();
    el.opsMicBtn.textContent = "Stop";
    el.opsMicBtn.classList.add("recording");
    setStatus("Recording.", "info");
  } catch (err) {
    setStatus(err.message, "error");
  }
}

function clampPiSecondaryHeight(value) {
  const min = 220;
  const max = Math.max(420, Math.floor(window.innerHeight * 0.72));
  return Math.max(min, Math.min(max, Math.round(value)));
}

function applyPiSecondaryHeight(height) {
  if (!el.piSecondaryPanel) return;
  const value = clampPiSecondaryHeight(height);
  el.piSecondaryPanel.style.height = `${value}px`;
}

function loadPiSecondaryHeight() {
  const stored = Number(window.localStorage.getItem(PI_SECONDARY_HEIGHT_KEY) || "");
  if (Number.isFinite(stored) && stored > 0) {
    applyPiSecondaryHeight(stored);
    return;
  }
  applyPiSecondaryHeight(360);
}

function startPiSecondaryResize(event) {
  if (!el.piSecondaryPanel || !el.piSecondaryResize) return;
  event.preventDefault();
  el.piSecondaryResize.setPointerCapture?.(event.pointerId);
  state.piSecondaryResize = {
    active: true,
    startY: event.clientY,
    startHeight: el.piSecondaryPanel.getBoundingClientRect().height,
    pointerId: event.pointerId,
  };
  document.body.classList.add("is-resizing-pi");
}

function updatePiSecondaryResize(event) {
  if (!state.piSecondaryResize.active) return;
  const delta = event.clientY - state.piSecondaryResize.startY;
  applyPiSecondaryHeight(state.piSecondaryResize.startHeight + delta);
}

function stopPiSecondaryResize() {
  if (!state.piSecondaryResize.active) return;
  state.piSecondaryResize.active = false;
  document.body.classList.remove("is-resizing-pi");
  try {
    if (Number.isFinite(state.piSecondaryResize.pointerId)) {
      el.piSecondaryResize?.releasePointerCapture?.(state.piSecondaryResize.pointerId);
    }
  } catch {}
  if (!el.piSecondaryPanel) return;
  const height = el.piSecondaryPanel.getBoundingClientRect().height;
  window.localStorage.setItem(PI_SECONDARY_HEIGHT_KEY, String(clampPiSecondaryHeight(height)));
}

function connectWs() {
  const ws = new WebSocket(`ws://${window.location.host}/ws`);
  ws.onopen = () => {
    state.wsState = "connected";
    updateTopStatus();
  };
  ws.onmessage = async (event) => {
    const payload = JSON.parse(event.data);
    setLastEvent(payload.type || "event");
    if (payload.type === "room.update") {
      await refreshRooms();
      renderAll();
    } else if (payload.type === "room.deleted") {
      await refreshRooms();
      renderAll();
    } else if (payload.type === "message.append") {
      await refreshRoom(payload.room_id);
      renderAll();
    } else if (payload.type === "participant.thinking") {
      setThinking(payload.room_id, payload.participant_id, payload.in_flight);
      if (payload.room_id === state.activeRoomId) renderAll();
    } else if (payload.type === "ops.message") {
      await refreshOps();
      renderOps();
    } else if (payload.type === "participant.thought" && payload.room_id === state.activeRoomId) {
      const participant = String(payload.participant_id || "");
      const slotId = participant.includes("gemini") ? "thoughtGemini" : participant.includes("codex") ? "thoughtCodex" : "thoughtClaude";
      const slot = document.getElementById(slotId);
      if (slot) {
        slot.textContent = payload.content || "";
        slot.closest(".neural-slot")?.classList.add("active");
      }
    } else if (payload.type === "error") {
      setStatus(payload.detail || "Gateway error", "error");
    }
  };
  ws.onclose = () => {
    state.wsState = "disconnected";
    updateTopStatus();
    window.setTimeout(connectWs, 2000);
  };
  ws.onerror = () => {
    state.wsState = "disconnected";
    updateTopStatus();
  };
}

function bindEvents() {
  document.querySelectorAll(".tab-btn").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });
  el.toggleRoomsBtn?.addEventListener("click", () => {
    state.roomsCollapsed = !state.roomsCollapsed;
    renderLayoutState();
  });
  el.openNewDebateBtn?.addEventListener("click", openNewRoomModal);
  el.cancelNewDebateBtn?.addEventListener("click", () => el.newDebateModal.close());
  el.newDebateForm?.addEventListener("submit", startRoom);
  el.modeInput?.addEventListener("change", applyModeDefaults);
  el.roundsInput?.addEventListener("input", renderRoundsLabel);
  el.pickTargetBtn?.addEventListener("click", () => openExplorer("targetFileInput", true).catch((err) => setStatus(err.message, "error")));
  el.pickDodBtn?.addEventListener("click", () => openExplorer("dodFileInput", false).catch((err) => setStatus(err.message, "error")));
  el.cancelExplorerBtn?.addEventListener("click", () => el.explorerModal.close());
  el.commandForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    const command = el.commandInput.value.trim();
    if (!command) return;
    runRoomCommand(command);
    el.commandInput.value = "";
  });
  el.opsForm?.addEventListener("submit", sendOpsMessage);
  el.opsModelSelect?.addEventListener("change", setOpsModel);
  el.opsMicBtn?.addEventListener("click", toggleRecording);
  el.opsInput?.addEventListener("input", () => {
    el.opsInput.style.height = "auto";
    el.opsInput.style.height = `${Math.min(el.opsInput.scrollHeight, 150)}px`;
  });
  el.piSecondaryResize?.addEventListener("pointerdown", startPiSecondaryResize);
  window.addEventListener("pointermove", updatePiSecondaryResize);
  window.addEventListener("pointerup", stopPiSecondaryResize);
}

async function boot() {
  bindEvents();
  loadPiSecondaryHeight();
  try {
    await Promise.all([refreshDrivers(), refreshRooms(), refreshOps()]);
    renderAll();
    connectWs();
    setStatus("Ready.", "ok");
  } catch (err) {
    setStatus(err.message, "error");
    renderAll();
  }
}

boot();
