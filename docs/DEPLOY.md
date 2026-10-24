# Deploying Room to Breathe for a real event

This guide is for a district office, police unit, municipality, temple or festival committee, or event
organiser. You need no programming. Each step says what to do, why it matters, and how to tell it worked.

Room to Breathe **recommends**; a trained, accountable person **decides**. Read [ETHICS.md](../ETHICS.md) first.

---

## 1. The physics that decides everything

Every design choice below follows from three numbers.

**Danger is density.** People die in crowd crushes from compressive asphyxia. Above about 4-5 people per
m² a moving crowd starts to lose control, and around 6-7 p/m² individuals can no longer move on their own.
Room to Breathe's defaults (per zone, adjustable in the venue file):

| Level | Starts at | Meaning |
|---|---|---|
| YELLOW | 2.0 p/m² | Busy. Watch the trend |
| ORANGE | 4.0 p/m² | Unsafe if moving. Prepare to hold people upstream |
| RED | 5.5 p/m² | Act now |
| critical | 6.5 p/m² | Used for the "time to critical" countdown |

**Danger builds at the rate people arrive.** A zone of area *A* fills at

```
time to critical = A × (6.5 − density now) / (inflow − outflow)
```

Exits move about 82 people per metre of width per minute on level ground, and 66 on stairs (UK Green Guide).
Take a 960 m² hall fed by a 4 m gate (5.5 p/s), leaving by a 3 m staircase (3.3 p/s):

| Density now | Exit flowing | Exit blocked |
|---|---|---|
| 2.0 p/m² | 33 min to critical | 13 min |
| 4.0 p/m² (ORANGE) | 18 min | **7 min** |
| 5.5 p/m² (RED) | 7 min | **3 min** |

**So the margin is minutes, and humans spend most of it.** Someone has to see the alert, decide, radio a
marshal and get a gate closed upstream. **Rehearse this and time it.** Room to Breathe alerts on the trend as well
as the level (RED when it projects critical within 3 minutes, ORANGE within 10, YELLOW within 20). It also
raises an alert when a zone has had no data for 15 seconds, because a dead camera must never look like an
empty zone.

What follows from this:
- A report every second or every 6 seconds makes no practical difference. A 35-40% undercount does. This is why
  slow camera devices report less often, and never shrink the picture (see [vision.md](vision.md)).
- Plan the exits before the event. Crushes happen at bottlenecks: where more people arrive than the way
  out can take.
  `roomtobreathe venue new` does that arithmetic.

---

## 2. What you need

| Item | Minimum | Why |
|---|---|---|
| Control-room computer | Any ordinary laptop | Runs the server and dashboard. Very light: the server does arithmetic, not video |
| Camera device, one per camera | A laptop or mini-PC that `roomtobreathe check` passes | Runs the density model. Frames never leave it |
| Network | Local Wi-Fi or LAN. Internet not needed during the event | Each camera sends about 200 bytes per zone per report: **~6 kbit/s** for 4 zones, so even a weak 2G link is enough |
| Power | UPS or power bank for the control room and router | Crowds and power cuts often arrive together |
| People | One control-room lead, operators, marshals with radios | Room to Breathe recommends; people act |

**Is a camera device fast enough?** Run `roomtobreathe check` on it (§4). Measured on the default model at
1024 px:

| Device | Per frame | Reports every | Verdict |
|---|---|---|---|
| Apple M1 laptop with CoreML | 0.5 s | 1 s | good |
| Apple M1, CPU only | 3-5 s | 5-8 s | fine for slow build-ups |
| Anything slower than ~9 s per frame | | 15 s or more | `check` fails it: the zone would keep flickering to UNKNOWN |

A PC with an NVIDIA GPU is faster again: replace `onnxruntime` with `onnxruntime-gpu` and Room to Breathe uses the GPU
automatically.

**No cameras?** The Gate counter tab works on any phone browser. Volunteers tap +1/−1 at each gate and the
counts feed the same alerts.

---

## 3. Before the event (one week ahead)

