const PARTICIPANT_KIND_CLASS = {
  "claude-code-new": "kind-claude-code-new",
  codex: "kind-codex",
  "gemini-cli": "kind-gemini-cli",
  fake: "kind-fake",
};

const VALID_COMMANDS_HINT = "/participants /start /pause /resume /stop /inject /to /rounds /phase /synthesize /list /attach /drivers";
const STYLE_CONFIG = {
  "ein-mdp": { min: 4, max: 5, defaultRounds: 5, defaultConvergence: "agree-marker" },
  "critic-terminate": { min: 4, max: 15, defaultRounds: 8, defaultConvergence: "terminate-majority" },
};

const state = {
  rooms: new Map(),
  activeRoomId: null,
  history: [],
  historyIndex: -1,
  participantFilter: null,
  wsState: "connecting",
  lastEventAt: null,
  drivers: [],
  thinkingByRoom: new Map(),
  modalConvergenceTouched: false,
};

const roomsActiveEl = document.getElementById("roomsActive");
const roomsArchivedEl = document.getElementById("roomsArchived");
const transcriptHeaderEl = document.getElementById("transcriptHeader");
const transcriptEl = document.getElementById("transcript");
const legendEl = document.getElementById("participantLegend");
const inputEl = document.getElementById("commandInput");
const hintEl = document.getElementById("commandHint");
const statusEl = document.getElementById("status");
const newMessagesPillEl = document.getElementById("newMessagesPill");
const wsStateEl = document.getElementById("wsState");
const activeRoomStateEl = document.getElementById("activeRoomState");
const lastEventStateEl = document.getElementById("lastEventState");
const openNewDebateBtnEl = document.getElementById("openNewDebateBtn");
const modalEl = document.getElementById("newDebateModal");
const formEl = document.getElementById("newDebateForm");
const topicInputEl = document.getElementById("topicInput");
const participantsListEl = document.getElementById("participantsList");
const roundsInputEl = document.getElementById("roundsInput");
const roundsLabelEl = document.getElementById("roundsLabel");
const styleInputEl = document.getElementById("styleInput");
const convergenceInputEl = document.getElementById("convergenceInput");
const autoVerdictInputEl = document.getElementById("autoVerdictInput");
const cancelNewDebateBtnEl = document.getElementById("cancelNewDebateBtn");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function kindClass(kind) {
  return PARTICIPANT_KIND_CLASS[kind] || "kind-unknown";
}

function shortId(roomId) {
  return roomId ? String(roomId).slice(0, 8) : "none";
}

function setStatus(message, kind = "info") {
  statusEl.className = `status status-${kind}`;
  statusEl.textContent = message;
}

function setLastEventNow() {
  state.lastEventAt = new Date();
  updateFooterStatus();
}

function updateFooterStatus() {
  wsStateEl.textContent = `WS: ${state.wsState}`;
  activeRoomStateEl.textContent = `Room: ${shortId(state.activeRoomId)}`;
  lastEventStateEl.textContent = `Last event: ${state.lastEventAt ? state.lastEventAt.toLocaleTimeString() : "--"}`;
}

function isNearBottom() {
  const threshold = 28;
  return transcriptEl.scrollHeight - transcriptEl.scrollTop - transcriptEl.clientHeight <= threshold;
}

function scrollTranscriptToBottom() {
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
  newMessagesPillEl.classList.add("hidden");
}

function roomSortDesc(a, b) {
  const key = (room) => room.updated_at || room.created_at || room.id;
  return String(key(b)).localeCompare(String(key(a)));
}

