// Animation Studio GUI: plain JavaScript, no build step. Hash routes:
//   #/                 runs
//   #/new              new run form
//   #/run/<id>[/<tab>] one run: pipeline | outputs | package | queue
//   #/library          the asset library manifest
"use strict";

const AGENT_LABEL = {
  story_analyst: "Story Analyst", character_designer: "Character Designer", scene_builder: "Scene Builder",
  director: "Director", layout_td: "Layout TD", continuity_checker: "Continuity Checker",
};
const POLL_MS = 1500;
let INFO = null;
let pollTimer = null;
let view = { key: null };

// ---------------------------------------------------------------- helpers
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
function put(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false && k !== ""));
}
const label = (a) => AGENT_LABEL[a] || a;
const badge = (s) => h("span", { class: `badge s-${s}` }, String(s || "new").replace(/_/g, " "));
const when = (iso) => (iso ? new Date(iso).toLocaleString() : "");

async function api(path, opts = {}) {
  const init = { headers: {} };
  if (opts.body !== undefined) {
    init.method = "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(`/api/${path}`, init);
  const ct = res.headers.get("Content-Type") || "";
  const data = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    const err = new Error((data && data.error) || `HTTP ${res.status}`);
    err.details = data && data.details;
    throw err;
  }
  return data;
}

function toast(msg, bad = false) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast" + (bad ? " bad" : "");
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), bad ? 6000 : 2500);
}

function errorDetails(err) {
  if (!err.details) return null;
  if (Array.isArray(err.details))
    return h("ul", { class: "small" }, err.details.slice(0, 20).map((d) => h("li", {}, `${(d.loc || []).join(".")}: ${d.msg || JSON.stringify(d)}`)));
  return h("pre", {}, JSON.stringify(err.details, null, 2));
}

// Collapsible JSON tree; open to `depth` levels.
function tree(value, depth = 1) {
  const wrap = h("div", { class: "tree" });
  wrap.append(node(null, value, 0, depth));
  return wrap;
}
function node(key, value, level, depth) {
  const k = key === null ? null : h("span", { class: "k" }, `${key}: `);
  if (value !== null && typeof value === "object") {
    const isArr = Array.isArray(value);
    const entries = isArr ? value.map((v, i) => [i, v]) : Object.entries(value);
    const hint = isArr ? `[${value.length}]` : `{${entries.length}}`;
    const peek = !isArr && (value.id || value.name || value.title);
    const d = h("details", { open: level < depth }, h("summary", {}, k, h("span", { class: "muted" }, hint), peek ? h("span", { class: "muted" }, `  ${peek}`) : null));
    let rendered = false;
    const fill = () => {
      if (rendered) return;
      rendered = true;
      for (const [ck, cv] of entries) d.append(node(ck, cv, level + 1, depth));
    };
    if (level < depth) fill();
    d.addEventListener("toggle", fill);
    return d;
  }
  const cls = value === null ? "nul" : typeof value === "string" ? "str" : "num";
  return h("div", { class: "leaf" }, k, h("span", { class: cls }, typeof value === "string" ? JSON.stringify(value) : String(value)));
}

function setNav(name) {
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("on", a.dataset.nav === name));
}

function stopPoll() {
  clearTimeout(pollTimer);
  pollTimer = null;
}

// ---------------------------------------------------------------- router
async function route() {
  stopPoll();
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  const app = document.getElementById("app");
  try {
    if (!INFO) {
      INFO = await api("info");
      document.getElementById("where").textContent = `${INFO.runs_dir} · agents: ${INFO.default_agents}`;
    }
    if (parts[0] === "new") return newRun(app);
    if (parts[0] === "library") return library(app);
    if (parts[0] === "run" && parts[1]) return runView(app, parts[1], parts[2] || "pipeline");
    return runsList(app);
  } catch (err) {
    put(app, h("div", { class: "panel err" }, h("h2", {}, "Something went wrong"), h("p", {}, err.message), errorDetails(err)));
  }
}
window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", route);

