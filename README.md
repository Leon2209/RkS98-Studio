# RK-S98 Studio

> [!WARNING]
> **Use at your own risk: direct hardware writes can damage your keyboard.**
> This experimental software sends configuration and image data directly to
> the keyboard using a reverse-engineered protocol. We cannot guarantee that
> it will not corrupt settings, make the device unusable, or permanently
> "brick" it, even on a previously tested model or firmware revision.
> There is no guaranteed recovery procedure or automatic rollback.
> To the fullest extent permitted by applicable law, the authors and
> contributors provide no warranty and accept no liability for device damage,
> data loss, or other loss arising from its use. See the [MIT license](LICENSE).
> Only proceed if you understand and accept these risks.

**RGB lighting and display controls for the Royal Kludge RK-S98 on Linux.**

A local desktop app built with Python, PySide6/Qt 6, and HIDAPI.
No browser, account, or server required.

> **Experimental, independent software.** Tested on one wired RK-S98 with
> firmware `2117`. Other firmware revisions may behave differently. This is
> not an official Royal Kludge application.

## Features

| Lighting | Display |
| --- | --- |
| 17 built-in effects plus Off | Upload pictures and animated GIFs |
| Brightness, speed, and built-in palette | Drag to crop and zoom up to 8x |
| Custom RGB with local color history | Fill screen or fit inside with black bars |
| Readback-verified lighting settings | Animated preview and adjustable frame delay |
| Automatic read-only refresh | Upload progress and cancellation |
| Firmware version under the keyboard image | Sync the keyboard clock to local computer time |

Settings load on startup and refresh every five seconds when you're not editing.
Changes are sent only when you apply them, sync the clock, or upload an image.

## Requirements

- Linux with a graphical desktop session.
- Python **3.11 or newer** for source installs (bundled with application downloads).
- An RK-S98 connected in **wired USB mode**.
- Qt platform libraries and Linux udev support.

The target USB ID is `258a:01af`. The app also checks the HID report descriptor
before opening a configuration interface; USB IDs alone do not prove compatibility.
Bluetooth and the 2.4 GHz receiver are not supported.

## Install and run

### Application downloads

The **Linux builds** GitHub workflow produces two native installers:
**`.deb` for Debian/Ubuntu/Mint** and **`.pkg.tar.zst` for Arch/CachyOS**.
Both bundle Python and Qt and install a menu entry, icon, and USB permission rule.
Downloads are available from GitHub Releases after a release is built and
published, or as artifacts from a manually triggered workflow run.

**Debian/Ubuntu/Mint** (replace the version with your download):

```sh
sha256sum -c rks98-studio_0.1.0-6_amd64.deb.sha256
sudo apt install ./rks98-studio_0.1.0-6_amd64.deb
sudo udevadm control --reload-rules
```

**Arch/CachyOS**:

```sh
sha256sum -c arch-package.sha256
sudo pacman -U rks98-studio-0.1.0-6-x86_64.pkg.tar.zst
sudo udevadm control --reload-rules
```

Reconnect the keyboard, then launch **RK-S98 Studio** from your application menu
or run `rks98`. The package installs the icon, launcher, and USB permission rule.
The same bundled icon is used for the application window and taskbar.
To uninstall, use `sudo apt remove rks98-studio` or `sudo pacman -R rks98-studio`.

Release builds target **x86_64, glibc 2.35+** (Ubuntu 22.04 build baseline).
Package dependencies must also be available on the target distribution.
Not all distribution versions have been tested. Fedora/RPM, portable builds,
Alpine/musl, ARM, and 32-bit builds are not included.
See [build and release instructions](docs/packaging.md) for details.

### From source

