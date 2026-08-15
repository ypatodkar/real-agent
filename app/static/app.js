// Second Unit — plain JS, no framework, no build step.

const $ = (id) => document.getElementById(id);

let projectId = null;
let askedAt = 0;          // when the question appeared — hesitation is signal

// ------------------------------------------------------------------ start

$("startForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const button = e.target.querySelector("button");
  button.disabled = true;
  button.textContent = "Thinking…";

  const state = await post("/api/project", { seed: $("seed").value });
  if (!state || state.error) {
    button.disabled = false;
    button.textContent = "Begin";
    return;
  }

  projectId = state.project_id;
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

// Cmd/Ctrl+Enter submits — writers keep their hands on the keyboard.
$("answer").addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") $("answerForm").requestSubmit();
});

$("answer").addEventListener("input", () => {
  const n = $("answer").value.trim().split(/\s+/).filter(Boolean).length;
  $("counter").textContent = n ? `${n} word${n === 1 ? "" : "s"}` : "";
});

async function advance(suffix, body) {
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
  $("question").textContent = state.question || "";
  askedAt = Date.now();

  $("rail").innerHTML = state.stages.map((s) => {
    const here = state.stages.indexOf(state.stage);
    const i = state.stages.indexOf(s);
    const st = i < here ? "done" : i === here ? "current" : "ahead";
    return `<span data-state="${st}">${s}</span>`;
  }).join("");

  const list = state.established || [];
  $("established").hidden = list.length === 0;
  $("establishedList").innerHTML = list
    .map((e) => `<dt>${escape(e.q)}</dt><dd>${escape(e.a)}</dd>`)
    .join("");

  const bits = [`${state.answered} answered`, `${state.words} words`];
  if (state.ranked) bits.push("question chosen from Grafana");
  if (state.stage_changed) bits.push(`→ ${state.stage}`);
  status(bits);
}

function escape(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// ------------------------------------------------------------------ plumbing

async function post(url, body) {
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await r.json();
  } catch (err) {
    status([`could not reach the server — ${err.message}`]);
    return null;
  }
}

function status(bits) {
  $("status").innerHTML = bits.map((b) => `<span>${escape(b)}</span>`).join("");
}

// health line on load, so the runtime dependencies are visible not assumed
fetch("/api/health").then((r) => r.json()).then((h) => {
  status([
    `model ${h.model}`,
    `grafana read <span class="on">on</span>`,
    `grafana write <span class="${h.grafana_write === "configured" ? "on" : "off"}">` +
      `${h.grafana_write === "configured" ? "on" : "off"}</span>`,
  ]);
  $("status").innerHTML = $("status").textContent
    ? $("status").innerHTML
    : $("status").innerHTML;
}).catch(() => {});
