**Room to Breathe 1.0** is the first public release of an open-source system that helps anyone who runs a
gathering, from a temple committee to a national disaster authority, stop crowd crushes before they happen.

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
- **Count a crowd from a photo**, a **gate counter** for volunteers, and a **permit check**, all in the
  browser. Photos never leave the device.
- **Open standards:** alerts export as CAP 1.2. Every action is recorded with who approved it.

### New since v0.3.0
- **Draw the venue on a map**: search anywhere in the world (OpenStreetMap, no account or key), trace zones,
  and tap across gates. Areas and widths are measured for you.
- **One-question planning**: the web app opens on *Plan an event* and gives a single verdict and fixes,
  using the new `roomtobreathe.assess` engine (`rtb assess venue.json --people N`, `POST /api/assess`).
- **Find the right place**: when many places share a name (there are many "Ramlila Maidan"s), the search
  lists every match with its area and state. It also tries spelling variants (*Maidan*, *Ground*, *Field*)
  and accepts a pasted Google Maps or OpenStreetMap link, or plain coordinates.
- Fixed: playbook actions in one simulation leaked into the next one, so an evacuation could start with
  nobody inside.
- Fixed: pressing Enter in the Permit check reloaded the page and cleared every answer.
- Fixed: a returning visitor's browser could keep using an old cached copy of the engine. It is now
  fingerprinted, so each release loads fresh.

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
