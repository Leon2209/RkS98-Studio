"""Build a native Debian installer and the input kit for a native Arch package."""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QT_LICENSES = {
    "LGPL-3.0-only.txt": "da7eabb7bafdf7d3ae5e9f223aa5bdc1eece45ac569dc21b3b037520b4464768",
    "GPL-3.0-only.txt": "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903",
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def normalize_permissions(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        if path.is_symlink():
            continue
        path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)


def root_owned(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    return info


def arch_kit(payload: Path, work: Path, version: str) -> Path:
    kit = work / "arch-kit"
    kit.mkdir()
    archive = kit / f"rks98-studio-{version}-payload.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        for path in sorted(payload.iterdir()):
            stream.add(path, arcname=path.name, filter=root_owned)
    template = (ROOT / "packaging/arch/PKGBUILD.in").read_text()
    (kit / "PKGBUILD").write_text(
        template.replace("@VERSION@", version)
        .replace("@SHA256@", sha256(archive))
        .replace("@GLIBC@", os.confstr("CS_GNU_LIBC_VERSION").split()[1])
    )
    packed = work / "arch-kit.tar.gz"
    with tarfile.open(packed, "w:gz") as stream:
        stream.add(kit, arcname="arch-kit", filter=root_owned)
    return packed


def debian_package(
    payload: Path,
    output: Path,
    work: Path,
    version: str,
    dpkg_deb: str,
) -> Path:
    staging = work / "debian"
    shutil.copytree(payload, staging, symlinks=True)
    control = staging / "DEBIAN"
    control.mkdir()
    size_kib = (
        sum(
            path.stat().st_size
            for path in payload.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
        + 1023
    ) // 1024
    template = (ROOT / "packaging/debian/control.in").read_text()
    (control / "control").write_text(
        template.replace("@VERSION@", version)
        .replace("@SIZE@", str(size_kib))
        .replace("@GLIBC@", os.confstr("CS_GNU_LIBC_VERSION").split()[1])
    )
    normalize_permissions(staging)
    package = output / f"rks98-studio_{version}-6_amd64.deb"
    subprocess.run(
        [dpkg_deb, "--root-owner-group", "-Zgzip", "--build", str(staging), str(package)],
        check=True,
    )
    (output / f"{package.name}.sha256").write_text(f"{sha256(package)}  {package.name}\n")
    return package


def build(dpkg_deb: str = "dpkg-deb") -> None:
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise RuntimeError("The native builds currently require Linux x86_64.")
    if shutil.which(dpkg_deb) is None:
        raise RuntimeError("dpkg-deb is required. Install dpkg or provide --dpkg-deb.")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    work = ROOT / "build/native"
    if work.exists():
        raise RuntimeError(f"{work} already exists. Move or remove that build directory first.")
    work.mkdir(parents=True)
    payload = work / "payload"
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--onedir",
            "--name",
            "rks98-studio",
            "--collect-data",
            "rks98",
            "--hidden-import",
            "hidraw",
            "--copy-metadata",
            "PySide6",
            "--copy-metadata",
            "PySide6_Essentials",
            "--copy-metadata",
            "shiboken6",
            "--copy-metadata",
            "hidapi",
            "--copy-metadata",
            "rks98-control",
            "--distpath",
            str(payload / "opt"),
            "--workpath",
            str(work / "pyinstaller"),
            "--specpath",
            str(work),
            str(ROOT / "packaging/native/entrypoint.py"),
        ],
        cwd=ROOT,
        check=True,
    )
    binary = payload / "opt/rks98-studio/rks98-studio"
    launcher = payload / "usr/bin/rks98"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to("/opt/rks98-studio/rks98-studio")
    copy(ROOT / "packaging/rks98.desktop", payload / "usr/share/applications/rks98.desktop")
    copy(
        ROOT / "src/rks98/assets/rks98.svg",
        payload / "usr/share/icons/hicolor/scalable/apps/rks98.svg",
    )
    copy(ROOT / "udev/70-rks98.rules", payload / "usr/lib/udev/rules.d/70-rks98.rules")
    licenses = payload / "usr/share/licenses/rks98-studio"
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        copy(ROOT / name, licenses / name)
    for name, checksum in QT_LICENSES.items():
        destination = licenses / name
        urllib.request.urlretrieve(
            f"https://raw.githubusercontent.com/qt/qtbase/v6.8.3/LICENSES/{name}",
            destination,
        )
        if sha256(destination) != checksum:
            raise RuntimeError(f"Qt license checksum mismatch: {name}")
    copy(ROOT / "THIRD_PARTY_NOTICES.md", payload / "usr/share/doc/rks98-studio/copyright")
    normalize_permissions(payload)
    for argument in ("--help", "--packaging-smoke-test"):
        subprocess.run(
            [str(binary), argument],
            check=True,
            env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        )
    deb = debian_package(payload, output, work, version, dpkg_deb)
    kit = arch_kit(payload, work, version)
    print(f"Built {deb}\nArch build input: {kit}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dpkg-deb", default="dpkg-deb", help="Path to Debian package builder")
    build(parser.parse_args().dpkg_deb)