function normalizeRoom(rawRoom, fallbackIndex = 0) {
  const id = String(rawRoom?.id ?? rawRoom?.room_id ?? `legacy-${fallbackIndex}`);
  const phaseFromIndex = rawRoom?.phase_sequence?.[rawRoom?.current_phase_index ?? 0]?.name;
  return {
    ...rawRoom,
    id,
    topic: String(rawRoom?.topic ?? "(untitled room)"),
    status: String(rawRoom?.status ?? "idle"),
    current_phase: String(rawRoom?.current_phase ?? phaseFromIndex ?? "unknown"),
    current_round: Number(rawRoom?.current_round ?? 0),
    participants: Array.isArray(rawRoom?.participants) ? rawRoom.participants : [],
    transcript: Array.isArray(rawRoom?.transcript) ? rawRoom.transcript : [],
    archived: Boolean(rawRoom?.archived),
    style: String(rawRoom?.style ?? "ein-mdp"),
    auto_verdict: Boolean(rawRoom?.auto_verdict ?? true),
    verdict_author: rawRoom?.verdict_author ?? null,
    verdict_text: rawRoom?.verdict_text ?? null,
    converged_round: Number(rawRoom?.converged_round ?? 0),
    agree_count: Number(rawRoom?.agree_count ?? 0),
    terminate_count: Number(rawRoom?.terminate_count ?? 0),
    warning_detail: rawRoom?.warning_detail ?? null,
  };
}

function upsertRoom(rawRoom) {
  const normalized = normalizeRoom(rawRoom, state.rooms.size);
  const existing = state.rooms.get(normalized.id);
  const merged = existing
    ? {
        ...existing,
        ...normalized,
        transcript: Array.isArray(normalized.transcript) ? normalized.transcript : existing.transcript || [],
      }
    : normalized;
  state.rooms.set(merged.id, merged);
}

function removeRoom(roomId) {
  state.rooms.delete(roomId);
  state.thinkingByRoom.delete(roomId);
  if (state.activeRoomId === roomId) {
    state.activeRoomId = null;
  }
}

function renderRoomCard(room) {
  const item = document.createElement("div");
  item.className = `room-item${room.id === state.activeRoomId ? " active" : ""}`;
  item.innerHTML = `
    <button class="room-body" type="button">
      <div class="room-id">${escapeHtml(shortId(room.id))}</div>
      <div class="room-topic">${escapeHtml(room.topic)}</div>
      <div class="room-meta">
        <span class="status-dot ${escapeHtml(room.status)}"></span>
        <span>${escapeHtml(room.current_phase)} - r${escapeHtml(room.current_round)} (${escapeHtml(room.status)})</span>
      </div>
    </button>
    <button class="delete-room-btn" type="button" title="Delete room">Delete</button>
  `;
  const bodyBtn = item.querySelector(".room-body");
  const deleteBtn = item.querySelector(".delete-room-btn");
  bodyBtn?.addEventListener("click", async () => {
    state.activeRoomId = room.id;
    state.participantFilter = null;
    await refreshRoom(room.id);
    renderAll();
    scrollTranscriptToBottom();
  });
  deleteBtn?.addEventListener("click", async () => {
    if (!confirm(`Delete room ${shortId(room.id)} permanently?`)) {
      return;
    }
    try {
      const resp = await fetch(`/api/rooms/${room.id}`, { method: "DELETE" });
      if (!resp.ok) {
        const data = await resp.json();
        throw new Error(data?.detail || "Delete failed");
      }
      removeRoom(room.id);
      await refreshRooms();
      renderAll();
      setStatus(`Deleted room ${room.id}`, "success");
    } catch (error) {
      setStatus(String(error?.message || error), "error");
    }
  });
  return item;
}

function renderRooms() {
  roomsActiveEl.innerHTML = "";
  roomsArchivedEl.innerHTML = "";
  const allRooms = [...state.rooms.values()].sort(roomSortDesc);
  const activeRooms = allRooms.filter((room) => !room.archived);
  const archivedRooms = allRooms.filter((room) => room.archived);
  activeRooms.forEach((room) => roomsActiveEl.appendChild(renderRoomCard(room)));
  archivedRooms.forEach((room) => roomsArchivedEl.appendChild(renderRoomCard(room)));
  updateFooterStatus();
}

function renderLegend() {
  legendEl.innerHTML = "";
  const room = state.rooms.get(state.activeRoomId);
  if (!room) {
    return;
  }
  room.participants.forEach((participant) => {
    const id = String(participant?.id ?? "unknown");
    const kind = String(participant?.kind ?? "unknown");
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `legend-chip ${kindClass(kind)}${state.participantFilter === id ? " active" : ""}`;
    chip.textContent = id;
    chip.onclick = () => {
      state.participantFilter = state.participantFilter === id ? null : id;
      renderLegend();
      renderTranscript({ sourceNewMessage: false });
    };
    legendEl.appendChild(chip);
  });
}

