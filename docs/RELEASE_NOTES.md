CrowdSafe v0.2.0 makes the camera pipeline usable on dense crowds, and makes the whole system deployable
by a local body or event organiser without programming. Start with [docs/DEPLOY.md](https://github.com/abhieq31/crowdsafe/blob/main/docs/DEPLOY.md).

### Install
- Control room: `docker compose up -d`, or `docker run -p 8000:8000 -v $PWD/data:/data ghcr.io/abhieq31/crowdsafe`
- Camera device: `ghcr.io/abhieq31/crowdsafe-edge` (density model included, works offline)
- Python: `pip install "crowdsafe[server,vision,onnx] @ <wheel URL from this release>"`

### New
- **A real density model.** DM-Count (MIT), installed with `crowdsafe model fetch` and checked against a
  pinned SHA-256. Published accuracy: MAE 69.9 on ShanghaiTech A and 14.4 on B, never trained on either. It
  undercounts the densest scenes; see docs/vision.md.
- **`crowdsafe venue new`** describes a venue in plain questions and prints its safe capacity and structural
  problems (single exit, trapped zones, crowd beyond exit capacity).
- **`crowdsafe setup` / `check` / `edge`:** answer once, test everything (camera, calibration, model, device
  speed, server, access code, zone names), then run with no options.
- **Operator sign-in** on the dashboard. Approved actions record the operator's name, and work when an
  access code is set.
- **An event record on disk:** every alert, approved action, trigger and restart, fsynced as it happens,
  kept across crashes, downloadable at `/api/log`.

### Safety fixes
- A camera device with a fast clock could look alive long after it died. The server now corrects device
  clocks and never accepts future timestamps.
- A batch the server rejected was retried forever, silencing that camera for the rest of the event. Bad
  values are now dropped one by one, and rejected batches are discarded.
- The dashboard showed an action as done even when the server had not recorded it. It now says
  "NOT recorded" and re-enables the button.
- Devices too slow to report within the 15 s no-data limit are rejected by `check`, instead of making
  zones flicker.
- Slow devices report less often instead of shrinking the model input, because 512 px quadruples the
  undercount of the densest scenes.