// ---------------------------------------------------------------- runs list
async function runsList(app) {
  setNav("runs");
  view = { key: "runs" };
  const runs = await api("runs");
  if (view.key !== "runs") return;
  const body = runs.length
    ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Run"), h("th", {}, "Story"), h("th", {}, "Status"), h("th", {}, "Progress"), h("th", {}, "Updated"))),
        h("tbody", {}, runs.map((r) => h("tr", { class: "click", onclick: () => (location.hash = `#/run/${encodeURIComponent(r.id)}`) },
          h("td", {}, h("strong", {}, r.id)),
          h("td", {}, r.title || h("span", { class: "muted" }, "untitled")),
          h("td", {}, badge(r.active ? "running" : r.status), r.awaiting ? h("span", { class: "small muted" }, ` ${label(r.awaiting)}`) : null,
            r.package_status ? h("span", {}, " ", badge(r.package_status)) : null),
          h("td", { class: "small" }, `${r.completed.length} agents done`),
          h("td", { class: "small muted" }, when(r.updated_at)))))))
    : h("div", { class: "empty" }, h("p", {}, "No runs yet."), h("a", { class: "btn", href: "#/new" }, "Start a run"));
  put(app, h("div", { class: "panel" }, h("div", { class: "row spread" }, h("h2", {}, "Runs"), h("a", { class: "btn", href: "#/new" }, "New run")), body));
  pollTimer = setTimeout(() => view.key === "runs" && runsList(app), POLL_MS * 2);
}

// ---------------------------------------------------------------- new run
function flagField(name, prop, value) {
  const title = prop.title || name;
  if (prop.type === "boolean") {
    const el = h("input", { type: "checkbox", "data-flag": name, checked: value });
    return h("label", { class: "check" }, el, title);
  }
  if (prop.enum) {
    return h("label", { class: "field" }, title,
      h("select", { "data-flag": name }, prop.enum.map((o) => h("option", { value: o, selected: o === value }, o))));
  }
  if (prop.type === "array" && prop.items && prop.items.enum) {
    return h("div", { class: "field" }, h("span", { class: "small muted" }, title),
      h("div", { class: "row" }, prop.items.enum.map((o) => h("label", { class: "check" },
        h("input", { type: "checkbox", "data-flag-list": name, value: o, checked: (value || []).includes(o) }), o))));
  }
  if (prop.type === "integer" || prop.type === "number") {
    return h("label", { class: "field" }, title, h("input", { type: "number", "data-flag": name, "data-num": prop.type, value,
      step: prop.type === "integer" ? 1 : 0.01, min: prop.minimum, max: prop.maximum }));
  }
  return h("label", { class: "field" }, title, h("input", { type: "text", "data-flag": name, value }));
}

