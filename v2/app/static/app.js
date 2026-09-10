// Second Unit — plain JS, no framework, no build step.

const $ = (id) => document.getElementById(id);

let projectId = null;
let askedAt = 0;          // when the question appeared — hesitation is signal
const STORAGE_KEY = "second-unit-project";
let speechPort = null;
let speechSocket = null;
let audioContext = null;
let mediaStream = null;
let microphoneNode = null;
let captureNode = null;
let speechBaseText = "";
let speechFinalText = "";
let speechStarted = false;
let lastVoiceAt = 0;
let speechTimer = null;
let speechTarget = null;
let speechButton = null;
let speechStatus = null;
let speechSubmit = null;
let storyObserver = null;
let currentWorkspace = "story";
let currentStoryView = "interview";
let currentOutline = null;
let currentOutlineApproved = false;
let currentSuggestions = [];
let outlineEditTarget = null;

const WORKSPACES = {
  screenplay: {
    title: "Screenplay",
    description: "Turn the approved outline into a screenplay draft, or import a screenplay you already have.",
    waiting: "Complete and approve the Story outline to unlock screenplay work.",
    ready: "Your outline is ready. Screenplay generation and import are the next feature to build.",
  },
  breakdown: {
    title: "Breakdown",
    description: "Confirm the cast, locations, props, wardrobe, equipment, and production concerns in every scene.",
    waiting: "This stage will open after a screenplay is approved.",
  },
  schedule: {
    title: "Schedule",
    description: "Arrange scenes into practical shoot days and catch conflicts before they reach the set.",
    waiting: "This stage will open after the scene breakdown is confirmed.",
  },
  shots: {
    title: "Shot list",
    description: "Plan essential coverage, setups, movement, and equipment scene by scene.",
    waiting: "This stage will open once scenes have been scheduled.",
  },
  readiness: {
    title: "Readiness",
    description: "See exactly what is ready, at risk, or blocking each shoot day.",
    waiting: "Readiness will become live as production tasks and shoot days are added.",
  },
};

function showNotice(message) {
  $("notice").textContent = message || "";
  $("notice").hidden = !message;
}

function setSaveState(message) {
  $("saveState").textContent = message || "";
}

function startOver() {
  localStorage.removeItem(STORAGE_KEY);
  projectId = null;
  currentOutline = null;
  currentOutlineApproved = false;
  currentWorkspace = "story";
  currentStoryView = "interview";
  $("productionFlow").hidden = true;
  $("futureView").hidden = true;
  $("interview").hidden = true;
  $("outlineView").hidden = true;
  $("start").hidden = false;
  $("startForm").hidden = true;
  $("pathGrid").hidden = false;
  $("seed").value = "";
  $("involvement").value = "50";
  const defaultFormat = document.querySelector('input[name="storytellingFormat"][value="not_sure"]');
  if (defaultFormat) defaultFormat.checked = true;
  renderInvolvement();
  showNotice("");
  setSaveState("");
  $("seed").focus();
  window.scrollTo(0, 0);
}

document.querySelectorAll(".flow-step").forEach((button) => {
  button.addEventListener("click", () => openWorkspace(button.dataset.workspace));
});

$("returnToStory").addEventListener("click", () => openWorkspace("story"));

function openWorkspace(workspace) {
  if (!projectId) return;
  currentWorkspace = workspace;
  $("start").hidden = true;
  $("interview").hidden = true;
  $("outlineView").hidden = true;
  $("futureView").hidden = true;

  if (workspace === "story") {
    if (currentStoryView === "outline" && currentOutline) {
      $("outlineView").hidden = false;
    } else {
      $("interview").hidden = false;
    }
  } else {
    const view = WORKSPACES[workspace];
    $("futureTitle").textContent = view.title;
    $("futureDescription").textContent = view.description;
    $("futureState").textContent = currentOutlineApproved && workspace === "screenplay"
      ? view.ready
      : (currentOutline && workspace === "screenplay"
        ? "Review and approve the Story outline to unlock screenplay work."
        : view.waiting);
    $("futureView").hidden = false;
  }
  updateFlow();
  window.scrollTo(0, 0);
}

