import { polygonArea } from "./geometry.js";

// What-if studies in the browser. The computation is the Python engine itself (roomtobreathe.whatif):
// on a control-room server it runs there (POST /api/whatif); on a plain web host it runs in this browser
// through Pyodide in a background worker. One source of truth, no JavaScript re-implementation to drift.

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem("rtb:" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("rtb:" + k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const LEVEL_COLORS = { GREEN: "var(--green)", YELLOW: "var(--yellow)", ORANGE: "var(--orange)", RED: "var(--red)", UNKNOWN: "var(--unknown)" };
const SERIES = ["#2456d6", "#e36b0a", "#2f9e5b", "#9b3fd1", "#d42a2a", "#0e8c8c", "#a8730a", "#5d6673"];

let demoVenue = null;
let serverVenue = null;
let useServer = false;
let worker = null;
let pending = null;
let last = null;

const slug = (name, taken) => {
  const base = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "zone";
  let s = base, i = 2;
  while (taken.has(s) || s === "outside") s = `${base}_${i++}`;
  return s;
};
export function parseArea(t) {
  const m = String(t).toLowerCase().replace(/m2|m²|m/g, "").trim().match(/^([\d.]+)\s*[x*×]\s*([\d.]+)$/);
  const v = m ? Number(m[1]) * Number(m[2]) : Number(t);
  return Number.isFinite(v) && v > 0 ? v : null;
}

// ------------------------------------------------------------------ venue builder
const blankVenue = () => ({
  name: "", zones: [{ name: "Main ground", area: "60 x 40", usable: 80 }],
  gates: [{ name: "Main gate", from: "outside", to: 0, width: 6, stairs: false, closed: false },
          { name: "Exit", from: 0, to: "outside", width: 4, stairs: false, closed: false }],
});
let draft = store.get("wi-draft", blankVenue());

function zoneOptions(sel) {
  const opts = [`<option value="outside"${sel === "outside" ? " selected" : ""}>Outside</option>`];
  draft.zones.forEach((z, i) => opts.push(`<option value="${i}"${String(sel) === String(i) ? " selected" : ""}>${esc(z.name || `Zone ${i + 1}`)}</option>`));
  return opts.join("");
}
function renderBuilder() {
  $("#wi-name").value = draft.name;
  $("#wi-zones").innerHTML = draft.zones.map((z, i) => `
    <div class="vrow" data-i="${i}">
      <input data-k="name" value="${esc(z.name)}" placeholder="Zone name" aria-label="Zone name">
      <input data-k="area" value="${esc(z.area)}" placeholder="Area: 960 or 40 x 24" aria-label="Area in square metres">
      <label class="small">usable % <input data-k="usable" type="number" min="10" max="100" value="${esc(z.usable)}"></label>
      <button type="button" class="link" data-del-zone="${i}" aria-label="Remove zone">✕</button>
    </div>`).join("");
  $("#wi-gates").innerHTML = draft.gates.map((g, i) => `
    <div class="vrow gate" data-i="${i}">
      <input data-k="name" value="${esc(g.name)}" placeholder="Gate name" aria-label="Gate name">
      <select data-k="from" aria-label="From">${zoneOptions(g.from)}</select>
      <span aria-hidden="true">→</span>
      <select data-k="to" aria-label="To">${zoneOptions(g.to)}</select>
      <label class="small">width m <input data-k="width" type="number" min="0.5" step="0.5" value="${esc(g.width)}"></label>
      <label class="small"><input data-k="stairs" type="checkbox"${g.stairs ? " checked" : ""}> steps</label>
      <label class="small"><input data-k="closed" type="checkbox"${g.closed ? " checked" : ""}> normally closed</label>
      <button type="button" class="link" data-del-gate="${i}" aria-label="Remove gate">✕</button>
    </div>`).join("");
}
function draftToVenue() {
  const problems = [], ids = [], taken = new Set();
  const zones = draft.zones.map((z, i) => {
    const area = parseArea(z.area);
    if (!z.name.trim()) problems.push(`Zone ${i + 1} needs a name.`);
    if (!area) problems.push(`Zone "${z.name || i + 1}" needs an area, e.g. 960 or 40 x 24.`);
    const id = slug(z.name || `zone ${i + 1}`, taken); taken.add(id); ids.push(id);
    return { id, name: z.name || id, area_m2: area || 0, usable_fraction: Math.min(1, Math.max(0.1, Number(z.usable || 100) / 100)) };
  });
  const ref = (v) => (v === "outside" ? "outside" : ids[Number(v)]);
  const gates = draft.gates.map((g, i) => {
    if (ref(g.from) === ref(g.to)) problems.push(`Gate "${g.name || i + 1}" must join two different places.`);
    if (!(Number(g.width) > 0)) problems.push(`Gate "${g.name || i + 1}" needs a width.`);
    const d = { id: `G${i + 1}`, name: g.name || `Gate ${i + 1}`, from: ref(g.from), to: ref(g.to), width_m: Number(g.width) || 0 };
    if (g.stairs) d.stairs = true;
    if (g.closed) d.open = false;
    return d;
  });
  if (!zones.length) problems.push("Add at least one zone.");
  const name = draft.name.trim() || "My venue";
  return { venue: { id: slug(name, new Set()), name, zones, gates }, problems };
}
function onBuilderInput(e) {
  const row = e.target.closest(".vrow");
  if (!row) return;
  const list = row.classList.contains("gate") ? draft.gates : draft.zones;
  const item = list[Number(row.dataset.i)];
  const k = e.target.dataset.k;
  item[k] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
  store.set("wi-draft", draft);
  if (k === "name" && !row.classList.contains("gate")) renderBuilder(); // zone names appear in gate menus
  refreshVenue();
}

// ------------------------------------------------------------------ current venue
let fileVenue = store.get("wi-file-venue", null);
function currentVenue() {
  const src = $("#wi-src").value;
  if (src === "custom") return draftToVenue();
  if (src === "file") return { venue: fileVenue, problems: fileVenue ? [] : ["Choose a venue file."] };
  return { venue: serverVenue || demoVenue, problems: [] };
}
function refreshVenue() {
  const { venue, problems } = currentVenue();
  const out = $("#wi-venue-out");
  const sel = $('#wi-form [name="blocked_gate"]');
  const keep = sel.value;
  sel.innerHTML = `<option value="">no gate blocked</option>` + (venue?.gates || []).map((g) => `<option value="${esc(g.id)}">${esc(g.id)} · ${esc(g.name)} (${g.width_m} m)</option>`).join("");
  sel.value = (venue?.gates || []).some((g) => g.id === keep) ? keep : "";
  if (!venue) { out.innerHTML = problems.map((p) => `<p class="err">${esc(p)}</p>`).join(""); return; }
  const zoneArea = (z) => z.usable_area_m2
    ?? (z.area_m2 ?? (z.polygon?.length ? polygonArea(z.polygon) : 0)) * (z.usable_fraction ?? 1);
  const area = venue.zones.reduce((s, z) => s + zoneArea(z), 0);
  out.innerHTML = problems.map((p) => `<p class="err">${esc(p)}</p>`).join("")
    + `<p><b>${esc(venue.name)}</b>: ${plural(venue.zones.length, "zone")}, ${plural(venue.gates.length, "gate")}, ${Math.round(area).toLocaleString()} m² usable.</p>`;
}

// ------------------------------------------------------------------ engine
function engineWorker() {
  if (!worker) {
    worker = new Worker(new URL("engine-worker.js", import.meta.url));
    worker.onmessage = (e) => {
      if (e.data.status) { $("#wi-status").textContent = e.data.status; return; }
      const p = pending; pending = null;
      if (!p) return;
      e.data.error ? p.reject(new Error(e.data.error)) : p.resolve(e.data.result);
    };
    worker.onerror = (e) => { if (pending) { pending.reject(new Error(e.message || "engine failed to start")); pending = null; } };
  }
  return worker;
}
async function runEngine(venue, params) {
  if (useServer) {
    const r = await fetch("api/whatif", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ venue, params }) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  }
  if (pending) throw new Error("a what-if is already running");
  return new Promise((resolve, reject) => {
    pending = { resolve, reject };
    engineWorker().postMessage({ venue, params });
  });
}

function readParams() {
  const f = new FormData($("#wi-form"));
  const num = (k) => (f.get(k) === "" || f.get(k) === null ? null : Number(f.get(k)));
  return {
    attendance: num("attendance"), arrival_minutes: num("arrival_minutes"), minutes: num("minutes"),
    surge_at_min: num("surge_at_min"), surge_multiplier: num("surge_multiplier"),
    blocked_gate: f.get("blocked_gate") || null, blocked_at_min: num("blocked_at_min"),
    blocked_to: num("blocked_to_pct") === null ? null : num("blocked_to_pct") / 100,
    evacuate_at_min: num("evacuate_at_min"),
  };
}

// ------------------------------------------------------------------ results
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const fmt = (v, d = 1) => (v === null || v === undefined ? "–" : Number(v).toFixed(d).replace(/\.0+$/, ""));
function chart(run, venue, title) {
  const W = 640, H = 240, L = 34, B = 22, T = 10, R = 8;
  const t = run.series.t_min, tmax = Math.max(1, t[t.length - 1] || 1);
  const zones = venue.zones.map((z) => z.id);
  const ymax = Math.max(8, ...zones.flatMap((z) => run.series[z] || [0]));
  const X = (v) => L + (v / tmax) * (W - L - R), Y = (v) => T + (1 - v / ymax) * (H - T - B);
  const bands = [[2, 4, "var(--yellow)"], [4, 5.5, "var(--orange)"], [5.5, ymax, "var(--red)"]]
    .map(([a, b, c]) => `<rect x="${L}" y="${Y(b)}" width="${W - L - R}" height="${Y(a) - Y(b)}" fill="${c}" opacity=".10"/>`).join("");
  const crit = `<line x1="${L}" x2="${W - R}" y1="${Y(6.5)}" y2="${Y(6.5)}" stroke="var(--red)" stroke-dasharray="4 3"/><text x="${W - R - 2}" y="${Y(6.5) - 3}" text-anchor="end" class="ax">critical 6.5</text>`;
  const ticksY = [0, 2, 4, 6, 8].filter((v) => v <= ymax).map((v) => `<text x="${L - 4}" y="${Y(v) + 3}" text-anchor="end" class="ax">${v}</text>`).join("");
  const step = tmax > 120 ? 30 : tmax > 40 ? 10 : 5;
  const ticksX = Array.from({ length: Math.floor(tmax / step) + 1 }, (_, i) => i * step).map((v) => `<text x="${X(v)}" y="${H - 6}" text-anchor="middle" class="ax">${v}</text>`).join("");
  const lines = zones.map((z, i) => {
    const pts = (run.series[z] || []).map((v, k) => `${X(t[k]).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
    return `<polyline points="${pts}" fill="none" stroke="${SERIES[i % SERIES.length]}" stroke-width="2"/>`;
  }).join("");
  const marks = run.actions.length ? `<line x1="${X(run.actions[0].t_min)}" x2="${X(run.actions[0].t_min)}" y1="${T}" y2="${H - B}" stroke="var(--ink)" stroke-dasharray="2 3"/><text x="${X(run.actions[0].t_min) + 3}" y="${T + 9}" class="ax">response</text>` : "";
  const legend = zones.map((z, i) => `<span><i style="background:${SERIES[i % SERIES.length]}"></i>${esc(venue.zones[i].name)}</span>`).join("");
  return `<figure class="wi-chart"><figcaption>${esc(title)} <span class="muted small">people per m², by minute</span></figcaption>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title)}: density over time">${bands}${crit}${ticksY}${ticksX}${lines}${marks}</svg>
    <div class="legend small">${legend}</div></figure>`;
}
function table(r) {
  const row = (s, b) => `<tr><td>${esc(s.zone_name)}</td>
    <td class="num" style="color:${s.peak_density >= 6.5 ? "var(--red)" : "inherit"}">${fmt(s.peak_density, 1)}</td>
    <td class="num">${fmt(s.first_orange_min)}</td><td class="num">${fmt(s.first_red_min)}</td>
    <td class="num"><b>${fmt(s.critical_at_min)}</b></td><td class="num">${fmt(s.warning_lead_min)}</td>
    <td class="num">${fmt(b.peak_density, 1)}</td></tr>`;
  const rows = r.no_response.summary.map((s, i) => row(s, r.with_response.summary[i])).join("");
  return `<div class="scroll"><table class="wi-table"><thead><tr><th rowspan="2">Zone</th><th colspan="5">Nobody acts</th><th>With playbook</th></tr>
    <tr><th>peak p/m²</th><th>ORANGE at min</th><th>RED at min</th><th>critical at min</th><th>warning, min</th><th>peak p/m²</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}
function renderResults(r, venue) {
  last = { r, venue };
  const verdictLevel = r.with_response.reached_critical.length ? "RED" : r.no_response.reached_critical.length ? "ORANGE" : "GREEN";
  const cap = `Safe capacity ${r.safe_capacity.toLocaleString()} (limited by ${esc(r.limiting_factor)}); simulated ${Number(r.params.attendance).toLocaleString()} people.`;
  const zname = Object.fromEntries(venue.zones.map((z) => [z.id, z.name]));
  const actions = r.with_response.actions.map((a) => `<li>min ${fmt(a.t_min)} · ${esc(zname[a.zone] || a.zone)}: <b>${esc(a.result)}</b>${a.action.gate_name ? ` (${esc(a.action.gate_name)})` : ""}${a.action.note ? ` — ${esc(a.action.note)}` : ""}</li>`).join("");
  const alerts = r.no_response.alerts.filter((a) => a.level !== "GREEN" && a.level !== "UNKNOWN").slice(0, 12)
    .map((a) => `<li><span class="dot" style="background:${LEVEL_COLORS[a.level]}"></span>min ${fmt(a.t_min)} · <b>${esc(a.zone_name)}</b> → ${esc(a.level)}: ${esc(a.reasons.join("; "))}</li>`).join("");
  $("#wi-out").innerHTML = `
    <div class="verdict" style="--lv:${LEVEL_COLORS[verdictLevel]}">${r.conclusions.map(esc).join("<br>")}</div>
    <p class="small muted">${cap}</p>
    ${r.findings.length ? `<ul class="factors">${r.findings.map((f) => `<li>⚠ ${esc(f)}</li>`).join("")}</ul>` : ""}
    <div class="grid plan-grid">${chart(r.no_response, venue, "Nobody acts")}${chart(r.with_response, venue, "Operators approve the playbook at the first RED alert")}</div>
    ${table(r)}
    <div class="grid plan-grid">
      <div><h3>Alerts operators would see (nobody acts)</h3><ul class="log small">${alerts || "<li>No warnings.</li>"}</ul></div>
      <div><h3>Actions taken in the response run</h3><ul class="log small">${actions || "<li>None needed.</li>"}</ul></div>
    </div>
    <p class="small muted">A macroscopic model (people per zone, flow limited by gate width): it shows where and roughly when,
      not exactly. Rehearse your real response and time it.</p>`;
  $("#wi-results").hidden = false;
}

async function run(e) {
  e.preventDefault();
  const { venue, problems } = currentVenue();
  if (problems.length || !venue) { $("#wi-status").textContent = problems[0] || "Choose a venue."; return; }
  const params = readParams();
  $("#wi-run").disabled = true;
  const t0 = performance.now();
  $("#wi-status").textContent = useServer ? "Running on the control-room server…" : "Starting the engine in your browser (first time: about 10 MB)…";
  try {
    const r = await runEngine(venue, params);
    renderResults(r, venue);
    $("#wi-status").textContent = `Done in ${((performance.now() - t0) / 1000).toFixed(1)} s.`;
    $("#wi-results").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    $("#wi-status").textContent = `Could not run: ${err.message}`;
  } finally {
    $("#wi-run").disabled = false;
  }
}

export async function initWhatIf({ online }) {
  useServer = online;
  if (online) serverVenue = await fetch("api/venue").then((r) => r.json()).catch(() => null);
  demoVenue = await fetch(new URL("../data/pandal.json", import.meta.url)).then((r) => r.json()).catch(() => null);
  if (serverVenue) $('#wi-src option[value="demo"]').textContent = `This control room: ${serverVenue.name}`;
  $("#wi-src").value = store.get("wi-src", "demo");
  const showSrc = () => {
    $("#wi-builder").hidden = $("#wi-src").value !== "custom";
    store.set("wi-src", $("#wi-src").value);
    if ($("#wi-src").value === "file" && !fileVenue) $("#wi-file").click();
    refreshVenue();
  };
  $("#wi-src").addEventListener("change", showSrc);
  $("#wi-file").addEventListener("change", async () => {
    const f = $("#wi-file").files[0];
    if (!f) return;
    try { fileVenue = JSON.parse(await f.text()); store.set("wi-file-venue", fileVenue); } catch { fileVenue = null; $("#wi-venue-out").innerHTML = `<p class="err">That is not a venue file.</p>`; return; }
    refreshVenue();
  });
  $("#wi-name").addEventListener("input", (e) => { draft.name = e.target.value; store.set("wi-draft", draft); refreshVenue(); });
  $("#wi-zones").addEventListener("change", onBuilderInput);
  $("#wi-gates").addEventListener("change", onBuilderInput);
  $("#wi-zones").addEventListener("click", (e) => {
    const b = e.target.closest("[data-del-zone]");
    if (!b) return;
    const i = Number(b.dataset.delZone);
    draft.zones.splice(i, 1);
    draft.gates = draft.gates.filter((g) => g.from !== i && g.to !== i && String(g.from) !== String(i) && String(g.to) !== String(i))
      .map((g) => ({ ...g, from: g.from !== "outside" && Number(g.from) > i ? Number(g.from) - 1 : g.from, to: g.to !== "outside" && Number(g.to) > i ? Number(g.to) - 1 : g.to }));
    store.set("wi-draft", draft); renderBuilder(); refreshVenue();
  });
  $("#wi-gates").addEventListener("click", (e) => {
    const b = e.target.closest("[data-del-gate]");
    if (!b) return;
    draft.gates.splice(Number(b.dataset.delGate), 1);
    store.set("wi-draft", draft); renderBuilder(); refreshVenue();
  });
  $("#wi-add-zone").addEventListener("click", () => { draft.zones.push({ name: "", area: "", usable: 100 }); store.set("wi-draft", draft); renderBuilder(); });
  $("#wi-add-gate").addEventListener("click", () => { draft.gates.push({ name: "", from: "outside", to: 0, width: 3, stairs: false, closed: false }); store.set("wi-draft", draft); renderBuilder(); refreshVenue(); });
  $("#wi-download").addEventListener("click", () => {
    const { venue } = currentVenue();
    if (!venue) return;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(venue, null, 2)], { type: "application/json" }));
    a.download = `${venue.id || "venue"}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  });
  $("#wi-form").addEventListener("submit", run);
  renderBuilder();
  showSrc();
}