function newRun(app) {
  setNav("new");
  view = { key: "new" };
  const d = INFO.defaults;
  const flagsProps = INFO.flags_schema.properties;
  const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
  const form = h("form", { class: "panel", onsubmit: submit },
    h("h2", {}, "New run"),
    h("div", { class: "form-grid" },
      h("label", { class: "field" }, "Run name", h("input", { type: "text", name: "name", required: true, value: `run-${stamp}`, pattern: "[A-Za-z0-9][A-Za-z0-9_.\\-]{0,63}" })),
      h("label", { class: "field" }, "Agents", h("select", { name: "agents" }, INFO.allowed_agents.map((a) => h("option", { value: a, selected: a === INFO.default_agents }, a === "stub" ? "stub (canned fixture, offline)" : a)))),
      h("label", { class: "field" }, "Target duration (s)", h("input", { type: "number", name: "target_duration_sec", value: d.target_duration_sec, min: 1, step: 1 })),
      h("label", { class: "field" }, "FPS", h("input", { type: "number", name: "fps", value: d.fps, min: 1 })),
      h("label", { class: "field" }, "Aspect ratio", h("input", { type: "text", name: "aspect_ratio", value: d.aspect_ratio })),
      h("label", { class: "field" }, "Audience", h("input", { type: "text", name: "audience", value: d.audience })),
    ),
    h("h3", {}, "Story"),
    h("textarea", { name: "story_text", rows: 12, required: true, placeholder: "Paste the story text…" }),
    h("div", { class: "row small" }, h("span", { class: "muted" }, "or load a .txt file:"),
      h("input", { type: "file", accept: ".txt,.md,text/plain", onchange: async (e) => {
        const f = e.target.files[0];
        if (f) form.story_text.value = await f.text();
      } })),
    h("h3", {}, "Visual style"),
    h("input", { type: "text", name: "visual_style", value: d.visual_style, style: "width:100%" }),
    h("h3", {}, "Approval gates"),
    h("div", { class: "row" },
      ["all", "selected", "none"].map((m) => h("label", { class: "check" }, h("input", { type: "radio", name: "gate_mode", value: m, checked: m === d.human_approval.mode, onchange: syncGates }),
        { all: "Every agent", selected: "Selected agents", none: "No gates (run straight through)" }[m]))),
    h("div", { class: "row", id: "gate-agents", style: "margin-top:8px" },
      INFO.agent_order.map((a) => h("label", { class: "check" }, h("input", { type: "checkbox", name: "gate_agent", value: a, checked: true }), label(a)))),
    h("details", { style: "margin-top:16px" }, h("summary", {}, "Flags"),
      h("div", { class: "form-grid", style: "margin-top:12px" }, Object.entries(flagsProps).map(([k, p]) => flagField(k, p, d.flags[k])))),
    h("div", { id: "form-error" }),
    h("div", { class: "row", style: "margin-top:18px" }, h("button", { class: "primary", type: "submit" }, "Start run"), h("a", { href: "#/" }, "Cancel")),
  );
  function syncGates() {
    const sel = form.querySelector("input[name=gate_mode]:checked").value === "selected";
    form.querySelectorAll("input[name=gate_agent]").forEach((c) => (c.disabled = !sel));
  }
  async function submit(e) {
    e.preventDefault();
    const flags = {};
    form.querySelectorAll("[data-flag]").forEach((el) => {
      flags[el.dataset.flag] = el.type === "checkbox" ? el.checked : el.dataset.num ? Number(el.value) : el.value;
    });
    form.querySelectorAll("[data-flag-list]").forEach((el) => {
      (flags[el.dataset.flagList] ||= []);
      if (el.checked) flags[el.dataset.flagList].push(el.value);
    });
    const mode = form.querySelector("input[name=gate_mode]:checked").value;
    const body = {
      name: form.name.value.trim(),
      agents: form.agents.value,
      story_text: form.story_text.value,
      config: {
        target_duration_sec: Number(form.target_duration_sec.value),
        fps: Number(form.fps.value),
        aspect_ratio: form.aspect_ratio.value,
        audience: form.audience.value,
        visual_style: form.visual_style.value,
        human_approval: { mode, agents: mode === "selected" ? [...form.querySelectorAll("input[name=gate_agent]:checked")].map((c) => c.value) : [] },
        flags,
      },
    };
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      await api("runs", { body });
      location.hash = `#/run/${encodeURIComponent(body.name)}`;
    } catch (err) {
      put(form.querySelector("#form-error"), h("div", { class: "panel err", style: "margin-top:14px" }, h("strong", {}, err.message), errorDetails(err)));
      btn.disabled = false;
    }
  }
  put(app, form);
  syncGates();
}

