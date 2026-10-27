import configparser
import importlib.util
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_linux", ROOT / "packaging/build_linux.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def payload(root):
    binary = root / "opt/rks98-studio/rks98-studio"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o700)
    for path in (root, *root.rglob("*")):
        if path.is_dir():
            path.chmod(0o700)
    launcher = root / "usr/bin/rks98"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to("/opt/rks98-studio/rks98-studio")
    return binary


def test_arch_kit_contains_verified_native_payload(tmp_path):
    source = tmp_path / "payload"
    payload(source)
    builder.normalize_permissions(source)
    kit = builder.arch_kit(source, tmp_path, "0.1.0")
    assert kit.is_file()
    archive = tmp_path / "arch-kit/rks98-studio-0.1.0-payload.tar.gz"
    recipe = (tmp_path / "arch-kit/PKGBUILD").read_text()
    assert f"sha256sums=('{builder.sha256(archive)}')" in recipe
    assert "@GLIBC@" not in recipe and "@VERSION@" not in recipe
    with tarfile.open(archive) as stream:
        for member in stream.getmembers():
            assert member.uid == member.gid == 0


def test_arch_package_is_accessible_to_non_owner(tmp_path):
    source = tmp_path / "payload"
    binary = payload(source)
    builder.normalize_permissions(source)
    builder.arch_kit(source, tmp_path, "0.1.0")
    package = tmp_path / "pkg"
    package.mkdir()
    subprocess.run(
        ["bash", "-eu", "-c", 'source "$1"; package', "test", str(tmp_path / "arch-kit/PKGBUILD")],
        env=dict(os.environ, srcdir=str(tmp_path / "arch-kit"), pkgdir=str(package)),
        check=True,
    )
    installed = package / binary.relative_to(source)
    assert installed.stat().st_mode & 0o055 == 0o055
    for path in package.rglob("*"):
        if path.is_dir():
            assert path.stat().st_mode & 0o055 == 0o055
    assert (package / "usr/bin/rks98").readlink() == Path("/opt/rks98-studio/rks98-studio")


def test_debian_control_and_permissions(tmp_path, monkeypatch):
    source = tmp_path / "payload"
    payload(source)
    calls = []

    def run(command, check):
        assert check
        calls.append(command)
        Path(command[-1]).write_bytes(b"test deb")

    monkeypatch.setattr(builder.subprocess, "run", run)
    deb = builder.debian_package(source, tmp_path, tmp_path, "0.1.0", "dpkg-deb")
    control = (tmp_path / "debian/DEBIAN/control").read_text()
    assert "Package: rks98-studio\n" in control
    assert "Version: 0.1.0-6\n" in control
    assert "Architecture: amd64\n" in control
    assert "Depends: libc6 (>= " in control and "@" not in control
    assert calls[0][:3] == ["dpkg-deb", "--root-owner-group", "-Zgzip"]
    assert (tmp_path / "debian/opt").stat().st_mode & 0o055 == 0o055
    assert (tmp_path / f"{deb.name}.sha256").read_text().startswith(builder.sha256(deb))


def test_build_refuses_to_overwrite_existing_work(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder.shutil, "which", lambda _: "/usr/bin/dpkg-deb")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    (tmp_path / "build/native").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="already exists"):
        builder.build()


def test_missing_debian_builder_is_reported(monkeypatch):
    monkeypatch.setattr(builder.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="dpkg-deb is required"):
        builder.build()


def test_desktop_launcher_matches_bundle():
    desktop = configparser.ConfigParser(interpolation=None)
    desktop.read(ROOT / "packaging/rks98.desktop")
    assert desktop["Desktop Entry"]["Exec"] == "rks98"
    assert desktop["Desktop Entry"]["Icon"] == "rks98"
    assert desktop["Desktop Entry"]["Terminal"] == "false"
    assert desktop["Desktop Entry"]["StartupWMClass"] == "rks98"
    assert (ROOT / "src/rks98/assets/rks98.svg").is_file()
