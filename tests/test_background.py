import shutil
import subprocess
import sys
import time
from threading import Event
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QApplication

from rks98 import background
from rks98.device import Keyboard


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    return tmp_path


def test_foreground_lease_excludes_service_and_releases(runtime):
    with background.ProcessLease():
        with pytest.raises(background.LeaseBusy):
            with background.ProcessLease():
                pytest.fail("Second owner acquired lease")
    with background.ProcessLease():
        pass


def test_missing_runtime_is_explicit(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    with pytest.raises(RuntimeError, match="XDG_RUNTIME_DIR"):
        background.ProcessLease()


def test_lease_timeout_is_bounded(runtime):
    with background.ProcessLease():
        started = time.monotonic()
        with pytest.raises(background.LeaseBusy):
            with background.ProcessLease(timeout=0.1):
                pytest.fail("Second owner acquired lease")
        assert 0.1 <= time.monotonic() - started < 1


def test_foreground_lease_excludes_other_processes(runtime):
    command = [
        sys.executable,
        "-c",
        "from rks98.background import ProcessLease; lease = ProcessLease(); lease.__enter__()",
    ]
    with background.ProcessLease():
        result = subprocess.run(command, capture_output=True, timeout=5)
        assert result.returncode != 0
        assert b"LeaseBusy" in result.stderr
    result = subprocess.run(command, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr.decode()


def test_service_unit_command_and_login_scope(monkeypatch):
    monkeypatch.setattr(sys, "executable", '/test/with spaces/%name"$python')
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    unit = background.service_unit()
    assert '"/test/with spaces/%%name\\"$$python" "-m" "rks98" "--volume-service"' in unit
    assert "WantedBy=graphical-session.target" in unit
    assert "After=graphical-session.target" not in unit
    assert "Restart=no" in unit
    assert "User=" not in unit
    monkeypatch.setattr(sys, "frozen", True)
    assert '"-m"' not in background.service_unit()


def test_generated_unit_passes_systemd_validation(tmp_path):
    tool = shutil.which("systemd-analyze")
    if tool is None:
        pytest.skip("systemd-analyze is not installed")
    unit = tmp_path / background.UNIT_NAME
    unit.write_text(background.service_unit())
    result = subprocess.run(
        [tool, "--user", "verify", str(unit)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("value", ["bad\npath", "bad\rpath", "bad\x00path"])
def test_service_command_rejects_line_breaks(value):
    with pytest.raises(ValueError):
        background.systemd_argument(value)


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    manager = background.UserVolumeService()
    calls = []
    state = {"enabled": False, "active": False, "failure": None}

    def run(command, **kwargs):
        assert kwargs["timeout"] == 10
        assert command[:2] == ["systemctl", "--user"]
        calls.append(command[2:])
        if state["failure"]:
            return SimpleNamespace(returncode=1, stderr=state["failure"], stdout="")
        if command[2] == "enable":
            state.update(enabled=True, active=True)
        if command[2] == "disable":
            state.update(enabled=False, active=False)
        output = (
            "LoadState=loaded\n"
            f"UnitFileState={'enabled' if state['enabled'] else 'disabled'}\n"
            f"ActiveState={'active' if state['active'] else 'inactive'}\n"
        )
        return SimpleNamespace(returncode=0, stderr="", stdout=output)

    monkeypatch.setattr(background.subprocess, "run", run)
    return manager, calls, state


def test_service_enable_disable_uses_user_manager_and_persists_unit(manager):
    manager, calls, _state = manager
    assert manager.state() == background.ServiceState(False, False)
    assert not manager.path.exists()
    assert manager.set_enabled(True) == background.ServiceState(True, True)
    assert manager.path.read_text() == background.service_unit()
    assert ["enable", "--now", background.UNIT_NAME] in calls
    assert manager.set_enabled(False) == background.ServiceState(False, False)
    assert ["disable", "--now", background.UNIT_NAME] in calls


def test_custom_service_is_never_overwritten(manager):
    manager, calls, _state = manager
    manager.path.parent.mkdir(parents=True)
    manager.path.write_text("custom unit\n")
    with pytest.raises(RuntimeError, match="custom"):
        manager.set_enabled(True)
    assert manager.path.read_text() == "custom unit\n"
    assert not calls


def test_symlink_service_is_never_overwritten(manager, tmp_path):
    manager, calls, _state = manager
    target = tmp_path / "custom.service"
    target.write_text(background.UNIT_MARKER)
    manager.path.parent.mkdir(parents=True)
    manager.path.symlink_to(target)
    with pytest.raises(RuntimeError, match="custom"):
        manager.set_enabled(True)
    assert target.read_text() == background.UNIT_MARKER
    assert not calls


def test_systemctl_failure_is_reported(manager):
    manager, _calls, state = manager
    state["failure"] = "No user bus"
    with pytest.raises(RuntimeError, match="No user bus"):
        manager.set_enabled(True)


def test_systemctl_timeout_is_not_reported_as_success(manager, monkeypatch):
    manager, _calls, _state = manager

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("systemctl", 10)

    monkeypatch.setattr(background.subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        manager.set_enabled(True)


def test_service_enable_partial_failure_retains_honest_state(manager, monkeypatch):
    manager, _calls, state = manager
    run = manager._systemctl

    def enable_then_fail(*args):
        result = run(*args)
        if args[0] == "enable":
            state["active"] = False
        return result

    monkeypatch.setattr(manager, "_systemctl", enable_then_fail)
    with pytest.raises(RuntimeError, match="not running"):
        manager.set_enabled(True)
    assert manager.state() == background.ServiceState(True, False)


@pytest.fixture
def daemon(runtime, monkeypatch):
    app = QApplication.instance() or QApplication([])
    writes, threads = [], []
    devices = [Keyboard(b"/dev/fake", "Fake keyboard", True, "Fake descriptor")]

    class Monitor(QObject):
        changed = Signal(int)
        failed = Signal(str)

        def __init__(self, parent):
            super().__init__(parent)
            self.requests = 0
            self.stopped = False

        def start(self):
            pass

        def stop(self):
            self.stopped = True

        def request_volume(self):
            self.requests += 1

    class Transport:
        def __init__(self, keyboard):
            assert keyboard.supported

        def __enter__(self):
            threads.append(QThread.currentThread())
            return self

        def __exit__(self, *_args):
            pass

    class Controller:
        def __init__(self, _transport):
            pass

        def sync_volume(self, volume):
            writes.append(volume)

    monkeypatch.setattr(background, "VolumeMonitor", Monitor)
    monkeypatch.setattr(background, "discover", lambda: devices)
    monkeypatch.setattr(background, "HidTransport", Transport)
    monkeypatch.setattr(background, "Controller", Controller)
    daemon = background.BackgroundVolumeSync(app)

    def wait():
        deadline = time.monotonic() + 5
        while daemon.job is not None:
            app.processEvents()
            if time.monotonic() > deadline:
                pytest.fail("Background USB job did not finish")
            time.sleep(0.001)
        app.processEvents()

    yield SimpleNamespace(
        daemon=daemon, writes=writes, devices=devices, app=app, wait=wait, threads=threads
    )
    daemon.stop()
    wait()


def test_daemon_initial_sync_events_and_duplicate_suppression(daemon):
    service = daemon.daemon
    service.start()
    daemon.wait()
    assert not daemon.writes
    assert service.monitor.requests == 1
    service.monitor.changed.emit(65)
    daemon.wait()
    assert daemon.writes == [65]
    assert daemon.threads[0] != daemon.app.thread()
    service.monitor.changed.emit(65)
    daemon.wait()
    service.monitor.changed.emit(30)
    daemon.wait()
    assert daemon.writes == [65, 30]


def test_daemon_pauses_for_gui_and_refreshes_when_gui_closes(daemon):
    service = daemon.daemon
    with background.ProcessLease():
        service.start()
        service.monitor.changed.emit(65)
        daemon.wait()
        assert not daemon.writes
        assert service.keyboard is None
    service._poll()
    daemon.wait()
    assert service.monitor.requests == 1
    assert not daemon.writes
    service.monitor.changed.emit(30)
    daemon.wait()
    assert daemon.writes == [30]


def test_daemon_reconnect_requires_fresh_audio_and_never_uses_unsupported(daemon):
    service = daemon.daemon
    service.start()
    daemon.wait()
    service.monitor.changed.emit(65)
    daemon.wait()
    devices = list(daemon.devices)
    daemon.devices.clear()
    daemon.devices.append(Keyboard(b"/dev/unsupported", "Other", False, "Bad descriptor"))
    service._poll()
    daemon.wait()
    service.monitor.changed.emit(30)
    daemon.wait()
    daemon.devices.extend(devices)
    service._poll()
    daemon.wait()
    assert daemon.writes == [65]
    service.monitor.changed.emit(65)
    daemon.wait()
    assert daemon.writes == [65, 65]


def test_daemon_ambiguous_keyboard_stops_without_writes(daemon):
    service = daemon.daemon
    errors = []
    service.failed.connect(errors.append)
    daemon.devices.append(Keyboard(b"/dev/other", "Other", True, "Fake descriptor"))
    service.start()
    daemon.wait()
    assert errors and "Multiple" in errors[0]
    assert service.stopping
    assert not daemon.writes


def test_daemon_transfer_failure_stops_without_retry(daemon, monkeypatch):
    service = daemon.daemon
    errors = []
    service.failed.connect(errors.append)
    attempts = []

    def fail(_controller, volume):
        attempts.append(volume)
        raise OSError("Fake USB failure")

    monkeypatch.setattr(background.Controller, "sync_volume", fail)
    service.start()
    daemon.wait()
    service.monitor.changed.emit(65)
    daemon.wait()
    service._poll()
    daemon.wait()
    assert attempts == [65]
    assert errors == ["Fake USB failure"]
    assert service.monitor.stopped


def test_daemon_audio_failure_stops_explicitly_without_writes(daemon):
    service = daemon.daemon
    errors = []
    service.failed.connect(errors.append)
    service.start()
    daemon.wait()
    service.monitor.failed.emit("Audio server disconnected")
    service.monitor.changed.emit(65)
    daemon.wait()
    assert errors == ["Audio server disconnected"]
    assert service.stopping
    assert not daemon.writes


def test_daemon_coalesces_events_and_stops_cleanly(daemon, monkeypatch):
    service = daemon.daemon
    started, release = Event(), Event()
    sync = background.Controller.sync_volume

    def blocked(controller, volume):
        started.set()
        if not release.wait(5):
            raise TimeoutError("Test did not release background write")
        return sync(controller, volume)

    monkeypatch.setattr(background.Controller, "sync_volume", blocked)
    service.start()
    daemon.wait()
    service.monitor.changed.emit(10)
    try:
        assert started.wait(2)
        service.monitor.changed.emit(20)
        service.monitor.changed.emit(30)
    finally:
        release.set()
        daemon.wait()
    assert daemon.writes == [10, 30]
    service.stop()
    service.monitor.changed.emit(40)
    daemon.wait()
    assert daemon.writes == [10, 30]