// ---------------------------------------------------------------- one run
async function runView(app, id, tab) {
  setNav("runs");
  const key = `run:${id}:${tab}`;
  view = { key, id, tab, gateKey: null, outSel: null };
  const shell = h("div", {},
    h("div", { class: "row spread", style: "margin-bottom:12px" }, h("div", { id: "run-head" }), h("div", { class: "row", id: "run-actions" })),
    h("nav", { class: "tabs" }, ["pipeline", "outputs", "package", "queue"].map((t) =>
      h("a", { href: `#/run/${encodeURIComponent(id)}/${t}`, class: t === tab ? "on" : "" }, { pipeline: "Pipeline", outputs: "Outputs", package: "Package", queue: "Build queue" }[t]))),
    h("div", { id: "tab" }));
  put(app, shell);
  const tabEl = shell.querySelector("#tab");

  const tick = async () => {
    if (view.key !== key) return;
    let run;
    try {
      run = await api(`runs/${encodeURIComponent(id)}`);
    } catch (err) {
      put(tabEl, h("div", { class: "panel err" }, err.message));
      return;
    }
    if (view.key !== key) return;
    renderHead(shell, run);
    if (tab === "pipeline") renderPipeline(tabEl, run);
    else if (!view.rendered) {
      view.rendered = true;
      if (tab === "outputs") renderOutputs(tabEl, run);
      if (tab === "package") renderPackage(tabEl, run);
      if (tab === "queue") renderQueue(tabEl, run);
    }
    const busy = run.active;
    pollTimer = setTimeout(tick, busy ? POLL_MS : POLL_MS * 3);
  };
  await tick();
}

function renderHead(shell, run) {
  const status = run.active ? "running" : run.status;
  put(shell.querySelector("#run-head"), 
    h("h1", {}, run.title || run.id),
    h("div", { class: "row small" }, h("span", { class: "muted" }, run.id), badge(status),
      run.package_status ? badge(run.package_status) : null,
      h("span", { class: "muted" }, `agents: ${run.agents_used || "?"}`),
      run.state.needs_review ? h("span", { class: "muted" }, "· continuity errors remain") : null));
  const canResume = !run.active && ["interrupted", "failed", "new"].includes(run.status);
  put(shell.querySelector("#run-actions"), 
    canResume ? h("button", { class: "primary", onclick: async () => {
      try { await api(`runs/${encodeURIComponent(run.id)}/resume`, { body: {} }); toast("Resumed"); }
      catch (err) { toast(err.message, true); }
    } }, "Resume run") : null);
}

function renderPipeline(el, run) {
  // Rebuild everything except an open gate the user may be typing into.
  const gateKey = run.awaiting && !run.active ? `${run.awaiting}:${run.state.attempts[run.awaiting]}` : null;
  let gate = el.querySelector("#gate");
  if (gateKey !== view.gateKey || !gate) {
    view.gateKey = gateKey;
    gate = gateKey ? gatePanel(run) : h("div", { id: "gate" });
  }
  const steps = h("div", { class: "panel" }, h("h2", {}, "Pipeline"),
    h("div", { class: "steps" }, run.steps.map((s) => h("div", { class: `step ${s.state}`, title: "Open output",
      onclick: () => { if (s.has_current || s.versions.length) location.hash = `#/run/${encodeURIComponent(run.id)}/outputs/${s.agent}`; } },
      h("div", { class: "name" }, label(s.agent)),
      h("div", { class: "small" }, badge(s.state)),
      h("div", { class: "small muted" }, `${s.attempts} attempt${s.attempts === 1 ? "" : "s"}${s.versions.length ? ` · v${s.versions.at(-1)}` : ""}${s.gated ? "" : " · no gate"}`)))),
    run.state.fix_round ? h("p", { class: "small muted" }, `Continuity fix rounds used: ${run.state.fix_round}`) : null);
  const err = run.last_error && !run.active ? h("div", { class: "panel err" }, h("h2", {}, run.last_error.agent ? `${label(run.last_error.agent)} failed` : "Run stopped with an error"),
    h("p", {}, run.last_error.message),
    run.last_error.issues.length ? h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Category"), h("th", {}, "Where"), h("th", {}, "Message"))),
      h("tbody", {}, run.last_error.issues.map((i) => h("tr", {}, h("td", {}, i.category), h("td", { class: "small" }, i.where || ""), h("td", {}, i.message)))))) : null,
    h("p", { class: "small muted" }, "Fix the config or the upstream output, then resume.")) : null;
  const logEl = h("pre", { class: "log" }, run.log.length ? run.log.join("\n") : "No log from this server session yet.");
  const approvals = h("div", { class: "panel" }, h("h2", {}, "Gate decisions"),
    run.approvals.length ? h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Agent"), h("th", {}, "Attempt"), h("th", {}, "Decision"), h("th", {}, "By"), h("th", {}, "Notes"), h("th", {}, "When"))),
      h("tbody", {}, run.approvals.slice().reverse().map((a) => h("tr", {}, h("td", {}, label(a.agent)), h("td", {}, a.attempt), h("td", {}, badge(a.decision)), h("td", {}, a.decided_by), h("td", { class: "small" }, a.notes), h("td", { class: "small muted" }, when(a.decided_at)))))))
      : h("p", { class: "muted small" }, "No decisions yet."));
  const prevLog = el.querySelector("pre.log");
  const stick = !prevLog || prevLog.scrollTop + prevLog.clientHeight >= prevLog.scrollHeight - 10;
  put(el, gate, err || "", steps, h("div", { class: "grid2" }, h("div", { class: "panel" }, h("h2", {}, "Log"), logEl), approvals));
  if (stick) logEl.scrollTop = logEl.scrollHeight;
  else logEl.scrollTop = prevLog.scrollTop;
}