function participantThinkingSet(roomId) {
  return state.thinkingByRoom.get(roomId) || new Set();
}

function renderTranscriptHeader() {
  transcriptHeaderEl.innerHTML = "";
  const room = state.rooms.get(state.activeRoomId);
  if (!room) {
    transcriptHeaderEl.textContent = "No room selected.";
    return;
  }
  const participants = (room.participants || []).map((p) => p.id).join(" Â· ");
  const thinking = [...participantThinkingSet(room.id)];
  const thinkingHtml =
    thinking.length > 0
      ? thinking.map((id) => `<span class="typing-bubble">${escapeHtml(id)} <span class="ellipsis">...</span></span>`).join(" ")
      : "";
  const pauseLabel = room.status === "paused" ? "Resume" : "Pause";
  const warningHtml = room.warning_detail
    ? `<div class="warning-banner">Debate has not seen any termination votes - consider /inject or /stop.</div>`
    : "";
  const doneActionsHtml =
    room.status === "done"
      ? `
      <div class="header-actions done-actions">
        <label class="regen-label">
          Regenerate verdict:
          <select id="regenerateVerdictParticipant">
            ${(room.participants || [])
              .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.id)}</option>`)
              .join("")}
          </select>
        </label>
        <button id="regenVerdictBtn" type="button">Regenerate verdict</button>
      </div>
      <div class="follow-up-composer">
        <select id="followUpParticipant">
          ${(room.participants || [])
            .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.id)}</option>`)
            .join("")}
        </select>
        <textarea id="followUpText" rows="2" placeholder="Ask a participant..."></textarea>
        <button id="followUpSendBtn" type="button">Send</button>
      </div>
    `
      : "";
  transcriptHeaderEl.innerHTML = `
    <div class="header-line">Phase: ${escapeHtml(room.current_phase)} - Round ${escapeHtml(room.current_round)}</div>
    <div class="header-line">Next up: ${escapeHtml(room.next_up || "n/a")} ${thinkingHtml}</div>
    <div class="header-line">Participants: ${escapeHtml(participants || "(none)")}</div>
    ${warningHtml}
    <div class="header-actions">
      <button id="pauseResumeBtn" type="button">${pauseLabel}</button>
      <button id="stopBtn" type="button">Stop</button>
      <button id="injectBtn" type="button">Inject</button>
      <button id="archiveBtn" type="button">Archive</button>
    </div>
    <div id="injectInline" class="inject-inline hidden">
      <input id="injectTextInput" type="text" placeholder="Injected instruction for next round" />
      <button id="injectSendBtn" type="button">Send</button>
    </div>
    ${doneActionsHtml}
  `;

  document.getElementById("pauseResumeBtn")?.addEventListener("click", async () => {
    await submitCommand(room.status === "paused" ? "/resume" : "/pause");
  });
  document.getElementById("stopBtn")?.addEventListener("click", async () => {
    await submitCommand("/stop");
  });
  document.getElementById("injectBtn")?.addEventListener("click", () => {
    document.getElementById("injectInline")?.classList.toggle("hidden");
  });
  document.getElementById("injectSendBtn")?.addEventListener("click", async () => {
    const input = document.getElementById("injectTextInput");
    const text = input?.value?.trim();
    if (!text) {
      return;
    }
    await submitCommand(`/inject ${text}`);
    if (input) {
      input.value = "";
    }
  });
  document.getElementById("archiveBtn")?.addEventListener("click", async () => {
    try {
      const resp = await fetch(`/api/rooms/${room.id}/archive`, { method: "POST" });
      if (!resp.ok) {
        const data = await resp.json();
        throw new Error(data?.detail || "Archive failed");
      }
      await refreshRoom(room.id);
      renderAll();
    } catch (error) {
      setStatus(String(error?.message || error), "error");
    }
  });
  document.getElementById("regenVerdictBtn")?.addEventListener("click", async () => {
    try {
      const selected = document.getElementById("regenerateVerdictParticipant")?.value || null;
      const resp = await fetch(`/api/rooms/${room.id}/regenerate-verdict`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ participant_id: selected }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data?.detail || "Regenerate verdict failed");
      }
      await refreshRoom(room.id);
      renderAll();
      setStatus(`Regenerated verdict by ${data.author}`, "success");
    } catch (error) {
      setStatus(String(error?.message || error), "error");
    }
  });
  document.getElementById("followUpSendBtn")?.addEventListener("click", async () => {
    try {
      const participantId = document.getElementById("followUpParticipant")?.value;
      const text = (document.getElementById("followUpText")?.value || "").trim();
      if (!participantId || !text) {
        return;
      }
      const resp = await fetch(`/api/rooms/${room.id}/follow-up`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ participant_id: participantId, text }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data?.detail || "Follow-up failed");
      }
      const textEl = document.getElementById("followUpText");
      if (textEl) {
        textEl.value = "";
      }
      await refreshRoom(room.id);
      renderAll();
      setStatus(`Follow-up sent to ${participantId}`, "success");
    } catch (error) {
      setStatus(String(error?.message || error), "error");
    }
  });
}