function updateFlow() {
  $("productionFlow").hidden = !projectId;
  document.querySelectorAll(".flow-step").forEach((step) => {
    const workspace = step.dataset.workspace;
    const active = workspace === currentWorkspace;
    step.classList.toggle("active", active);
    step.classList.toggle("complete", workspace === "story" && currentOutlineApproved);
    step.classList.toggle("available", workspace === "screenplay" && currentOutlineApproved);
    if (active) step.setAttribute("aria-current", "step");
    else step.removeAttribute("aria-current");
  });
}

$("home").addEventListener("click", () => {
  if (!projectId || window.confirm("Start a new film? Your current answers will remain saved on this device.")) {
    startOver();
  }
});

$("newProject").addEventListener("click", startOver);

// ------------------------------------------------------------ story history

$("openHistory").addEventListener("click", openHistory);
$("closeHistory").addEventListener("click", closeHistory);
$("historyBackdrop").addEventListener("click", closeHistory);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("historyShell").hidden) closeHistory();
});

async function openHistory() {
  $("historyShell").hidden = false;
  document.body.classList.add("panel-open");
  $("historyList").hidden = false;
  $("historyDetail").hidden = true;
  $("historyList").innerHTML = '<p class="history-empty">Loading your stories…</p>';
  const response = await fetch("/api/history");
  const payload = await response.json();
  const items = payload.history || [];
  if (!items.length) {
    $("historyList").innerHTML = '<p class="history-empty">Completed outlines and saved drafts will appear here.</p>';
  } else {
    $("historyList").innerHTML = items.map((item) => `
      <button type="button" class="history-item" data-kind="${escape(item.kind)}" data-id="${escape(item.id)}">
        <span class="history-meta">
          <span>${item.kind === "outline" ? "Completed outline" : "Saved draft"}</span>
          <span>${Math.round((item.readiness || 0) * 100)}% ready · ${formatDate(item.created_at)}</span>
        </span>
        <strong class="history-item-title">${escape(item.title || "Untitled")}</strong>
        <span class="history-item-summary">${escape(item.summary || "No summary")}</span>
      </button>`).join("");
    $("historyList").querySelectorAll(".history-item").forEach((button) => {
      button.addEventListener("click", () => openHistoryDetail(button.dataset.kind, button.dataset.id));
    });
  }
  $("closeHistory").focus();
}

function closeHistory() {
  $("historyShell").hidden = true;
  document.body.classList.remove("panel-open");
  $("openHistory").focus();
}

async function openHistoryDetail(kind, id) {
  $("historyList").hidden = true;
  $("historyDetail").hidden = false;
  $("historyDetail").className = "history-detail";
  $("historyDetail").innerHTML = '<p class="history-empty">Tracing the story…</p>';
  const response = await fetch(`/api/history/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`);
  const item = await response.json();
  if (!response.ok || item.error) {
    $("historyDetail").innerHTML = '<p class="history-empty">This story could not be opened.</p>';
    return;
  }

  const transcript = (item.transcript || []).filter((turn) => turn.question || turn.guidance);
  const scenes = item.outline?.scenes || [];
  $("historyDetail").innerHTML = `
    <button type="button" class="text-button history-detail-back" id="historyBack">← All stories</button>
    <p class="eyebrow">${item.kind === "outline" ? "Completed outline" : "Saved draft"} · ${Math.round((item.readiness || 0) * 100)}% ready</p>
    <h3>${escape(item.title || "Untitled")}</h3>
    <p class="history-detail-summary">${escape(item.summary || "")}</p>
    ${item.seed ? historySection("Starting point", `<p>${escape(item.seed)}</p>`) : ""}
    ${(item.established || []).length ? historySection("What was established",
      `<ul>${item.established.map((fact) => `<li>${escape(fact)}</li>`).join("")}</ul>`) : ""}
    ${transcript.length ? historySection("Story trace", `<dl>${transcript.map((turn) => `
      <div class="trace-turn"><dt>${[turn.guidance, turn.question].filter(Boolean).map(escape).join(" ")}</dt><dd>${turn.skipped ? "Skipped" : escape(turn.answer || "No answer")}</dd></div>`).join("")}</dl>`) : ""}
    ${scenes.length ? historySection("Created outline", scenes.map((scene) => `
      <article class="scene"><div class="scene-n">${escape(scene.n)}</div><div>
        <div class="slug">${escape(scene.slug || "")}</div>
        <p class="scene-action">${escape(scene.action || "")}</p>
      </div></article>`).join("")) : ""}`;
  $("historyBack").addEventListener("click", () => {
    $("historyDetail").hidden = true;
    $("historyList").hidden = false;
  });
}

