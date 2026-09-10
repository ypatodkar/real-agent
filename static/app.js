const $ = (id) => document.getElementById(id);

let interview = null;
let health = null;
let composerPurpose = "answer";
let busy = false;
let serverPending = false;
let noticeTimer = null;
let activeVoice = null;
let voiceStarting = false;
let voiceStopping = false;
let pendingPoll = null;
let historyReturnFocus = null;
let activeStage = "start";
let outlineState = null;
let outlineLoading = false;
let outlineStructureOptions = [];
let editingBeatId = "";
let screenplayState = null;
let screenplayModes = [];
let screenplayLoading = false;
let screenplayGenerating = false;
let editingSceneId = "";
let breakdownState = null;
let breakdownCategories = [];
let breakdownLoading = false;
let breakdownGenerating = false;
let editingBreakdownItemId = "";
let editingBreakdownSceneId = "";
let mapsLoadPromise = null;
let mapsAutocomplete = null;
let locationBase = null;
let locationSearchState = null;
let locationSearching = false;
let locationMap = null;
let locationMarkers = [];
const summaryDismissed = new Set();
const editingAnswers = new Set();

const STORAGE_ACTIVE = "second-unit.active-interview";
const draftKey = (id) => `second-unit.draft.${id}`;

function uid(prefix) {
  const value = globalThis.crypto?.randomUUID?.() || `${Date.now()}_${Math.random().toString(16).slice(2)}`;
  return `${prefix}_${value}`;
}

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function request(path, options = {}) {
  const { timeoutMs = 45_000, signal: callerSignal, ...fetchOptions } = options;
  const controller = new AbortController();
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const forwardAbort = () => controller.abort();
  if (callerSignal?.aborted) controller.abort();
  else callerSignal?.addEventListener("abort", forwardAbort, { once: true });
  let response;
  try {
    response = await fetch(path, { ...fetchOptions, signal: controller.signal });
  } catch (error) {
    if (timedOut) {
      const timeoutError = new Error("The local server took too long to respond. Please try again.");
      timeoutError.code = "request_timeout";
      timeoutError.name = "TimeoutError";
      throw timeoutError;
    }
    throw error;
  } finally {
    clearTimeout(timeout);
    callerSignal?.removeEventListener("abort", forwardAbort);
  }
  let payload = {};
  try { payload = await response.json(); } catch (_) { /* safe generic error below */ }
  if (!response.ok) {
    const error = new Error(payload.error?.message || `Request failed (${response.status})`);
    error.code = payload.error?.code || "request_failed";
    error.details = payload.error?.details || {};
    error.status = response.status;
    throw error;
  }
  return payload;
}

function jsonRequest(path, method, body) {
  return request(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function showNotice(message, { error = false, retry = null, sticky = false } = {}) {
  if (noticeTimer) clearTimeout(noticeTimer);
  const node = $("notice");
  node.className = `notice${error ? " error" : ""}`;
  node.replaceChildren();
  const text = document.createElement("span");
  text.textContent = message;
  node.append(text);
  if (retry) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "text-action";
    button.textContent = "Retry";
    button.addEventListener("click", retry, { once: true });
    node.append(" ", button);
  }
  node.hidden = false;
  if (!sticky && !retry) noticeTimer = setTimeout(() => { node.hidden = true; }, 4500);
}

function hideNotice() {
  $("notice").hidden = true;
}

function setBusy(value, label = "Considering your answer…") {
  busy = value;
  $("workingCard").hidden = !(value || serverPending);
  $("workingText").textContent = value ? label : "Finishing your saved turn…";
  document.querySelectorAll("button, textarea, input").forEach((element) => {
    if (value && !element.disabled) {
      element.disabled = true;
      element.dataset.disabledByBusy = "true";
    } else if (!value && element.dataset.disabledByBusy === "true") {
      element.disabled = false;
      delete element.dataset.disabledByBusy;
    }
  });
  if (!value) configureSpeechButtons();
}

function setServerPending(value) {
  serverPending = value;
  document.querySelectorAll("#composerForm button, #composerForm textarea, #responseActions button, #responseActions input, #responseActions textarea").forEach((element) => {
    if (value && !element.disabled) {
      element.disabled = true;
      element.dataset.disabledByPending = "true";
    } else if (!value && element.dataset.disabledByPending === "true") {
      element.disabled = false;
      delete element.dataset.disabledByPending;
    }
  });
  $("workingCard").hidden = !(busy || value);
  if (value && !busy) $("workingText").textContent = "Finishing your saved turn…";
  configureSpeechButtons();
}

function emptyDraft() {
  return {
    text: "",
    purpose: "answer",
    responseId: "",
    suggestionIds: [],
    suggestionNote: "",
    questionAnswers: {},
    openQuestionId: "",
  };
}

function readDraft(id) {
  if (!id) return emptyDraft();
  const raw = localStorage.getItem(draftKey(id));
  if (!raw) return emptyDraft();
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return emptyDraft();
    return {
      text: typeof parsed.text === "string" ? parsed.text : "",
      purpose: ["answer", "question", "correction", "nuance", "instruction"].includes(parsed.purpose) ? parsed.purpose : "answer",
      responseId: typeof parsed.responseId === "string" ? parsed.responseId : "",
      suggestionIds: Array.isArray(parsed.suggestionIds) ? parsed.suggestionIds.filter((item) => typeof item === "string") : [],
      suggestionNote: typeof parsed.suggestionNote === "string" ? parsed.suggestionNote : "",
      questionAnswers: parsed.questionAnswers && typeof parsed.questionAnswers === "object"
        ? Object.fromEntries(Object.entries(parsed.questionAnswers).filter(
            ([key, value]) => typeof key === "string" && typeof value === "string"
          ))
        : {},
      openQuestionId: typeof parsed.openQuestionId === "string" ? parsed.openQuestionId : "",
    };
  } catch (_) {
    // Upgrade drafts saved by the earlier text-only client.
    return { ...emptyDraft(), text: raw };
  }
}

function captureDraft(sessionId = interview?.session?.id) {
  if (!sessionId) return;
  const sameSession = interview?.session?.id === sessionId;
  const suggestionIds = sameSession
    ? [...document.querySelectorAll('#responseActions input[name="suggestion"]:checked')].map((item) => item.value)
    : [];
  const value = {
    text: sameSession ? $("composerInput").value : "",
    purpose: sameSession ? composerPurpose : "answer",
    responseId: sameSession
      ? (document.querySelector("#responseActions .question-set")?.dataset.responseId
        || interview.current_response?.id || "")
      : "",
    suggestionIds,
    suggestionNote: sameSession && $("suggestionNote") ? $("suggestionNote").value : "",
    questionAnswers: sameSession
      ? Object.fromEntries(
          [...document.querySelectorAll("#responseActions [data-question-id]")]
            .map((item) => [item.dataset.questionId, item.value])
        )
      : {},
    openQuestionId: sameSession
      ? (document.querySelector("#responseActions .question-card.open")?.dataset.questionCardId || "")
      : "",
  };
  if (!value.text && value.purpose === "answer" && !value.suggestionIds.length
      && !value.suggestionNote && !Object.values(value.questionAnswers).some(Boolean)
      && !value.openQuestionId) {
    localStorage.removeItem(draftKey(sessionId));
  } else {
    localStorage.setItem(draftKey(sessionId), JSON.stringify(value));
  }
}

function clearVisibleDraft() {
  $("composerInput").value = "";
  setComposerPurpose("answer", "", { focus: false, persist: false });
}

function restoreDraft() {
  const value = readDraft(interview?.session?.id);
  const activeResponseId = document.querySelector("#responseActions .question-set")?.dataset.responseId
    || interview?.current_response?.id;
  const sameResponse = value.responseId === activeResponseId;
  $("composerInput").value = value.text;
  setComposerPurpose(sameResponse ? value.purpose : "answer", "", { focus: false, persist: false });
  if (!sameResponse) return;
  const selected = new Set(value.suggestionIds);
  document.querySelectorAll('#responseActions input[name="suggestion"]').forEach((item) => {
    item.checked = selected.has(item.value);
  });
  if ($("suggestionNote")) $("suggestionNote").value = value.suggestionNote;
  if ($("useSuggestions")) {
    $("useSuggestions").disabled = !document.querySelector('#responseActions input[name="suggestion"]:checked');
  }
  document.querySelectorAll("#responseActions [data-question-id]").forEach((item) => {
    item.value = value.questionAnswers[item.dataset.questionId] || "";
  });
  if (document.querySelector("#responseActions .question-card")) {
    const cards = [...document.querySelectorAll("#responseActions .question-card")];
    const requested = cards.find((card) => card.dataset.questionCardId === value.openQuestionId);
    if (requested) {
      cards.forEach((card) => {
        const open = card === requested;
        card.classList.toggle("open", open);
        card.querySelector(".question-toggle")?.setAttribute("aria-expanded", String(open));
        const body = card.querySelector(".question-body");
        if (body) body.hidden = !open;
      });
    }
    syncQuestionSetState();
  }
}

function syncQuestionSetState() {
  const inputs = [...document.querySelectorAll("#responseActions [data-question-id]")];
  inputs.forEach((input) => {
    const answered = Boolean(input.value.trim());
    const card = input.closest(".question-card");
    card?.classList.toggle("answered", answered);
    const status = card?.querySelector(".question-answer-status");
    if (status) status.textContent = answered ? "Answered" : "Open";
  });
  if ($("submitQuestionSet")) {
    $("submitQuestionSet").disabled = !inputs.some((item) => item.value.trim());
  }
}

function questionBranches() {
  const branches = new Map();
  for (const item of interview?.timeline || []) {
    if (item.event.kind !== "question_ideas_requested" || !item.response) continue;
    const questionId = item.event.payload?.question_id;
    if (!questionId) continue;
    if (!branches.has(questionId)) branches.set(questionId, []);
    branches.get(questionId).push(item.response);
  }
  return branches;
}

function branchHistoryHTML(questionId, branches, { interactive = false } = {}) {
  const turns = branches.get(questionId) || [];
  if (!turns.length) return "";
  return `<div class="question-branch">
    <p class="branch-label">Ideas from the story editor</p>
    ${turns.map((turn) => `
      <div class="branch-turn">
        ${turn.guidance ? `<p>${escapeHTML(turn.guidance)}</p>` : ""}
        <div class="branch-ideas">${(turn.suggestions || []).map((idea) => `
          ${interactive
            ? `<button type="button" class="branch-idea" data-branch-idea-id="${escapeHTML(idea.id)}"><b>${escapeHTML(idea.label)}</b><span>${escapeHTML(idea.detail)}</span><small>Use this direction</small></button>`
            : `<div class="branch-idea static"><b>${escapeHTML(idea.label)}</b><span>${escapeHTML(idea.detail)}</span></div>`}
        `).join("")}</div>
      </div>`).join("")}
  </div>`;
}

function modeLabel(value) {
  return { ai_led: "AI-led", collaborative: "Collaborative", author_led: "Author-led" }[value] || value;
}

function eventLabel(event, suggestionsById) {
  const payload = event.payload || {};
  if (event.kind === "suggestions_requested") return "Asked the editor for options";
  if (event.kind === "question_ideas_requested") return "Asked for ideas on an interview question";
  if (event.kind === "question_skipped") return "Skipped this question";
  if (event.kind === "reflection_confirmed") return "Confirmed the editor’s understanding";
  if (event.kind === "response_continued") return "Asked the editor to continue";
  if (event.kind === "continue_interview") return "Kept developing the story";
  if (event.kind === "interview_finished") return "Finished the interview";
  if (event.kind === "questionnaire_submitted") {
    return `Submitted ${(payload.answers || []).length} interview answer${(payload.answers || []).length === 1 ? "" : "s"}`;
  }
  if (event.kind === "questionnaire_revised") {
    return `Revised ${(payload.answers || []).length} interview answer${(payload.answers || []).length === 1 ? "" : "s"}`;
  }
  if (event.kind === "suggestions_selected") {
    const labels = (payload.suggestion_ids || []).map((id) => suggestionsById.get(id)?.label).filter(Boolean);
    return `Selected ${labels.length ? labels.join(" + ") : "an option"}${payload.note ? ` — ${payload.note}` : ""}`;
  }
  if (event.kind === "suggestions_rejected") return `None of those options fit${payload.note ? ` — ${payload.note}` : ""}`;
  if (event.kind === "decision_revised") return `Revised an earlier decision — ${payload.replacement_text}`;
  return event.kind.replaceAll("_", " ");
}

