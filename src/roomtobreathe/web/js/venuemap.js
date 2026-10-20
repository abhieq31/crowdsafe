// Draw a venue on a real map: search the place, draw each zone's outline, and draw a short line across
// each gate's opening. Areas and widths are measured from the drawing, so nobody has to guess them.
// OpenStreetMap needs no account or key, which is what lets any local body use this for free.
import { areaM2, gateSides, lengthM } from "./geo.js";

const LEAFLET = "https://unpkg.com/leaflet@1.9.4/dist/";
const COLORS = ["#2456d6", "#e36b0a", "#2f9e5b", "#9b3fd1", "#d42a2a", "#0e8c8c", "#a8730a", "#5d6673"];
const $ = (s) => document.querySelector(s);

let L = null, map = null, layers = null, draft = null, onChange = null, changedOnly = null;
let mode = null;      // null | "zone" | "gate" | "adjust"
let pts = [];         // points placed while drawing
let sketch = null;    // the shape being drawn

function load(tag, attrs) {
  return new Promise((ok, fail) => {
    const el = document.createElement(tag);
    Object.assign(el, attrs);
    el.onload = ok; el.onerror = () => fail(new Error(`could not load ${attrs.src || attrs.href}`));
    document.head.appendChild(el);
  });
}
async function loadLibs() {
  if (window.L) return window.L;
  await load("link", { rel: "stylesheet", href: LEAFLET + "leaflet.css" });
  await load("script", { src: LEAFLET + "leaflet.js" });
  return window.L;
}

const help = (t) => { $("#wi-map-help").textContent = t; };
const ll = (latlng) => [Math.round(latlng.lat * 1e7) / 1e7, Math.round(latlng.lng * 1e7) / 1e7];

function label(z, i) {
  return `${z.name || `Zone ${i + 1}`} · ${Math.round(areaM2(draft.origin, z.ll)).toLocaleString()} m²`;
}

function redraw() {
  if (!map) return;
  layers.clearLayers();
  draft.zones.forEach((z, i) => {
    if (!z.ll?.length) return;
    const poly = L.polygon(z.ll, { color: COLORS[i % COLORS.length], weight: 2, fillOpacity: 0.25 })
      .bindTooltip(label(z, i), { permanent: true, direction: "center", className: "map-label" }).addTo(layers);
    if (mode !== "adjust") return;
    z.ll.forEach((p, k) => { // drag a corner to adjust the zone; its area is re-measured
      L.marker(p, { draggable: true, icon: L.divIcon({ className: "vertex", iconSize: [18, 18] }) })
        .on("drag", (e) => { z.ll[k] = ll(e.latlng); poly.setLatLngs(z.ll); poly.setTooltipContent(label(z, i)); })
        .on("dragend", () => { z.area = String(Math.round(areaM2(draft.origin, z.ll))); changedOnly(); })
        .addTo(layers);
    });
  });
  draft.gates.forEach((g, i) => {
    if (!g.ll?.length) return;
    L.polyline(g.ll, { color: g.closed ? "#d42a2a" : "#111", weight: 6, dashArray: g.closed ? "6 6" : null })
      .bindTooltip(`G${i + 1} ${g.name} · ${g.width} m`, { direction: "top" }).addTo(layers);
  });
}

function addZone(latlngs) {
  const pts = latlngs.map(ll);
  if (!draft.origin) draft.origin = pts[0];
  const n = draft.zones.length + 1;
  draft.zones.push({ name: `Zone ${n}`, area: String(Math.round(areaM2(draft.origin, pts))), usable: 100, ll: pts });
  help(`Zone ${n} added: ${Math.round(areaM2(draft.origin, pts)).toLocaleString()} m². Name it in the list below. Draw the next zone, or draw the gates.`);
  onChange();
}

function addGate(a, b) {
  const pts = [ll(a), ll(b)];
  if (!draft.origin) draft.origin = pts[0];
  const width = Math.round(lengthM(draft.origin, pts) * 10) / 10;
  const zonesLL = draft.zones.map((z) => z.ll || []);
  let [s1, s2] = gateSides(draft.origin, pts, zonesLL);
  if (s1 === s2) { // drawn inside one zone or outside everything: let the person choose
    help("That line does not cross a zone edge. Draw it across the opening, from one side of the gap to the other.");
    return;
  }
  // Direction people walk: the first opening to outside is taken as the entry, later ones as exits.
  const touchesOutside = s1 === "outside" || s2 === "outside";
  const hasEntry = draft.gates.some((g) => g.from === "outside");
  if (touchesOutside) {
    const zone = s1 === "outside" ? s2 : s1;
    [s1, s2] = hasEntry ? [zone, "outside"] : ["outside", zone];
  }
  const n = draft.gates.length + 1;
  draft.gates.push({ name: touchesOutside ? (hasEntry ? `Exit ${n}` : "Main entry") : `Gate ${n}`, from: s1, to: s2,
    width, stairs: false, closed: false, ll: pts });
  help(`Gate G${n} added: ${width} m wide. Check its direction ("From → To") in the list below.`);
  onChange();
}

