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
  py.runPython("import json\nfrom roomtobreathe.whatif import describe, run_whatif\nfrom roomtobreathe.assess import assess_event");
  return py;
}

self.onmessage = async (e) => {
  try {
    ready = ready || start();
    const py = await ready;
    py.globals.set("venue_json", JSON.stringify(e.data.venue));
    py.globals.set("params_json", JSON.stringify(e.data.params));
    let out;
    if (e.data.kind === "assess") {
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