function renderTranscript({ sourceNewMessage }) {
  const previousScrollTop = transcriptEl.scrollTop;
  const wasNearBottom = isNearBottom();
  transcriptEl.innerHTML = "";
  const room = state.rooms.get(state.activeRoomId);
  if (!room) {
    return;
  }
  const filtered = (room.transcript || []).filter((msg) => {
    if (!state.participantFilter) {
      return true;
    }
    return msg?.participant_id === state.participantFilter;
  });
  if (room.status === "done") {
    const summary = document.createElement("div");
    summary.className = "summary-bar";
    summary.textContent = `Converged round ${Number(room?.converged_round || 0)} of ${Number(room?.max_total_rounds || 0)} · ${Number(room?.agree_count || 0)}/${(room.participants || []).length} AGREE · ${Number(room?.terminate_count || 0)} TERMINATE · Style: ${String(room?.style || "ein-mdp")}`;
    transcriptEl.appendChild(summary);
  }
  if (room.verdict_text) {
    const card = document.createElement("div");
    card.className = "verdict-card";
    const author = room.verdict_author || "unknown";
    card.innerHTML = `
      <div class="verdict-title">Verdict by ${escapeHtml(author)} (one of ${(room.participants || []).length} participants)</div>
      <div class="verdict-content">${escapeHtml(room.verdict_text)}</div>
    `;
    transcriptEl.appendChild(card);
  }
  filtered.forEach((msg) => {
    const participantId = String(msg?.participant_id ?? "unknown");
    const participantKind = String(msg?.participant_kind ?? "unknown");
    const phase = String(msg?.phase ?? room.current_phase ?? "unknown");
    const round = Number(msg?.round ?? room.current_round ?? 0);
    const card = document.createElement("div");
    card.className = "msg-card";
    card.innerHTML = `
      <div class="msg-header">
        <div class="msg-name ${kindClass(participantKind)}">${escapeHtml(participantId)}</div>
        <div class="msg-meta">${escapeHtml(phase)} - r${escapeHtml(round)} ${msg?.role === "follow_up" ? "[follow-up]" : ""}</div>
      </div>
      <div class="msg-content">${escapeHtml(msg?.content ?? "")}</div>
      ${msg?.error ? `<div class="msg-error">error: ${escapeHtml(msg.error)}</div>` : ""}
    `;
    transcriptEl.appendChild(card);
  });
  const thinking = [...participantThinkingSet(room.id)];
  thinking.forEach((id) => {
    const bubble = document.createElement("div");
    bubble.className = "typing-row";
    bubble.innerHTML = `<span class="typing-bubble">${escapeHtml(id)} <span class="ellipsis">...</span></span>`;
    transcriptEl.appendChild(bubble);
  });

  if (sourceNewMessage && !wasNearBottom) {
    transcriptEl.scrollTop = previousScrollTop;
    newMessagesPillEl.classList.remove("hidden");
    return;
  }
  if (wasNearBottom || !sourceNewMessage) {
    scrollTranscriptToBottom();
  }
}

function renderAll() {
  renderRooms();
  renderTranscriptHeader();
  renderLegend();
  renderTranscript({ sourceNewMessage: false });
}

function updateHintVisibility() {
  hintEl.textContent = VALID_COMMANDS_HINT;
  hintEl.classList.toggle("hidden", inputEl.value.trim().length > 0);
}