function historySection(title, contents) {
  return `<section class="history-section"><h4>${escape(title)}</h4>${contents}</section>`;
}

function formatDate(timestamp) {
  return new Date(timestamp * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

$("chooseBrainstorm").addEventListener("click", () => {
  $("pathGrid").hidden = true;
  $("startForm").hidden = false;
  $("seed").focus();
});

$("changePath").addEventListener("click", () => {
  $("startForm").hidden = true;
  $("pathGrid").hidden = false;
  $("chooseBrainstorm").focus();
});

$("involvement").addEventListener("input", renderInvolvement);
renderInvolvement();

function renderInvolvement() {
  const value = Number($("involvement").value);
  $("involvementValue").textContent = `${value}%`;
  if (value <= 33) {
    $("involvementNote").textContent = "The agent decides when it has enough direction—usually 2–4 questions—then develops the outline and labels what it added.";
  } else if (value <= 66) {
    $("involvementNote").textContent = "The agent adapts to your answers—usually 4–7 questions—and may bridge minor gaps once the important decisions are clear.";
  } else {
    $("involvementNote").textContent = "You make the story decisions. The editor asks contextual questions and does not invent.";
  }
}

// ------------------------------------------------------------------ start

$("startForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const button = e.target.querySelector('button[type="submit"]');
  button.disabled = true;
  button.textContent = "Thinking…";

  const state = await post("/api/project", {
    seed: $("seed").value,
    storytelling_format: document.querySelector('input[name="storytellingFormat"]:checked')?.value || "not_sure",
    involvement: Number($("involvement").value),
  });
  if (!state || state.error) {
    button.disabled = false;
    button.textContent = "Begin interview";
    return;
  }

  projectId = state.project_id;
  localStorage.setItem(STORAGE_KEY, projectId);
  currentStoryView = "interview";
  openWorkspace("story");
  render(state);
  $("answer").focus();
});

// ------------------------------------------------------------------ answer

$("answerForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const selected = [...document.querySelectorAll('#suggestionOptions input[type="checkbox"]:checked')]
    .map((input) => currentSuggestions[Number(input.dataset.index)])
    .filter(Boolean);
  const note = $("answer").value.trim();
  const choice = selected.length
    ? `I want to use ${selected.length === 1 ? "this idea" : "these ideas"}:\n${selected.map((idea) => `- ${idea.label}${idea.detail ? ` — ${idea.detail}` : ""}`).join("\n")}`
    : "";
  const text = [choice, note].filter(Boolean).join("\n\n");
  if (!text) return;
  await advance("/answer", { text, seconds: (Date.now() - askedAt) / 1000 });
});

$("skip").addEventListener("click", () => advance("/skip", {}));
$("suggestIdeas").addEventListener("click", () => advance("/answer", {
  text: "I’m not sure yet. Give me two or three concrete possibilities I can choose or combine.",
  request_suggestions: true,
  seconds: (Date.now() - askedAt) / 1000,
}));
$("keepExploring").addEventListener("click", () => advance("/continue", {}));

$("abortProject").addEventListener("click", async () => {
  if (!window.confirm("End this interview, save it as a draft, and start over?")) return;
  const button = $("abortProject");
  button.disabled = true;
  button.textContent = "Summarizing & saving…";
  showNotice("");
  const result = await post(`/api/project/${projectId}/abort`, {});
  button.disabled = false;
  button.textContent = "Save as draft & start over";
  if (!result || result.error || !result.saved) return;
  const title = result.draft?.title || "Untitled draft";
  startOver();
  showNotice(`“${title}” was saved as a draft. You can begin a new story.`);
});

