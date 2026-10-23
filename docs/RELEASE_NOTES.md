**Room to Breathe 1.1** plans the streets around your venue, not just the venue. It is an open-source system
that helps anyone who runs a gathering, from a temple committee to a national disaster authority, stop crowd
crushes before they happen.

**Try it now, nothing to install:** https://abhieq31.github.io/roomtobreathe/

### What it does
- **Is my event safe?** Find the venue on a map, trace where people will stand, tap each gate and enter the
  expected crowd. You get one verdict (*Safe*, *Possible with precautions* or *Not safe*), the reasons worst
  first, and a numbered list of what to change, including how much extra exit width would fix it. It checks
  capacity with the largest exit blocked, crowding as people arrive, a sudden surge (with and without the
  response plan) and a full evacuation.
- **Live early warning at the venue.** Existing CCTV gives people per m² and crowd motion per zone. Alerts
  say why they fired and how many minutes are left before critical density. Each one comes with a
  pre-approved playbook (which gate to close or open, and what to announce in English, Hindi or Bengali)
  that a named operator approves.
- **The area around the venue.** How the crowd gets there and home through the real streets, stations and
  stops; which roads to close and the diversion for each; how ambulances get in; and **where to place police,
  traffic police, first aid, ambulances, stewards and station staff, how many, when and why**, with a
  briefing sheet for each agency.
- **Count a crowd from a photo**, a **gate counter** for volunteers, and a **permit check**, all in the
  browser. Photos never leave the device.
- **Open standards:** alerts export as CAP 1.2. Every action is recorded with who approved it.

### New since v1.0.0
- **The area around the venue** (*Plan an event → step 3*, `rtb overview`, `POST /api/overview`). From free
  OpenStreetMap data it routes arrival and departure to stations, stops, car parks and pick-up points, finds
  the outside choke points (Fruin level of service), closes roads and re-checks them, gives a diversion for
  every closure, names receiving hospitals with drive times under event traffic, and plans an ambulance route
  that avoids the crowd. It ends with a deployment plan: every post with its role, staff, place, reason and
  time. How it works and its limits: [docs/SURROUNDINGS.md](https://github.com/abhieq31/roomtobreathe/blob/main/docs/SURROUNDINGS.md).
- **Staggered exit, checked automatically.** If streets still overflow after the closures, it works out how
  long the departure must be spread over and adopts it when it helps. Around Eden Gardens for 60,000 people,
  27 overflowing streets become 4 with a 3-wave release over 120 minutes.
- **Briefing sheet per agency** (police, traffic police, health), each with only its own posts and facts,
  and every post as CSV for rosters (`rtb overview --briefings DIR`).
- **Correct the map.** Tap a street to set its real footpath width or mark it closed or blocked, then
  re-plan in seconds. Corrections are saved in the venue file.
- **Works when map servers are busy.** Downloads are hedged across four public servers, and you can plan
  from an exported `.osm` file instead (`--osm`).
- **Easier on phones:** every tab is visible (the tab bar no longer hides tabs off-screen), disabled buttons
  look disabled, and the gate counter no longer shows control-room options on the public site.

### Install
```bash
pip install "roomtobreathe[server]"          # control room:   roomtobreathe demo  /  roomtobreathe serve
pip install "roomtobreathe[vision,onnx]"     # camera device:  roomtobreathe setup && roomtobreathe edge
docker run -p 8000:8000 ghcr.io/abhieq31/roomtobreathe
```
Deployment guide: [docs/DEPLOY.md](https://github.com/abhieq31/roomtobreathe/blob/main/docs/DEPLOY.md).

### Read before relying on it
Room to Breathe is **decision support**. Trained people decide and remain responsible. The default density
model **undercounts the densest crowds by roughly 9–19%**
([benchmark](https://github.com/abhieq31/roomtobreathe/blob/main/docs/vision.md#benchmark)), so combine
cameras with gate counts. Calibrate thresholds and playbooks with your police, fire and medical teams before
the event. There is no face recognition and no individual tracking
([ETHICS.md](https://github.com/abhieq31/roomtobreathe/blob/main/ETHICS.md)).

Field pilots, translations and reviews from crowd-safety practitioners are very welcome. Please open an issue.