function parseCommand(text) {
  const trimmed = text.trim();
  if (!trimmed.startsWith("/")) {
    return { name: "", args: "" };
  }
  const firstSpace = trimmed.indexOf(" ");
  if (firstSpace === -1) {
    return { name: trimmed.slice(1).toLowerCase(), args: "" };
  }
  return {
    name: trimmed.slice(1, firstSpace).toLowerCase(),
    args: trimmed.slice(firstSpace + 1).trim(),
  };
}

async function refreshDrivers() {
  const response = await fetch("/api/drivers");
  if (!response.ok) {
    throw new Error(`Failed to load drivers: HTTP ${response.status}`);
  }
  const payload = await response.json();
  state.drivers = Array.isArray(payload?.drivers) ? payload.drivers : [];
}

async function refreshRoom(roomId) {
  if (!roomId) {
    return;
  }
  const response = await fetch(`/api/rooms/${roomId}`);
  if (!response.ok) {
    throw new Error(`Failed to load room ${roomId}: HTTP ${response.status}`);
  }
  const room = await response.json();
  upsertRoom(room);
}

async function refreshRooms() {
  const response = await fetch("/api/rooms");
  if (!response.ok) {
    throw new Error(`Failed to load rooms: HTTP ${response.status}`);
  }
  const payload = await response.json();
  const rooms = Array.isArray(payload) ? payload : [];
  const previousActive = state.activeRoomId;
  state.rooms.clear();
  rooms.forEach((room, index) => upsertRoom(normalizeRoom(room, index)));
  if (previousActive && state.rooms.has(previousActive)) {
    state.activeRoomId = previousActive;
  } else if (!state.activeRoomId && state.rooms.size > 0) {
    const newest = [...state.rooms.values()].sort(roomSortDesc)[0];
    state.activeRoomId = newest ? newest.id : null;
  }
}

async function submitCommand(text) {
  const parsed = parseCommand(text);
  try {
    if (parsed.name === "new") {
      if (!parsed.args) {
        throw new Error("/new requires a topic");
      }
      const createResp = await fetch("/api/rooms", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic: parsed.args }),
      });
      const createData = await createResp.json();
      if (!createResp.ok) {
        throw new Error(createData?.detail || "Failed to create room");
      }
      state.activeRoomId = String(createData?.room_id || "");
      await refreshRooms();
      await refreshRoom(state.activeRoomId);
      renderAll();
      setStatus(`Created room ${state.activeRoomId}`, "success");
      setLastEventNow();
      return;
    }
    const roomId = state.activeRoomId;
    if (!roomId) {
      throw new Error("No active room");
    }
    const resp = await fetch(`/api/rooms/${roomId}/command`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data?.detail || `Command failed with HTTP ${resp.status}`);
    }
    if (data?.room_id) {
      state.activeRoomId = String(data.room_id);
    }
    await refreshRooms();
    if (state.activeRoomId) {
      await refreshRoom(state.activeRoomId);
    }
    renderAll();
    setStatus(JSON.stringify(data, null, 2), "success");
    setLastEventNow();
  } catch (error) {
    setStatus(String(error?.message || error), "error");
  }
}

function renderRoundsLabel() {
  const total = Number(roundsInputEl.value);
  const debate = Math.max(1, total - 3);
  const style = styleInputEl.value;
  if (style === "critic-terminate") {
    roundsLabelEl.textContent = `Total rounds: ${total} (positions 1 + critic 1 + debate ${debate} + synthesis 1)`;
    return;
  }
  roundsLabelEl.textContent = `Total rounds: ${total} (positions 1 + contrarian 1 + debate ${debate} + verdict 1)`;
}

function setModalStyle(style, { forceConvergence = false } = {}) {
  const cfg = STYLE_CONFIG[style] || STYLE_CONFIG["ein-mdp"];
  roundsInputEl.min = String(cfg.min);
  roundsInputEl.max = String(cfg.max);
  // Always snap to the style's default to avoid confusing held-over values.
  roundsInputEl.value = String(cfg.defaultRounds);
  // Convergence is implicit in the style. No UI control; set hidden input for backend compatibility.
  convergenceInputEl.value = cfg.defaultConvergence;
  // Hide the rounds slider entirely for ein-mdp (always 5 rounds); show it for styles with a range.
  const roundsRow = document.getElementById("roundsRow");
  if (roundsRow) {
    const showSlider = style !== "ein-mdp";
    roundsRow.classList.toggle("hidden", !showSlider);
  }
  renderRoundsLabel();
}