// Cmd/Ctrl+Enter submits — writers keep their hands on the keyboard.
$("answer").addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") $("answerForm").requestSubmit();
});

$("answer").addEventListener("input", () => {
  const n = $("answer").value.trim().split(/\s+/).filter(Boolean).length;
  $("counter").textContent = n ? `${n} word${n === 1 ? "" : "s"}` : "";
});

// ------------------------------------------------------------ voice typing

$("mic").addEventListener("click", () => {
  if (captureNode) stopVoiceCapture();
  else startVoiceCapture("answer", "mic", "speechState");
});

$("micSeed").addEventListener("click", () => {
  if (captureNode) stopVoiceCapture();
  else startVoiceCapture("seed", "micSeed", "speechStateSeed");
});

function speechText(interim = "") {
  return [speechBaseText, speechFinalText, interim]
    .map((part) => part.trim())
    .filter(Boolean)
    .join(" ");
}

function commitSpeechText(interim = "") {
  speechTarget.value = speechText(interim);
  speechTarget.dispatchEvent(new Event("input"));
}

async function startVoiceCapture(targetId, buttonId, statusId) {
  showNotice("");
  if (!speechPort) {
    showNotice("Voice typing is not configured on this server.");
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) {
    showNotice("This browser does not support live microphone transcription.");
    return;
  }

  try {
    speechTarget = $(targetId);
    speechButton = $(buttonId);
    speechStatus = $(statusId);
    speechSubmit = speechTarget.closest("form")?.querySelector('button[type="submit"]') || null;
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    audioContext = new AudioContext({ latencyHint: "interactive", sampleRate: 16000 });
    await audioContext.audioWorklet.addModule("pcm-worklet.js");
    microphoneNode = audioContext.createMediaStreamSource(mediaStream);
    captureNode = new AudioWorkletNode(audioContext, "pcm-capture");

    speechBaseText = speechTarget.value;
    speechFinalText = "";
    speechStarted = false;
    lastVoiceAt = Date.now();

    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    speechSocket = new WebSocket(`${protocol}//${location.hostname}:${speechPort}`);
    speechSocket.binaryType = "arraybuffer";
    speechSocket.addEventListener("open", () => {
      speechSocket.send(JSON.stringify({
        type: "start",
        sample_rate: audioContext.sampleRate,
        language: document.documentElement.lang === "en" ? "en-US" : document.documentElement.lang,
      }));
    });
    speechSocket.addEventListener("message", handleSpeechMessage);
    speechSocket.addEventListener("error", () => finishVoiceCapture("Voice typing connection failed."));
    speechSocket.addEventListener("close", () => {
      if (captureNode) finishVoiceCapture("Voice typing stopped.", false);
    });

    captureNode.port.onmessage = ({ data }) => {
      if (data.rms > 0.012) {
        speechStarted = true;
        lastVoiceAt = Date.now();
      }
      if (speechStarted && Date.now() - lastVoiceAt > 2500) {
        stopVoiceCapture();
        return;
      }
      if (speechSocket?.readyState === WebSocket.OPEN) speechSocket.send(data.pcm);
    };
    microphoneNode.connect(captureNode);
    // Keep the worklet processing without making microphone audio audible.
    const silent = audioContext.createGain();
    silent.gain.value = 0;
    captureNode.connect(silent).connect(audioContext.destination);

    speechButton.classList.add("recording");
    speechButton.setAttribute("aria-label", "Stop voice typing");
    speechButton.title = "Stop voice typing";
    speechStatus.textContent = "Connecting to Google Speech-to-Text…";
    speechStatus.classList.add("listening");
    if (speechSubmit) speechSubmit.disabled = true;
    speechTimer = window.setTimeout(stopVoiceCapture, 120000);
  } catch (err) {
    finishVoiceCapture(`Microphone unavailable — ${err.message}`);
  }
}