function gatePanel(run) {
  const agent = run.awaiting;
  const payload = run.review;
  const base = `runs/${encodeURIComponent(run.id)}/gates/${agent}`;
  const notes = h("textarea", { rows: 4, placeholder: `What should the ${label(agent)} change?` });
  const edit = h("textarea", { class: "code", rows: 16, spellcheck: "false" }, JSON.stringify(payload, null, 2));
  const msg = h("div", { class: "small" });
  async function send(body, btn) {
    btn.disabled = true;
    msg.textContent = "";
    try {
      await api(base, { body });
      toast(`${body.decision} recorded for ${label(agent)}`);
      view.gateKey = "sent";
    } catch (err) {
      put(msg, h("strong", { style: "color:var(--bad)" }, err.message), errorDetails(err));
      btn.disabled = false;
    }
  }
  return h("div", { class: "panel gate", id: "gate" },
    h("h2", {}, `${label(agent)} is waiting for your review`),
    h("p", { class: "small muted" }, "The output passed the hard checks. Read it below, then approve it, send it back with notes, or correct the JSON yourself."),
    payload ? tree(payload, 1) : h("p", { class: "muted" }, "No review file found."),
    h("div", { class: "gate-actions" },
      h("div", {}, h("strong", {}, "Approve"), h("span", { class: "small muted" }, "Store this output and continue with the next agent."),
        h("button", { class: "primary", onclick: (e) => send({ decision: "approve" }, e.target) }, "Approve")),
      h("div", {}, h("strong", {}, "Revise"), h("span", { class: "small muted" }, "The agent runs again with your notes."), notes,
        h("button", { onclick: (e) => send({ decision: "revise", notes: notes.value }, e.target) }, "Send back")),
      h("div", {}, h("strong", {}, "Edit"), h("span", { class: "small muted" }, "Your JSON replaces the output; it is validated and checked."),
        h("details", {}, h("summary", {}, "Open the editor"), edit),
        h("button", { onclick: (e) => {
          let parsed;
          try { parsed = JSON.parse(edit.value); } catch (err) { msg.textContent = `JSON does not parse: ${err.message}`; return; }
          send({ decision: "edit", edited_payload: parsed, notes: "edited in the GUI" }, e.target);
        } }, "Save edit"))),
    msg);
}

