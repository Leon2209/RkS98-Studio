# Native Linux application builds

Only two installer formats are provided:

- **Debian/Ubuntu/Mint:** `rks98-studio_<version>-6_amd64.deb`
- **Arch/CachyOS:** `rks98-studio-<version>-6-x86_64.pkg.tar.zst`

Both install the bundled application in `/opt/rks98-studio`, a command at
`/usr/bin/rks98`, a desktop launcher, icon, license notices, and a udev rule.
Python and Qt are bundled; target systems do not need a matching Python ABI.
There is no portable format or RPM package.

## Install and uninstall

Debian/Ubuntu/Mint:

```sh
sha256sum -c rks98-studio_0.1.0-6_amd64.deb.sha256
sudo apt install ./rks98-studio_0.1.0-6_amd64.deb
sudo udevadm control --reload-rules
# To remove:
sudo apt remove rks98-studio
```

Arch/CachyOS:

```sh
sha256sum -c arch-package.sha256
sudo pacman -U rks98-studio-0.1.0-6-x86_64.pkg.tar.zst
sudo udevadm control --reload-rules
# To remove:
sudo pacman -R rks98-studio
```

Reconnect the keyboard and launch **RK-S98 Studio** from the application menu.
Do not run the app as root. Check the README hardware risk warning before use.
If you previously installed the source udev rule in `/etc/udev/rules.d`, it
takes precedence over the package's rule; avoid maintaining duplicate rules.
Package removal does not erase your local QSettings history.

## Compatibility

GitHub release builds use Ubuntu 22.04, Python 3.11, and pinned PySide6 6.8.3.
They target x86_64, glibc 2.35 or newer, and a graphical Linux desktop with
the native dependencies declared by each package. Availability of those packages
also limits supported distribution versions. Not every Debian/Ubuntu version is
guaranteed compatible; CI exercises installation on Ubuntu 22.04 and current Arch.
Alpine/musl, ARM, 32-bit systems, and RPM distributions are not supported builds.

Local builds on newer distributions can require a newer glibc and newer system
libraries; they are not substitutes for the release baseline. The generated
packages record the build host's glibc version.

## GitHub builds

1. Push this repository, then use **Actions > Linux builds > Run workflow**.
2. The workflow tests source, builds the shared frozen payload and real `.deb`
   using `dpkg-deb`, installs it with apt, checks startup/menu metadata, and removes it.
3. An internal artifact transfers a checksum-pinned payload and PKGBUILD to an
   Arch builder. It builds with makepkg, installs via pacman, checks startup as
   a non-root user, and removes the package.
4. Workflow downloads expose the Debian and Arch installers and SHA-256 files.
   The internal Arch build-input archive is not a public release asset.
5. To release, set `project.version`, commit, and push a matching `v<version>` tag.
   Successful builds create a **draft release** with only the two installer formats
   and checksums. Review and test before publishing.

No CI test writes to hardware. The frozen-app smoke test constructs Qt widgets
and checks the image asset without entering the event loop or enumerating devices.
Checksums detect corrupted downloads, not publisher authenticity.

## Build locally

Use Ubuntu 22.04 with Python 3.11 for release-compatible builds.
Install `dpkg` (providing `dpkg-deb`) and normal Qt runtime libraries first.

```sh
python3.11 -m venv .venv-packaging
.venv-packaging/bin/python -m pip install -r packaging/native/requirements.txt
.venv-packaging/bin/python -m pip install -e '.[dev]'
.venv-packaging/bin/python packaging/build_linux.py
```

Alternatively, install `.[packaging]` into your development environment.
The builder creates the Debian installer in `dist/` and an internal Arch kit at
`build/native/arch-kit/`. On Arch/CachyOS, run:

```sh
cd build/native/arch-kit
makepkg -s
```

Do not run makepkg as root. Install the resulting package with `pacman -U` only
when you intend to change your system. Move or remove the specific `build/native`
staging directory before rebuilding. The builder refuses to overwrite it.

The shared payload is a regular tar archive, not a portable application.
No application-image tooling is required. `dpkg-deb` is used directly to produce
the Debian package with root ownership, normalized file permissions, and native
dependency metadata.

## Licensing

See [third-party notices](../THIRD_PARTY_NOTICES.md). The builder includes Qt
license texts (downloaded from versioned URLs and SHA-256 checked), HIDAPI and Qt
metadata, and project notices. Dynamic Qt libraries can be replaced and the
application rebuilt; no signature lock restricts replacement.