function handleSpeechMessage(event) {
  const message = JSON.parse(event.data);
  if (message.type === "ready") {
    speechStatus.textContent = "Listening… click the microphone to stop.";
  } else if (message.type === "interim") {
    // Interim hypotheses can change several times per second. Rendering each
    // rewrite inside a textarea causes visible glyph smearing in some browsers,
    // even though the final transcript is correct. Keep unsettled words in the
    // status line and commit only Google's final segments to editable text.
    speechStatus.textContent = message.text ? `Hearing: ${message.text}` : "Listening…";
  } else if (message.type === "final") {
    speechFinalText = [speechFinalText, message.text].filter(Boolean).join(" ");
    commitSpeechText();
    speechStatus.textContent = "Listening… click the microphone to stop.";
  } else if (message.type === "done") {
    finishVoiceCapture("Voice typing complete.", false);
  } else if (message.type === "error") {
    finishVoiceCapture(`Voice typing stopped — ${message.message}`);
  }
}

function stopVoiceCapture() {
  if (speechSocket?.readyState === WebSocket.OPEN) {
    speechSocket.send(JSON.stringify({ type: "stop" }));
  }
  releaseMicrophone();
  speechStatus.textContent = "Finishing transcription…";
}

function releaseMicrophone() {
  if (speechTimer) window.clearTimeout(speechTimer);
  speechTimer = null;
  captureNode?.disconnect();
  microphoneNode?.disconnect();
  mediaStream?.getTracks().forEach((track) => track.stop());
  audioContext?.close();
  captureNode = null;
  microphoneNode = null;
  mediaStream = null;
  audioContext = null;
  speechButton?.classList.remove("recording");
  speechButton?.setAttribute("aria-label", "Start voice typing");
  if (speechButton) speechButton.title = "Start voice typing";
  if (speechSubmit) speechSubmit.disabled = false;
}

function finishVoiceCapture(message, closeSocket = true) {
  releaseMicrophone();
  if (closeSocket && speechSocket?.readyState === WebSocket.OPEN) speechSocket.close();
  speechSocket = null;
  if (speechStatus) {
    speechStatus.textContent = message;
    speechStatus.classList.remove("listening");
  }
  speechTarget?.focus();
}

async function advance(suffix, body) {
  showNotice("");
  setThinking(true);
  const state = await post(`/api/project/${projectId}${suffix}`, body);
  setThinking(false);
  if (!state || state.error) return;
  $("answer").value = "";
  $("counter").textContent = "";
  render(state);
  $("answer").focus();
}

function setThinking(on) {
  $("next").disabled = on;
  $("skip").disabled = on;
  $("suggestIdeas").disabled = on;
  if (on) {
    $("assistantGuidance").hidden = true;
    $("question").classList.add("thinking");
    $("question").textContent = "…";
  } else {
    $("question").classList.remove("thinking");
  }
}

// ------------------------------------------------------------------ render

function render(state) {
  $("goal").textContent = state.goal || "";
  const guidance = state.guidance || "";
  $("assistantGuidance").hidden = !guidance;
  $("assistantGuidance").innerHTML = escape(guidance).replace(/\n/g, "<br>");
  renderSuggestions(state.suggestions || []);
  $("question").textContent = state.question || "Your story is ready for an outline.";
  renderConversation(state);
  askedAt = Date.now();

  const readiness = Math.max(0, Math.min(100, state.readiness_percent || 0));
  $("readinessValue").textContent = `${readiness}%`;
  $("readinessBar").style.width = `${readiness}%`;
  $("askPanel").hidden = Boolean(state.interview_complete);

  const list = state.established || [];
  $("established").hidden = list.length === 0;
  $("establishedList").innerHTML = list
    .map((e) => typeof e === "string"
      ? `<li>${escape(e)}</li>`
      : `<li><span>${escape(e.q)}</span>${escape(e.a)}</li>`)
    .join("");

  $("finish").hidden = !state.can_write_up && !state.interview_complete;
  $("writeUp").hidden = !state.can_write_up;
  $("keepExploring").hidden = !state.interview_complete;
  $("finishNote").textContent = state.can_write_up ? state.readiness_reason || "" : (state.write_up_note || "");

  const bits = [`${state.answered} answered`, `${state.words} words`];
  const modeLabel = {
    ai_led: "AI-led",
    collaborative: "collaborative",
    author_led: "author-led",
  }[state.collaboration_mode];
  if (modeLabel) bits.push(modeLabel);
  if (state.ranked) bits.push("question chosen from Grafana");
  if (state.degraded) bits.push("built-in question — Vertex busy");
  bits.push(`${readiness}% outline readiness`);
  status(bits);
  setSaveState("Saved");
}