async function renderOutputs(el, run) {
  const want = location.hash.split("/")[4];
  const agents = run.steps.filter((s) => s.has_current || s.versions.length);
  if (!agents.length) {
    put(el, h("div", { class: "panel empty" }, "No agent output yet."));
    return;
  }
  let cur = agents.find((s) => s.agent === want) || agents[0];
  const agentSel = h("select", { onchange: () => { cur = agents.find((s) => s.agent === agentSel.value); fillVersions(); load(); } },
    agents.map((s) => h("option", { value: s.agent, selected: s === cur }, label(s.agent))));
  const verSel = h("select", { onchange: load });
  const body = h("div", {});
  const raw = h("a", { target: "_blank" }, "raw JSON");
  function fillVersions() {
    put(verSel, h("option", { value: "" }, "current"), cur.versions.slice().reverse().map((v) => h("option", { value: v }, `v${v}`)));
  }
  async function load() {
    const v = verSel.value;
    const path = `runs/${encodeURIComponent(run.id)}/outputs/${cur.agent}${v ? `?version=${v}` : ""}`;
    raw.href = `/api/${path}`;
    put(body, h("p", { class: "muted" }, "Loading…"));
    try {
      const data = await api(path);
      put(body, summaryFor(cur.agent, data), tree(data, 1));
    } catch (err) {
      put(body, h("p", { class: "muted" }, err.message));
    }
  }
  fillVersions();
  put(el, h("div", { class: "panel" }, h("div", { class: "row", style: "margin-bottom:14px" }, agentSel, verSel, raw), body));
  load();
}

function list(items, fn) {
  return h("ul", { class: "small" }, (items || []).map((x) => h("li", {}, fn(x))));
}

function summaryFor(agent, d) {
  const box = (...kids) => h("div", { style: "margin-bottom:14px" }, ...kids);
  try {
    if (agent === "story_analyst")
      return box(h("h2", {}, d.title || ""), h("p", {}, d.logline || ""), h("p", { class: "small muted" }, d.synopsis || ""),
        list(d.beats, (b) => `${b.id}: ${b.summary || b.description || b.title || ""}`));
    if (agent === "character_designer")
      return box(list(d.characters, (c) => [h("strong", {}, c.name), ` (${c.id}) ${c.role || ""} · asset ${c.asset ? c.asset.status : ""} ${c.asset && c.asset.library_id ? c.asset.library_id : ""}`]),
        d.build_requests && d.build_requests.length ? h("p", { class: "small" }, `${d.build_requests.length} build requests`) : null);
    if (agent === "scene_builder")
      return box(list(d.locations, (l) => [h("strong", {}, l.name), ` (${l.id}) · asset ${l.asset ? l.asset.status : ""}`]),
        list(d.scenes, (s) => `${s.id}: ${s.title || ""} · ${s.location_id} · ${s.time_of_day || ""}`));
    if (agent === "continuity_checker")
      return box(h("p", {}, badge(d.status), ` ${d.issues.length} issues`),
        list(d.issues, (i) => [badge(i.severity), ` [${i.category}] ${i.message} → ${i.owner_agent || ""}`]));
  } catch (_) { /* fall through to the tree */ }
  return h("span");
}

