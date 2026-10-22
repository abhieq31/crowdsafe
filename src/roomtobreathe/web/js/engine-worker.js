// Runs the Room to Breathe Python engine in a background thread with Pyodide, so the page stays
// responsive while a what-if simulates thousands of seconds of crowd flow.
/* global importScripts, loadPyodide */
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.2/full/";
let ready = null;

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
    + "from roomtobreathe.surroundings import event_overview, overpass_query, render_html");
  return py;
}

self.onmessage = async (e) => {
  try {
    ready = ready || start();
    const py = await ready;
    py.globals.set("venue_json", JSON.stringify(e.data.venue));
    py.globals.set("params_json", JSON.stringify(e.data.params));
    let out;
    if (e.data.kind === "overpass_query") {
      out = py.runPython("p = json.loads(params_json)\njson.dumps(overpass_query(p['lat'], p['lon'], p['radius_m']))");
    } else if (e.data.kind === "overview") {
      postMessage({ status: "Routing the crowd and the traffic through the streets, and placing teams…" });
      py.globals.set("osm_json", e.data.osm);
      out = py.runPython("p = json.loads(params_json)\n"
        + "r = event_overview(json.loads(venue_json), p['attendance'], json.loads(osm_json), modes=p.get('modes'), "
        + "arrival_minutes=p.get('arrival_minutes'), event_end_min=p.get('event_end_min'), "
        + "departure_minutes=p.get('departure_minutes'), public_level=p.get('public_level') or 1.0, radius_m=p.get('radius_m') or 1500)\n"
        + "json.dumps(r)");
      py.globals.delete("osm_json");
    } else if (e.data.kind === "overview_html") {
      py.globals.set("result_json", JSON.stringify(e.data.params));
      out = py.runPython("json.dumps(render_html(json.loads(result_json)))");
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
    postMessage({ error: String(err && err.message ? err.message : err).split("\n").slice(-3).join(" ") });
  }
};