function renderTimeline() {
  const node = $("timeline");
  const timeline = interview.timeline || [];
  const suggestionsById = new Map();
  timeline.forEach((item) => (item.response?.suggestions || []).forEach((idea) => suggestionsById.set(idea.id, idea)));
  const questionsById = new Map();
  timeline.forEach((item) => (item.response?.questions || []).forEach((question) => questionsById.set(question.id, question)));
  const branches = questionBranches();
  const fragments = [];

  for (const item of timeline) {
    const event = item.event;
    if (event.kind === "question_ideas_requested" && event.status === "completed") {
      continue;
    }
    if (event.kind === "interview_started") {
      fragments.push(`
        <article class="timeline-item">
          <div class="timeline-role">Starting idea</div>
          <div class="bubble writer-bubble">${escapeHTML(event.payload.seed).replaceAll("\n", "<br>")}</div>
        </article>`);
    } else if (event.kind === "message_submitted") {
      fragments.push(`
        <article class="timeline-item">
          <div class="timeline-role">You</div>
          <div class="bubble writer-bubble">${escapeHTML(event.payload.text).replaceAll("\n", "<br>")}</div>
        </article>`);
    } else if (["questionnaire_submitted", "questionnaire_revised"].includes(event.kind)) {
      const answers = (event.payload.answers || []).map((answer) => {
        const question = questionsById.get(answer.question_id);
        return `<div class="writer-answer"><b>${escapeHTML(question?.text || "Interview question")}</b><span>${escapeHTML(answer.text).replaceAll("\n", "<br>")}</span></div>`;
      }).join("");
      fragments.push(`
        <article class="timeline-item">
          <div class="timeline-role">You</div>
          <div class="bubble writer-bubble answer-set">${answers}</div>
        </article>`);
    } else {
      fragments.push(`
        <article class="timeline-item">
          <div class="timeline-role">Action</div>
          <div><span class="event-chip">${escapeHTML(eventLabel(event, suggestionsById))}</span></div>
        </article>`);
    }

    if (item.response) fragments.push(renderAssistantResponse(item.response, branches));
    if (event.status === "failed_retryable") {
      fragments.push(`
        <article class="timeline-item">
          <div class="timeline-role">Saved</div>
          <div class="saved-turn">
            <div class="event-chip">${escapeHTML(event.safe_error || "This turn is saved and can be retried.")}</div>
            <button class="text-action retry-saved-turn" type="button" data-event-id="${escapeHTML(event.id)}">Retry saved turn</button>
          </div>
        </article>`);
    }
  }

  node.innerHTML = fragments.join("");
  node.querySelectorAll(".retry-saved-turn").forEach((button) => {
    button.addEventListener("click", () => {
      const item = (interview.timeline || []).find((entry) => entry.event.id === button.dataset.eventId);
      if (!item) return;
      const event = item.event;
      if (event.kind === "interview_started") {
        createInterview({
          session_id: interview.session.id,
          event_id: event.id,
          ...event.payload,
        });
        return;
      }
      submitPreparedEvent(interview.session.id, {
        event_id: event.id,
        expected_revision: event.expected_revision,
        kind: event.kind,
        payload: event.payload,
      }, { preserveDraft: event.kind !== "message_submitted" });
    });
  });
  requestAnimationFrame(() => { node.scrollTop = node.scrollHeight; });
}

function renderAssistantResponse(response, branches = questionBranches()) {
  const suggestions = (response.suggestions || []).map((idea) => `
    <div class="transcript-suggestion${idea.status !== "shown" ? " resolved" : ""}">
      <b>${escapeHTML(idea.label)}${idea.status !== "shown" ? `<span class="suggestion-status">${escapeHTML(idea.status)}</span>` : ""}</b>
      <span>${escapeHTML(idea.detail)}</span>
    </div>`).join("");
  const plannedQuestions = response.id === interview.current_response?.id
    ? ""
    : (response.questions || []).map((item, index) => `
        <div class="transcript-question"><b>${index + 1}. ${escapeHTML(item.text)}</b><span>${escapeHTML(item.explanation)}</span>${branchHistoryHTML(item.id, branches)}</div>
      `).join("");
  return `
    <article class="timeline-item">
      <div class="timeline-role assistant">Story editor</div>
      <div class="bubble assistant-bubble">
        ${response.guidance ? `<p>${escapeHTML(response.guidance).replaceAll("\n", "<br>")}</p>` : ""}
        ${suggestions ? `<div class="transcript-suggestions">${suggestions}</div>` : ""}
        ${plannedQuestions ? `<div class="transcript-questions">${plannedQuestions}</div>` : ""}
        ${response.question ? `<p class="assistant-question">${escapeHTML(response.question)}</p>` : ""}
      </div>
    </article>`;
}

function questionnaireHandoff() {
  const timeline = interview?.timeline || [];
  const submitted = [...timeline].reverse().find(
    (item) => ["questionnaire_submitted", "questionnaire_revised"].includes(item.event.kind)
      && item.event.status === "completed"
  );
  if (!submitted) return null;
  const questionnaireResponseId = submitted.event.payload?.response_id
    || submitted.event.payload?.questionnaire_response_id;
  const questionnaire = timeline.find(
    (item) => item.response?.id === questionnaireResponseId
  )?.response;
  if (!questionnaire) return null;
  const answers = new Map(
    (submitted.event.payload.answers || []).map((item) => [item.question_id, item.text])
  );
  return {
    submitted,
    questionnaire,
    answered: (questionnaire.questions || [])
      .filter((question) => answers.has(question.id))
      .map((question) => ({ question, answer: answers.get(question.id) })),
    unanswered: (questionnaire.questions || []).filter((question) => !answers.has(question.id)),
  };
}

function setStageNavigation(stage) {
  const handoff = questionnaireHandoff();
  const outlineAvailable = Boolean(handoff || interview?.session?.status === "ready_for_outline");
  $("outlineStageButton").disabled = !outlineAvailable;
  $("screenplayStageButton").disabled = outlineState?.status !== "approved";
  $("breakdownStageButton").disabled = screenplayState?.status !== "approved";
  $("interviewStageButton").classList.toggle("active", stage === "interview");
  $("interviewStageButton").classList.toggle("complete", outlineAvailable && stage !== "interview");
  $("outlineStageButton").classList.toggle("active", stage === "outline");
  $("screenplayStageButton").classList.toggle("active", stage === "screenplay");
  $("breakdownStageButton").classList.toggle("active", stage === "breakdown");
  $("outlineStageButton").classList.toggle("complete", outlineState?.status === "approved" && !["interview", "outline"].includes(stage));
  $("screenplayStageButton").classList.toggle("complete", screenplayState?.status === "approved" && stage === "breakdown");
  $("interviewStageButton").setAttribute("aria-current", stage === "interview" ? "step" : "false");
  $("outlineStageButton").setAttribute("aria-current", stage === "outline" ? "step" : "false");
  $("screenplayStageButton").setAttribute("aria-current", stage === "screenplay" ? "step" : "false");
  $("breakdownStageButton").setAttribute("aria-current", stage === "breakdown" ? "step" : "false");
}

function recordStage(stage, mode = "push") {
  const previousStage = activeStage;
  activeStage = stage;
  if (mode === "none") return;
  const hash = stage === "breakdown" ? "#breakdown" : stage === "screenplay" ? "#screenplay" : stage === "outline" ? "#outline" : stage === "interview" ? "#interview" : "";
  const url = `${location.pathname}${location.search}${hash}`;
  const state = { ...history.state, secondUnitStage: stage, previousStage };
  if (mode === "replace" || location.hash === hash) history.replaceState(state, "", url);
  else history.pushState(state, "", url);
}

function renderHandoffSummary(node, handoff) {
  $("composerForm").hidden = true;
  document.querySelector(".conversation-column").classList.add("handoff-view");
  const currentGuidance = interview.current_response?.guidance || "Your interview material is ready for the outline.";
  node.innerHTML = `
    <div class="handoff-card">
      <div class="handoff-heading">
        <span class="handoff-check">✓</span>
        <div><p class="action-title">Interview captured</p><h2>Your story handoff is ready</h2></div>
      </div>
      <p class="handoff-intro">${escapeHTML(currentGuidance)}</p>
      <details class="handoff-review">
        <summary><span>Review answers</span><small>${handoff.answered.length} answered · ${handoff.unanswered.length} open</small></summary>
        <div class="handoff-decisions">
          ${handoff.answered.map(({ question, answer }) => `
            <div class="handoff-decision"><b>${escapeHTML(question.text)}</b><span>${escapeHTML(answer)}</span></div>
          `).join("")}
          ${handoff.unanswered.map((question) => `
            <div class="handoff-decision unanswered"><b>${escapeHTML(question.text)}</b><span>Open decision</span></div>
          `).join("")}
        </div>
      </details>
      <div class="handoff-actions">
        <button class="primary-button compact" id="continueToOutline" type="button">Continue to Outline <span>→</span></button>
        <button class="secondary-button" id="editInterviewAnswers" type="button">Edit answers</button>
        <button class="secondary-button" id="keepDevelopingSummary" type="button">Keep developing</button>
      </div>
    </div>`;
  node.hidden = false;
  $("continueToOutline").addEventListener("click", async () => {
    if (interview.session.status !== "ready_for_outline") {
      const completed = await sendEvent("interview_finished", {
        response_id: interview.current_response?.id || "",
      }, { preserveDraft: false });
      if (!completed) return;
    }
    showOutline();
  });
  $("editInterviewAnswers").addEventListener("click", () => {
    const sessionId = interview.session.id;
    editingAnswers.add(sessionId);
    const questionAnswers = Object.fromEntries(
      handoff.answered.map(({ question, answer }) => [question.id, answer])
    );
    localStorage.setItem(draftKey(sessionId), JSON.stringify({
      ...emptyDraft(),
      responseId: handoff.questionnaire.id,
      questionAnswers,
      openQuestionId: handoff.questionnaire.questions?.[0]?.id || "",
    }));
    renderResponseActions();
    restoreDraft();
    document.querySelector("#responseActions textarea")?.focus();
  });
  $("keepDevelopingSummary").addEventListener("click", async () => {
    summaryDismissed.add(interview.session.id);
    if (interview.session.status === "ready_for_outline") {
      await sendEvent("continue_interview", {
        response_id: interview.current_response?.id || "",
      });
      return;
    }
    renderResponseActions();
    $("composerForm").hidden = false;
    $("composerInput").focus();
  });
  requestAnimationFrame(() => node.scrollIntoView({ block: "end" }));
}

// The session status can advance while no lens is actually supported — a paused
// or failed turn still moves it. Trusting status alone showed "the interview is
// complete" above a 0 / 4 readiness panel, and hid the composer at the exact
// point the writer needed it. Treat that combination as unfinished.
function readinessIsEmpty() {
  const lenses = interview.readiness?.lenses || {};
  return !Object.values(lenses).some((lens) => lens.status === "supported");
}

