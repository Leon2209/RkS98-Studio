import json
import os
import sys
import time

import pytest
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from rks98 import audio


def volume_data(*values):
    return json.dumps(
        {"volume": {str(index): {"value": value} for index, value in enumerate(values)}}
    ).encode()


@pytest.mark.parametrize(
    ("values", "expected"),
    [((0,), 0), ((65536,), 100), ((42598, 42598), 65), ((1000, 32768), 50), ((98304,), 100)],
)
def test_volume_conversion(values, expected):
    assert audio.decode_volume(volume_data(*values)) == expected


@pytest.mark.parametrize(
    "data",
    [
        b"invalid",
        b"[]",
        b"{}",
        b'{"volume":{}}',
        volume_data(-1),
        volume_data(True),
        volume_data(1.5),
        b'{"volume":{"left":null}}',
    ],
)
def test_invalid_volume_is_reported(data):
    with pytest.raises(ValueError):
        audio.decode_volume(data)


@pytest.fixture
def monitor(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class Process(QProcess):
        def __init__(self, parent):
            super().__init__(parent)
            self.running = False
            self.commands = []
            self.output = b""
            self.errors = b""

        def start(self, command, arguments):
            self.commands.append((command, arguments))
            self.running = True

        def state(self):
            return self.ProcessState.Running if self.running else self.ProcessState.NotRunning

        def readAllStandardOutput(self):
            result, self.output = self.output, b""
            return result

        def readAllStandardError(self):
            return self.errors

        def kill(self):
            self.running = False

        def waitForFinished(self, _timeout):
            return True

        def complete(self, data, code=0):
            self.output = data
            self.running = False
            self.finished.emit(code, self.ExitStatus.NormalExit)

    monkeypatch.setattr(audio, "QProcess", Process)
    monitor = audio.VolumeMonitor(app)
    monitor.start()
    yield monitor
    monitor.stop()


def test_subscription_initial_query_and_event_coalescing(monitor):
    values = []
    monitor.changed.connect(values.append)
    monitor.subscription.started.emit()
    assert monitor.query.commands == [
        ("pactl", ["--format=json", "get-sink-volume", "@DEFAULT_SINK@"])
    ]
    monitor.subscription.output = b"Event 'change' on sink #1\nEvent 'change' on server #0\n"
    monitor.subscription.readyReadStandardOutput.emit()
    monitor.query.complete(volume_data(100))
    assert len(monitor.query.commands) == 2
    assert not values
    monitor.query.complete(volume_data(42598))
    assert values == [65]
    assert monitor.subscription.commands == [("pactl", ["subscribe"])]


def test_partial_and_unrelated_events(monitor):
    monitor.subscription.output = b"Event 'change' on client #3\nEvent 'change' on si"
    monitor.subscription.readyReadStandardOutput.emit()
    assert not monitor.query.commands
    monitor.subscription.output = b"nk #1\n"
    monitor.subscription.readyReadStandardOutput.emit()
    assert len(monitor.query.commands) == 1


@pytest.mark.parametrize("failure", ["exit", "query", "timeout", "missing", "invalid"])
def test_failures_stop_monitor_and_surface_error(monitor, failure):
    errors = []
    monitor.failed.connect(errors.append)
    monitor.subscription.started.emit()
    if failure == "exit":
        monitor.subscription.complete(b"", 1)
    elif failure == "query":
        monitor.query.errors = b"No default sink"
        monitor.query.complete(b"", 1)
    elif failure == "invalid":
        monitor.query.complete(b"not json")
    elif failure == "missing":
        monitor.subscription.errorOccurred.emit(QProcess.ProcessError.FailedToStart)
    else:
        monitor.timeout.timeout.emit()
    assert len(errors) == 1
    assert "Volume sync unavailable" in errors[0]
    assert not monitor.active
    assert not monitor.query.running and not monitor.subscription.running
    monitor.request_volume()
    assert len(monitor.query.commands) == 1


def test_stop_prevents_late_results(monitor):
    values, errors = [], []
    monitor.changed.connect(values.append)
    monitor.failed.connect(errors.append)
    monitor.subscription.started.emit()
    monitor.stop()
    monitor.query.complete(volume_data(42598))
    monitor.subscription.complete(b"")
    assert not values and not errors


def test_real_process_subscription_delivers_output_device_events(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    executable = tmp_path / "pactl"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys, time\n"
        "root = pathlib.Path(__file__).parent\n"
        "if sys.argv[1:] == ['subscribe']:\n"
        "    while True:\n"
        "        event = root / 'event'\n"
        "        if event.exists():\n"
        "            event.unlink()\n"
        "            print(\"Event 'change' on server #0\", flush=True)\n"
        "        time.sleep(0.01)\n"
        "else:\n"
        "    print((root / 'volume').read_text())\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    (tmp_path / "volume").write_bytes(volume_data(42598))
    monitor = audio.VolumeMonitor(app)
    values, errors = [], []
    monitor.changed.connect(values.append)
    monitor.failed.connect(errors.append)

    def wait_for_volume(value):
        deadline = time.monotonic() + 5
        while value not in values and not errors and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.001)
        assert not errors
        assert value in values

    try:
        monitor.start()
        wait_for_volume(65)
        (tmp_path / "volume").write_bytes(volume_data(32768))
        (tmp_path / "event").touch()
        wait_for_volume(50)
        assert values == [65, 50]
    finally:
        monitor.stop()
    assert monitor.subscription.state() == QProcess.ProcessState.NotRunning
    assert monitor.query.state() == QProcess.ProcessState.NotRunning