function renderSuggestions(suggestions) {
  currentSuggestions = suggestions;
  $("assistantSuggestions").hidden = suggestions.length === 0;
  $("suggestIdeas").hidden = suggestions.length > 0;
  $("suggestionOptions").innerHTML = suggestions.map((idea, index) => `
    <label class="suggestion-option">
      <input type="checkbox" data-index="${index}">
      <span class="suggestion-check" aria-hidden="true">✓</span>
      <span><strong>${escape(idea.label)}</strong><small>${escape(idea.detail || "")}</small></span>
    </label>`).join("");
  $("suggestionOptions").querySelectorAll('input[type="checkbox"]').forEach((input) => {
    input.addEventListener("change", () => {
      const chosen = $("suggestionOptions").querySelectorAll('input[type="checkbox"]:checked').length;
      $("next").textContent = chosen ? `Use ${chosen} selected & continue` : "Answer & continue";
    });
  });
  $("next").textContent = "Answer & continue";
}

function renderConversation(state) {
  const completed = (state.transcript || []).filter((turn) => turn.answer || turn.skipped);
  const hasConversation = Boolean(state.seed) || completed.length > 0;
  $("conversation").hidden = !hasConversation;
  $("conversationCount").textContent = `${completed.length} exchange${completed.length === 1 ? "" : "s"}`;

  const seed = state.seed
    ? `<div class="chat-row writer"><span class="chat-label">You began with</span><p>${escape(state.seed)}</p></div>`
    : "";
  const turns = completed.map((turn) => {
    const suggestionList = (turn.suggestions || []).length
      ? `<ul class="chat-suggestions">${turn.suggestions.map((idea) => `<li><strong>${escape(idea.label)}</strong>${idea.detail ? ` — ${escape(idea.detail)}` : ""}</li>`).join("")}</ul>`
      : "";
    const assistant = [turn.guidance, suggestionList, turn.question].filter(Boolean)
      .map((part) => part === suggestionList ? part : escape(part).replace(/\n/g, "<br>"))
      .join("<br><br>");
    const writer = turn.skipped ? "Skipped" : escape(turn.answer || "");
    return `<div class="conversation-exchange">
      <div class="chat-row assistant">
        <span class="chat-label">Story editor${turn.response_kind && turn.response_kind !== "question" ? ` · ${escape(turn.response_kind)}` : ""}</span>
        <p>${assistant}</p>
      </div>
      <div class="chat-row writer${turn.skipped ? " skipped" : ""}">
        <span class="chat-label">You</span><p>${writer}</p>
      </div>
    </div>`;
  }).join("");
  $("conversationTurns").innerHTML = seed + turns;
  // The rail scrolls on its own, so pin it to the latest exchange after every
  // render — otherwise a long history opens at the oldest question.
  $("conversation").scrollTop = $("conversation").scrollHeight;
}

