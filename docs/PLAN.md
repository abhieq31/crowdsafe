# CrowdSafe — Open-Source Crowd-Crush Prevention Platform
*Design plan v0.1 · working name, change freely*

> **Mission:** No one should die because they went to pray, watch, celebrate or protest.
> Give any administrative body, from a village temple committee to a national disaster authority, a free system that **plans** safe capacity, **sees** danger forming, **predicts** it minutes ahead, and **tells the right person what to do**.

---

## 1. What the science says (and what it means for the design)

| Finding | Source | Design consequence |
|---|---|---|
| Crowd disasters come from **structural failures**, not "panicking, irrational people". | Singh & Kishore 2025 (attached paper); Fruin; Still | Most of the value comes **before** the event: capacity, exits, layout, permits. The AI is the second line of defence. |
| Death comes from **compressive asphyxia**: forces up to ~4500 N/m on the chest. People die **standing up**, often without running. | Paper, §1 | Don't look for "running people". Look for **density + compression waves**. |
| Density near **~7 persons/m²** makes the crowd fluid and people get pushed around. | Arbon (cited in paper) | Hard alarm threshold. Warnings start well below it. |
| Before turbulence there are **stop-and-go waves**. *Crowd pressure* = density × variance of velocity is the measurable early signal (Helbing et al., Hajj 2006 video analysis). | Helbing, Johansson & Al-Abideen 2007 | Core metric: per-zone **density + optical-flow velocity variance**. Counting heads is not enough. |
| Density **and inflow vs outflow** give a "**time-to-critical**" estimate. | Massey Univ. (Lovreglio); KAIST KDD forecasting work | Main operator number: **"Zone B reaches critical in ~6 min at current inflow."** |
| Triggers: rumours, free items, sudden change of gate or platform, start/end-of-event surges, rain or mud, power cuts, single exit, convergence to one point. | Paper §4.2, Hathras case | The **trigger catalogue** becomes rule packs and playbooks. Rumour handling is a first-class feature. |
| Hathras 2024: permit for 80k, ~250k came, 80 police, one exit, muddy field. **116 died.** | Paper §4.4 | Make it **permit-gated**: capacity calculator plus a live counter compared against the permitted number. |
| ~79% of Indian stampede deaths happen at **religious** gatherings. Repeat incidents happen at the **same sites**. | Paper §4.1 | Site registry with incident history. Ship a religious and procession profile first. |
| Response fails from slow decisions, poor triage and lost medical records. | Paper abstract | Built-in pre-approved **decision playbooks** and a **triage/casualty module**. |

**Thresholds** (configurable per venue, aligned with Fruin Level of Service):
`< 2/m²` free · `2–4` dense · `4–5` **warning** (moving crowd) · `5–6` **danger** · `≥ 6–7` or high crowd pressure **critical / stop inflow now**.
Sources disagree (5–10/m²), so we treat thresholds as **calibrated parameters, not constants**.

---

## 2. The system in one picture

```
 BEFORE (days–weeks)          DURING (seconds–minutes)                      AFTER
┌──────────────────┐   ┌───────────────────────────────────────────────┐  ┌────────────────┐
│ 1. PLAN          │   │ 2. SENSE ──► 3. UNDERSTAND ──► 4. PREDICT     │  │ 6. LEARN       │
│ venue digital    │──►│ CCTV/phone/   density maps,    time-to-critical│─►│ after-action   │
│ twin, capacity,  │   │ drone, gates, flow, pressure,  per zone,        │  │ report, open   │
│ simulation,      │   │ telecom, SMS  per-zone state   surge forecast   │  │ incident data, │
│ permit checklist │   │                    │                 │          │  │ model retrain  │
└──────────────────┘   │                    ▼                 ▼          │  └────────────────┘
                       │              5. ACT: graded alerts + playbooks   │
                       │     marshals' app · PA/LED · cell broadcast ·   │
                       │     gate control · police/EMS dispatch · triage │
                       └───────────────────────────────────────────────┘
```

---

## 3. Modules in detail