**Start in the [web app](https://abhipatelz.github.io/roomtobreathe/)** (nothing to install): in *Plan an event*, find
the venue on the map, trace it, enter the crowd you expect and press *Check my event*. Fix what it reports, then
press *Save this venue* to get the venue file for the control room. The steps below do the same on the
command line (`roomtobreathe assess my_venue.json --people 12000`).

### 3.1 Describe the venue

```bash
pip install "roomtobreathe[server]"
roomtobreathe venue new my_venue.json
```

It asks for each zone (a name and its area, e.g. `120 x 80`) and each gate (from, to, narrowest width,
steps or not). It prints the **safe capacity** and every structural problem: a single exit, a zone with no
way out, more people expected than the exits can drain. **If it says the expected crowd exceeds the safe
capacity, fix that first: reduce entry, add exits, or use more space.** Software cannot rescue an event
permitted beyond what its exits can drain.

Check an existing file at any time with `roomtobreathe venue check my_venue.json`.

### 3.2 Calibrate each camera

Density per m² needs the camera's view of the ground in metres. Mark or measure four points on the ground
in view (tile corners, a 10 m rope square, road markings), note their pixel positions on a snapshot, and
write the calibration file. See [vision.md §1](vision.md#1-calibrate-each-camera-once). Use the zone names
`venue new` printed. `roomtobreathe check` refuses names the venue doesn't know, because the server would ignore
those counts.

---

## 4. Setting up (the day before)

### Control room

**Option A, Docker** (one command, restarts itself after a crash or reboot):

```bash
mkdir data && cp my_venue.json data/venue.json
export RTB_TOKEN='choose-a-long-access-code'
docker compose up -d                       # uses ghcr.io/abhipatelz/roomtobreathe
```

**Option B, plain Python:**

```bash
export RTB_TOKEN='choose-a-long-access-code'
roomtobreathe serve --venue my_venue.json --host 0.0.0.0
```

It prints the address other devices use, e.g. `http://192.168.1.20:8000/`. Open it in a browser and press
**Sign in**: enter your name and the access code. Every action you approve is recorded with your name.

### Each camera device

```bash
pip install "roomtobreathe[vision,onnx]"
roomtobreathe setup        # camera address, calibration file, control-room address, access code, model
roomtobreathe check        # must end with READY
```

Or with Docker (the model is already inside, so no internet is needed at the venue):

```bash
docker run --rm -v $PWD/cam1.json:/cam.json ghcr.io/abhipatelz/roomtobreathe-edge \
  check --source rtsp://user:pass@CAMERA-IP/stream --calibration /cam.json \
        --server http://192.168.1.20:8000 --token "$RTB_TOKEN"
```

`check` tests the software, calibration, camera picture, model, this device's speed, the control-room
connection, the access code, and whether each zone name exists on the server. **Fix everything it marks
FAIL. Read every WARN.**

### Rehearse (30 minutes, do not skip)

1. Start every camera device with `roomtobreathe edge` and confirm each zone shows a live density on the
   dashboard.
2. Unplug one camera. Within 15 seconds its zone must turn grey (UNKNOWN, "no data") and raise an alert. If it doesn't,
   stop and find out why.
3. Walk a group through one zone and watch the count rise.
4. Run the demo on a spare computer (`roomtobreathe demo`) and have each operator approve a RED playbook. Time
   how long your radio chain takes to close an upstream gate, and compare it with the minutes in §1.

---

## 5. On the day

```bash
roomtobreathe edge     # on each camera device, with the saved setup
```

- Watch the **trend** as well as the colour. "Rising +0.3 p/m² per minute" at YELLOW is the time to act
  gently, upstream.
- **Hold people back upstream; never seal the gate at the crush point.** The default playbook follows this
  rule, and the simulator showed that sealing the hall moved the crush into the corridor behind it.
- A grey zone (UNKNOWN, "no data") is not empty. Send someone to look.
- If the dashboard says an action was **NOT recorded**, carry it out by radio and try again.

---

## 6. When things fail

| Failure | What Room to Breathe does | What you do |
|---|---|---|
| A camera or its device dies | Zone turns grey (UNKNOWN) within 15 s and alerts, ranked like ORANGE | Send a marshal; use the gate counter |
| Network drops | The device keeps up to 600 readings (2.5 min for 4 zones) and sends them when it reconnects | Fix the link; the zones show UNKNOWN meanwhile |
| A device clock is wrong | The server corrects it from the device's send time | Nothing |
| Control-room crash or reboot | Docker restarts it; the event record on disk is kept | Sign in again |
| Power cut | Every alert and approved action is already written to disk | UPS for the control room and router |
| A bad calibration gives impossible numbers | The device drops values above 20 p/m² and warns once | Redo the calibration |
| A zone name typo | `check` fails it; the device warns once | Rename it in the calibration file |
| A very dense, dark or rainy scene | The model undercounts (see vision.md) | Set thresholds with margin; trust marshals' reports |

---

## 7. After the event: the record

Every alert, approved action (with the operator's name), trigger and restart is written to
`roomtobreathe-logs/roomtobreathe-YYYY-MM-DD.jsonl` (`data/logs/` with Docker), one JSON object per line, as it
happens. Signed-in users can also download it from `http://<control-room>:8000/api/log`. This is what an
inquiry will ask for. Keep a copy and restrict access (it contains operator names), then delete it after
your retention period.

Write down what happened, what the numbers showed, and what you would change. Then share the lessons with
the project if you can.