async function renderPackage(el, run) {
  const base = `/api/runs/${encodeURIComponent(run.id)}/files/`;
  if (!run.files.includes("production_package.json")) {
    put(el, h("div", { class: "panel empty" }, "The production package is written when the last agent is done."));
    return;
  }
  const pkg = await api(`runs/${encodeURIComponent(run.id)}/files/production_package.json`);
  const stats = pkg.stats || {};
  const exportsList = run.files.filter((f) => f.startsWith("exports/"));
  const viewer = h("div", {});
  const showFile = async (f) => {
    const text = await (await fetch(base + f)).text();
    put(viewer, h("h3", {}, f), h("pre", {}, text));
  };
  put(el, 
    h("div", { class: "panel" },
      h("div", { class: "row spread" }, h("div", {}, h("h2", {}, pkg.story.title), h("p", {}, pkg.story.logline)), badge(pkg.status)),
      h("div", { class: "tiles" }, Object.entries(stats).map(([k, v]) => h("div", { class: "tile" }, h("div", { class: "v" }, v), h("div", { class: "k" }, k.replace(/_/g, " "))))),
      h("p", { class: "small" }, h("a", { href: base + "production_package.json", target: "_blank" }, "production_package.json"))),
    h("div", { class: "panel" }, h("h2", {}, "Scenes and shots"),
      h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Scene"), h("th", {}, "Location"), h("th", {}, "Frames"), h("th", {}, "Cast"), h("th", {}, "Shots"))),
        h("tbody", {}, (pkg.scenes || []).map((s) => {
          const shots = (s.shots || []).map((x) => x.direction || x);
          return h("tr", {}, h("td", {}, h("strong", {}, s.title || s.id), h("div", { class: "small muted" }, s.summary || "")), h("td", {}, s.location_id, h("div", { class: "small muted" }, s.time_of_day || "")),
            h("td", { class: "small" }, `${s.frame_start}–${s.frame_end}`), h("td", { class: "small" }, (s.cast || []).map((c) => c.character_id || c).join(", ")),
            h("td", { class: "small" }, shots.map((sh) => h("div", { style: "margin-bottom:4px" },
              h("strong", {}, sh.id), ` ${sh.shot_type} · ${sh.camera_movement} · ${sh.duration_sec}s`,
              (sh.characters || []).filter((c) => c.action).map((c) => h("div", { class: "muted" }, `${c.character_id}: ${c.action}`)),
              (sh.dialogue || []).map((d) => h("div", {}, `${d.speaker_id}: \u201c${d.text}\u201d`))))));
        }))))),
    h("div", { class: "panel" }, h("h2", {}, "Exports"),
      exportsList.length ? h("div", { class: "row" }, exportsList.map((f) => h("button", { onclick: () => showFile(f) }, f.replace("exports/", "")))) : h("p", { class: "muted" }, "No exports (flags.human_exports is empty)."),
      viewer),
    pkg.continuity_report ? h("div", { class: "panel" }, h("h2", {}, "Continuity"), summaryFor("continuity_checker", pkg.continuity_report)) : null,
  );
  if (exportsList.length) showFile(exportsList[0]);
}

async function renderQueue(el, run) {
  const path = `runs/${encodeURIComponent(run.id)}/queue`;
  const draw = (q) => {
    if (!q.ready) {
      put(el, h("div", { class: "panel empty" }, q.reason));
      return;
    }
    const m = q.metrics;
    const tiles = ["opened", "closed_by_code", "closed_by_human", "open", "in_review", "automated_share", "screen_time_share_closed_by_code", "avg_rounds"];
    put(el, 
      h("div", { class: "panel" }, h("div", { class: "row spread" }, h("h2", {}, "Build requests"),
        h("div", { class: "row" }, q.saved ? h("span", { class: "small muted" }, "Saved to the run's outputs and assets/") : null,
          h("button", { disabled: run.active, title: "Write screen time and routes back into the outputs (studio build-requests --save)", onclick: async (e) => {
            e.target.disabled = true;
            try { draw(await api(path, { body: { save: true } })); toast("Routes saved"); } catch (err) { toast(err.message, true); e.target.disabled = false; }
          } }, "Save routes to outputs"))),
        h("div", { class: "tiles" }, tiles.map((k) => h("div", { class: "tile" }, h("div", { class: "v" }, m[k]), h("div", { class: "k" }, k.replace(/_/g, " "))))),
        h("div", { class: "row small", style: "margin-top:10px" },
          Object.entries(m.by_route).map(([k, v]) => h("span", { class: "chip" }, `${k}: ${v}`)),
          Object.entries(m.by_kind).map(([k, v]) => h("span", { class: "chip" }, `${k}: ${v}`)))),
      h("div", { class: "panel" }, q.queue.length ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Request"), h("th", {}, "Kind"), h("th", {}, "Frames"), h("th", {}, "Route"), h("th", {}, "Status"), h("th", {}, "Why"))),
        h("tbody", {}, q.queue.map((r, n) => h("tr", {}, h("td", {}, n + 1),
          h("td", {}, h("strong", {}, r.id), h("div", { class: "small muted" }, `for ${r.for_id}`), r.spec ? h("div", { class: "small" }, r.spec) : null),
          h("td", {}, r.kind), h("td", {}, r.screen_time_frames), h("td", {}, r.route || "–"),
          h("td", {}, badge(r.status), r.result_library_id ? h("div", { class: "small" }, r.result_library_id) : null),
          h("td", { class: "small" }, r.reason, r.duplicate_of ? h("div", { class: "muted" }, `duplicate of ${r.duplicate_of}`) : null,
            r.variant_of ? h("div", { class: "muted" }, `variant of ${r.variant_of}: ${r.variant_changes.join(", ")}`) : null)))))) :
        h("p", { class: "empty" }, "No build requests: everything this story needs is in the library.")));
  };
  put(el, h("p", { class: "muted" }, "Planning…"));
  try { draw(await api(path)); } catch (err) { put(el, h("div", { class: "panel err" }, err.message)); }
}

