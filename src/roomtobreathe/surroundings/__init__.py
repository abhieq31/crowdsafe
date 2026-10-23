"""The venue's surroundings: streets, transit, traffic, emergency access and a deployment plan.

Entry point: ``event_overview(venue_dict, attendance, osm_json, ...)``. Get ``osm_json`` with
``osm.fetch_osm`` (or ``load_or_fetch`` to cache it), or in the browser from ``osm.overpass_query``.
"""

from .osm import fetch_osm, load_or_fetch, merge_osm, overpass_queries, overpass_query, parse_osm  # noqa: F401
from .overview import event_overview, parse_modes  # noqa: F401
from .report import agencies, deployments_csv, render_html  # noqa: F401
