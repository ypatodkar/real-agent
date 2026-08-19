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
  $("interview").hidden = true;
  $("outlineView").hidden = true;
  $("start").hidden = false;
  $("startForm").hidden = true;
  $("pathGrid").hidden = false;
  $("seed").value = "";
  $("involvement").value = "75";
  renderInvolvement();
  showNotice("");
  setSaveState("");
  $("seed").focus();
  window.scrollTo(0, 0);
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

  const transcript = (item.transcript || []).filter((turn) => turn.question);
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
      <div class="trace-turn"><dt>${escape(turn.question)}</dt><dd>${turn.skipped ? "Skipped" : escape(turn.answer || "No answer")}</dd></div>`).join("")}</dl>`) : ""}
    ${scenes.length ? historySection("Created outline", scenes.map((scene) => `
      <article class="scene"><div class="scene-n">${escape(scene.n)}</div><div>
        <div class="slug">${escape(scene.slug || "")}</div>
        <p class="scene-action">${escape(scene.action || "")}</p>
      </div></article>`).join("")) : ""}
    ${(item.outline?.ai_added || []).length ? historySection("Developed by AI",
      `<ul>${item.outline.ai_added.map((detail) => `<li>${escape(detail)}</li>`).join("")}</ul>`) : ""}
    ${(item.critical_gaps || []).length ? historySection("Still unresolved",
      `<ul>${item.critical_gaps.map((gap) => `<li>${escape(gap)}</li>`).join("")}</ul>`) : ""}`;
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
    involvement: Number($("involvement").value),
  });
  if (!state || state.error) {
    button.disabled = false;
    button.textContent = "Begin interview";
    return;
  }

  projectId = state.project_id;
  localStorage.setItem(STORAGE_KEY, projectId);
  $("start").hidden = true;
  $("interview").hidden = false;
  render(state);
  $("answer").focus();
});

// ------------------------------------------------------------------ answer

$("answerForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("answer").value.trim();
  if (!text) return;
  await advance("/answer", { text, seconds: (Date.now() - askedAt) / 1000 });
});

$("skip").addEventListener("click", () => advance("/skip", {}));
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
  if (on) {
    $("question").classList.add("thinking");
    $("question").textContent = "…";
  } else {
    $("question").classList.remove("thinking");
  }
}

// ------------------------------------------------------------------ render

function render(state) {
  $("goal").textContent = state.goal || "";
  $("question").textContent = state.question || "Your story is ready for an outline.";
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

  const gaps = state.critical_gaps || [];
  $("interviewGaps").hidden = gaps.length === 0;
  $("interviewGapsList").innerHTML = gaps.map((g) => `<li>${escape(g)}</li>`).join("");

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
      $("start").hidden = true;
      $("interview").hidden = false;
      render(state);
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
  showOutline(res.outline);
});

$("backToInterview").addEventListener("click", () => {
  $("outlineView").hidden = true;
  $("interview").hidden = false;
  $("answer").focus();
});

function showOutline(o) {
  $("interview").hidden = true;
  $("outlineView").hidden = false;

  $("outTitle").textContent = o.title || "Untitled";
  $("outLogline").textContent = o.logline || "";

  $("scenes").innerHTML = (o.scenes || []).map((s) => `
    <article class="scene">
      <div class="scene-n">${s.n}</div>
      <div>
        <div class="slug">${escape(s.slug || "")}</div>
        ${s.who?.length ? `<div class="who">${escape(s.who.join(" · "))}</div>` : ""}
        <p class="scene-action">${escape(s.action || "")}</p>
        <p class="scene-why">${escape(s.why || "")}</p>
        ${s.missing ? `<p class="scene-missing">Undecided: ${escape(s.missing)}</p>` : ""}
      </div>
    </article>`).join("");

  const gaps = o.gaps || [];
  $("gapsBox").hidden = gaps.length === 0;
  $("gapsList").innerHTML = gaps.map((g) => `<li>${escape(g)}</li>`).join("");

  const aiAdded = o.ai_added || [];
  $("aiAddedBox").hidden = aiAdded.length === 0;
  $("aiAddedList").innerHTML = aiAdded.map((detail) => `<li>${escape(detail)}</li>`).join("");

  // The audit is the promise being kept in public: if a name appears that the
  // writer never typed, say so rather than hoping nobody checks.
  const flagged = o.unsupported || [];
  const existing = document.querySelector(".flagged");
  if (existing) existing.remove();
  if (flagged.length) {
    const el = document.createElement("p");
    el.className = "flagged";
    el.textContent = `These appear in the outline but you never mentioned them: ${flagged.join(", ")}`;
    $("outlineView").appendChild(el);
  }

  window.scrollTo(0, 0);
  status([`${(o.scenes || []).length} scenes`, `${gaps.length} still undecided`,
          flagged.length ? `${flagged.length} unsupported` : "nothing invented"]);
}
