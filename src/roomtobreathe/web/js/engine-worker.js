// Runs the Room to Breathe Python engine in a background thread with Pyodide, so the page stays
// responsive while a what-if simulates thousands of seconds of crowd flow.
/* global importScripts, loadPyodide */
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.2/full/";
let ready = null;
let lastTilesKey = null; // the tiles whose conversion the engine keeps (tiles_cache)

async function start() {
  postMessage({ status: "Loading Python in your browser…" });
  importScripts(PYODIDE + "pyodide.js");
  const py = await loadPyodide({ indexURL: PYODIDE });
  await py.loadPackage("micropip");
  const manifest = await (await fetch(new URL("../py/wheel.json", self.location.href), { cache: "no-cache" })).json();
  postMessage({ status: "Loading the Room to Breathe engine…" });
  const wheel = new URL("../py/" + manifest.wheel, self.location.href);
  if (manifest.sha256) wheel.searchParams.set("v", manifest.sha256); // a new engine never comes from cache
  await py.pyimport("micropip").install(wheel.href, { deps: false });
  py.runPython("import json\nfrom roomtobreathe.whatif import describe, run_whatif\nfrom roomtobreathe.assess import assess_event\n"
    + "from roomtobreathe.surroundings import event_overview, merge_osm, overpass_queries, parse_osm, render_html\n"
    + "from roomtobreathe.surroundings.tiles import tiles_to_osm");
  return py;
}

self.onmessage = async (e) => {
  try {
    ready = ready || start();
    const py = await ready;
    if (e.data.kind === "warm") return; // loaded ahead of the first check; nothing to answer
    py.globals.set("venue_json", JSON.stringify(e.data.venue));
    py.globals.set("params_json", JSON.stringify(e.data.params));
    let out;
    if (e.data.kind === "overpass_queries") {
      out = py.runPython("p = json.loads(params_json)\njson.dumps(overpass_queries(p['lat'], p['lon'], p['radius_m']))");
    } else if (e.data.kind === "overview") {
      postMessage({ status: "Routing the crowd and the traffic through the streets, and placing teams…" });
      // Map data is either vector tiles (fast: [z, x, y, bytes]) or Overpass/.osm texts, plus parts that failed.
      // The same tiles are converted once and kept, so re-planning (other crowd size, corrections) is quicker.
      const key = e.data.tiles ? `${e.data.tilesKey}` : "";
      const reuse = key && key === lastTilesKey;
      py.globals.set("reuse_tiles", reuse);
      py.globals.set("tile_list", reuse ? py.toPy([]) : py.toPy((e.data.tiles || []).map((t) => [t.z, t.x, t.y, t.data])));
      lastTilesKey = key || null;
      py.globals.set("osm_parts", JSON.stringify(e.data.osm || []));
      py.globals.set("osm_missing", JSON.stringify(e.data.missing || []));
      out = py.runPython("p = json.loads(params_json)\n"
        + "blob = lambda b: b.to_bytes() if hasattr(b, 'to_bytes') else bytes(b)\n"
        + "osm = tiles_cache if reuse_tiles else (tiles_to_osm([((int(t[0]), int(t[1]), int(t[2])), blob(t[3])) for t in tile_list], "
        + "tuple(json.loads(venue_json)['origin']), p.get('radius_m') or 1500) if len(tile_list) "
        + "else merge_osm([parse_osm(t) for t in json.loads(osm_parts)]))\n"
        + "tiles_cache = osm if len(tile_list) or reuse_tiles else None\n"
        + "m = json.loads(osm_missing)\n"
        + "osm.update({'missing': m} if m else {})\n"
        + "r = event_overview(json.loads(venue_json), p['attendance'], osm, modes=p.get('modes'), "
        + "arrival_minutes=p.get('arrival_minutes'), event_end_min=p.get('event_end_min'), "
        + "departure_minutes=p.get('departure_minutes'), public_level=p.get('public_level') or 1.0, radius_m=p.get('radius_m') or 1500)\n"
        + "json.dumps(r)");
      py.globals.delete("osm_parts");
      py.globals.delete("tile_list");
    } else if (e.data.kind === "overview_html") {
      py.globals.set("result_json", JSON.stringify(e.data.params));
      py.globals.set("agency", e.data.agency || "");
      out = py.runPython("json.dumps(render_html(json.loads(result_json), agency=agency))");
    } else if (e.data.kind === "assess") {
      postMessage({ status: "Checking capacity, arrival, a surge and an evacuation…" });
      out = py.runPython("p = json.loads(params_json)\njson.dumps(assess_event(json.loads(venue_json), p['attendance'], p.get('arrival_minutes') or 60))");
    } else {
      postMessage({ status: "Simulating the crowd, twice (nobody acts, then with the playbook)…" });
      out = py.runPython("r = run_whatif(json.loads(venue_json), json.loads(params_json))\nr['conclusions'] = describe(r)\njson.dumps(r)");
    }
    postMessage({ result: JSON.parse(out) });
  } catch (err) {
    ready = null;
    lastTilesKey = null; // a restarted engine has no converted tiles
    // A Python traceback ends with "SomeError: what went wrong"; people need only what went wrong.
    const lines = String(err && err.message ? err.message : err).trim().split("\n");
    const lastLine = lines[lines.length - 1].trim();
    postMessage({ error: lastLine.replace(/^[A-Za-z_.]*(Error|Exception): /, "") || lastLine });
  }
};
