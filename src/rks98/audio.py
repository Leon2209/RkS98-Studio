"""Event-driven default-output volume monitoring through PulseAudio/PipeWire."""

import json
import logging

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

logger = logging.getLogger(__name__)


def decode_volume(data: bytes) -> int:
    """Use the loudest channel, rounded and limited to the display's 0-100 range."""
    document = json.loads(data)
    if not isinstance(document, dict) or not isinstance(document.get("volume"), dict):
        raise ValueError("Audio server returned no channel volumes.")
    channels = document["volume"].values()
    values = []
    for channel in channels:
        value = channel.get("value") if isinstance(channel, dict) else None
        if type(value) is not int or value < 0:
            raise ValueError("Audio server returned an invalid channel volume.")
        values.append(value)
    if not values:
        raise ValueError("Audio server returned no channel volumes.")
    return min(100, (max(values) * 100 + 32768) // 65536)


class VolumeMonitor(QObject):
    changed = Signal(int)
    failed = Signal(str)

    def __init__(self, parent: QObject):
        super().__init__(parent)
        self.subscription = QProcess(self)
        self.query = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("LC_ALL", "C")
        for process in (self.subscription, self.query):
            process.setProcessEnvironment(environment)
            process.errorOccurred.connect(
                lambda _error, process=process: self._fail(process.errorString())
            )
        self.subscription.started.connect(self.request_volume)
        self.subscription.readyReadStandardOutput.connect(self._events)
        self.subscription.finished.connect(self._subscription_finished)
        self.query.finished.connect(self._queried)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.setInterval(3000)
        self.timeout.timeout.connect(lambda: self._fail("Audio volume query timed out."))
        self.active = False
        self.pending = False
        self.buffer = b""

    def start(self) -> None:
        if self.active:
            return
        self.buffer = b""
        self.active = True
        self.subscription.start("pactl", ["subscribe"])

    def stop(self) -> None:
        self.active = False
        self.pending = False
        self.timeout.stop()
        for process in (self.subscription, self.query):
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(1000)

    def _fail(self, detail: str) -> None:
        if not self.active:
            return
        logger.error("Volume monitoring stopped: %s", detail)
        self.stop()
        self.failed.emit(
            f"Volume sync unavailable: {detail} "
            "Check pactl and the audio server, then restart the app."
        )

    def _subscription_finished(self, _code: int, _status: QProcess.ExitStatus) -> None:
        self._fail("Audio event subscription ended.")

    def _events(self) -> None:
        self.buffer += bytes(self.subscription.readAllStandardOutput())
        lines = self.buffer.split(b"\n")
        self.buffer = lines.pop()
        if any(b" on sink #" in line or b" on server #" in line for line in lines):
            self.request_volume()

    def request_volume(self) -> None:
        if not self.active:
            return
        if self.query.state() != QProcess.ProcessState.NotRunning:
            self.pending = True
            return
        self.pending = False
        self.query.start("pactl", ["--format=json", "get-sink-volume", "@DEFAULT_SINK@"])
        self.timeout.start()

    def _queried(self, code: int, status: QProcess.ExitStatus) -> None:
        self.timeout.stop()
        if not self.active:
            return
        if code != 0 or status != QProcess.ExitStatus.NormalExit:
            detail = bytes(self.query.readAllStandardError()).decode(errors="replace").strip()
            self._fail(detail or "Audio volume query failed.")
            return
        try:
            volume = decode_volume(bytes(self.query.readAllStandardOutput()))
        except (ValueError, UnicodeDecodeError) as error:
            self._fail(str(error))
            return
        if self.pending:
            self.request_volume()
        else:
            self.changed.emit(volume)