function renderParticipantPicker() {
  participantsListEl.innerHTML = "";
  state.drivers.forEach((driver) => {
    const id = String(driver.id);
    const kind = String(driver.kind || "unknown");
    const ok = Boolean(driver?.health?.ok);
    const checked = id === "claude-code-new-1" || id === "codex-1";
    const row = document.createElement("label");
    row.className = "participant-option";
    row.innerHTML = `
      <input type="checkbox" name="driver-id" value="${escapeHtml(id)}" ${checked ? "checked" : ""} />
      <span class="health-dot ${ok ? "ok" : "bad"}"></span>
      <span>${escapeHtml(id)} (${escapeHtml(kind)})</span>
    `;
    participantsListEl.appendChild(row);
  });
}

async function startDebateFromModal() {
  const selected = [...participantsListEl.querySelectorAll("input[name='driver-id']:checked")].map((el) => el.value);
  if (selected.length === 0) {
    throw new Error("Select at least one participant");
  }
  const topic = topicInputEl.value.trim();
  if (!topic) {
    throw new Error("Topic is required");
  }
  const body = {
    topic,
    participants: selected,
    max_total_rounds: Number(roundsInputEl.value),
    convergence: convergenceInputEl.value,
    style: styleInputEl.value,
    auto_verdict: Boolean(autoVerdictInputEl.checked),
  };
  const resp = await fetch("/api/rooms/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(data?.detail || "Failed to start debate");
  }
  state.activeRoomId = String(data.room_id);
  await refreshRooms();
  await refreshRoom(state.activeRoomId);
  renderAll();
  setStatus(`Started debate ${state.activeRoomId}`, "success");
  setLastEventNow();
  modalEl.close();
}

function setThinking(roomId, participantId, inFlight) {
  const next = new Set(participantThinkingSet(roomId));
  if (inFlight) {
    next.add(participantId);
  } else {
    next.delete(participantId);
  }
  state.thinkingByRoom.set(roomId, next);
}

function connectWs() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  state.wsState = "connecting";
  updateFooterStatus();

  ws.onopen = () => {
    state.wsState = "connected";
    updateFooterStatus();
  };

  ws.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      setLastEventNow();
      if (payload.type === "room.update") {
        upsertRoom(payload.state || {});
        renderAll();
        return;
      }
      if (payload.type === "room.deleted") {
        removeRoom(String(payload.room_id || ""));
        renderAll();
        return;
      }
      if (payload.type === "participant.thinking") {
        const roomId = String(payload.room_id || "");
        const participantId = String(payload.participant_id || "");
        setThinking(roomId, participantId, Boolean(payload.in_flight));
        if (roomId === state.activeRoomId) {
          renderTranscriptHeader();
          renderTranscript({ sourceNewMessage: false });
        }
        return;
      }
      if (payload.type === "debate.warning") {
        const roomId = String(payload.room_id || "");
        const room = state.rooms.get(roomId);
        if (room) {
          room.warning_detail = String(payload.detail || "");
          state.rooms.set(roomId, room);
          if (roomId === state.activeRoomId) {
            renderTranscriptHeader();
          }
        }
        return;
      }
      if (payload.type === "message.append") {
        const roomId = String(payload.room_id || "");
        const room = state.rooms.get(roomId);
        if (!room) {
          return;
        }
        room.transcript = Array.isArray(room.transcript) ? room.transcript : [];
        room.transcript.push(payload.message || {});
        room.updated_at = new Date().toISOString();
        state.rooms.set(roomId, room);
        if (roomId === state.activeRoomId) {
          const visibleWithFilter = !state.participantFilter || payload?.message?.participant_id === state.participantFilter;
          renderRooms();
          renderLegend();
          renderTranscript({ sourceNewMessage: visibleWithFilter });
          renderTranscriptHeader();
        } else {
          renderRooms();
        }
        return;
      }
      if (payload.type === "error") {
        setStatus(String(payload.detail || "Unknown websocket error"), "error");
      }
      if (payload.type === "ops.message") {
        window.dispatchEvent(new CustomEvent("agora:ops-message", { detail: payload.message }));
      }
    } catch (error) {
      setStatus("WebSocket payload handling failed", "error");
    }
  };

  ws.onclose = () => {
    state.wsState = "disconnected";
    updateFooterStatus();
    setTimeout(connectWs, 1200);
  };

  ws.onerror = () => {
    state.wsState = "error";
    updateFooterStatus();
  };
}