### Module 1: Plan (the highest-impact, lowest-cost module)
- **Venue digital twin:** import an OpenStreetMap area, a CAD/DXF file or a photo, or draw it in the browser. Mark walkable area, gates, choke points, slopes, and ground type (mud, steps).
- **Capacity calculator:** safe capacity = usable area × target density, limited by **exit flow capacity** (persons/m/min, per Green Guide/Fruin). Also checks evacuation time.
- **Simulation:** runs open-source pedestrian simulators (**JuPedSim**, **Vadere**) in the cloud or on a laptop. You get heatmaps of where density will exceed 4/m² for a given arrival curve, and you can test "what if rain halves walking speed" or "what if the end-of-event exit happens all at once".
- **Permit workflow** (encodes the paper's 5-stage framework: decision → approval → risk assessment → integrated plan → debrief). A checklist-driven risk score covering event type, crowd profile (age, gender, socio-economic, as the paper recommends), site incident history, weather and resources. **Police-per-attendee ratio** and **exits-per-capacity** are hard rules. The output is a signed **Crowd Safety Plan PDF** for the approving authority.
- **Site registry:** every venue has a history. A past casualty raises the risk tier automatically.

### Module 2: Sense (use what already exists)
Run in priority order so it works **without** expensive hardware:
1. **Existing CCTV**, cheap **Android phones** on poles, and **drones**, read over RTSP/WebRTC.
2. **Gate counters:** turnstiles, ticket scans, or a **"clicker" mode** in the marshal app (a volunteer taps +1 per person or +10 per group). This is Tier 0 and needs no camera.
3. **Telecom aggregate counts:** anonymised cell-tower or Wi-Fi probe density, the approach Seoul adopted after Itaewon. Plugged in through an adapter interface.
4. **Context feeds:** weather (rain changes walking speed and causes slips), event schedule (prasad distribution or a VIP arrival is a known surge trigger), social media keyword watch for **rumours**.
5. **Human reports:** marshals press "too tight here" with a GPS pin. Citizens can report by SMS or WhatsApp.

### Module 3: Understand (computer vision at the edge)
- **Density estimation**, not bounding boxes. Detectors like YOLO fail at more than 3/m² because heads are occluded. Use density-map and point models (CSRNet → DM-Count → **P2PNet / CLIP-EBC** family) trained on ShanghaiTech, UCF-QNRF, JHU-Crowd++ and NWPU-Crowd, then fine-tuned on Indian festival footage.
- **Perspective calibration:** the operator clicks 4 ground points of known size once per camera, which turns a camera image into **people per m²** (a homography). This step is essential and is often skipped.
- **Motion:** dense optical flow (RAFT-small or Farnebäck on low-end hardware). Outputs per zone: mean speed, **direction coherence**, **velocity variance → crowd pressure**, counter-flow detection, and stop-and-go wave detection (FFT on the speed signal).
- **Fusion:** a Kalman or Bayesian fusion of camera, gate and telecom estimates per zone, with **uncertainty bars**. Operators must see how confident the system is.
- **Privacy by design:** no face recognition, ever. Video stays on the edge device. Only **numbers and heatmaps** leave the device. Short ring-buffer clips are kept only on a critical alert, for review.

### Module 4: Predict
- **Physics nowcast** (transparent, always on): `T_crit = (ρ_crit − ρ_now) · Area / (inflow − outflow)`
- **ML forecast** (5–15 min): a spatio-temporal graph neural network where zones are nodes and paths are edges, with schedule and weather as covariates. Similar in spirit to the KAIST KDD work.
- **Live twin:** during the event, feed real arrivals into the simulator and run it forward ("if Gate 3 closes now, where does the pressure go?").
- **Rule engine** for the paper's trigger catalogue: e.g. `free_distribution_active AND density>4 → amber`.
- **Explainable by default:** every alert says *why*: "density 5.6/m², rising 0.3/min, inflow 2× outflow, stop-go waves detected".

### Module 5: Act (where lives are actually saved)
- **Graded alerts** (Green → Yellow → Orange → Red), issued in the **Common Alerting Protocol (CAP)** standard so national alert systems, Google Public Alerts and others can ingest them directly.
- **Pre-approved playbooks:** decided calmly before the event, executed in seconds during it. Example: *Orange in Zone B → close inflow at Gate 2, open overflow Gate 5, PA message #3 in Bengali/Hindi/English, 4 marshals to the choke point.* This targets the "slow decision-making" failure the paper describes.
- **Channels:** marshal mobile app (push, vibrate, map with arrows), SMS fallback, PA and LED message templates, geofenced cell broadcast, and police/EMS dispatch through webhooks.
- **Rumour response:** one-tap verified counter-messages for "bridge collapsed", "free items at X" and similar.
- **Medical and triage module:** START triage tagging in the app, casualty registry (fixes lost records), hospital load balancing, and ambulance route clearing.
- **Human in the loop:** the system recommends and a person decides. Everything is logged with timestamps for accountability.

### Module 6: Learn
- An automatic **after-action report**: a timeline of density, alerts, decisions and response times.
- An **open, anonymised incident dataset** (with consent) so each event improves the models and thresholds for everyone. This is the global network effect.
- **Replay** of historical disasters in simulation to validate the system: Love Parade 2010 has public video and reports, plus Itaewon 2022 and Hathras 2024. The headline proof is: *"Would CrowdSafe have alerted, and how many minutes early?"*

---

## 4. Deployment tiers ("easily used by small or big bodies")

| Tier | Who | Hardware | What they get |
|---|---|---|---|
| **0 — Paper & Phone** | Village temple committee, small rally | Volunteers' phones | Capacity calculator, permit checklist, clicker counting vs permit cap, SMS alerts. **Works offline.** |
| **1 — Laptop Kit** | District police, college fest, mid-size concert | 1 laptop (+ optional GPU) + 4–10 phones/CCTV | Live density heatmap, alerts, marshal app. One-command install. |
| **2 — City Control Room** | City police, Durga Puja and Rath Yatra committees, stadiums | Edge boxes (Jetson Orin / Intel NPU) + server | Multi-site, 100+ cameras, telecom feed, playbooks, CAP integration. |
| **3 — Mega Event / National** | Kumbh, Hajj, national DMA | Kubernetes cluster, multi-region | Regional twins, ML forecasts, inter-agency dashboards, national incident database. |

Same codebase at every tier. Tiers differ only by configuration (feature flags + Helm values).

---

## 5. Technology architecture (resume-grade, chosen for real reasons)

```
EDGE (per camera group)                      CORE (on-prem or cloud)                     CLIENTS
┌────────────────────────┐   MQTT/gRPC   ┌───────────────────────────────┐        ┌─────────────────┐
│ ingest: GStreamer/RTSP  │──────────────►│ NATS JetStream / Redpanda      │──WS──► │ Command dashbd  │
│ infer: ONNX Runtime /   │  (numbers &   │ stream processing (Rust/Go)    │        │ React+MapLibre+ │
│   TensorRT / OpenVINO   │   heatmaps    │   zone state, fusion, rules    │        │ deck.gl         │
│ density + optical flow  │   only)       │ ML service (Python, PyTorch,   │        ├─────────────────┤
│ local buffer, offline   │               │   GNN forecast, Ray Serve)     │        │ Marshal app     │
│ k3s or single binary    │               │ Sim service (JuPedSim/Vadere)  │        │ Flutter, offline│
└────────────────────────┘               │ TimescaleDB (time series)      │        ├─────────────────┤
                                          │ PostGIS (venue geometry)       │        │ SMS/IVR/CAP/    │
 Tier 0: everything in one                │ Keycloak (auth, RBAC)          │        │ webhook gateway │
 Android app + SQLite                     │ OpenTelemetry + Grafana        │        └─────────────────┘
                                          └───────────────────────────────┘
```

**Key engineering decisions** (good to discuss in interviews):
- **Edge-first, offline-first.** Mobile networks collapse at mass gatherings. Every node degrades gracefully and syncs later using CRDTs.
- **Send numbers, not video.** Bandwidth drops about 1000×, which also gives privacy and makes deployment on 4G possible.
- **Event-driven architecture** with a clear schema (Protobuf). Sensors, models and alert channels are all **plugins**, so a city can add its own turnstile vendor without forking.
- **Hard real-time budget:** camera to alert in **< 5 s** (p99). Measured with OpenTelemetry tracing.
- **Safety-critical patterns:** heartbeat monitoring (a dead camera must show as "unknown", never as "safe"), redundancy, a fail-loud UI, and a full audit log.
- **Model ops:** ONNX export, quantised INT8 models for phones, a model registry, per-site calibration, drift monitoring, and active learning from operator corrections.
- **Interoperability:** CAP 1.2 alerts, OGC/GeoJSON venues, OpenAPI for everything.
- **Infra:** Docker Compose for Tier 1, Helm + k3s/K8s for Tiers 2–3, Terraform modules, and GitHub Actions CI with hardware-in-the-loop tests on a Jetson.
- **Localisation:** i18n from day 1 (Bengali, Hindi, Tamil, Arabic, Portuguese and more). Icon-heavy UI for low-literacy marshals.

---

## 6. Validation, so it is trusted and not just a demo
1. **Counting accuracy:** MAE/RMSE on public benchmarks plus your own labelled Indian footage. Publish the results.
2. **Density accuracy in the field:** ground-truth by aerial drone counts at a real event.
3. **Early warning:** replay historical incidents and report **lead time** and **false alarm rate per hour**. Too many false alarms means operators ignore the system.
4. **Usability:** tabletop exercises with real police and committee members. Measure time from alert to action.
5. **Red-team:** test camera failure, network loss, adversarial "fake rumour" inputs and misuse scenarios.

---

## 7. Ethics and governance (these decide whether governments adopt it)
- **License:** Apache-2.0 for code, CC-BY for docs and datasets. Note that the attached paper is CC BY-NC-SA, so cite it but don't copy its text into the repo.
- **Explicit misuse policy:** this is a **safety tool, not a surveillance tool**. No face recognition and no individual tracking. Protest-safety use is for protecting attendees, so it is designed to make crowd-*suppression* use hard (aggregate-only data, retention limits).
- Follow India's **DPDP Act 2023**, GDPR, and NDMA's *"Managing Crowds at Events and Venues of Mass Gathering"* guidelines. Map each guideline item to a feature.
- **Decision support, not autopilot.** Liability and authority stay with humans.

---

## 8. Roadmap

| Phase | Weeks | Deliverable | Resume line |
|---|---|---|---|
| **0. Foundation** | 1–2 | Repo, architecture decision records, Protobuf schemas, CI, docs site | "Designed event-driven architecture for safety-critical system" |
| **1. Plan module MVP** | 3–6 | Venue drawer (MapLibre), capacity calculator, JuPedSim integration, permit PDF | "Built digital-twin crowd simulator" |
| **2. Vision MVP** | 5–10 | Edge service: RTSP → density + flow → zone metrics, homography calibration, ONNX on laptop and Jetson | "Real-time edge CV pipeline, <5 s alert latency" |
| **3. Alert loop** | 9–12 | Stream processor, rules, time-to-critical, dashboard, marshal app, SMS, CAP output | "End-to-end early warning with CAP interoperability" |
| **4. Pilot** | 12–16 | Real deployment at a college fest or local puja pandal with police consent, plus after-action report | "Field-deployed to N-thousand-person event" |
| **5. Forecasting** | 16–24 | GNN forecaster, live-twin what-if, historical replay benchmark | "Spatio-temporal GNN crowd forecasting" |
| **6. Scale & community** | 24+ | Helm/K8s Tier 2–3, telecom adapters, multilingual, partner with SDMA/IIT/NGOs | "Open-source project adopted by X agencies" |

**Start tomorrow:** Phase 1 (capacity + permit) can save lives with zero cameras, and a single person can build it. It also gives you something concrete to show an SDMA or a police commissioner.

---

## 9. Honest limitations
- Vision counting error rises sharply in very dense, dark or rainy scenes, which are exactly the dangerous ones. That is why fusion, uncertainty display and gate counts matter.
- Technology cannot fix a permit for 80k when 250k show up and only 80 police are present. The software's job is to make that gap **visible, documented and impossible to ignore** before the event.
- Adoption is a people problem: training, trust and local champions. Budget time for it equal to the time spent coding.