function escape(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// ------------------------------------------------------------------ plumbing

async function post(url, body) {
  try {
    setSaveState("Saving…");
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await r.json();
    if (!r.ok || payload.error) {
      showNotice(payload.error || `Request failed (${r.status})`);
      setSaveState("Not saved");
    }
    return payload;
  } catch (err) {
    showNotice(`Could not reach the server — ${err.message}`);
    setSaveState("Offline");
    return null;
  }
}

function status(bits) {
  $("status").replaceChildren(...bits.map((bit) => {
    const span = document.createElement("span");
    span.textContent = bit;
    return span;
  }));
}

// health line on load, so the runtime dependencies are visible not assumed
fetch("/api/health").then((r) => r.json()).then((h) => {
  speechPort = h.speech_ws_port || null;
  [$("mic"), $("micSeed")].forEach((button) => {
    button.disabled = !speechPort;
    button.title = speechPort ? "Start voice typing" : `Voice typing unavailable: ${h.speech}`;
  });
  status([
    `model ${h.model}`,
    `grafana read ${h.grafana_read === false ? "off" : "on"}`,
    `grafana write ${h.grafana_write === "configured" ? "on" : "off"}`,
    `speech ${speechPort ? "on" : "off"}`,
  ]);
}).catch(() => {});

// Resume the last local session when the server still has it.
const savedProject = localStorage.getItem(STORAGE_KEY);
if (savedProject) {
  fetch(`/api/project/${encodeURIComponent(savedProject)}`)
    .then((r) => r.ok ? r.json() : Promise.reject())
    .then((state) => {
      projectId = state.project_id;
      if (state.outline) {
        showOutline(state.outline, state.outline_approved);
      } else {
        currentStoryView = "interview";
        openWorkspace("story");
        render(state);
      }
    })
    .catch(() => localStorage.removeItem(STORAGE_KEY));
}

// ------------------------------------------------------------------ outline

$("writeUp").addEventListener("click", async () => {
  const b = $("writeUp");
  b.disabled = true;
  b.textContent = "Assembling…";
  const res = await post(`/api/project/${projectId}/write-up`, {});
  b.disabled = false;
  b.textContent = "Write it up";
  if (!res || res.error) {
    showNotice(res?.error || "Could not assemble the outline.");
    return;
  }
  showOutline(res.outline, res.outline_approved);
});

$("approveOutline").addEventListener("click", async () => {
  const button = $("approveOutline");
  button.disabled = true;
  button.textContent = "Approving…";
  const state = await post(`/api/project/${projectId}/approve-outline`, {});
  button.disabled = false;
  button.textContent = "Approve outline & continue";
  if (!state || state.error) return;
  currentOutlineApproved = true;
  $("approveOutline").hidden = true;
  updateFlow();
  openWorkspace("screenplay");
});

$("backToInterview").addEventListener("click", () => {
  currentStoryView = "interview";
  $("outlineView").hidden = true;
  $("interview").hidden = false;
  updateFlow();
  $("answer").focus();
});

function showOutline(o, approved = false) {
  currentOutline = o;
  currentOutlineApproved = Boolean(approved);
  currentWorkspace = "story";
  currentStoryView = "outline";
  $("interview").hidden = true;
  $("futureView").hidden = true;
  $("outlineView").hidden = false;
  updateFlow();

  $("outTitle").textContent = o.title || "Untitled";
  $("outLogline").textContent = o.logline || "";
  $("approveOutline").hidden = currentOutlineApproved;

  const outlineScenes = o.scenes || [];
  renderStoryGraph(outlineScenes);

  $("scenes").innerHTML = outlineScenes.map((s, index) => `
    <article class="scene" id="outline-scene-${index + 1}" data-scene-index="${index}">
      <div class="scene-n">${s.n}</div>
      <div>
        <div class="scene-heading-row">
          <div class="slug">${escape(s.slug || "")}</div>
          <button type="button" class="edit-button edit-scene" data-scene-index="${index}">Edit</button>
        </div>
        ${s.who?.length ? `<div class="who">${escape(s.who.join(" · "))}</div>` : ""}
        <p class="scene-action">${escape(s.action || "")}</p>
        <p class="scene-why">${escape(s.why || "")}</p>
      </div>
    </article>`).join("");

  window.scrollTo(0, 0);
  status([`${(o.scenes || []).length} scenes`]);

  connectStoryGraph();
  document.querySelectorAll(".edit-scene").forEach((button) => {
    button.addEventListener("click", () => openSceneEditor(Number(button.dataset.sceneIndex)));
  });
}

$("editOutlineHeader").addEventListener("click", openOutlineHeaderEditor);
$("closeOutlineEditor").addEventListener("click", closeOutlineEditor);
$("cancelOutlineEdit").addEventListener("click", closeOutlineEditor);

function editField(label, name, value, rows = 2) {
  return `<label class="field"><span class="label">${escape(label)}</span>
    <textarea name="${escape(name)}" rows="${rows}">${escape(value || "")}</textarea></label>`;
}

function openOutlineHeaderEditor() {
  outlineEditTarget = { type: "header" };
  $("outlineEditorTitle").textContent = "Edit title and logline";
  $("outlineEditFields").innerHTML =
    editField("Title", "title", currentOutline.title, 2) +
    editField("Logline", "logline", currentOutline.logline, 4);
  $("outlineEditor").showModal();
}

function openSceneEditor(index) {
  const scene = currentOutline.scenes[index];
  if (!scene) return;
  outlineEditTarget = { type: "scene", index };
  $("outlineEditorTitle").textContent = `Edit scene ${index + 1}`;
  $("outlineEditFields").innerHTML =
    editField("Slug line", "slug", scene.slug, 2) +
    editField("Characters — separated by commas", "who", (scene.who || []).join(", "), 2) +
    editField("What happens", "action", scene.action, 7) +
    editField("Purpose of the scene", "why", scene.why, 3);
  $("outlineEditor").showModal();
}

function closeOutlineEditor() {
  outlineEditTarget = null;
  $("outlineEditor").close();
}

$("outlineEditForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!outlineEditTarget || !currentOutline) return;
  const data = new FormData(event.target);
  const updated = structuredClone(currentOutline);
  if (outlineEditTarget.type === "header") {
    updated.title = String(data.get("title") || "").trim();
    updated.logline = String(data.get("logline") || "").trim();
  } else {
    const scene = updated.scenes[outlineEditTarget.index];
    scene.slug = String(data.get("slug") || "").trim();
    scene.who = String(data.get("who") || "").split(",").map((name) => name.trim()).filter(Boolean);
    scene.action = String(data.get("action") || "").trim();
    scene.why = String(data.get("why") || "").trim();
  }

  const button = $("saveOutlineEdit");
  button.disabled = true;
  button.textContent = "Saving…";
  const state = await post(`/api/project/${projectId}/edit-outline`, { outline: updated });
  button.disabled = false;
  button.textContent = "Save changes";
  if (!state || state.error) return;
  $("outlineEditor").close();
  outlineEditTarget = null;
  showOutline(state.outline, state.outline_approved);
  showNotice("Outline updated. Review and approve the new version when it is ready.");
});