async function initialLoad() {
  try {
    await refreshDrivers();
    await refreshRooms();
    renderParticipantPicker();
    setModalStyle(styleInputEl.value || "ein-mdp", { forceConvergence: true });
    renderAll();
    setStatus("Loaded rooms.", "success");
  } catch (error) {
    setStatus(`Failed to load: ${String(error?.message || error)}`, "error");
  }
}

openNewDebateBtnEl.addEventListener("click", () => {
  topicInputEl.value = "";
  styleInputEl.value = "ein-mdp";
  roundsInputEl.value = "5";
  convergenceInputEl.value = "agree-marker";
  autoVerdictInputEl.checked = true;
  state.modalConvergenceTouched = false;
  renderParticipantPicker();
  setModalStyle(styleInputEl.value, { forceConvergence: true });
  modalEl.showModal();
});

cancelNewDebateBtnEl.addEventListener("click", () => {
  modalEl.close();
});

formEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await startDebateFromModal();
  } catch (error) {
    setStatus(String(error?.message || error), "error");
  }
});

roundsInputEl.addEventListener("input", renderRoundsLabel);
styleInputEl.addEventListener("change", () => {
  setModalStyle(styleInputEl.value, { forceConvergence: false });
});
convergenceInputEl.addEventListener("change", () => {
  state.modalConvergenceTouched = true;
});

inputEl.addEventListener("keydown", async (event) => {
  if (event.key === "Enter") {
    const value = inputEl.value.trim();
    if (!value) {
      return;
    }
    state.history.push(value);
    state.historyIndex = state.history.length;
    await submitCommand(value);
    inputEl.value = "";
    updateHintVisibility();
  }
  if (event.key === "ArrowUp") {
    if (state.history.length === 0) {
      return;
    }
    state.historyIndex = Math.max(0, state.historyIndex - 1);
    inputEl.value = state.history[state.historyIndex] || "";
    updateHintVisibility();
    event.preventDefault();
  }
});

inputEl.addEventListener("input", updateHintVisibility);

transcriptEl.addEventListener("scroll", () => {
  if (isNearBottom()) {
    newMessagesPillEl.classList.add("hidden");
  }
});

newMessagesPillEl.addEventListener("click", scrollTranscriptToBottom);

async function boot() {
  updateHintVisibility();
  updateFooterStatus();
  await initialLoad();
  connectWs();
}

boot().catch((error) => {
  setStatus(`Boot failed: ${String(error?.message || error)}`, "error");
});

// ---- Tabs + Ops panel (M4) ----

const opsPanelEl = document.getElementById("opsPanel");
const opsTranscriptEl = document.getElementById("opsTranscript");
const opsFormEl = document.getElementById("opsForm");
const opsInputEl = document.getElementById("opsInput");
const opsMicBtnEl = document.getElementById("opsMicBtn");
const opsVoiceRepliesEl = document.getElementById("opsVoiceReplies");
const opsAudioEl = document.getElementById("opsAudio");
const layoutEl = document.querySelector("main.layout");

function switchTab(name) {
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  layoutEl.dataset.tab = name;
  opsPanelEl.classList.toggle("show", name === "ops");
  if (name === "ops") { refreshOps(); }
}
document.querySelectorAll(".tab-btn").forEach((b) => {
  b.addEventListener("click", () => switchTab(b.dataset.tab));
});

function renderOpsMessage(msg) {
  const bubble = document.createElement("div");
  bubble.className = `ops-bubble role-${msg.role}`;
  const tag = document.createElement("div");
  tag.className = "role-tag";
  tag.textContent = msg.role === "tool_result" ? "tool result" : msg.role;
  bubble.appendChild(tag);
  const body = document.createElement("div");
  body.textContent = msg.content || "";
  bubble.appendChild(body);
  (msg.tool_calls || []).forEach((call) => {
    const card = document.createElement("div");
    card.className = "ops-tool-card";
    card.textContent = `▶ ${call.name}(${JSON.stringify(call.args || {})})`;
    bubble.appendChild(card);
  });
  opsTranscriptEl.appendChild(bubble);
  opsTranscriptEl.scrollTop = opsTranscriptEl.scrollHeight;
}