function renderResponseActions() {
  const node = $("responseActions");
  let response = interview.current_response;
  const session = interview.session;
  const readyButEmpty = session.status === "ready_for_outline" && readinessIsEmpty();
  node.hidden = true;
  node.innerHTML = "";
  document.querySelector(".conversation-column").classList.remove("handoff-view");
  $("composerForm").hidden = session.status === "ready_for_outline" && !readyButEmpty;
  $("skipButton").hidden = !response?.question;

  const handoff = questionnaireHandoff();
  const editingQuestionnaire = Boolean(handoff && editingAnswers.has(session.id));
  if (handoff && !editingQuestionnaire && !summaryDismissed.has(session.id)) {
    renderHandoffSummary(node, handoff);
    return;
  }

  if (editingQuestionnaire) response = handoff.questionnaire;

  if (session.status === "ready_for_outline" && !editingQuestionnaire) {
    node.innerHTML = `
      <p class="action-title">${readyButEmpty ? "Nothing established yet" : "Ready for a first outline"}</p>
      <p class="ready-copy">${readyButEmpty
        ? "The interview was closed, but no story material has been accepted yet — none of the four lenses are supported. An outline built now would use only your original idea. Keep developing, or open the outline anyway."
        : "The interview is complete. Accepted material and open questions are saved for the outline; you can still return and keep developing."}</p>
      <div class="ready-actions">
        <button class="${readyButEmpty ? "secondary-button" : "primary-button compact"}" id="outlineButton" type="button">Build outline${readyButEmpty ? "" : " <span>→</span>"}</button>
        <button class="${readyButEmpty ? "primary-button compact" : "secondary-button"}" id="keepDevelopingButton" type="button">Keep developing${readyButEmpty ? " <span>→</span>" : ""}</button>
      </div>`;
    node.hidden = false;
    $("outlineButton").addEventListener("click", showOutline);
    $("keepDevelopingButton").addEventListener("click", () => sendEvent("continue_interview", { response_id: response?.id || "" }));
    return;
  }
  if (!response) return;

  const plannedQuestions = response.questions || [];
  if (plannedQuestions.length) {
    $("composerForm").hidden = true;
    const branches = questionBranches();
    const branchIdeas = new Map();
    for (const turns of branches.values()) {
      turns.forEach((turn) => (turn.suggestions || []).forEach((idea) => branchIdeas.set(idea.id, idea)));
    }
    node.innerHTML = `
      <div class="question-set-heading">
        <div><p class="action-title">Work through your story</p><p>Open one question at a time. Leave anything uncertain blank.</p></div>
      </div>
      <div class="question-set" data-response-id="${escapeHTML(response.id)}">
        ${plannedQuestions.map((item, index) => `
          <section class="question-card${index === 0 ? " open" : ""}" data-question-card-id="${escapeHTML(item.id)}">
            <button class="question-toggle" type="button" aria-expanded="${index === 0 ? "true" : "false"}">
              <span class="question-number">${index + 1}</span>
              <span class="question-copy"><b>${escapeHTML(item.text)}</b><small>${escapeHTML(item.explanation)}</small></span>
              <span class="question-answer-status">Open</span>
              <span class="question-chevron" aria-hidden="true">⌄</span>
            </button>
            <div class="question-body"${index === 0 ? "" : " hidden"}>
              <div class="textarea-shell">
                <textarea data-question-id="${escapeHTML(item.id)}" maxlength="4000" rows="3" placeholder="Write your answer, or ask for ideas"></textarea>
                <button class="mic-button question-mic" type="button" aria-label="Start voice typing" aria-pressed="false" disabled>
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Zm-6 9a6 6 0 0 0 12 0M12 18v3M9 21h6"/></svg>
                </button>
              </div>
              <div class="speech-row"><span class="question-speech">Voice answer is editable before submission.</span><button type="button" class="text-action question-cancel" hidden>Cancel recording</button></div>
              <div class="question-tools">
                <button class="secondary-button question-ideas-button" type="button">${(branches.get(item.id) || []).length ? "Give me more ideas" : "Give me ideas"}</button>
                ${index < plannedQuestions.length - 1 ? `<button class="text-action next-question" type="button">Next question →</button>` : ""}
              </div>
              ${branchHistoryHTML(item.id, branches, { interactive: true })}
            </div>
          </section>`).join("")}
      </div>
      <div class="action-row question-set-actions">
        <button class="primary-button compact" id="submitQuestionSet" type="button" disabled>${editingQuestionnaire ? "Save revised answers" : "Submit answers"} <span>→</span></button>
        <button class="secondary-button" id="finishQuestionSet" type="button">${editingQuestionnaire ? "Cancel editing" : "Finish with open questions"}</button>
      </div>`;
    node.hidden = false;
    const answerInputs = [...node.querySelectorAll("[data-question-id]")];
    const updateBatch = () => {
      syncQuestionSetState();
      captureDraft();
    };
    answerInputs.forEach((input) => input.addEventListener("input", updateBatch));
    const cards = [...node.querySelectorAll(".question-card")];
    const openCard = (selected) => {
      cards.forEach((card) => {
        const open = card === selected;
        card.classList.toggle("open", open);
        card.querySelector(".question-toggle").setAttribute("aria-expanded", String(open));
        card.querySelector(".question-body").hidden = !open;
      });
      captureDraft();
    };
    cards.forEach((card, index) => {
      const input = card.querySelector("[data-question-id]");
      const mic = card.querySelector(".question-mic");
      const status = card.querySelector(".question-speech");
      const cancel = card.querySelector(".question-cancel");
      card.querySelector(".question-toggle").addEventListener("click", () => {
        openCard(card.classList.contains("open") ? null : card);
      });
      mic.addEventListener("click", () => startVoice(input, mic, status, cancel));
      cancel.addEventListener("click", cancelVoice);
      card.querySelector(".question-ideas-button").addEventListener("click", () => {
        sendEvent("question_ideas_requested", {
          response_id: response.id,
          question_id: input.dataset.questionId,
          draft_answer: input.value.trim(),
        });
      });
      card.querySelector(".next-question")?.addEventListener("click", () => {
        const next = cards[index + 1];
        if (!next) return;
        openCard(next);
        next.scrollIntoView({ behavior: "smooth", block: "nearest" });
        next.querySelector("textarea")?.focus();
      });
    });
    node.querySelectorAll(".branch-idea").forEach((button) => {
      button.addEventListener("click", () => {
        const idea = branchIdeas.get(button.dataset.branchIdeaId);
        const input = button.closest(".question-card")?.querySelector("[data-question-id]");
        if (!idea || !input) return;
        input.value = input.value.trim()
          ? `${input.value.trim()}\n${idea.detail}`
          : idea.detail;
        syncQuestionSetState();
        captureDraft();
        input.focus();
      });
    });
    syncQuestionSetState();
    $("submitQuestionSet").addEventListener("click", async () => {
      const answers = answerInputs
        .filter((item) => item.value.trim())
        .map((item) => ({ question_id: item.dataset.questionId, text: item.value.trim() }));
      if (!editingQuestionnaire) {
        sendEvent("questionnaire_submitted", { response_id: response.id, answers });
        return;
      }
      editingAnswers.delete(session.id);
      const saved = await sendEvent("questionnaire_revised", {
        target_event_id: handoff.submitted.event.id,
        questionnaire_response_id: response.id,
        answers,
      });
      if (!saved) {
        editingAnswers.add(session.id);
        renderResponseActions();
        restoreDraft();
      }
    });
    $("finishQuestionSet").addEventListener("click", () => {
      if (editingQuestionnaire) {
        editingAnswers.delete(session.id);
        localStorage.removeItem(draftKey(session.id));
        renderResponseActions();
        return;
      }
      sendEvent("interview_finished", { response_id: response.id }, { preserveDraft: true });
    });
    return;
  }

  const shown = (response.suggestions || []).filter((idea) => idea.status === "shown");
  if (shown.length) {
    node.innerHTML = `
      <p class="action-title">Choose any ideas that fit</p>
      <div class="selectable-options">
        ${shown.map((idea) => `
          <label class="selectable-option">
            <input type="checkbox" name="suggestion" value="${escapeHTML(idea.id)}">
            <span class="check-box">✓</span>
            <span><b>${escapeHTML(idea.label)}</b><small>${escapeHTML(idea.detail)}</small></span>
          </label>`).join("")}
      </div>
      <textarea class="suggestion-note" id="suggestionNote" maxlength="2000" rows="2" placeholder="Adapt or combine them in your own words (optional)"></textarea>
      <div class="action-row">
        <button class="primary-button compact" id="useSuggestions" type="button" disabled>Use selected <span>→</span></button>
        <button class="secondary-button" id="rejectSuggestions" type="button">None fit</button>
      </div>`;
    node.hidden = false;
    const checks = [...node.querySelectorAll('input[name="suggestion"]')];
    checks.forEach((check) => check.addEventListener("change", () => {
      $("useSuggestions").disabled = !checks.some((item) => item.checked);
      captureDraft();
    }));
    $("suggestionNote").addEventListener("input", () => captureDraft());
    $("useSuggestions").addEventListener("click", () => {
      const ids = checks.filter((item) => item.checked).map((item) => item.value);
      sendEvent("suggestions_selected", { suggestion_ids: ids, note: $("suggestionNote").value.trim() });
    });
    $("rejectSuggestions").addEventListener("click", () => {
      sendEvent("suggestions_rejected", {
        suggestion_ids: shown.map((idea) => idea.id),
        note: $("suggestionNote").value.trim(),
      });
    });
    return;
  }

  if (response.intent === "reflect_and_confirm") {
    node.innerHTML = `
      <p class="action-title">Is that understanding right?</p>
      <div class="action-row">
        <button class="primary-button compact" id="confirmReflection" type="button">Confirm <span>✓</span></button>
        <button class="secondary-button" id="correctReflection" type="button">Correct it</button>
        <button class="secondary-button" id="nuanceReflection" type="button">Add nuance</button>
      </div>`;
    node.hidden = false;
    $("confirmReflection").addEventListener("click", () => sendEvent("reflection_confirmed", { response_id: response.id, note: "" }));
    $("correctReflection").addEventListener("click", () => setComposerPurpose("correction", "Correcting the editor’s understanding"));
    $("nuanceReflection").addEventListener("click", () => setComposerPurpose("nuance", "Adding nuance to the editor’s understanding"));
  } else if (response.intent === "coach_writer" && !response.question) {
    node.innerHTML = `
      <p class="action-title">Continue when useful</p>
      <button class="secondary-button" id="continueResponse" type="button">Continue developing</button>`;
    node.hidden = false;
    $("continueResponse").addEventListener("click", () => sendEvent("response_continued", { response_id: response.id, note: "" }));
  }
}

function renderStoryState() {
  const session = interview.session;
  $("revisionLabel").textContent = `r${session.revision}`;
  const readiness = interview.readiness;
  const lenses = readiness?.lenses || {};
  const labels = { carrier: "Carrier", pressure: "Pressure", visible_sequence: "Events", ending: "Ending" };
  const supported = Object.values(lenses).filter((lens) => lens.status === "supported").length;
  $("readinessFraction").textContent = `${supported} / 4`;
  $("lensList").innerHTML = Object.entries(labels).map(([key, label]) => {
    const status = lenses[key]?.status || "missing";
    const evidence = lenses[key]?.evidence || "Not established yet";
    return `<div class="lens ${escapeHTML(status)}" title="${escapeHTML(evidence)}">${escapeHTML(label)}</div>`;
  }).join("");
  // A reason from before the answers were submitted is worse than no reason —
  // it reads as a current judgement of material the editor never saw.
  const answered = (interview.timeline || []).some((item) =>
    ["questionnaire_submitted", "questionnaire_revised", "message_submitted"].includes(item.event?.kind));
  const reason = readiness?.reason || session.readiness_reason || "";
  const stale = answered && supported === 0 && /before any questions/i.test(reason);
  $("readinessReason").textContent = stale
    ? `${reason} (not re-assessed since your answers)`
    : reason;

  const facts = interview.facts || [];
  $("factCount").textContent = String(facts.length);
  $("factList").innerHTML = facts.length
    ? facts.map((fact) => `<li>${escapeHTML(fact.text)}</li>`).join("")
    : '<li class="empty-state">Your accepted story details will appear here.</li>';

  const gaps = interview.gaps || [];
  $("gapCount").textContent = String(gaps.length);
  $("gapList").innerHTML = gaps.length
    ? gaps.map((gap) => `<li>${escapeHTML(gap.description)}</li>`).join("")
    : '<li class="empty-state">No outline-blocking gap is currently recorded.</li>';
}

function renderOutline() {
  const handoff = questionnaireHandoff();
  $("outlineProjectTitle").textContent = interview.session.title;
  $("outlineSeed").textContent = interview.session.seed;
  const decisions = handoff?.answered.map(({ answer }) => answer) || (interview.facts || []).map((fact) => fact.text);
  $("outlineFacts").innerHTML = decisions.length
    ? decisions.map((decision) => `<li>${escapeHTML(decision)}</li>`).join("")
    : "<li>No accepted decisions yet.</li>";
  const openItems = [
    ...(interview.gaps || []).map((gap) => gap.description),
    ...(handoff?.unanswered || []).map((question) => question.text),
  ];
  const uniqueOpen = [...new Set(openItems)];
  $("outlineGaps").innerHTML = uniqueOpen.length
    ? uniqueOpen.map((item) => `<li class="open">${escapeHTML(item)}</li>`).join("")
    : '<li>No open decisions need to be carried forward.</li>';
  $("outlineSourceMeta").textContent = `${decisions.length} decision${decisions.length === 1 ? "" : "s"} · ${uniqueOpen.length} open`;
  renderOutlineStructures();
}

function structureKindLabel(kind) {
  return {
    visual_progression: "Visual progression",
    sequences: "Sequences",
    acts: "Acts",
    narration_led: "Narration-led",
    hybrid: "Hybrid",
    custom: "Custom",
  }[kind] || kind;
}

function renderOutlineStructures() {
  const area = $("outlineStructureArea");
  const revision = outlineState?.revision;
  const activeId = outlineState?.active_structure_id;
  const beats = outlineState?.beats || [];
  $("outlineAgentButton").disabled = !activeId || Boolean(beats.length) || outlineLoading;
  $("outlineRevisionLabel").textContent = outlineLoading
    ? "Saving…"
    : revision === undefined
      ? "Not started"
      : `Outline r${revision}`;

  if (outlineLoading && !outlineState) {
    area.innerHTML = '<div class="structure-empty"><span class="thinking-dots"><i></i><i></i><i></i></span><p>Loading the outline workspace…</p></div>';
    return;
  }

  const structures = outlineState?.structures || [];
  if (!structures.length) {
    area.innerHTML = `
      <div class="structure-empty">
        <span class="outline-symbol">Ⅱ</span>
        <h3>Choose a structure type before writing beats.</h3>
        <p>Pick the structure type that feels closest. The Outline agent will use it to organize your story—you do not need to explain why it fits.</p>
        <button class="secondary-button" type="button" data-add-structure>Choose a structure</button>
      </div>`;
  } else {
    area.innerHTML = `
      <div class="structure-list">
        ${structures.map((structure) => {
          const accepted = structure.id === activeId;
          return `
            <article class="structure-card${accepted ? " accepted" : ""}">
              <div class="structure-card-top">
                <span class="structure-kind">${escapeHTML(structureKindLabel(structure.kind))}</span>
                <span class="structure-state">${accepted ? "Selected" : "Option"}</span>
              </div>
              <h3>${escapeHTML(structure.label)}</h3>
              <p>${escapeHTML(structure.rationale)}</p>
              <div class="structure-card-footer">
                <small>${structure.origin === "agent" ? "Suggested by editor" : "Added by you"}</small>
                ${accepted
                  ? '<span class="selected-mark">✓ Your structure</span>'
                  : `<button class="secondary-button compact" type="button" data-select-structure="${escapeHTML(structure.id)}"${outlineLoading ? " disabled" : ""}>Use this structure</button>`}
              </div>
            </article>`;
        }).join("")}
      </div>
      ${structures.length < 5
        ? '<button class="text-action add-structure-action" type="button" data-add-structure>+ Add another option</button>'
        : '<p class="structure-limit">Five options saved. Select the strongest fit.</p>'}`;
  }

  area.querySelectorAll("[data-add-structure]").forEach((button) => {
    button.addEventListener("click", showStructureProposalForm);
  });
  area.querySelectorAll("[data-select-structure]").forEach((button) => {
    button.addEventListener("click", () => selectOutlineStructure(button.dataset.selectStructure));
  });
  $("outlineNextStep").hidden = !activeId || Boolean(beats.length);
  $("generateBeatsButton").disabled = outlineLoading;
  renderOutlineBeats();
  setStageNavigation(activeStage);
}