function renderStoryGraph(scenes) {
  $("storyGraphSection").hidden = scenes.length === 0;
  $("storyGraphCount").textContent = `${scenes.length} scene${scenes.length === 1 ? "" : "s"}`;
  $("storyGraph").innerHTML = scenes.map((scene, index) => `
    <div class="story-node-wrap">
      <button type="button" class="story-node${index === 0 ? " active" : ""}"
        data-scene-index="${index}" aria-label="Go to scene ${escape(scene.n || index + 1)}"
        ${index === 0 ? 'aria-current="step"' : ""}>
        <span class="story-node-number">${escape(scene.n || index + 1)}</span>
        <span class="story-node-slug">${escape(scene.slug || "Unspecified scene")}</span>
        <span class="story-node-purpose">${escape(scene.why || scene.action || "")}</span>
      </button>
    </div>`).join("");
}

function connectStoryGraph() {
  storyObserver?.disconnect();
  const nodes = [...document.querySelectorAll(".story-node")];
  const scenes = [...document.querySelectorAll("#scenes .scene")];

  function activateScene(index, center = false) {
    nodes.forEach((node, i) => {
      node.classList.toggle("active", i === index);
      if (i === index) node.setAttribute("aria-current", "step");
      else node.removeAttribute("aria-current");
    });
    if (center && nodes[index]) {
      const viewport = $("storyGraphViewport");
      const left = nodes[index].offsetLeft - (viewport.clientWidth - nodes[index].offsetWidth) / 2;
      viewport.scrollTo({ left: Math.max(0, left), behavior: "smooth" });
    }
  }

  nodes.forEach((node) => {
    node.addEventListener("click", () => {
      const index = Number(node.dataset.sceneIndex);
      activateScene(index, true);
      scenes[index]?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  storyObserver = new IntersectionObserver((entries) => {
    const visible = entries
      .filter((entry) => entry.isIntersecting)
      .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (visible) activateScene(Number(visible.target.dataset.sceneIndex), true);
  }, { rootMargin: "-20% 0px -55%", threshold: [0.15, 0.45, 0.75] });
  scenes.forEach((scene) => storyObserver.observe(scene));
}
