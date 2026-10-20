Room to Breathe v0.3.0 is the project's new name (formerly CrowdSafe) and adds a web app that needs no
installation: **https://abhieq31.github.io/roomtobreathe/**

### New
- **Web app, nothing to install.** What-if studies, photo counting, capacity planning and the gate counter
  all run in the browser. Live camera monitoring still runs on a computer at the venue
  ([docs/DEPLOY.md](https://github.com/abhieq31/roomtobreathe/blob/main/docs/DEPLOY.md)).
- **What-if studies on your own venue** (`roomtobreathe whatif`, `POST /api/whatif`, and the web app):
  attendance, surge, a blocked gate or a full evacuation, each run twice, with nobody acting and with
  operators approving the playbook at the first RED alert. It reports where and when each zone reaches
  critical density, how many minutes of warning the RED alert gives, and how long an evacuation takes. In
  the browser it runs the same Python engine through Pyodide, so the numbers are identical.
- **Photo count:** DM-Count in the browser (WebGPU or WebAssembly). Mark a ground rectangle of known size to
  get the people per m² and the densest square metre, and download it as a camera calibration file for
  `roomtobreathe edge`. The photo never leaves the device.

### Fixed
- A venue saved and loaded through the API had its usable-area share applied twice (the demo hall shrank
  from 720 to 540 m²).
- Engine ticks are 3x faster (stop-and-go detection is cached for 5 s).

### Renamed
- Package and command `roomtobreathe` (short alias `rtb`), environment variables `RTB_*`, images
  `ghcr.io/abhieq31/roomtobreathe` and `roomtobreathe-edge`. Old GitHub links redirect.