function renderOutlineBeats() {
  const section = $("outlineBeatSection");
  const beats = outlineState?.beats || [];
  section.hidden = !beats.length;
  if (!beats.length) return;
  const locked = outlineState.status === "approved";
  const accepted = beats.filter((beat) => beat.approval_status === "accepted").length;
  $("beatProgress").textContent = `${accepted} of ${beats.length} accepted`;
  $("addBeatButton").disabled = outlineLoading || outlineState.status === "approved";
  $("outlineBeatList").innerHTML = beats.map((beat, index) => `
    <article class="beat-card ${escapeHTML(beat.approval_status)}">
      <div class="beat-position">${index + 1}</div>
      <div class="beat-content">
        <div class="beat-card-heading">
          <span>${beat.origin === "agent" ? "Agent proposal" : "Filmmaker beat"}</span>
          <b>${escapeHTML(beat.approval_status)}</b>
        </div>
        <h3>${escapeHTML(beat.title)}</h3>
        <p>${escapeHTML(beat.summary)}</p>
        ${beat.purpose ? `<small><b>Purpose</b> ${escapeHTML(beat.purpose)}</small>` : ""}
        <div class="beat-actions">
          <button class="text-action" type="button" data-edit-beat="${escapeHTML(beat.id)}"${locked ? " disabled" : ""}>Edit</button>
          <button class="text-action" type="button" data-move-beat="${escapeHTML(beat.id)}" data-position="${index}"${index === 0 || outlineLoading || locked ? " disabled" : ""}>↑ Earlier</button>
          <button class="text-action" type="button" data-move-beat="${escapeHTML(beat.id)}" data-position="${index + 2}"${index === beats.length - 1 || outlineLoading || locked ? " disabled" : ""}>↓ Later</button>
          ${beat.approval_status === "proposed"
            ? `<button class="beat-decision accept" type="button" data-decide-beat="${escapeHTML(beat.id)}" data-decision="accepted"${locked ? " disabled" : ""}>Accept</button><button class="beat-decision reject" type="button" data-decide-beat="${escapeHTML(beat.id)}" data-decision="rejected"${locked ? " disabled" : ""}>Reject</button>`
            : `<button class="text-action" type="button" data-decide-beat="${escapeHTML(beat.id)}" data-decision="reopened"${locked ? " disabled" : ""}>Reopen</button>`}
          <button class="text-action danger" type="button" data-archive-beat="${escapeHTML(beat.id)}"${locked ? " disabled" : ""}>Remove</button>
        </div>
      </div>
    </article>
  `).join("");

  $("outlineBeatList").querySelectorAll("[data-edit-beat]").forEach((button) => {
    button.addEventListener("click", () => openBeatForm(
      beats.find((beat) => beat.id === button.dataset.editBeat)
    ));
  });
  $("outlineBeatList").querySelectorAll("[data-move-beat]").forEach((button) => {
    button.addEventListener("click", () => runOutlineTool("move_beat", {
      expected_revision: outlineState.revision,
      beat_id: button.dataset.moveBeat,
      position: Number(button.dataset.position),
    }));
  });
  $("outlineBeatList").querySelectorAll("[data-decide-beat]").forEach((button) => {
    button.addEventListener("click", async () => {
      const result = await runOutlineTool("record_beat_decision", {
        expected_revision: outlineState.revision,
        beat_id: button.dataset.decideBeat,
        decision: button.dataset.decision,
      });
      if (result) showNotice(`Beat ${button.dataset.decision}.`);
    });
  });
  $("outlineBeatList").querySelectorAll("[data-archive-beat]").forEach((button) => {
    button.addEventListener("click", async () => {
      const result = await runOutlineTool("archive_beat", {
        expected_revision: outlineState.revision,
        beat_id: button.dataset.archiveBeat,
      });
      if (result) showNotice("Beat removed from the active outline.");
    });
  });
  renderOutlineApproval(beats);
}

function renderOutlineApproval(beats) {
  const panel = $("outlineApprovalPanel");
  if (outlineState.status === "approved") {
    panel.innerHTML = `
      <div><p class="eyebrow">Outline approved</p><h3>Ready for the screenplay stage.</h3><p>Your accepted structure and beats are now the writing plan. Screenplay generation is the next build.</p></div>
      <div class="outline-approved-actions"><button class="secondary-button" id="reopenOutlineButton" type="button">Reopen outline</button><button class="primary-button compact" id="openScreenplayButton" type="button">Build screenplay <span>→</span></button></div>`;
    $("reopenOutlineButton").addEventListener("click", () => decideOutline("reopened"));
    $("openScreenplayButton").addEventListener("click", showScreenplay);
    return;
  }
  const allAccepted = beats.length && beats.every((beat) => beat.approval_status === "accepted");
  panel.innerHTML = allAccepted
    ? `<div><p class="eyebrow">Review complete</p><h3>Ready to approve this outline?</h3><p>Approval freezes this version as the plan for screenplay writing.</p></div><button class="primary-button compact" id="approveOutlineButton" type="button">Approve outline <span>→</span></button>`
    : `<div><p class="eyebrow">Filmmaker review</p><h3>Decide on every beat</h3><p>Accept useful beats, edit them, or reject and remove what does not belong.</p></div>`;
  $("approveOutlineButton")?.addEventListener("click", () => decideOutline("accepted"));
}

function openBeatForm(beat = null) {
  if (outlineLoading || outlineState?.status === "approved") return;
  editingBeatId = beat?.id || "";
  $("beatFormEyebrow").textContent = beat ? "Edit beat" : "Your beat";
  $("beatFormTitle").textContent = beat ? "Change this story beat" : "Add a story beat";
  $("beatTitle").value = beat?.title || "";
  $("beatSummary").value = beat?.summary || "";
  $("beatPurpose").value = beat?.purpose || "";
  $("beatForm").hidden = false;
  $("beatTitle").focus();
}

function closeBeatForm() {
  editingBeatId = "";
  $("beatForm").reset();
  $("beatForm").hidden = true;
}

async function decideOutline(decision) {
  const result = await runOutlineTool("record_outline_decision", {
    expected_revision: outlineState.revision,
    decision,
  });
  if (result) showNotice(decision === "accepted" ? "Outline approved." : "Outline reopened for editing.");
}

function showStructureProposalForm() {
  if (outlineLoading) return;
  renderStructureChoices();
  $("customStructureFields").hidden = true;
  $("structureProposalForm").hidden = false;
  $("structureChoiceGrid").querySelector("button")?.focus();
}

function hideStructureProposalForm({ reset = false } = {}) {
  $("structureProposalForm").hidden = true;
  $("customStructureFields").hidden = true;
  if (reset) $("structureProposalForm").reset();
}

function renderStructureChoices() {
  $("structureChoiceGrid").innerHTML = outlineStructureOptions.map((option) => `
    <button class="structure-choice" type="button" data-choose-kind="${escapeHTML(option.kind)}">
      <b>${escapeHTML(option.label)}</b>
      <span>${escapeHTML(option.description)}</span>
      <i>${option.kind === "custom" ? "Describe it" : "Choose"} →</i>
    </button>
  `).join("");
  $("structureChoiceGrid").querySelectorAll("[data-choose-kind]").forEach((button) => {
    button.addEventListener("click", async () => {
      const kind = button.dataset.chooseKind;
      if (kind === "custom") {
        $("customStructureFields").hidden = false;
        $("structureLabel").focus();
        return;
      }
      const result = await runOutlineTool("propose_story_structure", {
        expected_revision: outlineState?.revision || 0,
        kind,
      });
      if (result) {
        hideStructureProposalForm({ reset: true });
        showNotice("Structure option saved locally.");
      }
    });
  });
}

async function loadOutline() {
  if (!interview || outlineLoading) return;
  const sessionId = interview.session.id;
  outlineLoading = true;
  renderOutlineStructures();
  try {
    const handoff = await request(`/api/interviews/${encodeURIComponent(sessionId)}/outline`);
    if (interview?.session?.id !== sessionId) return;
    outlineStructureOptions = handoff.structure_options || [];
    outlineState = handoff.outline;
  } catch (error) {
    showNotice(error.message, { error: true, retry: loadOutline, sticky: true });
  } finally {
    if (interview?.session?.id === sessionId) {
      outlineLoading = false;
      renderOutlineStructures();
    }
  }
}

async function runOutlineTool(name, payload) {
  if (!interview || outlineLoading) return null;
  const sessionId = interview.session.id;
  outlineLoading = true;
  renderOutlineStructures();
  hideNotice();
  try {
    const result = await jsonRequest(
      `/api/interviews/${encodeURIComponent(sessionId)}/outline/tools/${encodeURIComponent(name)}`,
      "POST",
      payload,
    );
    if (interview?.session?.id !== sessionId) return null;
    outlineState = result.outline;
    return result.outline;
  } catch (error) {
    if (error.code === "stale_outline_revision") {
      outlineLoading = false;
      await loadOutline();
      showNotice("The outline changed in another tab. The latest version is loaded; please try again.", { error: true });
    } else {
      showNotice(error.message, { error: true, sticky: true });
    }
    return null;
  } finally {
    if (interview?.session?.id === sessionId) {
      outlineLoading = false;
      renderOutlineStructures();
    }
  }
}

