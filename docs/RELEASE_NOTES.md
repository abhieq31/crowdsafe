## Introducing Room to Breathe 1.1: check if your event is safe from a crowd crush

**Open the app, nothing to install:** https://abhieq31.github.io/roomtobreathe/

### Why this exists
People in a crowd crush do not die from panic. They die because the crowd presses on their chests until they
cannot breathe, often while standing still. And it keeps happening:

- **3,074 people died in stampedes in India between 2001 and 2022**, and **147 more in 2024 alone**, by the
  government's own count (NCRB, *Accidental Deaths and Suicides in India*; [Factly](https://factly.in/data-over-3000-lives-lost-in-stampedes-between-2001-2022/),
  [Dataful](https://insights.dataful.in/articles/stampede-deaths-in-india-what-24-years-of-ncrb-data-reveals)).
- **79% of India's stampedes happen at religious gatherings** (Illiyas et al., [*International Journal of
  Disaster Risk Reduction*, 2013](https://www.sciencedirect.com/science/article/abs/pii/S2212420913000459)).
- Worldwide, a review of **281 crowd disasters from 1900 to 2019** found them **becoming more frequent**,
  with India and West Africa the hot spots, and **thousands killed in the last decade alone** (Feliciani et
  al., [*Safety Science*, 2023](https://doi.org/10.1016/j.ssci.2023.106174)).

The causes are physical: too many people for the space, too little exit width, a surge, and decisions that come
too late. All of that can be measured before the event. So it can be prevented.

### Try it in one minute
1. **Where is your event?** Find it on the map, trace where people will stand, tap across each gate.
   (Or press *Try an example*.)
2. **How many people are coming?**
3. Press **Check my event**: *Safe*, *Possible with precautions* or *Not safe*, the reasons, and what to change.

It runs entirely in your browser. Nothing you draw or photograph is uploaded. No face recognition, no tracking
of individuals. Free and open source (Apache-2.0).

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
- **Works when map servers are busy.** Each part starts on a different public server, so none gets parallel
  requests (the main one refuses them), hedged across three; or plan from an exported `.osm` file (`--osm`).
- **Feels like an app on phones:** a bottom tab bar within thumb reach, the engine loads while you type so the
  first check starts at once, a clear "Checking…" state, no zoom-in when a field is tapped, and it can be
  added to the home screen. The engine is about 20% faster, with identical results.
- **Plain error messages:** one sentence saying what went wrong and what to do, never a Python traceback.

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
