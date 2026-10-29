# Contributing

Small, focused fixes, tests, documentation improvements, and compatibility
reports are welcome. Discuss larger features in an issue before implementing them.

## Development setup

Use Linux and Python 3.11 or newer:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/python -m build
```

Apply formatting with `.venv/bin/python -m ruff format .`.
The tests use fake devices and do not require a connected keyboard.
See [Linux packaging](docs/packaging.md) for Debian and Arch packages.

PySide6 is currently limited to versions below 6.12. With Python 3.11 and
PySide6 6.12.0, all tests can pass before Python aborts during shutdown with
`bool_dealloc`. The same suite exits cleanly with 6.8.3 and 6.11.3. Keep the
upper bound until a newer Qt binding passes the full suite, including a clean
process exit, on the supported Python versions.

## Code and pull requests

- Keep packet encoding independent of Qt and USB.
- Put device operations in the service layer, off the GUI thread.
- Validate inputs before sending packets; preserve unknown configuration bytes.
- Report transfer failures explicitly. Do not silently claim hardware verification.
- Add regression tests and update documentation for behavior changes.
- Describe what changed, how it was tested, and any hardware/firmware involved.
- Do not add credentials, captured typing, personal device identifiers, or local logs.
- Disclose substantial AI assistance in your pull request. Human contributors
  remain responsible for reviewing their changes and describing actual validation.

AI coding assistants should follow the
[project instructions](.github/copilot-instructions.md).

## Hardware testing

Hardware writes are separate from automated tests. Only test writes on a device
you own or have permission to change. Close other configurators, use wired USB
mode, record the firmware version and original settings, and document exactly
what was confirmed physically versus acknowledged by USB.

Do not introduce guessed commands, firmware flashing, or automatic rollback
without protocol evidence. Image uploads replace the current display image;
failed writes can leave partial changes. See [the protocol notes](docs/protocol.md).

## Reporting problems

Include your Linux distribution, Python version, app version, firmware version
(if available), wired connection mode, steps to reproduce, and the footer error
details. The output of `rks98 --list` helps identify descriptor mismatches.
Remove personal information before posting logs.

## Licensing

Contributions to the code are provided under the [MIT license](LICENSE).
Third-party assets remain subject to [their own notices](THIRD_PARTY_NOTICES.md).
