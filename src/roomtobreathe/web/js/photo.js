// Count a crowd in a photo with the DM-Count model, entirely in this browser (onnxruntime-web).
// Same preprocessing as vision/density.py: width 1024, height rounded to /16, ImageNet normalisation.
// The model's density map sums to the head count; with a marked ground rectangle it becomes people per m².
import { densityInArea, levelFor } from "./geometry.js";

const $ = (s) => document.querySelector(s);
const ORT = "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.20.1/dist/";
const MODEL = "models/dmcount_qnrf.onnx";
const MODEL_MB = 86;
const INPUT_W = 1024;
const MEAN = [0.485, 0.456, 0.406], STD = [0.229, 0.224, 0.225];

let img = null;        // the loaded photo
let session = null;    // onnxruntime session (created once)
let result = null;     // { dm, mw, mh, count }
let corners = [];      // clicked ground corners in photo pixels
let marking = false;

function loadScript(src) {
  return new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = src; s.onload = ok; s.onerror = () => fail(new Error(`could not load ${src}`));
    document.head.appendChild(s);
  });
}

async function modelBytes(status) {
  const url = new URL(MODEL, document.baseURI).href;
  let cache = null;
  try { cache = await caches.open("rtb-models-v1"); } catch { /* no Cache API (private mode) */ }
  const hit = cache && await cache.match(url);
  if (hit) return new Uint8Array(await hit.arrayBuffer());
  const r = await fetch(url);
  if (!r.ok) throw new Error(`the counting model is not available here (${r.status}). On a control-room server, run  roomtobreathe model fetch`);
  const total = Number(r.headers.get("content-length")) || MODEL_MB * 1e6;
  const reader = r.body.getReader(), parts = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parts.push(value); got += value.length;
    status(`Downloading the counting model, once: ${Math.round((100 * got) / total)}% of ${MODEL_MB} MB`);
  }
  const bytes = new Uint8Array(got);
  let o = 0;
  for (const p of parts) { bytes.set(p, o); o += p.length; }
  if (cache) cache.put(url, new Response(bytes)).catch(() => {});
  return bytes;
}

async function getSession(status) {
  if (session) return session;
  if (!window.ort) await loadScript(ORT + "ort.webgpu.min.js");
  const ort = window.ort;
  ort.env.wasm.wasmPaths = ORT;
  ort.env.wasm.numThreads = self.crossOriginIsolated ? Math.min(4, navigator.hardwareConcurrency || 1) : 1;
  const bytes = await modelBytes(status);
  status("Preparing the model…");
  const providers = "gpu" in navigator ? ["webgpu", "wasm"] : ["wasm"];
  try {
    session = await ort.InferenceSession.create(bytes, { executionProviders: providers });
  } catch {
    session = await ort.InferenceSession.create(bytes, { executionProviders: ["wasm"] });
  }
  return session;
}

function draw() {
  const c = $("#ph-canvas");
  if (!img) return;
  c.width = img.naturalWidth; c.height = img.naturalHeight;
  const g = c.getContext("2d");
  g.drawImage(img, 0, 0);
  if (result && $("#ph-heat").checked) {
    const { dm, mw, mh } = result;
    let max = 0;
    for (const v of dm) max = Math.max(max, v);
    const heat = document.createElement("canvas");
    heat.width = mw; heat.height = mh;
    const hg = heat.getContext("2d"), id = hg.createImageData(mw, mh);
    for (let k = 0; k < mw * mh; k++) {
      const t = max > 0 ? Math.min(1, dm[k] / max) : 0;
      // transparent where empty, then yellow -> red as people pack together
      id.data[k * 4] = 255; id.data[k * 4 + 1] = Math.round(220 * (1 - t)); id.data[k * 4 + 2] = 0;
      id.data[k * 4 + 3] = Math.round(200 * Math.sqrt(t));
    }
    hg.putImageData(id, 0, 0);
    g.imageSmoothingEnabled = true;
    g.drawImage(heat, 0, 0, c.width, c.height);
  }
  if (corners.length) {
    const lw = Math.max(2, c.width / 400);
    g.lineWidth = lw; g.strokeStyle = "#fff"; g.fillStyle = "rgba(36,86,214,.18)";
    g.beginPath();
    corners.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
    if (corners.length === 4) { g.closePath(); g.fill(); }
    g.stroke();
    corners.forEach(([x, y], i) => {
      g.beginPath(); g.arc(x, y, lw * 4, 0, 2 * Math.PI); g.fillStyle = "#2456d6"; g.fill(); g.stroke();
      g.fillStyle = "#fff"; g.font = `${lw * 6}px system-ui`; g.fillText(String(i + 1), x + lw * 5, y - lw * 5);
    });
  }
}