Download or clone this repository, then run these commands from its directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/rks98
```

For development, use `pip install -e '.[dev]'` in the same virtual environment.
If your distribution does not include venv support, install its Python venv
package first (for example, `python3-venv` on Debian/Ubuntu).

### USB permissions

**Do not run the desktop app as root.** Install the supplied device-scoped rule:

```sh
sudo install -m 0644 udev/70-rks98.rules /etc/udev/rules.d/70-rks98.rules
sudo udevadm control --reload-rules
```

Native packages already install the rule; do not install another copy manually.
The commands above are for source installations.

Unplug and reconnect the keyboard, then click **Refresh**.
The rule uses `uaccess`/systemd-logind to grant the active local desktop user
access. It does not make all HID devices world-writable. Systems without
systemd-logind may need a distribution-specific permissions setup.

To list candidate interfaces without changing keyboard settings:

```sh
.venv/bin/rks98 --list
```

## Use

### Lighting

1. Connect the keyboard in wired USB mode.
2. Select an effect and adjust brightness, speed, or color.
3. Click **Apply lighting**. **Discard** reloads the keyboard's current settings.

Brightness uses the keyboard's native **0-20** range. The built-in palette is
Red, Green, Blue, Yellow, Pink, Cyan, White, and Multicolor/rainbow.
Enable **Custom color** to choose an RGB value.
Rainbow, Windmill, and Waterfall use their own colors, so color controls are hidden.

Polling pauses while there are unsaved edits or a USB operation is running.
The app always reads and edits the first onboard lighting slot; there is no
profile selector or active-slot switching.

### Pictures and GIFs

1. Open **Display** and choose a PNG, JPEG, BMP, WebP, or GIF.
2. In **Fill screen**, drag the preview to select an area. Scroll or use **Zoom**
   to zoom in; **Reset** restores the centered crop.
3. Choose **Fit inside** to show the entire image with black bars instead.
4. For animations, adjust the frame delay and check the animated preview.
5. Click **Upload to keyboard**. This replaces the existing display image.

Images can have any dimensions; output is resized to **320 x 172**.
The same crop applies to all animation frames. The firmware supports up to
**100 frames**; longer animations are sampled evenly and their delay is scaled.
Variable frame delays are reduced to a single average delay.
The decoder currently reads at most 1,000 source frames.

A still image took about **13 seconds** on the development keyboard. Upload
time scales with frame count: 100 frames can take roughly 20 minutes.
**Cancel** requests a transfer abort; it does not restore the previous image.
Only still-image transfer acknowledgements have been verified on hardware;
GIF playback has not yet been physically verified.

### Clock and firmware

**Sync clock** sends your computer's local date and time. The clock shown in the
app is a computer-time preview, not a reading from the keyboard.
The firmware version is queried once per connection and displayed below the
keyboard image. The app does not check for or install firmware updates.

## Important limitations

- **Custom RGB readback:** the tested firmware rejects color-table reads, as
  does RK's web tool. The app remembers colors it successfully sent rather than
  pretending to read them back. A write replaces the full color table and may
  overwrite unknown custom colors set in another app. History is scoped to
  local profile slots, not a unique physical keyboard.
- **Local history can be stale:** built-in Red and custom RGB share palette
  index 0. Changes made outside this app cannot always be distinguished.
  Refreshing never reapplies saved colors.
- **No automatic rollback:** failed writes can leave partial changes. Read the
  keyboard settings before trying again. Do not use another configurator at
  the same time.
- **Verification is limited:** profile writes are checked by readback. Clock
  transfers and acknowledged image uploads still need a physical display check.
  Other profile writes, speed settings, and firmware variants have not all been
  individually tested on hardware.
- **Not implemented:** key remapping, per-key lighting, other screen settings,
  wireless transports, firmware flashing, or automatic clock synchronization.

For packet formats, hardware evidence, and write-only color behavior, see
[the protocol reference](docs/protocol.md).

## Troubleshooting

| Problem | What to check |
| --- | --- |
| No keyboard detected | Use a data-capable USB cable and wired mode. Run `rks98 --list`. |
| Permission denied | Install the udev rule, reconnect, and use your normal desktop account. |
| Unsupported interface | Check firmware and descriptor details from `--list`; do not bypass the check. |
| Unexpected response or failed upload | Close RK's web tool and other configurators. Reconnect and refresh. |
| Qt platform plugin error | Install your distribution's Qt/X11/Wayland runtime libraries. On Debian/Ubuntu, `libxcb-cursor0` is often needed for X11. |
| Color differs after refresh | Custom colors may be local history rather than hardware readback; see the limitations above. |

The footer appears only for errors; hover over it for technical details.
Routine lighting/clock updates do not show status messages. Upload progress
and cancellation are shown inside the loading indicator in the Display card.
When reporting a problem, include your distribution, Python version, firmware,
connection mode, reproduction steps, and sanitized diagnostics.

## Development

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/python -m build
```

Tests use fake transports and **never write to hardware**. GitHub Actions runs
lint, formatting, tests, and package builds on Python 3.11 and 3.14.
Use `.venv/bin/python -m ruff format .` to format changes.

```text
src/rks98/
  protocol.py   Packet encoding and validation (no Qt or USB)
  device.py     Descriptor-based discovery and HID transport
  service.py    Read/modify/write operations and upload flow control
  display.py    Image decoding, cropping, and RGB565 conversion
  gui.py        Qt interface and background operations
tests/          Hardware-free regression tests
udev/           Linux device permission rule
```

Window geometry and custom-color history are stored locally through Qt
QSettings. The app does not contact RK's website at runtime.
See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution and hardware-test guidance.

## AI-assisted development

This software was created with **GitHub Copilot** and co-authored by human
contributors. AI assistance was used for implementation, documentation, and
tests, alongside human direction and hardware feedback.

AI-generated code can contain mistakes, and human involvement and passing tests
do not guarantee correctness or hardware safety. The protocol is reverse-engineered
and hardware validation is limited; review the code and the risk warning above
before using it. This disclosure does not imply endorsement by GitHub or Royal Kludge.

Project-wide guidance for AI coding assistants is in
[`.github/copilot-instructions.md`](.github/copilot-instructions.md).

## License and acknowledgements

Code and project artwork are licensed under the [MIT license](LICENSE).
The keyboard image was created and supplied by the project owner; it replaces
the previously bundled vendor image. See
[third-party notices](THIRD_PARTY_NOTICES.md) for artwork attribution, trademarks,
and dependency licenses.

Protocol research references:

- [Official Royal Kludge web tool](https://drive.rkgaming.com/)
- [rk-s98-mac-time-sync](https://github.com/nai-tro/rk-s98-mac-time-sync)
