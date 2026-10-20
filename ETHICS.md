# Ethics & acceptable use

CrowdSafe exists to keep people alive in crowds. It is a **safety** tool, not a **surveillance** tool.

## Hard lines (enforced by design and by project policy)

1. **No face recognition, no re-identification, no individual tracking.** The vision pipeline estimates
   aggregate density and motion fields. Pull requests that add identity features will be rejected.
2. **Frames stay on the edge device.** Only per-zone aggregate numbers are sent. Any video clip retention
   (e.g. for after-action review) must be opt-in, short, access-controlled and documented.
3. **Decision support, not autopilot.** CrowdSafe recommends; a trained, accountable person decides.
   Every executed action is logged with who approved it.
4. **Protecting attendees, including protesters.** At political gatherings CrowdSafe's role is to keep
   participants safe from crushes. It must not be used to identify, profile or disperse lawful assemblies.

## Data protection

Deployers are responsible for complying with local law, for example India's Digital Personal Data
Protection Act 2023 or the EU GDPR. Aggregate density data is designed to contain no personal data.
Telecom or Wi-Fi feeds must be aggregated and anonymised *before* they reach CrowdSafe.

## Known limitations (read before deploying)

- Counting accuracy drops in very dense, dark, rainy or occluded scenes, which are the most dangerous
  ones. Always combine cameras with gate counts and human reports, and watch the ± uncertainty.
- Thresholds and playbooks are defaults from the literature. Calibrate them for your venue with your
  police, fire and medical teams *before* the event.
- Translations in the default playbook must be reviewed by native speakers.
- Software cannot fix an event permitted for 80,000 people where 250,000 come and 80 police are deployed.
  Its job is to make that gap visible and documented, and impossible to ignore *before* the event.

## Reporting misuse or safety issues

Open a GitHub issue labelled `safety`, or contact the maintainers privately for security issues (see SECURITY.md).
