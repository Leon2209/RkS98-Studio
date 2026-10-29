# RK-S98 Studio

> [!WARNING]
> **Use at your own risk: direct hardware writes can damage your keyboard.**
> This experimental app sends configuration and image data using a
> reverse-engineered protocol. It may corrupt settings or make a device
> unusable; there is no guaranteed recovery or automatic rollback. The authors
> and contributors provide no warranty and accept no liability to the fullest
> extent permitted by law. See the [MIT license](LICENSE). Continue only if
> you understand and accept these risks.

Linux desktop controls for the Royal Kludge RK-S98: RGB lighting, display
images and GIFs, and keyboard volume/clock sync. Built with Python, PySide6, and
HIDAPI; no account or network connection is required.

This is independent experimental software, not an official Royal Kludge app.
It has been tested on one wired RK-S98; other firmware versions may behave
differently.

![RK-S98 Studio lighting screen](docs/images/mainscreen.png)

## Features

- Select lighting effects, brightness, speed, and built-in or custom colors.
- Upload still images and GIFs with crop, zoom, fit, and animation preview.
- Read back lighting settings, view firmware version, and sync the keyboard
  clock to local computer time.
- Automatically sync the keyboard's volume indicator with desktop output volume
  while the app is open, using OS audio events rather than volume polling.
- Optionally keep volume/time sync running after the app closes and start it at
  graphical login with a default-off systemd user service.
- Manage volume sync in a dedicated Settings tab, with saved in-app preferences,
  background-service status, and the application version.
- Lighting refresh and device discovery do not write lighting configuration.
  Connecting a keyboard triggers a separate volume/time sync when enabled.

See the [changelog](CHANGELOG.md) for release changes.

## Requirements

- Linux with a graphical desktop session.
- RK-S98 connected in wired USB mode. Bluetooth and the 2.4 GHz receiver are
  unsupported.
- Python 3.11 or newer for source installation.
- PulseAudio or PipeWire with its PulseAudio compatibility service, and `pactl`
  (`pulseaudio-utils` on Debian/Ubuntu, `libpulse` on Arch).
- A systemd graphical user session for the optional background service.
  In-app volume sync does not require systemd.

The app checks both USB identity (`258a:01af`) and the HID report descriptor
before opening a configuration interface.

## Install

### Linux packages

Debian/Ubuntu/Mint `.deb` and Arch/CachyOS `.pkg.tar.zst` installers are
available from [GitHub Releases](https://github.com/Leon2209/RkS98-Studio/releases)
and the Linux builds workflow. Packages include Python, Qt, a launcher, and a
USB permission rule. See [packaging instructions](docs/packaging.md) for
compatibility, install, and uninstall details.

After installation, reconnect the keyboard and launch **RK-S98 Studio** from
the application menu or run `rks98`. Do not run the app as root.

### From source

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/rks98
```

For source installs, install the device permission rule and reconnect the
keyboard:

```sh
sudo install -m 0644 udev/70-rks98.rules /etc/udev/rules.d/70-rks98.rules
sudo udevadm control --reload-rules
```

To list candidate device interfaces without changing settings:

```sh
.venv/bin/rks98 --list
```

## Use

Select an effect and adjust its controls in the lighting panel, then click
**Apply lighting**. In the display panel, choose an image or GIF, adjust the
crop or fit, preview animation if applicable, and click **Upload to keyboard**.
Uploading replaces the current display image. **Sync clock** sends the
computer's local date and time.

### Volume sync and Settings

While the app is open, volume sync sends the current default output volume on
connection and follows OS volume/output-device events. Each update also syncs
local date/time. USB operations are serialized; changes during an image upload
are combined into the latest volume and sent afterward. No audio is recorded
and desktop volume is never changed by this feature.

The indicator uses the loudest output channel, rounded to a whole percentage
and capped at 100%; amplification above 100% is not changed on the desktop.
Mute does not replace the stored volume percentage with zero, since no separate
keyboard mute encoding is verified. Missing `pactl` or audio-server failures
are shown in the window; fix the audio connection and restart the app to resume.
In the **Settings** tab, **Sync desktop volume to keyboard** is enabled by
default; turn it off to stop automatic volume/time updates. The preference is
saved, and re-enabling it requests the current volume immediately. The Settings
tab also shows volume-sync status and the application version.
An update already in progress may finish after volume sync is disabled.
Closing the app stops its own volume sync. **Sync clock** includes the known
desktop volume when available; without it, the legacy clock packet sends zero
in that field.

### Optional background volume service

On desktops with a systemd user session, Settings offers **Sync volume in the
background and start at login**. It is off by default. Enabling it requires
confirmation because it permits volume/time hardware writes while the app is
closed and at future graphical logins. No root privileges or login lingering
are required.

To enable it:

1. Open **Settings**.
2. Select **Sync volume in the background and start at login**.
3. Accept the hardware-write confirmation and check the service status.

The service pauses while RK-S98 Studio is open; the existing volume checkbox
controls synchronization during that time. When the app closes, the service
resumes within about five seconds and requests fresh desktop volume. Audio
changes are event-driven; a five-second device/ownership check handles keyboard
reconnection and the app/service handoff, not audio polling. The service only
writes when exactly one descriptor-matched keyboard is connected.

Turning the background checkbox off stops the service and disables login
startup; it does not change the in-app volume preference. Opening Settings
refreshes the service's running/enabled status; error details are logged to the
systemd user journal. Failed writes are not retried, and the service does not
automatically restart after an error. After
fixing the problem, disable and re-enable the service in Settings, or use:

```sh
systemctl --user status rks98-volume.service
journalctl --user -u rks98-volume.service
systemctl --user restart rks98-volume.service
```

To stop it and disable login startup without opening the app:

```sh
systemctl --user disable --now rks98-volume.service
```

The app manages `rks98-volume.service` under your user systemd configuration.
For source installs, keep the virtual environment and project at their original
paths while the service is enabled. Re-enable the service after moving or
switching installations to update its executable path. Disable the service
before uninstalling the app.

## Limitations and safety

- Custom-color history is stored locally; it is not hardware readback and is
  never reapplied automatically. A custom-color write replaces the full color
  table and may overwrite colors set elsewhere.
- Failed writes can leave partial changes. Do not use another configurator at
  the same time; there is no automatic rollback.
- Image upload cancellation does not restore the previous image. USB
  acknowledgements do not guarantee the physical display result.
- Wireless support, firmware updates, key remapping, and per-key lighting are
  not implemented.

See the [protocol reference](docs/protocol.md) for packet details and hardware
evidence.

## Development

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

Automated tests use fake transports and do not write to hardware. Read
[contribution guidelines](CONTRIBUTING.md) before making changes.

Code and project artwork are licensed under the [MIT license](LICENSE).
Third-party licenses and attributions are listed in
[third-party notices](THIRD_PARTY_NOTICES.md).

Developed with AI assistance and human direction/review; human contributors
remain responsible for changes and their validation.
