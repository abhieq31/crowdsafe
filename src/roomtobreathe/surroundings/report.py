"""Printable event plan: one HTML page with a map and a section for every agency.

The page is self-contained except for the map library and tiles (Leaflet and OpenStreetMap), which load
from the internet when it is opened. Without them the tables still print.
"""

from __future__ import annotations

import html
import json
from typing import Dict, List

LEVEL = {"pass": ("OK", "#2f9e5b"), "warn": ("Needs a plan", "#e36b0a"), "fail": ("Not OK", "#d42a2a")}
AGENCY_ORDER = ["Police", "Traffic police", "Health", "Organisers", "Transport operator", "Fire service"]
LOS_COLOR = {"A": "#2f9e5b", "B": "#2f9e5b", "C": "#d7a300", "D": "#e36b0a", "E": "#d42a2a", "F": "#8b0000"}


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _ll(p) -> str:
    if not p:
        return ""
    lat, lon = p
    return f'<a href="https://www.openstreetmap.org/?mlat={lat}&amp;mlon={lon}#map=18/{lat}/{lon}">{lat:.5f}, {lon:.5f}</a>'


def _extra(m) -> str:
    return "" if m is None else f"{m:,} m"


def render_html(r: Dict, title: str = "") -> str:
    title = title or f"Event plan: {r['venue']}"
    t = r["times"]
    lvl, col = LEVEL[r["verdict"]]
    checks = "".join(
        f'<li><b style="color:{LEVEL[c["level"]][1]}">{_e(LEVEL[c["level"]][0])}</b> · <b>{_e(c["title"])}</b>'
        f'<br><span class="muted">{_e(c["detail"])}</span></li>'
        for c in r["checks"]
    )
    by_agency: Dict[str, List[dict]] = {}
    for d in r["deployments"]:
        by_agency.setdefault(d["agency"], []).append(d)
    totals = "".join(
        f"<tr><td>{_e(x['agency'])}</td><td>{_e(x['role'])}</td><td class=n>{x['count']:,}</td>"
        f"<td class=n>{x['posts']}</td></tr>"
        for x in r["totals"]
    )
    sections = []
    for ag in sorted(by_agency, key=lambda a: AGENCY_ORDER.index(a) if a in AGENCY_ORDER else 99):
        rows = "".join(
            f"<tr><td class=n>{i + 1}</td><td>{_e(d['role'])}</td><td class=n>{d['count']}</td><td>{_e(d['place'])}"
            f"<br><small>{_ll(d.get('lat_lon'))}</small></td><td>{_e(d['why'])}</td><td>{_e(d['when'])}</td></tr>"
            for i, d in enumerate(by_agency[ag])
        )
        extra = ""
        if ag == "Traffic police" and r["traffic"]["closures"]:
            extra = (
                "<h3>Road closures and diversions</h3><table><tr><th>Close to traffic</th><th>When</th>"
                "<th>Diversion via</th><th>Extra distance</th></tr>"
                + "".join(
                    f"<tr><td>{_e(c['street'])} ({c['length_m']:,} m)</td><td>{_e(c['when'])}</td>"
                    f"<td>{_e(', '.join(c['diversion_via']) or 'local access only')}</td>"
                    f"<td class=n>{_extra(c['diversion_extra_m'])}</td></tr>"
                    for c in r["traffic"]["closures"]
                )
                + "</table>"
            )
        if ag == "Health":
            em = r["emergency"]
            hosp = (
                "".join(
                    f"<li>{_e(h['name'])}: {h['drive_min']:g} min by road with event traffic ({_e(h['suitability'])})</li>"
                    for h in em["hospitals"]
                )
                or "<li>None found on the map: agree receiving hospitals with the health department.</li>"
            )
            cor = em["corridor"]
            cor_txt = (
                (
                    f"<p><b>Ambulance route</b> from {_e(cor['hospital'])} to {_e(cor['gate'])}: "
                    f"{cor['length_m']:,} m, about {cor['drive_min']:g} min, via {_e(', '.join(cor['via']) or 'local streets')}. "
                    + (
                        "It avoids the departing crowd."
                        if not cor["shared_with_crowd"]
                        else "Keep one lane coned off where it meets the crowd: "
                        + _e(", ".join(f"{s['street']} ({s['length_m']} m)" for s in cor["shared_with_crowd"]))
                        + "."
                    )
                    + "</p>"
                )
                if cor.get("found")
                else ""
            )
            extra = f"<h3>Receiving hospitals</h3><ul>{hosp}</ul>{cor_txt}"
        sections.append(
            f'<section class="agency"><h2>{_e(ag)}</h2><table><tr><th>#</th><th>Role</th><th>Staff</th>'
            f"<th>Where</th><th>Why</th><th>When</th></tr>{rows}</table>{extra}</section>"
        )
    transit = "".join(
        f"<tr><td>{_e(x['name'])}</td><td>{_e(x['mode'])}</td><td>{_e(x['phase'])}</td>"
        f"<td class=n>{x['people']:,}</td><td class=n>{x['peak_15min']:,}</td></tr>"
        for x in r["transit"][:14]
    )
    chokes = "".join(
        f"<tr><td>{_e(s['street'])}</td><td>{_e(s['phase'])}</td><td style='color:{LOS_COLOR[s['los']]}'><b>{s['los']}</b></td>"
        f"<td class=n>{s['load']:.0%}</td><td class=n>{s['walk_width_m']:g}{' (est.)' if s['width_estimated'] else ''}</td>"
        f"<td>{_e(s['fixes'][0] if s.get('fixes') else '')}</td></tr>"
        for s in r["choke_points"][:20]
    )
    notes = "".join(f"<li>{_e(n)}</li>" for n in r["notes"] + r["rules_of_thumb"])
    modes = ", ".join(f"{k} {v:.0%}" for k, v in r["modes"].items())
    geo = json.dumps(r["geojson"]).replace("</", "<\\/")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{_e(title)}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root{{--ink:#15181d;--muted:#5d6673;--line:#dde1e7;--bg:#fff}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--ink:#e8ebef;--muted:#98a1ad;--line:#2a3039;--bg:#0f1216}}}}
:root[data-theme="dark"]{{--ink:#e8ebef;--muted:#98a1ad;--line:#2a3039;--bg:#0f1216}}
body{{font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans",sans-serif;color:var(--ink);background:var(--bg);margin:0 auto;padding:16px;max-width:1100px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:26px 0 8px;border-bottom:2px solid var(--line);padding-bottom:4px}}
h3{{font-size:14px;margin:14px 0 6px}} .muted{{color:var(--muted)}} small{{color:var(--muted)}}
table{{border-collapse:collapse;width:100%;margin:6px 0}} th,td{{border:1px solid var(--line);padding:5px 7px;vertical-align:top;text-align:left}}
th{{background:color-mix(in srgb,var(--line) 50%,transparent)}} td.n{{text-align:right;font-variant-numeric:tabular-nums}}
.verdict{{border-left:5px solid {col};padding:8px 12px;font-weight:650;margin:10px 0;background:color-mix(in srgb,{col} 10%,transparent)}}
#map{{height:460px;border:1px solid var(--line);border-radius:8px;margin:10px 0}}
ul.checks{{padding-left:18px}} ul.checks li{{margin-bottom:6px}}
.scroll{{overflow-x:auto}} .legend span{{margin-right:12px;white-space:nowrap}} .legend i{{display:inline-block;width:12px;height:4px;margin-right:4px;vertical-align:middle}}
@media print{{#map{{height:380px;break-inside:avoid}} section{{break-inside:auto}} a{{color:inherit;text-decoration:none}} button{{display:none}}}}
</style></head><body>
<h1>{_e(title)}</h1>
<div class="muted">{r["attendance"]:,} people · arrival over {t["arrival_minutes"]:g} min · event ends at minute {t["event_end_min"]:g}
 · departure over {t["departure_minutes"]:g} min · travel: {_e(modes)}</div>
<div class="verdict">{_e(lvl)}: {_e(r["headline"])}</div>
<ul class="checks">{checks}</ul>
<div id="map" role="img" aria-label="Map of crowd routes, closures, ambulance route and deployments"></div>
<div class="legend small"><span><i style="background:#2f9e5b"></i>comfortable</span><span><i style="background:#d7a300"></i>busy</span>
<span><i style="background:#e36b0a"></i>crowded (D)</span><span><i style="background:#d42a2a"></i>overflowing (E/F)</span>
<span><i style="background:#555;height:3px;border-top:2px dashed #000"></i>closed to traffic</span><span><i style="background:#7a3fd1"></i>diversion</span>
<span><i style="background:#0a84ff"></i>ambulance route</span></div>
<h2>Staff summary</h2><div class="scroll"><table><tr><th>Agency</th><th>Role</th><th>Staff</th><th>Posts</th></tr>{totals}</table></div>
{"".join(sections)}
<h2>Outside choke points</h2><div class="scroll"><table><tr><th>Street</th><th>When</th><th>Level</th><th>Load</th><th>Footpath m</th><th>First fix</th></tr>{chokes or "<tr><td colspan=6>None.</td></tr>"}</table></div>
<h2>Stations and stops</h2><div class="scroll"><table><tr><th>Place</th><th>Mode</th><th>When</th><th>People</th><th>Busiest 15 min</th></tr>{transit or "<tr><td colspan=5>None.</td></tr>"}</table></div>
<h2>Assumptions and data</h2><ul>{notes}</ul>
<p class="small muted">Room to Breathe {_e(r.get("version", ""))} · Map data © OpenStreetMap contributors (ODbL).
This plan is decision support: police, traffic, health and the organisers must check it on the ground and adapt it.</p>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
(function(){{
  if(!window.L) return;
  var gj={geo}, los={json.dumps(LOS_COLOR)};
  var map=L.map('map'); L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:'© OpenStreetMap'}}).addTo(map);
  var icon={{"Police":"👮","Traffic police":"🚦","Steward":"🦺","First-aid post":"⛑️","Ambulance":"🚑","Station staff":"🚉","Fire tender":"🚒"}};
  var layer=L.geoJSON(gj,{{
    filter:function(f){{return f.properties.layer!=='crowd_arrival'}},
    style:function(f){{var p=f.properties;
      if(p.layer==='crowd_departure') return {{color:los[p.los]||'#888',weight:p.los>'C'?6:3,opacity:.85}};
      if(p.layer==='closure') return {{color:'#000',weight:5,dashArray:'6 6'}};
      if(p.layer==='diversion') return {{color:'#7a3fd1',weight:3,dashArray:'2 5'}};
      if(p.layer==='ambulance_route') return {{color:'#0a84ff',weight:5}};
      if(p.layer==='venue') return {{color:'#2456d6',weight:2,fillOpacity:.15}};
      return {{}};}},
    pointToLayer:function(f,ll){{var p=f.properties;
      var t=p.layer==='deployment'?(icon[p.role]||'•'):p.layer==='hospital'?'🏥':p.layer==='transit'?'🚇':p.layer==='pickup'?'🚕':'•';
      return L.marker(ll,{{icon:L.divIcon({{html:'<span style="font-size:18px">'+t+'</span>',className:'',iconSize:[22,22]}})}});}},
    onEachFeature:function(f,l){{var p=f.properties;
      var s=p.layer==='deployment'?('<b>'+p.role+' × '+p.count+'</b><br>'+p.place+'<br>'+p.why+'<br><i>'+p.when+'</i>')
        :p.layer==='hospital'?('<b>'+p.name+'</b><br>'+p.drive_min+' min by road')
        :p.layer==='transit'?('<b>'+p.name+'</b><br>'+p.people+' people ('+p.phase+'), '+p.peak_15min+' in the busiest 15 min')
        :(p.street||p.name||p.hospital||'');
      if(s) l.bindPopup(s);}}
  }}).addTo(map);
  var near=L.featureGroup(); layer.eachLayer(function(l){{var p=l.feature.properties;
    if(p.layer==='venue'||p.layer==='deployment') near.addLayer(L.geoJSON(l.feature));}});
  try{{map.fitBounds((near.getLayers().length?near:layer).getBounds(),{{padding:[20,20]}});}}catch(e){{map.setView([0,0],2);}}
}})();
</script></body></html>"""
