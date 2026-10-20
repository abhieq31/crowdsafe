"""Common Alerting Protocol (CAP 1.2, OASIS) export.

CAP is the international standard used by national alert systems, cell
broadcast gateways and public alert aggregators. Emitting CAP means a
Room to Breathe alert can reach existing warning infrastructure without custom
integration.
"""

from __future__ import annotations

import datetime as _dt
import xml.etree.ElementTree as ET
from typing import Optional

from .alerts import Alert
from .venue import Venue

CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"

_SEVERITY = {"YELLOW": "Moderate", "ORANGE": "Severe", "RED": "Extreme",
             "GREEN": "Minor", "UNKNOWN": "Unknown"}
_URGENCY = {"YELLOW": "Expected", "ORANGE": "Immediate", "RED": "Immediate",
            "GREEN": "Past", "UNKNOWN": "Unknown"}


def _iso(ts: float) -> str:
    # CAP wants e.g. 2026-10-20T08:15:00+05:30; use UTC with explicit offset.
    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).replace(microsecond=0) \
        .isoformat().replace("+00:00", "-00:00")


def alert_to_cap(alert: Alert, venue: Venue, sender: str = "roomtobreathe@localhost",
                 status: str = "Actual", instruction: Optional[str] = None) -> str:
    ET.register_namespace("", CAP_NS)
    q = lambda tag: f"{{{CAP_NS}}}{tag}"  # noqa: E731
    root = ET.Element(q("alert"))
    for tag, val in (
        ("identifier", alert.id),
        ("sender", sender),
        ("sent", _iso(alert.ts)),
        ("status", status),
        ("msgType", "Alert" if alert.previous in ("GREEN", "UNKNOWN") else "Update"),
        ("scope", "Public"),
    ):
        ET.SubElement(root, q(tag)).text = val

    info = ET.SubElement(root, q("info"))
    ended = alert.level == "GREEN"
    for tag, val in (
        ("language", "en"),
        ("category", "Safety"),
        ("event", "Crowd crush risk"),
        ("responseType", "AllClear" if ended else "Avoid"),
        ("urgency", _URGENCY[alert.level]),
        ("severity", _SEVERITY[alert.level]),
        ("certainty", "Observed" if alert.level == "RED" else "Likely"),
        ("senderName", venue.name),
        ("headline", f"{alert.level}: crowd density risk at {alert.zone_name}"),
        ("description", "; ".join(alert.reasons) or "Conditions normal."),
    ):
        ET.SubElement(info, q(tag)).text = val
    if instruction is None:
        instruction = ("Conditions back to normal." if ended else
                       f"Avoid {alert.zone_name}. Do not push. Follow marshals' instructions.")
    ET.SubElement(info, q("instruction")).text = instruction

    area = ET.SubElement(info, q("area"))
    ET.SubElement(area, q("areaDesc")).text = f"{venue.name} - {alert.zone_name}"
    zone = venue.zones.get(alert.zone_id)
    if zone and zone.polygon and venue.origin:
        pts = [venue.local_to_latlon(p) for p in zone.polygon]
        pts.append(pts[0])  # CAP polygons are closed
        ET.SubElement(area, q("polygon")).text = " ".join(f"{la},{lo}" for la, lo in pts)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)
