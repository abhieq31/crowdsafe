# Contributing to CrowdSafe

Thank you for helping prevent crowd crushes. Contributions of every kind are needed:

- **Crowd-safety practitioners, police, fire and disaster officers**: review thresholds, playbooks and the permit checklist.
- **Translators**: review and add playbook messages in more languages (`src/crowdsafe/data/default_playbook.json`).
- **ML / CV engineers**: density models, calibration UX, accuracy benchmarks (see `docs/vision.md`).
- **Web and mobile developers**: the venue editor and the marshal app.
- **Researchers**: replay historical incidents, and validate lead time and false-alarm rates.

## Ground rules

1. Read [ETHICS.md](ETHICS.md). Features that identify or track individuals will not be merged.
2. Safety logic changes need tests. If you change `planning.py`, update `web/js/planning.js` too and
   regenerate `tests/vectors/planning.json`. Both test suites must pass.
3. Every alert must stay explainable. A new risk signal must add a human-readable reason.
4. Keep the core (`src/crowdsafe/*.py` outside `server/` and `vision/`) dependency-free.

## Dev setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest && node --test tests/js && ruff check src tests
```

Commit messages: imperative mood ("Add stairs flow rate"), with a reference to the source for any safety constant you add.