async function generateOutlineBeats() {
  if (!interview || !outlineState?.active_structure_id || outlineLoading) return;
  const sessionId = interview.session.id;
  outlineLoading = true;
  renderOutlineStructures();
  hideNotice();
  try {
    const result = await request(
      `/api/interviews/${encodeURIComponent(sessionId)}/outline/generate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_revision: outlineState.revision }),
        timeoutMs: 75_000,
      },
    );
    if (interview?.session?.id !== sessionId) return;
    outlineState = result.outline;
    showNotice("The Outline agent created editable beat proposals.");
  } catch (error) {
    if (error.code === "stale_outline_revision") {
      outlineLoading = false;
      await loadOutline();
      showNotice("The outline changed in another tab. The latest version is loaded.", { error: true });
    } else {
      showNotice(error.message, { error: true, sticky: true, retry: generateOutlineBeats });
    }
  } finally {
    if (interview?.session?.id === sessionId) {
      outlineLoading = false;
      renderOutlineStructures();
    }
  }
}

async function selectOutlineStructure(structureId) {
  if (!structureId) return;
  const result = await runOutlineTool("select_story_structure", {
    expected_revision: outlineState?.revision || 0,
    structure_id: structureId,
  });
  if (result) showNotice("Structure selected. Your choice is saved locally.");
}

async function showScreenplay({ historyMode = "push" } = {}) {
  if (!interview || outlineState?.status !== "approved") return;
  $("startScreen").hidden = true;
  $("workspace").hidden = true;
  $("outlineWorkspace").hidden = true;
  $("screenplayWorkspace").hidden = false;
  $("breakdownWorkspace").hidden = true;
  $("screenplayProjectTitle").textContent = interview.session.title;
  renderScreenplay();
  setStageNavigation("screenplay");
  recordStage("screenplay", historyMode);
  await loadScreenplay();
  $("backToOutline").focus();
}

async function loadScreenplay() {
  if (!interview || screenplayLoading) return;
  const sessionId = interview.session.id;
  screenplayLoading = true;
  renderScreenplay();
  try {
    const result = await request(`/api/interviews/${encodeURIComponent(sessionId)}/screenplay`);
    if (interview?.session?.id !== sessionId) return;
    screenplayState = result.screenplay;
    screenplayModes = result.modes || [];
  } catch (error) {
    showNotice(error.message, { error: true, retry: loadScreenplay });
  } finally {
    screenplayLoading = false;
    renderScreenplay();
  }
}

function renderScreenplay() {
  const locked = screenplayState?.status === "approved";
  $("screenplayRevisionLabel").textContent = screenplayLoading ? "Working…" : screenplayState ? `Screenplay r${screenplayState.revision}` : "Not started";
  $("screenplayModeGrid").innerHTML = screenplayModes.map((item) => `
    <button class="structure-choice${screenplayState?.mode === item.mode ? " selected" : ""}" type="button" data-screenplay-mode="${escapeHTML(item.mode)}"${screenplayLoading || screenplayState?.scenes?.length || locked ? " disabled" : ""}>
      <b>${escapeHTML(item.label)}</b><span>${escapeHTML(item.description)}</span><i>${screenplayState?.mode === item.mode ? "Selected ✓" : "Choose →"}</i>
    </button>`).join("");
  $("screenplayModeGrid").querySelectorAll("[data-screenplay-mode]").forEach((button) => {
    button.addEventListener("click", () => chooseScreenplayMode(button.dataset.screenplayMode));
  });
  const scenes = screenplayState?.scenes || [];
  $("screenplayGeneratePanel").hidden = !screenplayState?.mode || Boolean(scenes.length);
  const generateButton = $("generateScreenplayButton");
  generateButton.disabled = screenplayLoading;
  generateButton.setAttribute("aria-busy", screenplayGenerating ? "true" : "false");
  generateButton.innerHTML = screenplayGenerating
    ? `<span class="button-loading"><i class="loading-spinner" aria-hidden="true"></i>Generating screenplay…</span>`
    : `Generate screenplay <span>→</span>`;
  $("screenplayGenerateStatus").textContent = screenplayGenerating
    ? "Writing scenes from your approved outline. This can take up to a minute."
    : "The Screenplay agent will translate every accepted beat into filmable action, narration, and dialogue.";
  $("screenplayScenes").hidden = !scenes.length;
  $("screenplaySceneCount").textContent = `${scenes.length} scene${scenes.length === 1 ? "" : "s"}`;
  $("screenplaySceneList").innerHTML = scenes.map((scene, index) => `
    <article class="screenplay-scene-card">
      <header><span>Scene ${index + 1}${locked ? " · Approved" : ""}</span><button class="text-action" type="button" data-edit-scene="${escapeHTML(scene.id)}"${locked || screenplayLoading ? " disabled" : ""}>Edit</button></header>
      <h3>${escapeHTML(scene.heading)}</h3>
      <p class="scene-action">${escapeHTML(scene.action)}</p>
      ${scene.narration ? `<div class="scene-speech"><b>NARRATION</b><p>${escapeHTML(scene.narration)}</p></div>` : ""}
      ${(scene.dialogue || []).map((line) => `<div class="scene-speech"><b>${escapeHTML(line.character)}</b><p>${escapeHTML(line.line)}</p></div>`).join("")}
    </article>`).join("");
  $("screenplaySceneList").querySelectorAll("[data-edit-scene]").forEach((button) => {
    button.addEventListener("click", () => openSceneForm(scenes.find((scene) => scene.id === button.dataset.editScene)));
  });
  renderScreenplayApproval(scenes);
  setStageNavigation(activeStage);
}

function renderScreenplayApproval(scenes) {
  const panel = $("screenplayApprovalPanel");
  if (!scenes.length) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  panel.hidden = false;
  if (screenplayState.status === "approved") {
    panel.innerHTML = `
      <div><p class="eyebrow">Screenplay approved</p><h3>Ready for scene breakdown.</h3><p>The approved scenes are now locked as the source for production planning.</p></div>
      <div class="outline-approved-actions"><button class="secondary-button" id="reopenScreenplayButton" type="button">Reopen screenplay</button><button class="primary-button compact" id="openBreakdownButton" type="button">Build breakdown <span>→</span></button></div>`;
    $("reopenScreenplayButton").addEventListener("click", () => decideScreenplay("reopened"));
    $("openBreakdownButton").addEventListener("click", showBreakdown);
    return;
  }
  panel.innerHTML = `
    <div><p class="eyebrow">Review complete</p><h3>Ready to approve this screenplay?</h3><p>You can edit any scene first. Approval locks the draft for production breakdown.</p></div>
    <button class="primary-button compact" id="approveScreenplayButton" type="button">Approve screenplay <span>→</span></button>`;
  $("approveScreenplayButton").addEventListener("click", () => decideScreenplay("accepted"));
}

async function decideScreenplay(decision) {
  if (!screenplayState || screenplayLoading) return;
  screenplayLoading = true;
  renderScreenplay();
  try {
    const result = await jsonRequest(
      `/api/interviews/${encodeURIComponent(interview.session.id)}/screenplay/decision`,
      "POST", { expected_revision: screenplayState.revision, decision },
    );
    screenplayState = result.screenplay;
    if (decision === "reopened") breakdownState = null;
    showNotice(decision === "accepted" ? "Screenplay approved." : "Screenplay reopened for editing.");
  } catch (error) {
    if (error.code === "stale_screenplay_revision") await loadScreenplay();
    showNotice(error.message, { error: true });
  } finally {
    screenplayLoading = false;
    renderScreenplay();
  }
}

async function chooseScreenplayMode(mode) {
  if (screenplayLoading) return;
  screenplayLoading = true; renderScreenplay();
  try {
    const result = await jsonRequest(`/api/interviews/${encodeURIComponent(interview.session.id)}/screenplay/mode`, "POST", {
      expected_revision: screenplayState?.revision || 0, mode,
    });
    screenplayState = result.screenplay;
  } catch (error) { showNotice(error.message, { error: true }); }
  finally { screenplayLoading = false; renderScreenplay(); }
}

async function generateScreenplay() {
  if (!screenplayState || screenplayLoading) return;
  screenplayLoading = true;
  screenplayGenerating = true;
  renderScreenplay();
  try {
    const result = await request(`/api/interviews/${encodeURIComponent(interview.session.id)}/screenplay/generate`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: screenplayState.revision }), timeoutMs: 90_000,
    });
    screenplayState = result.screenplay;
    showNotice("The Screenplay agent created an editable scene draft.");
  } catch (error) { showNotice(error.message, { error: true, retry: generateScreenplay, sticky: true }); }
  finally {
    screenplayLoading = false;
    screenplayGenerating = false;
    renderScreenplay();
  }
}

function openSceneForm(scene) {
  if (!scene) return;
  editingSceneId = scene.id;
  $("sceneFormTitle").textContent = scene.heading;
  $("sceneHeading").value = scene.heading;
  $("sceneAction").value = scene.action;
  $("sceneNarration").value = scene.narration || "";
  $("sceneDialogue").value = (scene.dialogue || []).map((line) => `${line.character}: ${line.line}`).join("\n");
  $("sceneForm").hidden = false;
  $("sceneHeading").focus();
}

function closeSceneForm() { editingSceneId = ""; $("sceneForm").reset(); $("sceneForm").hidden = true; }

function showBreakdown({ historyMode = "push" } = {}) {
  if (!interview || screenplayState?.status !== "approved") return;
  $("startScreen").hidden = true;
  $("workspace").hidden = true;
  $("outlineWorkspace").hidden = true;
  $("screenplayWorkspace").hidden = true;
  $("breakdownWorkspace").hidden = false;
  $("breakdownProjectTitle").textContent = interview.session.title;
  renderBreakdown();
  setStageNavigation("breakdown");
  recordStage("breakdown", historyMode);
  loadBreakdown();
  $("backToScreenplay").focus();
}

async function loadBreakdown() {
  if (!interview || breakdownLoading) return;
  const sessionId = interview.session.id;
  breakdownLoading = true;
  renderBreakdown();
  try {
    const result = await request(`/api/interviews/${encodeURIComponent(sessionId)}/breakdown`);
    if (interview?.session?.id !== sessionId) return;
    breakdownState = result.breakdown;
    breakdownCategories = result.categories || [];
  } catch (error) {
    showNotice(error.message, { error: true, retry: loadBreakdown });
  } finally {
    breakdownLoading = false;
    renderBreakdown();
  }
}

function googleMapsPlaceLink(placeId) {
  return `https://www.google.com/maps/search/?api=1&query=Google&query_place_id=${encodeURIComponent(placeId)}`;
}

function renderRequirementLocationLinks(item) {
  if (item.category !== "location") return "";
  const currentGroup = locationSearchState?.groups?.find(
    (group) => group.breakdown_item_id === item.id,
  );
  const links = currentGroup
    ? (currentGroup.candidates || []).map((candidate) => ({
        place_id: candidate.place_id,
        label: candidate.name,
        status: candidate.shortlisted ? "shortlisted" : "suggested",
      }))
    : (item.location_links || []).map((link, index) => ({
        ...link,
        label: `${link.status === "shortlisted" ? "Shortlisted" : "Suggested"} location ${index + 1}`,
      }));
  if (!links.length) return "";
  return `<div class="requirement-location-links"><span>Location options</span>${links.map((link) => `
    <a href="${escapeHTML(link.maps_url || googleMapsPlaceLink(link.place_id))}" target="_blank" rel="noopener noreferrer"${link.status === "shortlisted" ? ' class="shortlisted"' : ""}>${escapeHTML(link.label)} ↗</a>
  `).join("")}</div>`;
}

function renderBreakdown() {
  const locked = breakdownState?.status === "approved";
  const scenes = breakdownState?.scenes || [];
  const items = scenes.flatMap((scene) => scene.items || []);
  $("breakdownRevisionLabel").textContent = breakdownLoading
    ? "Working…" : breakdownState ? `Breakdown r${breakdownState.revision}` : "Not started";
  $("breakdownGeneratePanel").hidden = Boolean(items.length);
  const generateButton = $("generateBreakdownButton");
  generateButton.disabled = breakdownLoading;
  generateButton.setAttribute("aria-busy", breakdownGenerating ? "true" : "false");
  generateButton.innerHTML = breakdownGenerating
    ? `<span class="button-loading"><i class="loading-spinner" aria-hidden="true"></i>Building breakdown…</span>`
    : `Generate breakdown <span>→</span>`;
  $("breakdownGenerateStatus").textContent = breakdownGenerating
    ? "Reading every scene and organizing its production needs. This can take up to a minute."
    : "The Breakdown agent will organize requirements scene by scene for your review.";
  $("breakdownItems").hidden = !items.length;
  $("breakdownItemCount").textContent = `${items.length} requirement${items.length === 1 ? "" : "s"}`;
  const regenerateButton = $("regenerateBreakdownButton");
  regenerateButton.hidden = locked;
  regenerateButton.disabled = breakdownLoading;
  regenerateButton.innerHTML = breakdownGenerating && items.length
    ? `<span class="button-loading"><i class="loading-spinner dark" aria-hidden="true"></i>Regenerating…</span>`
    : `↻ Regenerate with AI`;
  const labels = new Map(breakdownCategories.map((item) => [item.category, item.label]));
  $("breakdownSceneList").innerHTML = scenes.map((scene) => `
    <article class="breakdown-scene">
      <header><div><span>Scene ${scene.position}</span><h3>${escapeHTML(scene.heading)}</h3></div><button class="secondary-button" type="button" data-add-breakdown="${escapeHTML(scene.scene_id)}"${locked || breakdownLoading ? " disabled" : ""}>+ Add requirement</button></header>
      <div class="breakdown-requirements">
        ${(scene.items || []).length ? scene.items.map((item) => `
          <div class="breakdown-requirement">
            <span class="breakdown-category">${escapeHTML(labels.get(item.category) || item.category)}</span>
            <div><h4>${escapeHTML(item.name)}</h4>${item.details ? `<p>${escapeHTML(item.details)}</p>` : ""}${renderRequirementLocationLinks(item)}</div>
            <div class="breakdown-item-actions"><button class="text-action" type="button" data-edit-breakdown="${escapeHTML(item.id)}"${locked || breakdownLoading ? " disabled" : ""}>Edit</button><button class="text-action danger" type="button" data-remove-breakdown="${escapeHTML(item.id)}"${locked || breakdownLoading ? " disabled" : ""}>Remove</button></div>
          </div>`).join("") : `<p class="breakdown-empty">No requirements recorded for this scene.</p>`}
      </div>
    </article>`).join("");
  $("breakdownSceneList").querySelectorAll("[data-add-breakdown]").forEach((button) => {
    button.addEventListener("click", () => openBreakdownItemForm(null, button.dataset.addBreakdown));
  });
  $("breakdownSceneList").querySelectorAll("[data-edit-breakdown]").forEach((button) => {
    const item = items.find((value) => value.id === button.dataset.editBreakdown);
    button.addEventListener("click", () => openBreakdownItemForm(item, item?.scene_id));
  });
  $("breakdownSceneList").querySelectorAll("[data-remove-breakdown]").forEach((button) => {
    button.addEventListener("click", () => removeBreakdownItem(button.dataset.removeBreakdown));
  });
  renderLocationScout(items);
  renderBreakdownApproval(items);
  setStageNavigation(activeStage);
}

function renderLocationScout(items) {
  const locationItems = items.filter((item) => item.category === "location");
  const panel = $("locationScoutPanel");
  panel.hidden = !locationItems.length;
  if (!locationItems.length) {
    $("locationResults").hidden = true;
    return;
  }
  const findButton = $("findLocationsButton");
  findButton.disabled = !locationBase || locationSearching;
  findButton.setAttribute("aria-busy", locationSearching ? "true" : "false");
  findButton.innerHTML = locationSearching
    ? `<span class="button-loading"><i class="loading-spinner" aria-hidden="true"></i>Searching…</span>`
    : `Find locations <span>→</span>`;
  if (locationBase) {
    $("locationBaseLabel").textContent = `Starting from ${locationBase.label}.`;
  }
  if (locationSearchState) renderLocationResults(locationSearchState);
  if (!mapsAutocomplete) {
    ensureGoogleMaps().catch((error) => {
      $("locationAutocomplete").innerHTML = `<small>${escapeHTML(error.message)}</small>`;
      showNotice(error.message, { error: true });
    });
  }
}

async function ensureGoogleMaps() {
  if (mapsAutocomplete) return;
  if (!mapsLoadPromise) {
    mapsLoadPromise = (async () => {
      const config = await request("/api/maps/config", { timeoutMs: 10_000 });
      if (!config.available || !config.browser_key) {
        throw new Error("Google Maps is not configured for location search.");
      }
      const existingMaps = globalThis.google?.maps;
      if (!existingMaps?.importLibrary && !existingMaps?.places) {
        await new Promise((resolve, reject) => {
          const callbackName = `secondUnitMapsReady_${Date.now()}`;
          const script = document.createElement("script");
          globalThis[callbackName] = () => {
            delete globalThis[callbackName];
            resolve();
          };
          script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(config.browser_key)}&libraries=places,marker&v=weekly&callback=${encodeURIComponent(callbackName)}`;
          script.async = true;
          script.onerror = () => {
            delete globalThis[callbackName];
            reject(new Error("Google Maps could not load. Check the browser-key restrictions."));
          };
          document.head.append(script);
        });
      }
      return globalThis.google.maps;
    })();
  }
  let maps;
  try {
    maps = await mapsLoadPromise;
  } catch (error) {
    mapsLoadPromise = null;
    throw error;
  }
  if (typeof maps.importLibrary === "function") {
    const places = await maps.importLibrary("places");
    if (typeof places.PlaceAutocompleteElement === "function") {
      setupModernLocationAutocomplete(places.PlaceAutocompleteElement);
      return;
    }
  }
  if (typeof maps.places?.Autocomplete === "function") {
    setupLegacyLocationAutocomplete(maps.places.Autocomplete);
    return;
  }
  mapsLoadPromise = null;
  throw new Error("Google Maps loaded without Place Autocomplete. Check that Maps JavaScript API and Places API are enabled.");
}

function acceptLocationBase(placeId, latitude, longitude, label) {
  if (!placeId || !Number.isFinite(latitude) || !Number.isFinite(longitude)) {
    throw new Error("Choose a complete place from Google’s suggestions.");
  }
  locationBase = { placeId, latitude, longitude, label: label || "your production base" };
  locationSearchState = null;
  $("locationResults").hidden = true;
  renderBreakdown();
}

function setupModernLocationAutocomplete(PlaceAutocompleteElement) {
  mapsAutocomplete = new PlaceAutocompleteElement();
  mapsAutocomplete.setAttribute("aria-label", "Production base");
  $("locationAutocomplete").replaceChildren(mapsAutocomplete);
  const selectPlace = async (event) => {
    try {
      const place = event.placePrediction?.toPlace?.() || event.place;
      if (!place) throw new Error("Choose a place from Google’s suggestions.");
      await place.fetchFields({ fields: ["id", "displayName", "formattedAddress", "location"] });
      const latitude = typeof place.location?.lat === "function" ? place.location.lat() : place.location?.lat;
      const longitude = typeof place.location?.lng === "function" ? place.location.lng() : place.location?.lng;
      acceptLocationBase(
        place.id, latitude, longitude,
        place.formattedAddress || place.displayName || "your production base",
      );
    } catch (error) {
      showNotice(error.message || "That production base could not be selected.", { error: true });
    }
  };
  mapsAutocomplete.addEventListener("gmp-select", selectPlace);
  mapsAutocomplete.addEventListener("gmp-placeselect", selectPlace);
}

function setupLegacyLocationAutocomplete(Autocomplete) {
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = "Enter an address or place";
  input.setAttribute("aria-label", "Production base");
  $("locationAutocomplete").replaceChildren(input);
  const autocomplete = new Autocomplete(input, {
    fields: ["place_id", "name", "formatted_address", "geometry.location"],
  });
  mapsAutocomplete = autocomplete;
  autocomplete.addListener("place_changed", () => {
    try {
      const place = autocomplete.getPlace();
      acceptLocationBase(
        place.place_id,
        place.geometry?.location?.lat(),
        place.geometry?.location?.lng(),
        place.formatted_address || place.name || "your production base",
      );
    } catch (error) {
      showNotice(error.message || "That production base could not be selected.", { error: true });
    }
  });
}

async function findLocationSuggestions() {
  if (!locationBase || locationSearching) return;
  locationSearching = true;
  renderBreakdown();
  try {
    const result = await jsonRequest(
      `/api/interviews/${encodeURIComponent(interview.session.id)}/locations/search`,
      "POST", {
        base_place_id: locationBase.placeId,
        base_latitude: locationBase.latitude,
        base_longitude: locationBase.longitude,
        max_travel_minutes: Number($("locationTravelTime").value),
      },
    );
    locationSearchState = result.locations;
    showNotice("Location suggestions are ready.");
  } catch (error) {
    showNotice(error.message, { error: true, retry: findLocationSuggestions, sticky: true });
  } finally {
    locationSearching = false;
    renderBreakdown();
  }
}

function renderLocationResults(search) {
  const resultNode = $("locationResults");
  resultNode.hidden = false;
  $("locationResultGroups").innerHTML = (search.groups || []).map((group) => `
    <section class="location-result-group">
      <header><span>${escapeHTML(group.scene_heading)}</span><h3>${escapeHTML(group.requirement)}</h3></header>
      ${(group.candidates || []).length ? group.candidates.map((candidate) => `
        <label class="location-candidate">
          <input type="checkbox" data-location-place="${escapeHTML(candidate.place_id)}" data-location-item="${escapeHTML(group.breakdown_item_id)}"${candidate.shortlisted ? " checked" : ""}>
          <span><b>${escapeHTML(candidate.name)}</b><small>${escapeHTML(candidate.address)}</small><em>${formatTravel(candidate.duration_seconds, candidate.distance_meters)}${candidate.rating ? ` · ★ ${escapeHTML(candidate.rating)}` : ""}</em></span>
        </label>`).join("") : `<p class="location-no-results">No matching public place was found inside your travel limit.</p>`}
    </section>`).join("");
  $("locationResultGroups").querySelectorAll("[data-location-place]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => saveLocationShortlist(checkbox));
  });
  renderLocationMap(search);
}

function formatTravel(seconds, meters) {
  const minutes = Math.max(1, Math.round(Number(seconds || 0) / 60));
  const miles = (Number(meters || 0) / 1609.344).toFixed(1);
  return `${minutes} min drive · ${miles} mi`;
}

async function renderLocationMap(search) {
  if (!globalThis.google?.maps || !locationBase) return;
  const candidates = [];
  const seen = new Set();
  for (const group of search.groups || []) {
    for (const candidate of group.candidates || []) {
      if (!seen.has(candidate.place_id)) {
        seen.add(candidate.place_id);
        candidates.push(candidate);
      }
    }
  }
  const center = { lat: locationBase.latitude, lng: locationBase.longitude };
  locationMap ||= new google.maps.Map($("locationMap"), {
    center, zoom: 10, mapId: "DEMO_MAP_ID", streetViewControl: false,
  });
  locationMap.setCenter(center);
  for (const marker of locationMarkers) detachLocationMarker(marker);
  locationMarkers = [];
  let AdvancedMarkerElement = google.maps.marker?.AdvancedMarkerElement;
  if (!AdvancedMarkerElement && typeof google.maps.importLibrary === "function") {
    ({ AdvancedMarkerElement } = await google.maps.importLibrary("marker"));
  }
  const bounds = new google.maps.LatLngBounds();
  bounds.extend(center);
  locationMarkers.push(createLocationMarker(AdvancedMarkerElement, center, "Production base"));
  for (const candidate of candidates) {
    if (!Number.isFinite(candidate.latitude) || !Number.isFinite(candidate.longitude)) continue;
    const position = { lat: candidate.latitude, lng: candidate.longitude };
    bounds.extend(position);
    locationMarkers.push(createLocationMarker(AdvancedMarkerElement, position, candidate.name));
  }
  if (candidates.length) locationMap.fitBounds(bounds, 40);
}

function createLocationMarker(AdvancedMarkerElement, position, title) {
  if (typeof AdvancedMarkerElement === "function") {
    return new AdvancedMarkerElement({ map: locationMap, position, title });
  }
  return new google.maps.Marker({ map: locationMap, position, title });
}

function detachLocationMarker(marker) {
  if (typeof marker.setMap === "function") marker.setMap(null);
  else marker.map = null;
}

async function saveLocationShortlist(checkbox) {
  checkbox.disabled = true;
  try {
    await jsonRequest(
      `/api/interviews/${encodeURIComponent(interview.session.id)}/locations/shortlist`,
      "POST", {
        search_id: locationSearchState.search_id,
        place_id: checkbox.dataset.locationPlace,
        breakdown_item_id: checkbox.dataset.locationItem,
        shortlisted: checkbox.checked,
      },
    );
    for (const group of locationSearchState.groups || []) {
      const candidate = (group.candidates || []).find((item) => (
        item.place_id === checkbox.dataset.locationPlace
        && group.breakdown_item_id === checkbox.dataset.locationItem
      ));
      if (candidate) candidate.shortlisted = checkbox.checked;
    }
    showNotice(checkbox.checked ? "Location shortlisted." : "Location removed from shortlist.");
    renderBreakdown();
  } catch (error) {
    checkbox.checked = !checkbox.checked;
    showNotice(error.message, { error: true });
  } finally {
    checkbox.disabled = false;
  }
}

function resetLocationScout() {
  locationBase = null;
  locationSearchState = null;
  locationSearching = false;
  locationMap = null;
  for (const marker of locationMarkers) detachLocationMarker(marker);
  locationMarkers = [];
  mapsAutocomplete = null;
  const autocomplete = $("locationAutocomplete");
  if (autocomplete) autocomplete.innerHTML = "<small>Loading Google location search…</small>";
  if ($("locationBaseLabel")) {
    $("locationBaseLabel").textContent = "Select a suggested address or place as your starting point.";
  }
  if ($("locationResults")) $("locationResults").hidden = true;
}

function renderBreakdownApproval(items) {
  const panel = $("breakdownApprovalPanel");
  if (!items.length) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  panel.hidden = false;
  if (breakdownState.status === "approved") {
    panel.innerHTML = `
      <div><p class="eyebrow">Breakdown approved</p><h3>Ready for scheduling.</h3><p>These requirements are locked as the production plan for the next stage.</p></div>
      <button class="secondary-button" id="reopenBreakdownButton" type="button">Reopen breakdown</button>`;
    $("reopenBreakdownButton").addEventListener("click", () => decideBreakdown("reopened"));
    return;
  }
  panel.innerHTML = `
    <div><p class="eyebrow">Review the requirements</p><h3>Does this cover what every scene needs?</h3><p>Edit, remove, or add anything the agent missed, then approve the breakdown.</p></div>
    <button class="primary-button compact" id="approveBreakdownButton" type="button">Approve breakdown <span>→</span></button>`;
  $("approveBreakdownButton").addEventListener("click", () => decideBreakdown("accepted"));
}

async function generateBreakdown(replaceExisting = false) {
  if (breakdownLoading) return;
  if (replaceExisting && !window.confirm("Generate a new breakdown? Your current active requirements will be replaced, but their previous version remains saved.")) return;
  breakdownLoading = true;
  breakdownGenerating = true;
  renderBreakdown();
  try {
    const result = await request(
      `/api/interviews/${encodeURIComponent(interview.session.id)}/breakdown/${replaceExisting ? "regenerate" : "generate"}`,
      {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_revision: breakdownState?.revision || 0 }),
        timeoutMs: 90_000,
      },
    );
    breakdownState = result.breakdown;
    if (replaceExisting) locationSearchState = null;
    showNotice(replaceExisting ? "The Breakdown agent replaced the active requirements." : "The Breakdown agent created editable production requirements.");
  } catch (error) {
    showNotice(error.message, { error: true, retry: () => generateBreakdown(replaceExisting), sticky: true });
  } finally {
    breakdownLoading = false;
    breakdownGenerating = false;
    renderBreakdown();
  }
}

function openBreakdownItemForm(item, sceneId) {
  if (!sceneId || breakdownLoading || breakdownState?.status === "approved") return;
  editingBreakdownItemId = item?.id || "";
  editingBreakdownSceneId = sceneId;
  $("breakdownItemFormTitle").textContent = item ? "Edit requirement" : "Add requirement";
  $("breakdownItemCategory").innerHTML = breakdownCategories.map((category) =>
    `<option value="${escapeHTML(category.category)}">${escapeHTML(category.label)} — ${escapeHTML(category.description)}</option>`
  ).join("");
  $("breakdownItemCategory").value = item?.category || breakdownCategories[0]?.category || "prop";
  $("breakdownItemName").value = item?.name || "";
  $("breakdownItemDetails").value = item?.details || "";
  $("breakdownItemForm").hidden = false;
  $("breakdownItemName").focus();
}

function closeBreakdownItemForm() {
  editingBreakdownItemId = "";
  editingBreakdownSceneId = "";
  $("breakdownItemForm").reset();
  $("breakdownItemForm").hidden = true;
}

async function removeBreakdownItem(itemId) {
  if (!itemId || breakdownLoading || !breakdownState) return;
  breakdownLoading = true;
  renderBreakdown();
  try {
    const result = await jsonRequest(
      `/api/interviews/${encodeURIComponent(interview.session.id)}/breakdown/items/${encodeURIComponent(itemId)}/archive`,
      "POST", { expected_revision: breakdownState.revision },
    );
    breakdownState = result.breakdown;
    showNotice("Requirement removed.");
  } catch (error) { showNotice(error.message, { error: true }); }
  finally { breakdownLoading = false; renderBreakdown(); }
}

async function decideBreakdown(decision) {
  if (!breakdownState || breakdownLoading) return;
  breakdownLoading = true;
  renderBreakdown();
  try {
    const result = await jsonRequest(
      `/api/interviews/${encodeURIComponent(interview.session.id)}/breakdown/decision`,
      "POST", { expected_revision: breakdownState.revision, decision },
    );
    breakdownState = result.breakdown;
    showNotice(decision === "accepted" ? "Production breakdown approved." : "Breakdown reopened for editing.");
  } catch (error) { showNotice(error.message, { error: true }); }
  finally { breakdownLoading = false; renderBreakdown(); }
}

function showOutline({ historyMode = "push" } = {}) {
  if (!interview || (!questionnaireHandoff() && interview.session.status !== "ready_for_outline")) return;
  captureDraft();
  $("startScreen").hidden = true;
  $("workspace").hidden = true;
  $("outlineWorkspace").hidden = false;
  $("screenplayWorkspace").hidden = true;
  $("breakdownWorkspace").hidden = true;
  renderOutline();
  setStageNavigation("outline");
  recordStage("outline", historyMode);
  $("backToInterview").focus();
  loadOutline();
}

function showInterviewWorkspace({ historyMode = "push" } = {}) {
  if (!interview) return;
  $("startScreen").hidden = true;
  $("outlineWorkspace").hidden = true;
  $("screenplayWorkspace").hidden = true;
  $("breakdownWorkspace").hidden = true;
  $("workspace").hidden = false;
  summaryDismissed.delete(interview.session.id);
  renderTimeline();
  renderResponseActions();
  renderStoryState();
  restoreDraft();
  setStageNavigation("interview");
  recordStage("interview", historyMode);
  configureSpeechButtons();
}

function renderInterview(value) {
  if (interview?.session?.id !== value.session.id) {
    outlineState = null;
    screenplayState = null;
    breakdownState = null;
    resetLocationScout();
  }
  interview = value;
  localStorage.setItem(STORAGE_ACTIVE, value.session.id);
  $("startScreen").hidden = true;
  $("outlineWorkspace").hidden = true;
  $("screenplayWorkspace").hidden = true;
  $("breakdownWorkspace").hidden = true;
  $("workspace").hidden = false;
  $("projectTitle").textContent = value.session.title;
  $("projectMode").textContent = modeLabel(value.session.involvement_mode);
  renderTimeline();
  renderResponseActions();
  renderStoryState();
  restoreDraft();
  setStageNavigation("interview");
  recordStage("interview", "replace");
  const hasPending = (value.timeline || []).some((item) => ["pending", "processing"].includes(item.event.status));
  setServerPending(hasPending);
  schedulePendingRefresh();
  configureSpeechButtons();
}

function showStart() {
  if (busy || activeVoice || voiceStarting || voiceStopping) return;
  captureDraft();
  if (pendingPoll) clearTimeout(pendingPoll);
  pendingPoll = null;
  setServerPending(false);
  interview = null;
  outlineState = null;
  outlineLoading = false;
  outlineStructureOptions = [];
  screenplayState = null;
  screenplayModes = [];
  screenplayLoading = false;
  screenplayGenerating = false;
  breakdownState = null;
  breakdownCategories = [];
  breakdownLoading = false;
  breakdownGenerating = false;
  editingBreakdownItemId = "";
  editingBreakdownSceneId = "";
  resetLocationScout();
  clearVisibleDraft();
  $("workspace").hidden = true;
  $("outlineWorkspace").hidden = true;
  $("screenplayWorkspace").hidden = true;
  $("breakdownWorkspace").hidden = true;
  $("startScreen").hidden = false;
  setStageNavigation("interview");
  recordStage("start", "replace");
  closeHistory({ restoreFocus: false });
  hideNotice();
  configureSpeechButtons();
  $("seedInput").focus();
}

function setComposerPurpose(purpose, label = "", { focus = true, persist = true } = {}) {
  composerPurpose = purpose;
  $("composerMode").hidden = purpose === "answer";
  const defaultLabels = {
    question: "Asking the editor a question",
    correction: "Correcting the editor’s understanding",
    nuance: "Adding nuance to the editor’s understanding",
    instruction: "Giving the editor an instruction",
  };
  $("composerModeText").textContent = label || defaultLabels[purpose] || "";
  if (focus) $("composerInput").focus();
  if (persist && interview) captureDraft();
}

function inferredPurpose(text) {
  if (composerPurpose !== "answer") return composerPurpose;
  const cleaned = text.trim().toLowerCase();
  if (cleaned.includes("?") || /^(can|could|would|should|how|what|why|do|does|is|are)\b/.test(cleaned)) return "question";
  if (/^(actually\b|correction\b|i meant\b|no(?:\b|[,—:])|not quite\b)/.test(cleaned)) return "correction";
  if (/^(please|help me|give me|show me|suggest|offer|ask me|focus on|try another)\b/.test(cleaned)) return "instruction";
  return "answer";
}

async function sendEvent(kind, payload, { preserveDraft = false, eventId = null } = {}) {
  if (!interview || busy || serverPending || activeVoice || voiceStarting || voiceStopping) return;
  const sessionId = interview.session.id;
  const outgoing = {
    event_id: eventId || uid("event"),
    expected_revision: interview.session.revision,
    kind,
    payload,
  };
  captureDraft(sessionId);
  return submitPreparedEvent(sessionId, outgoing, { preserveDraft });
}

async function submitPreparedEvent(sessionId, outgoing, { preserveDraft = false } = {}) {
  if (busy || serverPending || activeVoice || voiceStarting || voiceStopping) return;
  if (!interview || interview.session.id !== sessionId) {
    const opened = await openInterview(sessionId, { quiet: true });
    if (!opened || interview?.session?.id !== sessionId) {
      showNotice("Open the original Interview before retrying that saved turn.", { error: true });
      return;
    }
  }
  setBusy(
    true,
    ["suggestions_requested", "question_ideas_requested"].includes(outgoing.kind)
      ? "Preparing useful ideas…"
      : "Considering the story…",
  );
  hideNotice();
  try {
    const result = await jsonRequest(`/api/interviews/${encodeURIComponent(sessionId)}/events`, "POST", outgoing);
    if (result.session?.id !== sessionId) throw new Error("The server returned the wrong Interview.");
    if (!preserveDraft && ["questionnaire_submitted", "questionnaire_revised"].includes(outgoing.kind)) {
      localStorage.removeItem(draftKey(sessionId));
      clearVisibleDraft();
    } else if (!preserveDraft && outgoing.kind === "message_submitted") {
      const draftStillMatches = $("composerInput").value.trim() === outgoing.payload.text;
      if (draftStillMatches) {
        localStorage.removeItem(draftKey(sessionId));
        clearVisibleDraft();
      } else {
        captureDraft(sessionId);
      }
    }
    renderInterview(result);
    return result;
  } catch (error) {
    if (error.code === "stale_revision") {
      try {
        const latest = await request(`/api/interviews/${encodeURIComponent(sessionId)}`, { timeoutMs: 15_000 });
        if (latest.session?.id === sessionId) renderInterview(latest);
      } catch (_) { /* keep the saved local draft and original error */ }
      showNotice("This Interview changed in another tab. The latest version is loaded and your draft is still here.", { error: true });
    } else {
      showNotice(error.message, {
        error: true,
        sticky: true,
        retry: () => submitPreparedEvent(sessionId, outgoing, { preserveDraft }),
      });
    }
    return null;
  } finally {
    setBusy(false);
  }
}

async function openInterview(id, { quiet = false } = {}) {
  if (!id || busy || activeVoice || voiceStarting || voiceStopping) return false;
  captureDraft();
  setBusy(true, "Loading the Interview…");
  try {
    const result = await request(`/api/interviews/${encodeURIComponent(id)}`, { timeoutMs: 15_000 });
    if (result.session?.id !== id) throw new Error("The server returned the wrong Interview.");
    closeHistory({ restoreFocus: false });
    clearVisibleDraft();
    renderInterview(result);
    return true;
  } catch (error) {
    if (!quiet) showNotice(error.message, { error: true });
    return false;
  } finally {
    setBusy(false);
  }
}

function schedulePendingRefresh() {
  if (pendingPoll) clearTimeout(pendingPoll);
  pendingPoll = null;
  if (!serverPending || !interview) return;
  const sessionId = interview.session.id;
  pendingPoll = setTimeout(async () => {
    pendingPoll = null;
    if (!interview || interview.session.id !== sessionId) return;
    if (busy || activeVoice || voiceStarting || voiceStopping) {
      schedulePendingRefresh();
      return;
    }
    try {
      const result = await request(`/api/interviews/${encodeURIComponent(sessionId)}`, { timeoutMs: 15_000 });
      if (interview?.session?.id === sessionId && result.session?.id === sessionId) renderInterview(result);
    } catch (_) {
      schedulePendingRefresh();
    }
  }, 1_500);
}

async function loadHistory() {
  const list = $("historyList");
  list.innerHTML = '<p class="history-empty">Loading interviews…</p>';
  try {
    const result = await request("/api/interviews", { timeoutMs: 15_000 });
    const items = result.interviews || [];
    if (!items.length) {
      list.innerHTML = '<p class="history-empty">No Interviews yet. Start with the image you can already see.</p>';
      return;
    }
    list.innerHTML = items.map((item) => `
      <button class="history-item" type="button" data-id="${escapeHTML(item.id)}">
        <h3>${escapeHTML(item.title)}</h3>
        <p>${escapeHTML(item.current_question || item.seed)}</p>
        <span class="history-meta"><span>${escapeHTML(item.status.replaceAll("_", " "))}</span><span>r${item.revision}</span></span>
      </button>`).join("");
    list.querySelectorAll(".history-item").forEach((button) => button.addEventListener("click", () => openInterview(button.dataset.id)));
  } catch (error) {
    list.innerHTML = `<p class="history-empty">${escapeHTML(error.message)}</p>`;
  }
}

// Speech is a draft input adapter. It never submits a filmmaker event.
function speechSupported() {
  return Boolean(
    navigator.mediaDevices?.getUserMedia
    && globalThis.AudioContext
    && globalThis.AudioWorkletNode
  );
}

function configureSpeechButtons() {
  const configured = Boolean(health?.speech?.available && speechSupported());
  const unavailableReason = !health?.speech?.available
    ? (health?.speech?.message || "Voice typing is not configured.")
    : (!speechSupported() ? "This browser does not support the required microphone recorder." : "");
  for (const button of [$("seedMic"), $("composerMic"), ...document.querySelectorAll(".question-mic")]) {
    if (!button) continue;
    if (activeVoice?.button === button && !voiceStopping) {
      button.disabled = false;
      continue;
    }
    button.disabled = busy || serverPending || voiceStarting || voiceStopping || Boolean(activeVoice) || !configured;
    button.title = configured ? "Start voice typing" : unavailableReason;
  }
}

function setVoiceControls(value, exempt = []) {
  const allowed = new Set(exempt);
  document.querySelectorAll("button, input, textarea").forEach((element) => {
    if (allowed.has(element)) {
      element.disabled = false;
      return;
    }
    if (value && !element.disabled) {
      element.disabled = true;
      element.dataset.disabledByVoice = "true";
    } else if (!value && element.dataset.disabledByVoice === "true") {
      element.disabled = false;
      delete element.dataset.disabledByVoice;
    }
  });
}

async function startVoice(target, button, status, cancelButton) {
  if (activeVoice) {
    if (activeVoice.button === button) await stopVoice(true);
    return;
  }
  if (busy || serverPending || voiceStarting || voiceStopping) return;
  voiceStarting = true;
  setVoiceControls(true, []);
  configureSpeechButtons();
  const baseText = target.value;
  status.textContent = "Requesting microphone permission…";
  let stream = null;
  let context = null;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: false,
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    context = new AudioContext({ sampleRate: 48000 });
    await context.resume();
    if (context.state !== "running" || context.sampleRate > 48000) {
      throw new Error("This microphone sample rate is not supported.");
    }
    await context.audioWorklet.addModule("/pcm-worklet.js");
    const source = context.createMediaStreamSource(stream);
    const worklet = new AudioWorkletNode(context, "second-unit-pcm");
    const silent = context.createGain();
    silent.gain.value = 0;
    source.connect(worklet);
    worklet.connect(silent);
    silent.connect(context.destination);
    const chunks = [];
    let bytes = 0;
    const started = Date.now();
    activeVoice = {
      target, button, status, cancelButton, baseText, stream, context,
      source, worklet, silent, chunks, sampleRate: context.sampleRate,
      interval: null, timeout: null,
    };
    worklet.port.onmessage = (event) => {
      if (!activeVoice || activeVoice.worklet !== worklet) return;
      const part = new Uint8Array(event.data);
      chunks.push(part);
      bytes += part.byteLength;
      if (bytes >= 5_280_000) stopVoice(true);
    };
    activeVoice.interval = setInterval(() => {
      const seconds = Math.floor((Date.now() - started) / 1000);
      status.textContent = `Listening… ${seconds}s · press the microphone to finish`;
    }, 500);
    activeVoice.timeout = setTimeout(() => stopVoice(true), 55_000);
    button.classList.add("recording");
    button.title = "Stop and transcribe";
    button.setAttribute("aria-label", "Stop and transcribe voice recording");
    button.setAttribute("aria-pressed", "true");
    status.className = "listening";
    status.textContent = "Listening… press the microphone to finish";
    cancelButton.hidden = false;
    setVoiceControls(true, [button, cancelButton]);
    configureSpeechButtons();
  } catch (error) {
    stream?.getTracks().forEach((track) => track.stop());
    if (context && context.state !== "closed") await context.close().catch(() => {});
    status.className = "";
    status.textContent = error.name === "NotAllowedError"
      ? "Microphone permission was denied. You can keep typing."
      : "The microphone could not start. You can keep typing.";
    activeVoice = null;
  } finally {
    voiceStarting = false;
    if (!activeVoice) setVoiceControls(false);
    configureSpeechButtons();
  }
}

function joinChunks(chunks) {
  const size = chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0);
  const joined = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { joined.set(chunk, offset); offset += chunk.byteLength; }
  return joined;
}

async function stopVoice(transcribe) {
  const voice = activeVoice;
  if (!voice || voiceStopping) return;
  voiceStopping = true;
  setVoiceControls(true, []);
  configureSpeechButtons();
  clearInterval(voice.interval);
  clearTimeout(voice.timeout);
  voice.worklet.port.onmessage = null;
  try { voice.source.disconnect(); voice.worklet.disconnect(); voice.silent.disconnect(); } catch (_) { /* already disconnected */ }
  voice.stream.getTracks().forEach((track) => track.stop());
  await voice.context.close().catch(() => {});
  activeVoice = null;
  voice.button.classList.remove("recording");
  voice.button.setAttribute("aria-label", "Start voice typing");
  voice.button.setAttribute("aria-pressed", "false");
  voice.cancelButton.hidden = true;
  voice.status.className = "";
  setVoiceControls(false);

  if (!transcribe) {
    voice.target.value = voice.baseText;
    voice.target.dispatchEvent(new Event("input"));
    voice.status.textContent = "Recording cancelled. Your draft is unchanged.";
    configureSpeechButtons();
    voice.target.focus();
    voiceStopping = false;
    configureSpeechButtons();
    return;
  }

  const audio = joinChunks(voice.chunks);
  voice.status.textContent = "Transcribing…";
  setBusy(true, "Transcribing your recording…");
  const controller = new AbortController();
  const transcriptionTimeout = setTimeout(() => controller.abort(), 70_000);
  try {
    const result = await request("/api/speech/transcribe", {
      method: "POST",
      signal: controller.signal,
      timeoutMs: 75_000,
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Sample-Rate": String(Math.round(voice.sampleRate)),
        "X-Speech-Language": "en-US",
      },
      body: audio,
    });
    if (typeof result.transcript !== "string") throw new Error("The speech service returned an invalid transcript.");
    const combined = [voice.baseText.trim(), result.transcript.trim()].filter(Boolean).join(voice.baseText.trim() ? " " : "");
    if (combined.length > voice.target.maxLength) {
      throw new Error("The transcript is too long for this message. Your original draft is unchanged.");
    }
    voice.target.value = combined;
    voice.target.dispatchEvent(new Event("input"));
    voice.status.textContent = "Transcribed. Edit the text before sending if you like.";
  } catch (error) {
    voice.target.value = voice.baseText;
    voice.target.dispatchEvent(new Event("input"));
    voice.status.textContent = error.name === "AbortError"
      ? "Transcription timed out. Your draft is unchanged; you can try again."
      : error.message;
  } finally {
    clearTimeout(transcriptionTimeout);
    setBusy(false);
    voiceStopping = false;
    configureSpeechButtons();
    voice.target.focus();
  }
}

function cancelVoice() { return stopVoice(false); }

window.addEventListener("beforeunload", () => {
  activeVoice?.stream?.getTracks().forEach((track) => track.stop());
});

async function createInterview(payload) {
  if (busy || serverPending || activeVoice || voiceStarting || voiceStopping) return;
  setBusy(true, "Opening the Interview…");
  hideNotice();
  try {
    const result = await jsonRequest("/api/interviews", "POST", payload);
    if (result.session?.id !== payload.session_id) throw new Error("The server returned the wrong Interview.");
    clearVisibleDraft();
    renderInterview(result);
    $("startForm").reset();
    $("startForm").querySelector('[name="storytelling_format"][value="not_sure"]').checked = true;
    $("startForm").querySelector('[name="involvement_mode"][value="collaborative"]').checked = true;
  } catch (error) {
    showNotice(error.message, {
      error: true,
      sticky: true,
      retry: () => createInterview(payload),
    });
  } finally {
    setBusy(false);
  }
}

$("startForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy || serverPending || activeVoice || voiceStarting || voiceStopping) return;
  const data = new FormData(event.currentTarget);
  const payload = {
    session_id: uid("interview"),
    event_id: uid("event"),
    title: data.get("title"),
    seed: data.get("seed"),
    storytelling_format: data.get("storytelling_format"),
    involvement_mode: data.get("involvement_mode"),
  };
  await createInterview(payload);
});

$("composerForm").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!interview || busy || serverPending || activeVoice || voiceStarting || voiceStopping) return;
  const text = $("composerInput").value.trim();
  if (!text) { $("composerInput").focus(); return; }
  sendEvent("message_submitted", {
    text,
    purpose: inferredPurpose(text),
    response_id: interview.current_response?.id,
  });
});

$("composerInput").addEventListener("input", () => {
  if (interview) captureDraft();
});
$("clearComposerMode").addEventListener("click", () => setComposerPurpose("answer"));
$("optionsButton").addEventListener("click", () => sendEvent(
  "suggestions_requested",
  { response_id: interview.current_response?.id || "" },
  { preserveDraft: true },
));
$("finishInterviewButton").addEventListener("click", () => sendEvent(
  "interview_finished",
  { response_id: interview.current_response?.id || "" },
  { preserveDraft: true },
));
$("skipButton").addEventListener("click", () => {
  const response = interview.current_response;
  if (response) sendEvent("question_skipped", { response_id: response.id, note: "" }, { preserveDraft: true });
});

$("seedMic").addEventListener("click", () => startVoice($("seedInput"), $("seedMic"), $("seedSpeech"), $("seedCancelVoice")));
$("composerMic").addEventListener("click", () => startVoice($("composerInput"), $("composerMic"), $("composerSpeech"), $("composerCancelVoice")));
$("seedCancelVoice").addEventListener("click", cancelVoice);
$("composerCancelVoice").addEventListener("click", cancelVoice);

$("structureProposalForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (outlineLoading || $("customStructureFields").hidden) return;
  const label = $("structureLabel").value.trim();
  const rationale = $("structureRationale").value.trim();
  if (!label || !rationale) {
    showNotice("Give the custom structure a name and a brief description.", { error: true });
    return;
  }
  const result = await runOutlineTool("propose_story_structure", {
    expected_revision: outlineState?.revision || 0,
    kind: "custom",
    label,
    rationale,
  });
  if (result) {
    hideStructureProposalForm({ reset: true });
    showNotice("Structure option saved locally.");
  }
});
$("cancelStructureProposal").addEventListener("click", () => hideStructureProposalForm());
$("outlineAgentButton").addEventListener("click", generateOutlineBeats);
$("generateBeatsButton").addEventListener("click", generateOutlineBeats);
$("addBeatButton").addEventListener("click", () => openBeatForm());
$("cancelBeatEdit").addEventListener("click", closeBeatForm);
$("beatForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (outlineLoading || !outlineState) return;
  const fields = {
    title: $("beatTitle").value.trim(),
    summary: $("beatSummary").value.trim(),
    purpose: $("beatPurpose").value.trim(),
  };
  if (!fields.title || !fields.summary) {
    showNotice("Give the beat a title and describe what visibly happens.", { error: true });
    return;
  }
  const tool = editingBeatId ? "edit_beat" : "create_beat";
  const payload = {
    expected_revision: outlineState.revision,
    ...fields,
    ...(editingBeatId ? { beat_id: editingBeatId } : {}),
  };
  const wasEditing = Boolean(editingBeatId);
  const result = await runOutlineTool(tool, payload);
  if (result) {
    closeBeatForm();
    showNotice(wasEditing ? "Beat updated." : "Beat added.");
  }
});
$("screenplayStageButton").addEventListener("click", showScreenplay);
$("backToOutline").addEventListener("click", showOutline);
$("generateScreenplayButton").addEventListener("click", generateScreenplay);
$("cancelSceneEdit").addEventListener("click", closeSceneForm);
$("sceneForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!editingSceneId || screenplayLoading) return;
  const dialogue = [];
  for (const row of $("sceneDialogue").value.split("\n").map((item) => item.trim()).filter(Boolean)) {
    const divider = row.indexOf(":");
    if (divider < 1 || !row.slice(divider + 1).trim()) {
      showNotice("Write dialogue as CHARACTER: line, one per row.", { error: true });
      return;
    }
    dialogue.push({ character: row.slice(0, divider).trim(), line: row.slice(divider + 1).trim() });
  }
  screenplayLoading = true; renderScreenplay();
  try {
    const result = await jsonRequest(
      `/api/interviews/${encodeURIComponent(interview.session.id)}/screenplay/scenes/${encodeURIComponent(editingSceneId)}`,
      "POST", {
        expected_revision: screenplayState.revision,
        heading: $("sceneHeading").value.trim(), action: $("sceneAction").value.trim(),
        narration: $("sceneNarration").value.trim(), dialogue,
      },
    );
    screenplayState = result.screenplay;
    closeSceneForm();
    showNotice("Scene updated.");
  } catch (error) { showNotice(error.message, { error: true }); }
  finally { screenplayLoading = false; renderScreenplay(); }
});
$("breakdownStageButton").addEventListener("click", showBreakdown);
$("backToScreenplay").addEventListener("click", showScreenplay);
$("generateBreakdownButton").addEventListener("click", () => generateBreakdown(false));
$("regenerateBreakdownButton").addEventListener("click", () => generateBreakdown(true));
$("findLocationsButton").addEventListener("click", findLocationSuggestions);
$("cancelBreakdownItemEdit").addEventListener("click", closeBreakdownItemForm);
$("breakdownItemForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!editingBreakdownSceneId || breakdownLoading || !breakdownState) return;
  const body = {
    expected_revision: breakdownState.revision,
    category: $("breakdownItemCategory").value,
    name: $("breakdownItemName").value.trim(),
    details: $("breakdownItemDetails").value.trim(),
  };
  if (!body.name) {
    showNotice("Give the production requirement a short name.", { error: true });
    return;
  }
  const path = editingBreakdownItemId
    ? `/api/interviews/${encodeURIComponent(interview.session.id)}/breakdown/items/${encodeURIComponent(editingBreakdownItemId)}`
    : `/api/interviews/${encodeURIComponent(interview.session.id)}/breakdown/items`;
  if (!editingBreakdownItemId) body.scene_id = editingBreakdownSceneId;
  const wasEditing = Boolean(editingBreakdownItemId);
  breakdownLoading = true;
  renderBreakdown();
  try {
    const result = await jsonRequest(path, "POST", body);
    breakdownState = result.breakdown;
    closeBreakdownItemForm();
    showNotice(wasEditing ? "Requirement updated." : "Requirement added.");
  } catch (error) { showNotice(error.message, { error: true }); }
  finally { breakdownLoading = false; renderBreakdown(); }
});

function openHistory() {
  if (busy || activeVoice || voiceStarting || voiceStopping) return;
  historyReturnFocus = document.activeElement;
  $("historyDrawer").hidden = false;
  document.querySelector(".app-header").inert = true;
  document.querySelector("main").inert = true;
  loadHistory();
  $("closeHistory").focus();
}

function closeHistory({ restoreFocus = true } = {}) {
  if ($("historyDrawer").hidden) return;
  $("historyDrawer").hidden = true;
  document.querySelector(".app-header").inert = false;
  document.querySelector("main").inert = false;
  if (restoreFocus && historyReturnFocus?.focus) historyReturnFocus.focus();
  historyReturnFocus = null;
}

$("historyButton").addEventListener("click", openHistory);
$("historyBackdrop").addEventListener("click", () => closeHistory());
$("closeHistory").addEventListener("click", () => closeHistory());
$("newInterview").addEventListener("click", () => {
  closeHistory({ restoreFocus: false });
  showStart();
});
$("homeButton").addEventListener("click", showStart);
$("interviewStageButton").addEventListener("click", showInterviewWorkspace);
$("outlineStageButton").addEventListener("click", showOutline);
$("backToInterview").addEventListener("click", () => {
  if (history.state?.secondUnitStage === "outline" && history.state?.previousStage === "interview") {
    history.back();
    return;
  }
  showInterviewWorkspace();
});
window.addEventListener("popstate", (event) => {
  const requested = event.state?.secondUnitStage
    || (location.hash === "#breakdown" ? "breakdown" : location.hash === "#screenplay" ? "screenplay" : location.hash === "#outline" ? "outline" : location.hash === "#interview" ? "interview" : "start");
  if (requested === "breakdown" && interview && screenplayState?.status === "approved") {
    showBreakdown({ historyMode: "none" });
  } else if (requested === "screenplay" && interview && outlineState?.status === "approved") {
    showScreenplay({ historyMode: "none" });
  } else if (requested === "outline" && interview && (questionnaireHandoff() || interview.session.status === "ready_for_outline")) {
    showOutline({ historyMode: "none" });
  } else if (requested === "interview" && interview) {
    showInterviewWorkspace({ historyMode: "none" });
  }
});
document.addEventListener("keydown", (event) => {
  if ($("historyDrawer").hidden) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeHistory();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [...document.querySelector(".drawer").querySelectorAll("button:not(:disabled)")];
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});

async function initialize() {
  const requestedStage = location.hash === "#breakdown" ? "breakdown" : location.hash === "#screenplay" ? "screenplay" : location.hash === "#outline" ? "outline" : "interview";
  try {
    health = await request("/api/health", { timeoutMs: 10_000 });
  } catch (_) {
    health = { speech: { available: false, message: "The local server is unavailable." } };
  }
  configureSpeechButtons();
  const active = localStorage.getItem(STORAGE_ACTIVE);
  if (active) {
    const opened = await openInterview(active, { quiet: true });
    if (opened && requestedStage === "outline") showOutline({ historyMode: "replace" });
    if (opened && ["screenplay", "breakdown"].includes(requestedStage)) {
      showOutline({ historyMode: "replace" });
      await loadOutline();
      if (outlineState?.status === "approved") {
        await showScreenplay({ historyMode: "replace" });
        if (requestedStage === "breakdown" && screenplayState?.status === "approved") {
          showBreakdown({ historyMode: "replace" });
        }
      }
    }
  } else {
    recordStage("start", "replace");
  }
}

initialize();