async function refreshOps() {
  try {
    const snap = await fetch("/api/ops").then((r) => r.json());
    opsTranscriptEl.innerHTML = "";
    (snap.transcript || []).forEach(renderOpsMessage);
  } catch (e) {
    console.error("[agora] ops refresh failed", e);
  }
}

opsFormEl?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = opsInputEl.value.trim();
  if (!text) return;
  opsInputEl.value = "";
  try {
    await fetch("/api/ops/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
  } catch (e) {
    setStatus(`Ops send failed: ${e?.message || e}`, "error");
  }
});

// Voice: push-to-talk
let mediaRecorder = null;
let recordedChunks = [];
async function startRecording() {
  if (mediaRecorder) return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recordedChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) recordedChunks.push(e.data); };
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(recordedChunks, { type: "audio/webm" });
      const fd = new FormData();
      fd.append("audio", blob, "clip.webm");
      try {
        const resp = await fetch("/api/ops/transcribe", { method: "POST", body: fd });
        const data = await resp.json();
        if (data.text) {
          opsInputEl.value = data.text;
          opsFormEl.dispatchEvent(new Event("submit"));
        }
      } catch (e) {
        setStatus(`Transcribe failed: ${e?.message || e}`, "error");
      }
      mediaRecorder = null;
    };
    mediaRecorder.start();
    opsMicBtnEl.classList.add("ops-mic-recording");
  } catch (e) {
    setStatus(`Mic error: ${e?.message || e}`, "error");
  }
}
function stopRecording() {
  if (mediaRecorder && mediaRecorder.state === "recording") {
    mediaRecorder.stop();
    opsMicBtnEl.classList.remove("ops-mic-recording");
  }
}
// Push-to-talk: press on button OR hold Space, release anywhere.
// mouseleave intentionally NOT used — push-to-talk survives cursor drift.
opsMicBtnEl?.addEventListener("mousedown", (e) => { e.preventDefault(); startRecording(); });
document.addEventListener("mouseup", stopRecording);
opsMicBtnEl?.addEventListener("touchstart", (e) => { e.preventDefault(); startRecording(); });
opsMicBtnEl?.addEventListener("touchend", (e) => { e.preventDefault(); stopRecording(); });
opsMicBtnEl?.addEventListener("touchcancel", stopRecording);

// Space hotkey — only when Ops tab is active and user is not typing in a
// textarea/input. Uses keydown/keyup, ignores auto-repeat.
let spaceHeld = false;
document.addEventListener("keydown", (event) => {
  if (event.code !== "Space") return;
  if (event.repeat) return;
  const tag = (event.target && event.target.tagName) || "";
  if (tag === "TEXTAREA" || tag === "INPUT") return;
  const opsActive = layoutEl?.dataset.tab === "ops";
  if (!opsActive) return;
  event.preventDefault();
  if (!spaceHeld) {
    spaceHeld = true;
    startRecording();
  }
});
document.addEventListener("keyup", (event) => {
  if (event.code !== "Space") return;
  if (!spaceHeld) return;
  spaceHeld = false;
  stopRecording();
});

async function playAdminTts(text) {
  if (!opsVoiceRepliesEl?.checked || !text) return;
  try {
    const url = `/api/ops/tts?text=${encodeURIComponent(text.slice(0, 4000))}`;
    opsAudioEl.src = url;
    await opsAudioEl.play();
  } catch (e) {
    console.warn("[agora] TTS playback failed", e);
  }
}

// Hook ops WS events — additive to existing ws handler.
const _origHandleWs = window.__origWsHandler;  // placeholder; new handler below extends the existing onmessage
// Instead of monkey-patching, listen on a custom event from WS.
window.addEventListener("agora:ops-message", (e) => {
  renderOpsMessage(e.detail);
  if (e.detail.role === "admin" && e.detail.content) {
    playAdminTts(e.detail.content);
  }
});