// ---------------------------------------------------------------- library
async function library(app) {
  setNav("library");
  view = { key: "library" };
  const lib = await api("library");
  if (!lib.found) {
    put(app, h("div", { class: "panel empty" }, `No manifest at ${lib.path}. Run python -m tools.scan_library first.`));
    return;
  }
  const m = lib.manifest;
  const sections = Object.entries(m).filter(([, v]) => Array.isArray(v) || (v && typeof v === "object"));
  const filter = h("input", { type: "text", placeholder: "Filter by id or tag…", oninput: () => draw() });
  const out = h("div", {});
  const fmt = (v) => (Array.isArray(v) ? (v.length && (typeof v[0] !== "object" || (v[0] && v[0].id)) ? h("div", { class: "chips" }, v.map((x) => h("span", { class: "chip" }, typeof x === "object" ? x.id : x))) : `${v.length} items`)
    : v && typeof v === "object" ? h("span", { class: "small muted" }, Object.keys(v).join(", ")) : String(v));
  function draw() {
    const q = filter.value.toLowerCase();
    put(out, ...sections.map(([name, entries]) => {
      const rows = Array.isArray(entries) ? entries.map((e) => (e && typeof e === "object" ? e : { id: String(e) })) : Object.entries(entries).map(([id, v]) => ({ id, ...(typeof v === "object" ? v : { value: v }) }));
      const shown = rows.filter((r) => !q || JSON.stringify(r).toLowerCase().includes(q));
      if (!shown.length) return h("span");
      const cols = [...new Set(shown.flatMap((r) => Object.keys(r)))].filter((c) => c !== "id").slice(0, 6);
      return h("div", { class: "panel" }, h("h2", {}, `${name.replace(/_/g, " ")} `, h("span", { class: "muted small" }, `(${shown.length})`)),
        h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "id"), cols.map((c) => h("th", {}, c.replace(/_/g, " "))))),
          h("tbody", {}, shown.map((r) => h("tr", {}, h("td", {}, h("strong", {}, r.id)), cols.map((c) => h("td", { class: "small" }, r[c] === undefined ? "" : fmt(r[c])))))))));
    }));
  }
  draw();
  put(app, 
    h("div", { class: "panel" }, h("div", { class: "row spread" }, h("div", {}, h("h2", {}, "Asset library"), h("p", { class: "small muted" }, `${lib.path} · version ${m.library_version || "?"} · ${m.units || ""}`)), filter)),
    lib.previews.length ? h("div", { class: "panel" }, h("h2", {}, "Turntables"), h("div", { class: "previews" }, lib.previews.map((p) =>
      h("figure", {}, h("a", { href: `/api/library/preview/${p}`, target: "_blank" }, h("img", { src: `/api/library/preview/${p}`, loading: "lazy", alt: p })), h("figcaption", {}, p))))) : null,
    lib.generator_scripts ? h("div", { class: "panel" }, h("h2", {}, "Accepted generator scripts"), tree(lib.generator_scripts, 1)) : null,
    out);
}
