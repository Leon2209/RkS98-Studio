# RK-S98 Studio: AI contributor instructions

## Context and architecture

This is an experimental Linux desktop app using Python 3.11+, PySide6, and
HIDAPI's `hidraw` binding. It writes directly to keyboard hardware.
Read [CONTRIBUTING.md](../CONTRIBUTING.md) before changes and
[docs/protocol.md](../docs/protocol.md) before changing device behavior.

- Keep packet encoding and validation in `src/rks98/protocol.py`, independent of Qt and USB.
- Keep HID discovery and transport in `device.py`, operations in `service.py`,
  image conversion in `display.py`, and UI/background jobs in `gui.py`.
- Reuse existing helpers and patterns; keep changes focused and update related docs.

## Hardware safety

- Never send writes to a real device without explicit user permission for that
  test and its effects. Automated tests must use fake transports.
- Preserve descriptor checks, packet validation, unknown configuration bytes,
  transfer timeouts, cancellation, and readback verification.
- Do not guess commands, bypass compatibility checks, add firmware flashing,
  or add automatic rollback/retries without documented protocol evidence.
- Startup, polling, and refresh must not write configuration. Local custom-color
  history is not hardware readback and must never be reapplied automatically.
- Keep USB operations off the GUI thread. In HIDAPI, input-read timeout 0 means
  wait forever; use a positive timeout for bounded reads.
- Surface failures explicitly. An acknowledged USB transfer is not proof of
  physical display behavior, hardware safety, or recovery after a failed write.
- Preserve the README's hardware-risk warning and accurate AI authorship disclosure.
  Never describe an unperformed hardware test as verified.

## Validation

After code changes, run the relevant hardware-free tests and checks:

```sh
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

For packaging changes, also run `.venv/bin/python -m build`.
Install the declared `.[dev]` dependencies if needed; do not introduce new
tools unnecessarily. Report which checks ran and any remaining limitations.

## Licensing and privacy

Code and project artwork, including the owner-supplied keyboard image, are
MIT-licensed. Preserve
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) and do not assume other
vendor assets are freely redistributable. Never include secrets, captured
typing, personal identifiers, or private logs in commits or reports.
