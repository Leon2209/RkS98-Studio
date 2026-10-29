import os
import time
from dataclasses import replace
from importlib.metadata import version
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QObject, QSettings, QThread
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QColorDialog, QLabel, QScrollArea, QTabWidget

from rks98 import gui
from rks98.device import Keyboard
from rks98.protocol import Lighting, default_color_table
from rks98.service import CustomColorResult


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_for_job(app, window):
    deadline = time.monotonic() + 5
    app.processEvents()
    while window.job is not None:
        app.processEvents()
        if time.monotonic() > deadline:
            pytest.fail("GUI worker did not finish")
        time.sleep(0.001)
    app.processEvents()


@pytest.mark.parametrize(
    ("accepted", "remember"), [(True, True), (True, False), (False, True), (False, False)]
)
def test_startup_warning_only_saves_opt_out_on_continue(
    app, monkeypatch, tmp_path, accepted, remember
):
    settings = QSettings(str(tmp_path / "warning.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(gui, "QSettings", lambda *_: settings)

    def respond(dialog):
        assert "brick" in " ".join(item.text() for item in dialog.findChildren(QLabel))
        dialog.findChild(gui.QCheckBox).setChecked(remember)
        return gui.QDialog.DialogCode.Accepted if accepted else gui.QDialog.DialogCode.Rejected

    monkeypatch.setattr(gui.QDialog, "exec", respond)
    assert gui.confirm_hardware_risk() is accepted
    assert settings.value("safety/hideStartupWarning", False, type=bool) == (accepted and remember)


def test_startup_warning_honors_saved_opt_out(app, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / "warning.ini"), QSettings.Format.IniFormat)
    settings.setValue("safety/hideStartupWarning", True)
    monkeypatch.setattr(gui, "QSettings", lambda *_: settings)
    monkeypatch.setattr(gui.QDialog, "exec", lambda _: pytest.fail("Warning should be skipped"))
    assert gui.confirm_hardware_risk()


@pytest.fixture
def backend(monkeypatch):
    class Backend:
        devices = [Keyboard(b"/dev/hidraw5", "Test RK-S98", True, "Test descriptor")]
        lighting = Lighting(1, 20, 1, None, 2)
        reads = []
        writes = []
        error = None
        version = "2117"
        version_reads = 0
        background_state = gui.ServiceState(False, False)
        service_changes = []
        service_error = None

    backend = Backend()
    monkeypatch.setattr(gui, "discover", lambda: backend.devices)

    class UserVolumeService:
        def state(self):
            return backend.background_state

        def set_enabled(self, enabled):
            backend.service_changes.append(enabled)
            if backend.service_error:
                raise RuntimeError(backend.service_error)
            backend.background_state = gui.ServiceState(enabled, enabled)
            return backend.background_state

    monkeypatch.setattr(gui, "UserVolumeService", UserVolumeService)

    class VolumeMonitor(QObject):
        changed = gui.Signal(int)
        failed = gui.Signal(str)

        def __init__(self, parent):
            super().__init__(parent)
            self.stopped = False
            self.requests = 0
            self.starts = 0

        def start(self):
            self.stopped = False
            self.starts += 1

        def stop(self):
            self.stopped = True

        def request_volume(self):
            self.requests += 1

    monkeypatch.setattr(gui, "VolumeMonitor", VolumeMonitor)

    class Transport:
        def __init__(self, keyboard):
            self.keyboard = keyboard

        def __enter__(self):
            if backend.error:
                raise OSError(backend.error)
            return self

        def __exit__(self, *_):
            pass

    class Controller:
        def __init__(self, transport):
            self.path = transport.keyboard.path

        def read_lighting(self, board):
            backend.reads.append((self.path, board, QThread.currentThread()))
            return backend.lighting

        def read_firmware_version(self):
            backend.version_reads += 1
            if backend.version is None:
                raise gui.ProtocolError("No version")
            return backend.version

        def apply_lighting(self, board, lighting):
            backend.writes.append(("palette", board, lighting))
            backend.lighting = lighting
            return lighting

        def apply_custom_color(self, board, lighting, saved_colors):
            backend.writes.append(("custom", board, lighting))
            table = bytearray(saved_colors or default_color_table())
            offset = lighting.effect * 21
            table[offset : offset + 3] = bytes(lighting.color)
            desired = replace(lighting, palette_index=0)
            backend.lighting = replace(desired, color=None)
            return CustomColorResult(desired, bytes(table), False)

        def upload_display(self, frames, delay, progress=None, cancelled=None):
            backend.writes.append(("display", len(frames), delay, len(frames[0])))
            backend.display_frames = frames
            progress(1, 2)
            if cancelled():
                raise gui.UploadCancelled()
            progress(2, 2)
            return len(frames)

        def sync_clock(self):
            backend.writes.append(("clock",))
            return "Clock packet sent."

        def sync_volume(self, volume):
            backend.writes.append(("volume", volume, QThread.currentThread()))
            return "Volume/time packet sent."

    monkeypatch.setattr(gui, "HidTransport", Transport)
    monkeypatch.setattr(gui, "Controller", Controller)
    return backend


@pytest.fixture
def window(app, backend, monkeypatch, tmp_path):
    monkeypatch.setattr(
        gui,
        "QSettings",
        lambda *_: QSettings(
            str(tmp_path / "settings.ini"),
            QSettings.Format.IniFormat,
        ),
    )
    window = gui.MainWindow()
    window.show()
    wait_for_job(app, window)
    yield window
    wait_for_job(app, window)
    window.close()


def test_startup_reads_settings_without_configuration_writes(app, window, backend):
    assert len(window.devices) == 1
    assert window.loaded
    assert window.effect.currentData() == 1
    assert window.palette.currentData() == 2
    assert window.brightness.value() == 20
    assert backend.reads[0][:2] == (b"/dev/hidraw5", 0)
    assert backend.reads[0][2] != app.thread()
    assert not backend.writes
    assert not hasattr(window, "experimental")
    assert not hasattr(window, "log")
    assert window.clock_button.isEnabled()
    assert not window.apply_button.isEnabled()
    assert window.poll_timer.isActive()
    assert window.poll_timer.interval() == 5000


def test_volume_sync_on_connection_and_events_preserves_edits(app, window, backend):
    assert window.volume_monitor.requests == 1
    window.brightness.setValue(16)
    window.volume_monitor.changed.emit(65)
    wait_for_job(app, window)
    assert backend.writes == [("volume", 65, backend.writes[0][2])]
    assert backend.writes[0][2] != app.thread()
    assert window.dirty and window.brightness.value() == 16
    window.volume_monitor.changed.emit(65)
    wait_for_job(app, window)
    assert len(backend.writes) == 1
    window.volume_monitor.changed.emit(40)
    wait_for_job(app, window)
    assert [write[:2] for write in backend.writes] == [("volume", 65), ("volume", 40)]


def test_volume_events_coalesce_while_usb_busy(app, window, backend):
    window._start(lambda: time.sleep(0.03), lambda _: None)
    for volume in (10, 20, 30):
        window.volume_monitor.changed.emit(volume)
    assert not backend.writes
    wait_for_job(app, window)
    assert [write[:2] for write in backend.writes] == [("volume", 30)]


def test_volume_sync_waits_for_read(app, window, backend, pending_poll):
    window.volume_monitor.changed.emit(65)
    assert not backend.writes
    pending_poll.set()
    wait_for_job(app, window)
    assert [write[:2] for write in backend.writes] == [("volume", 65)]


def test_volume_sync_reconnect_requests_fresh_volume(app, window, backend):
    window.volume_monitor.changed.emit(65)
    wait_for_job(app, window)
    devices = backend.devices
    backend.devices = []
    window._poll()
    wait_for_job(app, window)
    window.volume_monitor.changed.emit(30)
    assert len(backend.writes) == 1
    backend.devices = devices
    window._poll()
    wait_for_job(app, window)
    assert len(backend.writes) == 1
    assert window.volume_monitor.requests == 2
    window.volume_monitor.changed.emit(65)
    wait_for_job(app, window)
    assert len(backend.writes) == 2


def test_volume_failure_is_visible_without_retries_or_discarding_edits(app, window, backend):
    window.brightness.setValue(16)
    backend.error = "USB unavailable"
    window.volume_monitor.changed.emit(65)
    wait_for_job(app, window)
    assert "USB unavailable" in window.volume_status.text()
    assert window.loaded and window.dirty
    backend.error = None
    window.volume_monitor.changed.emit(65)
    wait_for_job(app, window)
    assert not backend.writes
    window.volume_monitor.failed.emit("Audio server disconnected")
    window._clear_error()
    assert window.volume_status.text() == "Audio server disconnected"
    assert window.desktop_volume is None


def test_clock_sync_includes_known_volume(app, window, backend):
    window.volume_monitor.changed.emit(65)
    wait_for_job(app, window)
    window.clock_button.click()
    wait_for_job(app, window)
    assert [write[:2] for write in backend.writes] == [("volume", 65), ("volume", 65)]


def test_audio_failure_during_volume_write_remains_visible(app, window, backend, monkeypatch):
    started, release = Event(), Event()
    sync = gui.Controller.sync_volume

    def blocked_sync(controller, volume):
        started.set()
        if not release.wait(5):
            raise TimeoutError("Test did not release volume write")
        return sync(controller, volume)

    monkeypatch.setattr(gui.Controller, "sync_volume", blocked_sync)
    window.volume_monitor.changed.emit(65)
    try:
        assert started.wait(2)
        window.volume_monitor.failed.emit("Audio server disconnected")
    finally:
        release.set()
        wait_for_job(app, window)
    assert window.volume_status.text() == "Audio server disconnected"


def test_volume_sync_preserves_existing_lighting_error(app, window):
    window._show_error("Saved color history is invalid")
    window.volume_monitor.changed.emit(65)
    wait_for_job(app, window)
    assert window.status.text() == "Saved color history is invalid"


def test_close_stops_volume_monitor(app, window):
    assert window.close()
    assert window.volume_monitor.stopped


def test_background_service_is_off_by_default_and_changes_only_on_request(
    app, window, backend, monkeypatch
):
    assert not window.background_sync.isChecked()
    assert window.background_sync.isEnabled()
    assert not backend.service_changes
    monkeypatch.setattr(gui.QMessageBox, "question", lambda *_: gui.QMessageBox.StandardButton.Yes)
    window.background_sync.setChecked(True)
    wait_for_job(app, window)
    assert backend.service_changes == [True]
    assert "enabled" in window.background_status.text()
    assert window.background_sync.isEnabled()
    window.background_sync.setChecked(False)
    wait_for_job(app, window)
    assert backend.service_changes == [True, False]
    assert window.background_status.text() == "Background service off."


def test_background_service_declined_confirmation_makes_no_changes(window, backend, monkeypatch):
    monkeypatch.setattr(gui.QMessageBox, "question", lambda *_: gui.QMessageBox.StandardButton.No)
    window.background_sync.setChecked(True)
    assert not window.background_sync.isChecked()
    assert not backend.service_changes


def test_background_service_failure_refreshes_actual_state(app, window, backend, monkeypatch):
    backend.service_error = "User manager unavailable"
    monkeypatch.setattr(gui.QMessageBox, "question", lambda *_: gui.QMessageBox.StandardButton.Yes)
    window.background_sync.setChecked(True)
    wait_for_job(app, window)
    assert "User manager unavailable" in window.background_status.text()
    assert not window.background_sync.isChecked()
    assert window.background_sync.isEnabled()


def test_background_service_setting_is_independent_of_in_app_volume(app, window, backend):
    window.service_state = gui.ServiceState(True, True)
    window.background_sync.blockSignals(True)
    window.background_sync.setChecked(True)
    window.background_sync.blockSignals(False)
    window.volume_sync.setChecked(False)
    wait_for_job(app, window)
    assert not backend.service_changes
    assert window.background_sync.isChecked()


def test_settings_refreshes_external_service_state_without_changing_it(app, window, backend):
    backend.background_state = gui.ServiceState(True, False)
    tabs = window.findChild(gui.QTabWidget)
    tabs.setCurrentIndex(2)
    wait_for_job(app, window)
    assert window.background_sync.isChecked()
    assert "not running" in window.background_status.text()
    assert not backend.service_changes


def test_settings_tab_contains_volume_controls_and_application_version(window):
    tabs = window.findChild(QTabWidget)
    assert [tabs.tabText(index) for index in range(tabs.count())] == [
        "Lighting",
        "Display",
        "Settings",
    ]
    settings_page = tabs.widget(2)
    assert settings_page.isAncestorOf(window.volume_sync)
    assert settings_page.isAncestorOf(window.volume_status)
    assert settings_page.isAncestorOf(window.application_version)
    assert window.application_version.text() == f"Version {version('rks98-control')}"
    assert window.volume_sync.isChecked()
    assert window.volume_monitor.starts == 1


def test_settings_options_group_their_own_help_and_status(window):
    volume_option = window.volume_sync.parentWidget()
    background_option = window.background_sync.parentWidget()
    assert volume_option is not background_option
    for option, status in (
        (volume_option, window.volume_status),
        (background_option, window.background_status),
    ):
        assert option.objectName() == "settingOption"
        assert status.parentWidget() is option
        assert status.objectName() == "settingStatus"
        help_text = option.findChild(gui.QLabel, "settingHelp")
        assert help_text is not None
        assert len(help_text.text()) < 80
        assert help_text.wordWrap()


def test_volume_sync_toggle_stops_updates_and_persists(app, window, backend):
    window.volume_sync.setChecked(False)
    assert window.volume_monitor.stopped
    assert not window.settings.value("volumeSync/enabled", True, type=bool)
    window.volume_monitor.changed.emit(65)
    wait_for_job(app, window)
    assert not backend.writes
    assert window.volume_status.text() == "Volume sync off."
    window.volume_sync.setChecked(True)
    assert window.volume_monitor.starts == 2
    assert window.settings.value("volumeSync/enabled", False, type=bool)
    window.volume_monitor.changed.emit(65)
    wait_for_job(app, window)
    assert [write[:2] for write in backend.writes] == [("volume", 65)]


def test_saved_disabled_volume_sync_does_not_start_monitor(app, backend, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("volumeSync/enabled", False)
    monkeypatch.setattr(gui, "QSettings", lambda *_: settings)
    window = gui.MainWindow()
    try:
        window.show()
        wait_for_job(app, window)
        assert not window.volume_sync.isChecked()
        assert window.volume_monitor.starts == 0
        assert window.volume_monitor.requests == 0
        assert not backend.writes
    finally:
        wait_for_job(app, window)
        window.close()


def test_disable_volume_sync_drops_pending_write(app, window, backend):
    window._start(lambda: time.sleep(0.03), lambda _: None)
    window.volume_monitor.changed.emit(65)
    window.volume_sync.setChecked(False)
    wait_for_job(app, window)
    assert not backend.writes


def test_volume_write_completion_does_not_change_lighting_pixels(app, window):
    before = window.grab().toImage()
    window.volume_monitor.changed.emit(65)
    wait_for_job(app, window)
    assert window.grab().toImage() == before


def test_disable_during_volume_write_keeps_disabled_status(app, window, monkeypatch):
    started, release = Event(), Event()
    sync = gui.Controller.sync_volume

    def blocked_sync(controller, volume):
        started.set()
        if not release.wait(5):
            raise TimeoutError("Test did not release volume write")
        return sync(controller, volume)

    monkeypatch.setattr(gui.Controller, "sync_volume", blocked_sync)
    window.volume_monitor.changed.emit(65)
    try:
        assert started.wait(2)
        window.volume_sync.setChecked(False)
    finally:
        release.set()
        wait_for_job(app, window)
    assert window.volume_status.text() == "Volume sync off."
    assert window.desktop_volume is None


def test_volume_write_does_not_blink_lighting_ui_and_queues_user_action(
    app, window, backend, monkeypatch
):
    started, release = Event(), Event()
    sync = gui.Controller.sync_volume

    def blocked_sync(controller, volume):
        started.set()
        if not release.wait(5):
            raise TimeoutError("Test did not release volume write")
        return sync(controller, volume)

    class Watch(QObject):
        def __init__(self):
            super().__init__()
            self.changes = []

        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.EnabledChange:
                self.changes.append(watched)
            return False

    watcher = Watch()
    for widget in window.findChildren(gui.QWidget):
        widget.installEventFilter(watcher)
    monkeypatch.setattr(gui.Controller, "sync_volume", blocked_sync)
    before = window.grab().toImage()
    window.volume_monitor.changed.emit(65)
    try:
        assert started.wait(2)
        app.processEvents()
        assert window.effect.isEnabled()
        assert window.clock_button.isEnabled()
        assert window.grab().toImage() == before
        assert not watcher.changes
        window.clock_button.click()
        assert len(window.pending_actions) == 1
    finally:
        release.set()
        wait_for_job(app, window)
    assert [write[:2] for write in backend.writes] == [("volume", 65), ("volume", 65)]
    assert window.effect.isEnabled()


def test_poll_updates_effect_and_palette_without_writing(app, window, backend):
    backend.lighting = Lighting(2, 14, 3, None, 1)
    window._poll()
    wait_for_job(app, window)
    assert window.effect.currentData() == 2
    assert window.palette.currentData() == 1
    assert window.brightness.value() == 14
    assert window.speed.value() == 3
    assert not window.dirty
    assert not backend.writes


def test_timer_itself_triggers_read(app, window, backend):
    reads = len(backend.reads)
    window.poll_timer.setInterval(10)
    deadline = time.monotonic() + 2
    while len(backend.reads) == reads and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    window.poll_timer.stop()
    wait_for_job(app, window)
    assert len(backend.reads) > reads
    assert not backend.writes


@pytest.fixture
def pending_poll(app, window, monkeypatch):
    started, release = Event(), Event()
    read = gui.Controller.read_lighting

    def blocked_read(controller, board):
        result = read(controller, board)
        started.set()
        if not release.wait(5):
            raise TimeoutError("Test did not release background read")
        return result

    monkeypatch.setattr(gui.Controller, "read_lighting", blocked_read)
    window._poll()
    assert started.wait(2)
    app.processEvents()
    yield release
    release.set()
    wait_for_job(app, window)


def test_unchanged_poll_does_not_blink_or_reset_selector(app, window, backend):
    class Watch(QObject):
        def __init__(self):
            super().__init__()
            self.changes = []

        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.EnabledChange:
                self.changes.append(watched)
            return False

    watcher = Watch()
    for widget in window.findChildren(gui.QWidget):
        widget.installEventFilter(watcher)
    indices, resets = [], []
    window.device_selector.currentIndexChanged.connect(indices.append)
    window.device_selector.model().modelReset.connect(lambda: resets.append(True))
    before = window.grab().toImage()
    for _ in range(2):
        window._poll()
        app.processEvents()
        assert window.effect.isEnabled()
        assert window.brightness_slider.isEnabled()
        assert window.clock_button.isEnabled()
        assert window.grab().toImage() == before
        wait_for_job(app, window)
        assert window.grab().toImage() == before
    assert not watcher.changes
    assert not indices
    assert not resets
    assert not backend.writes


def test_edits_started_during_read_take_precedence(app, window, pending_poll, backend):
    assert window.effect.isEnabled()
    window.brightness.setValue(12)
    window.effect.setCurrentIndex(window.effect.findData(2))
    pending_poll.set()
    wait_for_job(app, window)
    assert window.brightness.value() == 12
    assert window.effect.currentData() == 2
    assert window.dirty
    assert window.apply_button.isEnabled()
    assert not backend.writes


def test_refresh_during_read_keeps_first_slot(app, window, pending_poll, backend):
    backend.lighting = Lighting(2, 14, 3, None, 1)
    window.scan()
    pending_poll.set()
    wait_for_job(app, window)
    assert [entry[1] for entry in backend.reads[-2:]] == [0, 0]
    assert window.effect.currentData() == 2
    assert window.brightness.value() == 14
    assert window.loaded
    assert not backend.writes


def test_explicit_actions_during_read_are_serialized(app, window, pending_poll, backend):
    window.clock_button.click()
    window.brightness.setValue(12)
    window.apply_button.click()
    window.apply_button.click()
    assert not backend.writes
    pending_poll.set()
    wait_for_job(app, window)
    assert backend.writes == [("clock",), ("palette", 0, Lighting(1, 12, 1, None, 2))]
    assert not window.dirty
    assert window.loaded


def test_color_picker_during_inflight_read_preserves_selection(
    app,
    window,
    pending_poll,
    backend,
    monkeypatch,
):
    def choose(*_):
        pending_poll.set()
        wait_for_job(app, window)
        return QColor(12, 34, 56)

    monkeypatch.setattr(QColorDialog, "getColor", choose)
    window._choose_color()
    assert window.color.getRgb()[:3] == (12, 34, 56)
    assert window.dirty
    assert window.use_custom.isChecked()
    assert not backend.writes


def test_keyboard_is_static_bundled_image_and_copy_is_plain(window):
    assert isinstance(window.preview, QLabel)
    assert not window.preview.image.isNull()
    original = window.preview.image.cacheKey()
    window.effect.setCurrentIndex(window.effect.findData(2))
    window.palette.setCurrentIndex(window.palette.findData(7))
    assert window.preview.image.cacheKey() == original
    texts = " ".join(widget.text() for widget in window.findChildren(QLabel))
    for removed in ("98 keys", "A little", "Make it yours", "LIVE SETTINGS", "Updates every"):
        assert removed not in texts
    assert window.status.isHidden()


@pytest.mark.parametrize("enabled", [True, False])
def test_slider_background_has_no_rectangle(app, window, enabled):
    slider = window.brightness_slider
    slider.setEnabled(enabled)
    app.processEvents()
    image = slider.grab().toImage()
    for x in (2, image.width() // 2, image.width() - 3):
        assert image.pixelColor(x, 1).alpha() == 0
        assert image.pixelColor(x, image.height() - 2).alpha() == 0


def test_unsaved_edits_are_not_overwritten_by_polling(app, window, backend):
    window.brightness.setValue(12)
    assert window.dirty
    assert window.apply_button.isEnabled()
    assert window.status.isHidden()
    reads = len(backend.reads)
    backend.lighting = Lighting(3, 5, 2, None, 7)
    window._poll()
    wait_for_job(app, window)
    assert len(backend.reads) == reads
    assert window.brightness.value() == 12
    assert not backend.writes
    window.discard_edits()
    wait_for_job(app, window)
    assert window.effect.currentData() == 3
    assert window.brightness.value() == 5
    assert not window.dirty


def test_direct_apply_resumes_polling(app, window, backend):
    window.brightness.setValue(16)
    window.apply_button.click()
    wait_for_job(app, window)
    assert backend.writes == [("palette", 0, Lighting(1, 16, 1, None, 2))]
    assert not window.dirty
    assert window.loaded
    reads = len(backend.reads)
    window._poll()
    wait_for_job(app, window)
    assert len(backend.reads) == reads + 1
    assert len(backend.writes) == 1


def test_edits_disable_device_switching_until_discarded(app, window):
    window.brightness.setValue(12)
    assert not window.device_selector.isEnabled()
    window.discard_edits()
    wait_for_job(app, window)
    assert window.device_selector.isEnabled()


def test_compact_layout_keeps_buttons_and_controls_readable(app, window):
    window.resize(980, 740)
    window.use_custom.setChecked(True)
    app.processEvents()
    for button in (window.apply_button, window.revert_button, window.color_button):
        assert button.height() >= button.sizeHint().height()
    scroll = window.findChild(QScrollArea, "controlsScroll")
    assert scroll.widget().height() >= scroll.widget().minimumSizeHint().height()
    scroll.ensureWidgetVisible(window.color_button)
    app.processEvents()
    assert window.color_button.isVisibleTo(window)
    assert window.apply_button.isVisibleTo(window)
    assert not window.palette.isVisible()
    window.use_custom.setChecked(False)
    assert window.palette.isVisible()
    assert not window.color_button.isVisible()


def test_all_lighting_reads_use_first_slot_without_profile_selector(app, window, backend):
    assert not hasattr(window, "profile")
    window.read_lighting()
    wait_for_job(app, window)
    window._poll()
    wait_for_job(app, window)
    assert all(entry[1] == 0 for entry in backend.reads)
    assert window.loaded
    assert not backend.writes


def test_disconnect_and_reconnect_are_discovered_without_popups(app, window, backend):
    backend.devices = []
    window._poll()
    wait_for_job(app, window)
    assert not window.loaded
    assert not window.apply_button.isEnabled()
    assert not window.clock_button.isEnabled()
    assert window.status.isHidden()
    assert window.connection.text() == "Not connected"
    backend.devices = [Keyboard(b"/dev/hidraw8", "RK-S98", True, "New path")]
    backend.lighting = Lighting(15, 16, 1, None, 7)
    window._poll()
    wait_for_job(app, window)
    assert window.loaded
    assert window._keyboard().path == b"/dev/hidraw8"
    assert window.effect.currentData() == 15
    assert not backend.writes


def test_poll_error_has_inline_details_and_can_recover(app, window, backend):
    backend.error = "Permission denied: install the udev rule"
    window._poll()
    wait_for_job(app, window)
    assert not window.loaded
    assert window.status.property("error")
    assert "Permission denied" in window.status.toolTip()
    assert not backend.writes
    backend.error = None
    window._poll()
    wait_for_job(app, window)
    assert window.loaded
    assert not window.status.property("error")


def test_color_and_off_capabilities(window):
    assert window.color_button.isEnabled()
    assert not window.speed.isEnabled()
    assert window.brightness.maximum() == 20
    window.effect.setCurrentIndex(window.effect.findData(0))
    assert not window.brightness.isEnabled()
    assert not window.color_button.isEnabled()
    assert not window.palette.isEnabled()


def test_worker_result_returns_to_gui_and_blocks_parallel_poll(app, window, backend):
    threads = []
    reads = len(backend.reads)
    window._start(lambda: time.sleep(0.02), lambda _: threads.append(QThread.currentThread()))
    assert not window.refresh.isEnabled()
    assert not window.effect.isEnabled()
    window._poll()
    wait_for_job(app, window)
    assert threads == [app.thread()]
    assert len(backend.reads) == reads
    assert window.refresh.isEnabled()


def test_clock_sync_is_direct_and_does_not_discard_lighting_edits(app, window, backend):
    window.brightness.setValue(16)
    window.clock_button.click()
    wait_for_job(app, window)
    assert backend.writes == [("clock",)]
    assert window.dirty
    assert window.brightness.value() == 16
    assert window.status.isHidden()


@pytest.mark.parametrize("palette_index", [0, 1, 2, 3, 7, 11])
def test_palette_read_apply_does_not_convert_solid_color_to_rainbow(
    app,
    window,
    backend,
    palette_index,
):
    lighting = Lighting(1, 20, 1, None, palette_index)
    window._lighting_read(lighting)
    window.apply_lighting()
    wait_for_job(app, window)
    assert backend.writes == [("palette", 0, lighting)]


def test_custom_rgb_persists_and_poll_does_not_disable_mode(app, window, backend):
    window.color = QColor(0, 132, 255)
    window.use_custom.setChecked(True)
    window.apply_lighting()
    wait_for_job(app, window)
    table = bytes.fromhex(window.settings.value("customRgb/profile0/table"))
    assert table[21:24] == bytes((0, 132, 255))
    assert window.use_custom.isChecked()
    assert not window.custom_color_available
    assert "saved on this computer" in window.color_source.text()
    assert window.status.isHidden()
    assert window.settings.value("customRgb/profile0/effect1/custom", type=bool)
    window._poll()
    wait_for_job(app, window)
    assert window.use_custom.isChecked()
    assert window.color.getRgb()[:3] == (0, 132, 255)
    assert len(backend.writes) == 1
    # A fresh QSettings instance reproduces restart behavior.
    from PySide6.QtCore import QSettings as RealSettings

    window.settings = RealSettings(window.settings.fileName(), RealSettings.Format.IniFormat)
    window._lighting_read(backend.lighting)
    assert window.use_custom.isChecked()
    assert window.color.getRgb()[:3] == (0, 132, 255)
    window.use_custom.setChecked(False)
    window.palette.setCurrentIndex(window.palette.findData(1))
    window.apply_lighting()
    wait_for_job(app, window)
    window._poll()
    wait_for_job(app, window)
    assert not window.use_custom.isChecked()
    assert window.palette.currentData() == 1
    assert backend.writes[-1][0] == "palette"
    assert not window.settings.value("customRgb/profile0/effect1/custom", type=bool)


def test_saved_colors_keep_first_slot_history_and_separate_effects(app, window, backend):
    table = bytearray(default_color_table())
    table[21:24] = bytes((0, 132, 255))
    table[42:45] = bytes((255, 32, 96))
    window.settings.setValue("customRgb/profile0/table", bytes(table).hex())
    window._lighting_read(Lighting(1, 20, 1, None))
    assert window.color.getRgb()[:3] == (0, 132, 255)
    window.effect.setCurrentIndex(window.effect.findData(2))
    assert window.color.getRgb()[:3] == (255, 32, 96)
    window.discard_edits()
    wait_for_job(app, window)
    assert window.saved_colors == bytes(table)
    assert window.color.getRgb()[:3] == (0, 132, 255)


def test_color_dialog_blocks_refresh_and_cancel_does_not_edit(app, window, backend, monkeypatch):
    reads = len(backend.reads)

    def cancelled(*_):
        assert window.picking_color
        window._poll()
        app.processEvents()
        return QColor()

    monkeypatch.setattr(QColorDialog, "getColor", cancelled)
    window._choose_color()
    assert not window.picking_color
    assert not window.dirty
    assert len(backend.reads) == reads
    monkeypatch.setattr(QColorDialog, "getColor", lambda *_: QColor(12, 34, 56))
    window._choose_color()
    assert window.dirty
    assert window.use_custom.isChecked()
    assert window.color.getRgb()[:3] == (12, 34, 56)
    assert not backend.writes


def test_invalid_saved_history_is_reported_without_replacing_it(window):
    window.settings.setValue("customRgb/profile0/table", "not hex")
    window._lighting_read(Lighting(1, 20, 1, None))
    assert not window.loaded
    assert window.status.property("error")
    assert "hexadecimal" in window.status.toolTip()
    assert window.settings.value("customRgb/profile0/table") == "not hex"


def test_failed_apply_keeps_edits_and_saved_history(app, window, backend):
    window.color = QColor(0, 132, 255)
    window.use_custom.setChecked(True)
    window.apply_lighting()
    wait_for_job(app, window)
    saved = window.settings.value("customRgb/profile0/table")
    backend.error = "Keyboard unplugged"
    window.color = QColor(255, 32, 96)
    window._edited()
    window.apply_lighting()
    wait_for_job(app, window)
    assert window.status.property("error")
    assert window.dirty
    assert window.settings.value("customRgb/profile0/table") == saved


def test_close_while_busy_preserves_worker(app, window):
    window._start(lambda: time.sleep(0.03), lambda _: None)
    window.close()
    assert window.isVisible()
    wait_for_job(app, window)
    assert window.close()


def test_firmware_version_is_shown_under_image_and_read_once(app, window, backend):
    layout = window.preview.parentWidget().layout()
    assert layout.indexOf(window.firmware) == layout.indexOf(window.preview) + 1
    assert window.firmware.text() == "Firmware 2117"
    window._poll()
    wait_for_job(app, window)
    assert backend.version_reads == 1
    assert window.firmware.text() == "Firmware 2117"
    assert not backend.writes


def test_unreadable_firmware_version_does_not_block_connection(app, backend, monkeypatch, tmp_path):
    backend.version = None
    monkeypatch.setattr(
        gui,
        "QSettings",
        lambda *_: QSettings(
            str(tmp_path / "settings.ini"),
            QSettings.Format.IniFormat,
        ),
    )
    window = gui.MainWindow()
    window.show()
    wait_for_job(app, window)
    try:
        assert window.loaded
        assert window.firmware.text() == ""
    finally:
        window.close()


def test_firmware_version_clears_on_disconnect_and_rereads_on_reconnect(app, window, backend):
    devices = backend.devices
    backend.devices = []
    window._poll()
    wait_for_job(app, window)
    assert window.firmware.text() == ""
    backend.devices = devices
    backend.version = "2120"
    window._poll()
    wait_for_job(app, window)
    assert window.firmware.text() == "Firmware 2120"
    assert backend.version_reads == 2


@pytest.mark.parametrize("effect", [3, 15, 16])
def test_fixed_color_effects_hide_color_controls_and_send_no_custom_rgb(
    app, window, backend, effect
):
    window.color = QColor(0, 132, 255)
    window.use_custom.setChecked(True)
    window.effect.setCurrentIndex(window.effect.findData(effect))
    for widget in (
        window.color_heading,
        window.use_custom,
        window.palette,
        window.color_button,
        window.color_source,
        window.custom_note,
    ):
        assert not widget.isVisible()
    assert window.preview_subtitle.text().startswith("Built-in colors")
    window.apply_lighting()
    wait_for_job(app, window)
    assert [write[0] for write in backend.writes] == ["palette"]
    assert backend.writes[0][2].effect == effect
    assert backend.writes[0][2].color is None


def test_color_controls_return_for_effects_with_color(app, window):
    window.effect.setCurrentIndex(window.effect.findData(3))
    window.effect.setCurrentIndex(window.effect.findData(2))
    assert window.color_heading.isVisible()
    assert window.use_custom.isVisible()
    assert window.palette.isVisible()


def test_window_uses_bundled_launcher_icon(window):
    from importlib.resources import files

    from PySide6.QtGui import QIcon

    expected = QIcon(str(files("rks98").joinpath("assets/rks98.svg")))
    assert not window.windowIcon().pixmap(64, 64).isNull()
    assert window.windowIcon().pixmap(64, 64).toImage() == expected.pixmap(64, 64).toImage()


def test_image_upload_requires_image_and_sends_rgb565(app, window, backend, tmp_path):
    assert not window.upload_button.isEnabled()
    picture = gui.QImage(400, 300, gui.QImage.Format.Format_RGB32)
    picture.fill(QColor("green"))
    path = str(tmp_path / "pic.png")
    picture.save(path)
    window.load_image(path)
    assert window.upload_button.isEnabled() and window.delay_row.isHidden()
    window.upload_image()
    wait_for_job(app, window)
    assert backend.writes[-1] == ("display", 1, 0, 110080)
    assert window.status.isHidden()
    assert not window.uploading and not window.upload_button.isHidden()


def test_bad_image_shows_message_without_upload(app, window, backend, tmp_path):
    path = tmp_path / "bad.gif"
    path.write_bytes(b"nope")
    window.load_image(str(path))
    assert window.animation is None and "Can't open" in window.status.text()
    assert not window.upload_button.isEnabled()
    assert not window.status.isHidden()


def test_upload_progress_is_inside_indicator_and_cancel_remains_visible(window):
    window.uploading = True
    window.upload_stop.clear()
    window.upload_bar.show()
    window._upload_progressed(27, 100)
    assert window.upload_bar.value() == 27
    assert window.upload_bar.text() == "Uploading... 27%"
    assert window.upload_bar.isTextVisible()
    assert window.status.isHidden()
    window._cancel_upload()
    window._upload_progressed(28, 100)
    assert window.upload_bar.text() == "Cancelling upload..."
    assert window.status.isHidden()
    window._finished()
    assert window.upload_bar.isHidden()


def test_lighting_success_has_no_footer_but_errors_are_visible(app, window, backend):
    window.brightness.setValue(12)
    window.apply_lighting()
    wait_for_job(app, window)
    assert window.status.isHidden()
    assert backend.writes
    window._error("Disconnected")
    assert not window.status.isHidden()
    assert window.status.toolTip() == "Disconnected"
    window.read_lighting()
    wait_for_job(app, window)
    assert window.status.isHidden()


def test_drag_zoom_reset_and_fit_controls(app, window, tmp_path):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QTabWidget

    picture = gui.QImage(960, 172, gui.QImage.Format.Format_RGB32)
    picture.fill(QColor("green"))
    path = str(tmp_path / "wide.png")
    assert picture.save(path)
    window.findChild(QTabWidget).setCurrentIndex(1)
    window.load_image(path)
    preview = window.screen_preview
    app.processEvents()
    assert window.crop_zoom.isEnabled()
    QTest.mousePress(preview, Qt.MouseButton.LeftButton, pos=QPoint(160, 86))
    QTest.mouseMove(preview, QPoint(10, 86))
    QTest.mouseRelease(preview, Qt.MouseButton.LeftButton, pos=QPoint(10, 86))
    assert preview.crop.x > 0.5
    window.crop_zoom.setValue(300)
    assert preview.crop.zoom == 3
    wheel = QWheelEvent(
        QPointF(160, 86),
        QPointF(preview.mapToGlobal(QPoint(160, 86))),
        QPoint(),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    app.sendEvent(preview, wheel)
    assert preview.crop.zoom == 3.1 and window.crop_zoom.value() == 310
    # Drag far enough to reach an edge; the crop must not expose empty space.
    QTest.mousePress(preview, Qt.MouseButton.LeftButton, pos=QPoint(160, 86))
    QTest.mouseMove(preview, QPoint(-4000, -4000))
    QTest.mouseRelease(preview, Qt.MouseButton.LeftButton, pos=QPoint(-4000, -4000))
    assert preview.crop.x == 1 and preview.crop.y == 1
    QTest.mouseClick(window.crop_reset, Qt.MouseButton.LeftButton)
    assert preview.crop == gui.Crop() and window.crop_zoom.value() == 100
    window.fit_mode.setCurrentIndex(1)
    assert not window.crop_zoom.isEnabled() and not window.crop_reset.isEnabled()
    assert not preview.fill
    window.fit_mode.setCurrentIndex(0)
    assert window.crop_zoom.isEnabled()
    window.crop_zoom.setValue(800)
    window.load_image(path)
    assert preview.crop == gui.Crop() and window.crop_zoom.value() == 100


def test_custom_crop_preview_matches_every_uploaded_animation_frame(
    app,
    window,
    backend,
    monkeypatch,
):
    from PySide6.QtGui import QPainter

    frames = []
    for color in ("red", "green"):
        picture = gui.QImage(960, 172, gui.QImage.Format.Format_RGB32)
        picture.fill(QColor("blue"))
        painter = QPainter(picture)
        painter.fillRect(640, 0, 320, 172, QColor(color))
        painter.end()
        frames.append(picture)
    monkeypatch.setattr(
        gui, "load_animation", lambda *_args, **_kwargs: gui.Animation(frames, 100, 2)
    )
    window.load_image("animation.gif")
    window.preview_timer.stop()
    window.screen_preview.crop = gui.Crop(2, 1, 0)
    window.crop_zoom.setValue(200)
    expected = []
    for _ in frames:
        expected.append(gui.rgb565(window.screen_preview.pixmap().toImage()))
        window._next_preview_frame()
    window.upload_image()
    assert not window.screen_preview.isEnabled() and not window.crop_zoom.isEnabled()
    wait_for_job(app, window)
    assert backend.display_frames == expected
    assert backend.writes[-1][:3] == ("display", 2, 100)
    assert window.crop_zoom.isEnabled()
