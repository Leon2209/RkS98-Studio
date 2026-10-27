import os
import time
from dataclasses import replace
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QObject, QSettings, QThread
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QColorDialog, QLabel, QScrollArea

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

    backend = Backend()
    monkeypatch.setattr(gui, "discover", lambda: backend.devices)

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
