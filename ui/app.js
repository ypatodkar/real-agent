// Second Unit — plain JS, no framework, no build step.
//
// The gate is the interesting part. The server holds a suspended generator, so
// the run genuinely stops mid-pipeline and waits: nothing downstream has been
// spent when you are looking at the proposal.

const $ = (id) => document.getElementById(id);

const DIAL_HINT = {
  0: "never interrupts — the eval path",
  1: "asks only when genuinely stuck",
  3: "asks only when genuinely stuck",
  5: "asks about the one or two real forks",
  8: "checks most creative calls with you",
  10: "confirms nearly everything",
};

const CATALOG = {
  intent: ["explainer", "comedy", "commentary"],
  format: ["monologue", "debate"],
  tone: ["engaging", "professional", "funny", "dramatic", "casual", "wry", "dry"],
};

let runId = null;
let poll = null;
let currentPause = null;

// ------------------------------------------------------------------ dial

function hintFor(v) {
  const keys = Object.keys(DIAL_HINT).map(Number).sort((a, b) => a - b);
  return DIAL_HINT[keys.reduce((best, k) => (k <= v ? k : best), 0)];
}

$("involvement").addEventListener("input", (e) => {
  const v = +e.target.value;
  $("invOut").textContent = v;
  $("invHint").textContent = hintFor(v);
});

// ------------------------------------------------------------------ start

$("go").addEventListener("click", async () => {
  const topic = $("topic").value.trim() || $("topic").placeholder;

  $("go").disabled = true;
  $("go").textContent = "Running…";
  $("progress").hidden = false;
  $("result").hidden = true;
  $("gate").hidden = true;

  const res = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ topic, involvement: +$("involvement").value }),
  });
  const data = await res.json();

  if (data.error) {
    $("composeNote").textContent = data.error;
    $("go").disabled = false;
    $("go").textContent = "Generate";
    return;
  }

  runId = data.run_id;
  $("runid").textContent = runId;
  poll = setInterval(tick, 400);
  tick();
});

// ------------------------------------------------------------------ poll

async function tick() {
  if (!runId) return;
  const snap = await (await fetch(`/api/run/${runId}`)).json();

  renderStages(snap.stages);
  renderSpend(snap.spend, snap.cap);

  if (snap.status === "paused" && snap.pause) {
    renderGate(snap.pause);
  } else {
    $("gate").hidden = true;
    currentPause = null;
  }

  if (snap.status === "done" || snap.status === "error") {
    clearInterval(poll);
    $("go").disabled = false;
    $("go").textContent = "Generate";
    renderResult(snap);
  }
}

// ------------------------------------------------------------------ render

function renderStages(stages) {
  const html = stages.map((s) => `
    <li data-state="${s.state}">
      <span class="dot"></span>
      <span>${s.label}</span>
      <span class="cost">${s.cost ? "$" + s.cost.toFixed(4) : s.state === "done" ? "free" : ""}</span>
    </li>`).join("");
  if ($("stages").innerHTML !== html) $("stages").innerHTML = html;
}

function renderSpend(spend, cap) {
  $("spendBar").style.width = Math.min(100, (spend / cap) * 100) + "%";
  $("spendNum").textContent = `$${(spend || 0).toFixed(4)} / $${cap.toFixed(3)}`;
}

function renderGate(pause) {
  if (currentPause && currentPause.gate === pause.gate) return;
  currentPause = pause;

  $("gate").hidden = false;
  $("gateName").textContent = pause.gate === "gate1" ? "Gate 1 · confirm the brief"
                                                     : "Gate 2 · review the plan";
  $("gateJson").textContent = JSON.stringify(pause.proposal, null, 2);
  $("gatePlan").hidden = false;

  const brief = pause.proposal.brief || {};
  $("gateFields").innerHTML = ["intent", "format", "tone"]
    .filter((k) => k in brief)
    .map((k) => {
      const opts = [...new Set([...CATALOG[k], brief[k]])]
        .map((o) => `<option ${o === brief[k] ? "selected" : ""}>${o}</option>`).join("");
      return `<label class="field">
                <span class="lbl">${k}</span>
                <select data-key="${k}">${opts}</select>
              </label>`;
    })
    .concat(brief.duration_s !== undefined ? [`
      <label class="field">
        <span class="lbl">duration (s)</span>
        <input type="text" data-key="duration_s" value="${brief.duration_s}">
      </label>`] : [])
    .join("");
}

function collectEdits() {
  const brief = { ...(currentPause.proposal.brief || {}) };
  let changed = false;
  $("gateFields").querySelectorAll("[data-key]").forEach((el) => {
    const key = el.dataset.key;
    const value = key === "duration_s" ? parseFloat(el.value) : el.value;
    if (brief[key] !== value) { brief[key] = value; changed = true; }
  });
  return changed ? { brief } : {};
}

async function resume(edits) {
  $("gate").hidden = true;
  await fetch(`/api/run/${runId}/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ edits }),
  });
}

$("accept").addEventListener("click", () => resume({}));
$("applyEdits").addEventListener("click", () => resume(collectEdits()));

function renderResult(snap) {
  $("result").hidden = false;

  if (snap.status === "error") {
    $("verdict").className = "verdict fail";
    $("verdict").innerHTML = `Failed<small>${snap.error || "unknown error"}</small>`;
    return;
  }

  const green = snap.outcome === "green";
  const failed = new Set(snap.violations.map((v) => v.rule_id));
  const applicable = snap.applicable || [];

  $("verdict").className = "verdict" + (green ? "" : " fail");
  $("verdict").innerHTML =
    `${green ? "Green" : "Escalated"} — ${applicable.length - failed.size}/${applicable.length} checks pass` +
    `<small>${snap.rounds} repair round${snap.rounds === 1 ? "" : "s"} · ` +
    `$${snap.spend.toFixed(4)} of $${snap.cap.toFixed(3)}` +
    (snap.brief ? ` · ${snap.brief.intent} · ${snap.brief.format} · ${snap.brief.tone}` : "") +
    `</small>`;

  const ALL = ["beat_alignment", "duration_adherence", "reading_speed", "pacing_curve",
               "screen_time_balance", "speaker_attribution", "identity_drift",
               "music_ducking", "loudness_spec", "grounding", "coherence"];

  $("rules").innerHTML = ALL.map((id) => {
    const na = !applicable.includes(id);
    const cls = na ? "na" : failed.has(id) ? "fail" : "pass";
    const label = na ? "n/a" : failed.has(id) ? "fail" : "pass";
    return `<li><span>${id.replace(/_/g, " ")}</span><span class="tag ${cls}">${label}</span></li>`;
  }).join("");

  if (snap.video) {
    $("video").src = snap.video;
    $("videoNote").textContent = snap.script ? `${snap.script.length} lines` : "";
  } else {
    $("videoNote").textContent = "no video produced";
  }

  $("log").textContent = (snap.log || []).join("\n");
}
