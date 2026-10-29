"""Optional user-systemd volume service and exclusive foreground handoff."""

import fcntl
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, Signal

from .audio import VolumeMonitor
from .device import HidTransport, discover
from .service import Controller

logger = logging.getLogger(__name__)
UNIT_NAME = "rks98-volume.service"
UNIT_MARKER = "# Managed by RK-S98 Studio\n"


class LeaseBusy(RuntimeError):
    """Another RK-S98 process owns the requested operation."""


class ProcessLease:
    def __init__(self, name: str = "foreground", timeout: float = 0):
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if not runtime:
            raise RuntimeError("XDG_RUNTIME_DIR is required for safe background-service handoff.")
        directory = Path(runtime) / "rks98-studio"
        directory.mkdir(mode=0o700, exist_ok=True)
        self.path = directory / f"{name}.lock"
        self.timeout = timeout
        self.stream = None

    def __enter__(self) -> "ProcessLease":
        stream = self.path.open("a")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    stream.close()
                    raise LeaseBusy("Another RK-S98 process is using the keyboard.") from None
                time.sleep(0.05)
            except OSError:
                stream.close()
                raise
            else:
                self.stream = stream
                return self

    def __exit__(self, *_args: object) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None


def systemd_argument(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("Invalid service command path.")
    return (
        '"'
        + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
        + '"'
    )


def service_unit() -> str:
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.extend(["-m", "rks98"])
    command.append("--volume-service")
    return (
        UNIT_MARKER + "[Unit]\nDescription=RK-S98 desktop volume synchronization\n"
        "After=pipewire-pulse.service pulseaudio.service\n"
        "PartOf=graphical-session.target\n\n"
        "[Service]\nType=simple\n"
        f"ExecStart={' '.join(systemd_argument(part) for part in command)}\n"
        "Restart=no\nTimeoutStopSec=10\n\n"
        "[Install]\nWantedBy=graphical-session.target\n"
    )


@dataclass(frozen=True)
class ServiceState:
    enabled: bool
    active: bool

    @property
    def text(self) -> str:
        if self.enabled and self.active:
            return "Background service enabled."
        if self.enabled:
            return "Enabled at login, but not running. Check the user service journal."
        if self.active:
            return "Service is running but is not enabled at login."
        return "Background service off."


class UserVolumeService:
    def __init__(self) -> None:
        config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        self.path = config / "systemd/user" / UNIT_NAME

    def _systemctl(self, *arguments: str) -> str:
        result = subprocess.run(
            ["systemctl", "--user", *arguments],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(
                result.stderr.strip() or result.stdout.strip() or "systemctl failed."
            )
        return result.stdout

    def state(self) -> ServiceState:
        output = self._systemctl(
            "show", UNIT_NAME, "--property=LoadState,UnitFileState,ActiveState", "--no-pager"
        )
        fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        if fields.get("LoadState") not in ("loaded", "not-found"):
            raise RuntimeError("Cannot load the volume service. Check the systemd user manager.")
        return ServiceState(
            fields.get("UnitFileState") in ("enabled", "enabled-runtime"),
            fields.get("ActiveState") in ("active", "activating"),
        )

    def set_enabled(self, enabled: bool) -> ServiceState:
        if enabled:
            if self.path.is_symlink() or (
                self.path.exists() and not self.path.read_text().startswith(UNIT_MARKER)
            ):
                raise RuntimeError("Refusing to overwrite an existing custom volume service.")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(service_unit())
            self._systemctl("daemon-reload")
            self._systemctl("enable", "--now", UNIT_NAME)
        else:
            self._systemctl("disable", "--now", UNIT_NAME)
        state = self.state()
        if state.enabled != enabled or state.active != enabled:
            raise RuntimeError(f"Service state did not match the request. {state.text}")
        return state


class SyncWorker(QThread):
    def __init__(self, action: Callable[[], tuple[bytes | None, int | None]], parent: QObject):
        super().__init__(parent)
        self.action = action
        self.result: tuple[bytes | None, int | None] = (None, None)
        self.error: str | None = None

    def run(self) -> None:
        try:
            self.result = self.action()
        except Exception as error:
            logger.exception("Background volume operation failed")
            self.error = str(error) or type(error).__name__


class BackgroundVolumeSync(QObject):
    failed = Signal(str)

    def __init__(self, parent: QObject):
        super().__init__(parent)
        self.monitor = VolumeMonitor(self)
        self.monitor.changed.connect(self._volume_changed)
        self.monitor.failed.connect(self._fail)
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self._poll)
        self.job: SyncWorker | None = None
        self.volume: int | None = None
        self.keyboard: bytes | None = None
        self.attempt: int | None = None
        self.pending = False
        self.stopping = False

    def start(self) -> None:
        self.monitor.start()
        self.timer.start()
        self._poll()

    def stop(self) -> None:
        self.stopping = True
        self.timer.stop()
        self.monitor.stop()
        if self.job is not None:
            self.job.wait()

    def _fail(self, text: str) -> None:
        logger.error("Background volume sync stopped: %s", text)
        self.stop()
        self.failed.emit(text)

    def _volume_changed(self, volume: int) -> None:
        self.volume = volume
        self._poll()

    def _poll(self) -> None:
        if self.stopping:
            return
        if self.job is not None:
            self.pending = True
            return
        volume, previous, attempt = self.volume, self.keyboard, self.attempt

        def sync() -> tuple[bytes | None, int | None]:
            try:
                with ProcessLease():
                    keyboards = [keyboard for keyboard in discover() if keyboard.supported]
                    if len(keyboards) > 1:
                        raise RuntimeError(
                            "Multiple supported keyboards connected; background sync requires one."
                        )
                    if not keyboards:
                        return None, None
                    keyboard = keyboards[0]
                    if keyboard.path != previous:
                        return keyboard.path, None
                    if volume is None or volume == attempt:
                        return keyboard.path, attempt
                    with HidTransport(keyboard) as transport:
                        Controller(transport).sync_volume(volume)
                    return keyboard.path, volume
            except LeaseBusy:
                return None, None

        self.pending = False
        self.job = SyncWorker(sync, self)
        self.job.finished.connect(self._finished)
        self.job.start()

    def _finished(self) -> None:
        job = self.job
        if job is None:
            return
        self.job = None
        job.deleteLater()
        if self.stopping:
            return
        if job.error is not None:
            self._fail(job.error)
            return
        previous = self.keyboard
        self.keyboard, self.attempt = job.result
        if self.keyboard is not None and self.keyboard != previous:
            self.volume = None
            self.monitor.request_volume()
        if self.pending:
            self._poll()


def run_volume_service() -> int:
    app = QCoreApplication(["rks98-volume"])
    service = BackgroundVolumeSync(app)
    service.failed.connect(lambda _text: app.exit(1))
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda _signal, _frame: app.quit())
    # Wake the Python interpreter so termination signals are handled while idle.
    heartbeat = QTimer(app)
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(500)
    with ProcessLease("volume-service"):
        QTimer.singleShot(0, service.start)
        try:
            return app.exec()
        finally:
            service.stop()