function setMode(m) {
  mode = m; pts = [];
  if (sketch) { map.removeLayer(sketch); sketch = null; }
  map.getContainer().classList.toggle("drawing", m === "zone" || m === "gate");
  if (m) map.getContainer().scrollIntoView({ block: "nearest" }); // fully in view, not under the sticky header
  $("#wi-zone-tools").hidden = m !== "zone";
  $("#wi-adjust").textContent = m === "adjust" ? "Done adjusting" : "Adjust shapes";
  redraw();
}

function onMapClick(e) {
  if (mode !== "zone" && mode !== "gate") return;
  pts.push(e.latlng);
  if (sketch) map.removeLayer(sketch);
  if (mode === "gate") {
    if (pts.length === 1) {
      sketch = L.circleMarker(pts[0], { radius: 6, color: "#111" }).addTo(map);
      help("Now tap the other side of the opening.");
      return;
    }
    const [a, b] = pts;
    setMode(null);
    addGate(a, b);
    return;
  }
  sketch = L.polygon(pts, { color: "#2456d6", dashArray: "4 4", fillOpacity: 0.15 }).addTo(map);
  help(pts.length < 3 ? `Corner ${pts.length} placed. Keep tapping round the zone's edge.`
    : `${pts.length} corners, ${Math.round(areaM2(draft.origin || ll(pts[0]), pts.map(ll))).toLocaleString()} m² so far. `
      + "Tap more corners, or press Finish zone.");
}

function finishZone() {
  if (pts.length < 3) { help("A zone needs at least 3 corners."); return; }
  const done = pts;
  setMode(null);
  addZone(done);
}

async function search(e) {
  e.preventDefault();
  const q = $("#wi-map-q").value.trim();
  if (!q) return;
  help("Searching…");
  try {
    // OpenStreetMap's free geocoder. Only the typed place name is sent.
    const r = await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(q)}`);
    const [hit] = await r.json();
    if (!hit) { help(`No place called "${q}" found. Try a nearby town or landmark.`); return; }
    const [s, n, w, ea] = hit.boundingbox.map(Number);
    map.fitBounds([[s, w], [n, ea]], { maxZoom: 18 });
    help(`Showing ${hit.display_name}. Zoom in until you can see the ground, then draw the zones.`);
  } catch {
    help("Search is not available right now. Pan and zoom the map instead.");
  }
}

export async function openMap(state, changed) {
  draft = state; changedOnly = changed; onChange = () => { changed(); redraw(); };
  $("#wi-map-panel").hidden = false;
  if (map) { map.invalidateSize(); redraw(); return; }
  help("Loading the map…");
  L = await loadLibs();
  map = L.map("wi-map", { zoomControl: true }).setView(draft.origin || [20, 0], draft.origin ? 17 : 2);
  const streets = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' });
  const satellite = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 19, attribution: "Imagery © Esri, Maxar, Earthstar Geographics" });
  satellite.addTo(map);
  L.control.layers({ Satellite: satellite, Streets: streets }, null, { position: "topright" }).addTo(map);
  layers = L.layerGroup().addTo(map);
  map.on("click", onMapClick);
  map.doubleClickZoom.disable(); // fast taps while drawing must not zoom
  $("#wi-map-search").addEventListener("submit", search);
  $("#wi-draw-zone").addEventListener("click", () => {
    setMode("zone");
    help("Tap each corner of the zone's edge in turn, then press Finish zone.");
  });
  $("#wi-zone-done").addEventListener("click", finishZone);
  $("#wi-zone-undo").addEventListener("click", () => {
    pts.pop();
    if (sketch) { map.removeLayer(sketch); sketch = null; }
    if (pts.length) sketch = L.polygon(pts, { color: "#2456d6", dashArray: "4 4", fillOpacity: 0.15 }).addTo(map);
    help(`${pts.length} corners.`);
  });
  $("#wi-zone-cancel").addEventListener("click", () => { setMode(null); help("Cancelled."); });
  $("#wi-draw-gate").addEventListener("click", () => {
    setMode("gate");
    help("Tap one side of the opening, at its narrowest point.");
  });
  $("#wi-adjust").addEventListener("click", () => {
    setMode(mode === "adjust" ? null : "adjust");
    help(mode === "adjust" ? "Drag the white corners. Press Done adjusting when finished." : "Shapes saved.");
  });
  $("#wi-locate").addEventListener("click", () => {
    help("Finding you…");
    map.locate({ setView: true, maxZoom: 17 });
  });
  map.on("locationfound", () => help("This is where you are. Draw the zones around you."));
  map.on("locationerror", () => help("Location not available. Search for the place instead."));
  help(draft.origin ? "Your venue. Press Adjust shapes to move corners, or draw more zones and gates."
    : "Search for the venue, or press 'Where am I', then draw the zones.");
  redraw();
  const drawn = draft.zones.flatMap((z) => z.ll || []).concat(draft.gates.flatMap((g) => g.ll || []));
  if (drawn.length) map.fitBounds(drawn, { padding: [30, 30], maxZoom: 19 });
}

export const refreshMap = () => redraw();