async function count() {
  const status = (t) => { $("#ph-status").textContent = t; };
  $("#ph-count").disabled = true;
  try {
    const s = await getSession(status);
    status("Counting…");
    const iw = INPUT_W, ih = Math.max(16, Math.round((img.naturalHeight * iw) / img.naturalWidth / 16) * 16);
    const cv = document.createElement("canvas");
    cv.width = iw; cv.height = ih;
    cv.getContext("2d").drawImage(img, 0, 0, iw, ih);
    const px = cv.getContext("2d").getImageData(0, 0, iw, ih).data;
    const x = new Float32Array(3 * iw * ih);
    for (let k = 0; k < iw * ih; k++) {
      for (let ch = 0; ch < 3; ch++) x[ch * iw * ih + k] = (px[k * 4 + ch] / 255 - MEAN[ch]) / STD[ch];
    }
    const t0 = performance.now();
    const out = await s.run({ [s.inputNames[0]]: new window.ort.Tensor("float32", x, [1, 3, ih, iw]) });
    const d = out[s.outputNames[0]];
    const [, , mh, mw] = d.dims;
    const dm = Float32Array.from(d.data);
    let n = 0;
    for (const v of dm) n += v;
    result = { dm, mw, mh, count: n };
    status(`Counted in ${((performance.now() - t0) / 1000).toFixed(1)} s, on this device.`);
    $("#ph-out").innerHTML = `<div class="kpis"><div class="kpi hl"><div class="small muted">People in the photo</div><div class="v">≈ ${Math.round(n).toLocaleString()}</div></div></div>
      <p class="small muted">Model: DM-Count (MIT). On public test photos its error is about 12-16% on average, and it
        <b>undercounts the densest crowds</b>. To know whether the crowd is dangerous, mark an area of known size →</p>`;
    $("#ph-mark").disabled = false;
    draw();
    measure();
  } catch (err) {
    status(`Could not count: ${err.message}`);
  } finally {
    $("#ph-count").disabled = false;
  }
}

function measure() {
  const out = $("#ph-density");
  if (!result || corners.length !== 4) { out.innerHTML = ""; $("#ph-calib").disabled = true; return; }
  const W = Number($("#ph-w").value), D = Number($("#ph-d").value);
  if (!(W > 0 && D > 0)) { out.innerHTML = `<p class="err">Type the width and depth in metres.</p>`; return; }
  try {
    const r = densityInArea(result.dm, result.mw, result.mh, img.naturalWidth, img.naturalHeight, corners, W, D);
    const lv = levelFor(r.density), hot = r.busiest_density !== null ? levelFor(r.busiest_density) : null;
    const worst = hot && hot.at > lv.at ? hot : lv;
    const col = { GREEN: "var(--green)", YELLOW: "var(--yellow)", ORANGE: "var(--orange)", RED: "var(--red)" };
    out.innerHTML = `<div class="verdict" style="--lv:${col[worst.name]}">${worst.name}: ${worst.say}.</div>
      <div class="kpis">
        <div class="kpi"><div class="small muted">People in the marked area</div><div class="v">≈ ${Math.round(r.count)}</div></div>
        <div class="kpi"><div class="small muted">Average, people/m²</div><div class="v">${r.density.toFixed(1)}</div></div>
        <div class="kpi hl"><div class="small muted">Densest square metre</div><div class="v">${r.busiest_density === null ? "–" : r.busiest_density.toFixed(1)}</div></div>
      </div>
      <p class="small muted">${W} × ${D} m = ${r.area_m2} m². Danger starts around 4-5 people/m² in a moving crowd; at 6-7
        people can no longer move by themselves. Remember the model undercounts dense crowds, so treat these as minimums.</p>`;
    $("#ph-calib").disabled = false;
  } catch (err) {
    out.innerHTML = `<p class="err">${err.message}. Click the corners going round the rectangle.</p>`;
  }
}

function canvasPoint(e) {
  const c = $("#ph-canvas"), b = c.getBoundingClientRect();
  return [((e.clientX - b.left) / b.width) * c.width, ((e.clientY - b.top) / b.height) * c.height];
}

function downloadCalibration() {
  const W = Number($("#ph-w").value), D = Number($("#ph-d").value);
  const pts = corners.map(([x, y]) => [Math.round(x), Math.round(y)]);
  const cal = {
    camera_id: $("#ph-cam").value.trim() || "cam-1",
    image_size: [img.naturalWidth, img.naturalHeight],
    image_points: pts,
    ground_points_m: [[0, 0], [W, 0], [W, D], [0, D]],
    zones: [{ zone_id: $("#ph-zone").value.trim() || "zone", image_polygon: pts }],
  };
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(cal, null, 2)], { type: "application/json" }));
  a.download = `${cal.camera_id}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}

export function initPhoto() {
  $("#ph-file").addEventListener("change", () => {
    const f = $("#ph-file").files[0];
    if (!f) return;
    const im = new Image();
    im.onload = () => {
      img = im; result = null; corners = [];
      $("#ph-count").disabled = false; $("#ph-mark").disabled = true; $("#ph-calib").disabled = true;
      $("#ph-out").innerHTML = ""; $("#ph-density").innerHTML = ""; $("#ph-status").textContent = "";
      draw();
    };
    im.src = URL.createObjectURL(f); // stays in this browser
  });
  $("#ph-count").addEventListener("click", count);
  $("#ph-heat").addEventListener("change", draw);
  $("#ph-mark").addEventListener("click", () => {
    corners = []; marking = true; $("#ph-canvas").classList.add("marking");
    $("#ph-status").textContent = "Click the 4 corners of the rectangle on the ground, going round.";
    draw(); measure();
  });
  $("#ph-clear").addEventListener("click", () => { corners = []; marking = false; $("#ph-canvas").classList.remove("marking"); draw(); measure(); });
  $("#ph-canvas").addEventListener("click", (e) => {
    if (!marking || !img) return;
    corners.push(canvasPoint(e));
    if (corners.length === 4) { marking = false; $("#ph-canvas").classList.remove("marking"); $("#ph-status").textContent = ""; }
    draw(); measure();
  });
  for (const id of ["#ph-w", "#ph-d"]) $(id).addEventListener("input", measure);
  $("#ph-calib").addEventListener("click", downloadCalibration);
}
