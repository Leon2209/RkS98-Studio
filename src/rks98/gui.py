"""Desktop controls with background USB operations and read-only auto refresh."""

import logging
import threading
from collections import deque
from collections.abc import Callable
from datetime import datetime
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

from PySide6.QtCore import QPointF, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QIcon,
    QImage,
    QMouseEvent,
    QPixmap,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .audio import VolumeMonitor
from .background import ServiceState, UserVolumeService
from .device import DeviceError, HidTransport, Keyboard, discover
from .display import FILE_FILTER, Animation, Crop, crop_frame, fit_frame, load_animation, rgb565
from .protocol import (
    COLORS_SIZE,
    DISPLAY_HEIGHT,
    DISPLAY_WIDTH,
    EFFECTS,
    FIXED_COLOR_EFFECTS,
    PALETTE,
    Lighting,
    ProtocolError,
)
from .service import Controller, CustomColorResult, UploadCancelled

logger = logging.getLogger(__name__)
REFRESH_INTERVAL_MS = 5000

STYLE = """
QMainWindow, QWidget#root { background: #10131d; }
QWidget { color: #e9edf7; font-family: "Inter", "Noto Sans", sans-serif; font-size: 13px; }
QLabel { background: transparent; }
QLabel#brand { font-size: 12px; font-weight: 700; color: #a494d9; }
QLabel#title { font-size: 30px; font-weight: 700; }
QLabel#muted, QLabel#colorSource { color: #929bb0; }
QLabel#sectionTitle { font-size: 18px; font-weight: 600; }
QLabel#eyebrow { color: #8d97ad; font-size: 11px; font-weight: 600; }
QLabel#connection { color: #88e0b5; background: #1b322e; border-radius: 12px;
    padding: 6px 12px; font-weight: 600; }
QLabel#connection[connected="false"] { color: #b5bdcc; background: #252b3a; }
QFrame#card { background: #1a1f2d; border: 1px solid #2a3143; border-radius: 18px; }
QFrame#settingOption { background: #202638; border: 1px solid #343e55; border-radius: 12px; }
QLabel#settingHelp { color: #b5bdcc; }
QLabel#settingStatus { color: #d3c3ff; }
QScrollArea#controlsScroll, QWidget#fields { background: #1a1f2d; border: none; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 0; }
QScrollBar::handle:vertical { background: #46516d; min-height: 24px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QLabel#status { color: #a9b5ca; }
QLabel#status[error="true"] { color: #ff9a9f; }
QComboBox, QSpinBox { background: #242b3c; border: 1px solid #343e55;
    border-radius: 8px; padding: 8px 12px; min-height: 20px; selection-background-color: #6751a7; }
QComboBox:hover, QSpinBox:hover { border-color: #8e77cc; }
QComboBox:focus, QSpinBox:focus { border-color: #b69aff; }
QComboBox:disabled, QSpinBox:disabled { color: #657087; background: #1c2231; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView { background: #252b3d; border: 1px solid #434c64;
    selection-background-color: #51416f; padding: 5px; outline: none; }
QSpinBox::up-button, QSpinBox::down-button { width: 18px; border: none; }
QPushButton { background: #293146; border: 1px solid #39435c; border-radius: 9px;
    padding: 10px 18px; font-weight: 600; }
QPushButton:hover { background: #343e55; border-color: #8f7abf; }
QPushButton:pressed { background: #202638; }
QPushButton:focus { border: 1px solid #c2a8ff; }
QPushButton:disabled { color: #657087; background: #222939; border-color: #2b3346; }
QPushButton#primary { background: #bba1ff; color: #221938; border: none; }
QPushButton#primary:hover { background: #ccb8ff; }
QPushButton#primary:pressed { background: #a48adf; }
QPushButton#primary:disabled { background: #3d3654; color: #847b98; }
QPushButton#quiet { background: transparent; border: 1px solid #343d52; color: #aeb8cc; }
QCheckBox { spacing: 10px; padding: 4px 0; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px;
    background: #242b3c; border: 1px solid #56617b; }
QCheckBox::indicator:checked { background: #bba1ff; border: 1px solid #bba1ff; }
QCheckBox:disabled { color: #657087; }
QSlider { background: transparent; border: none; padding: 0; min-height: 22px; }
QSlider::groove:horizontal { height: 5px; background: #333c52; border-radius: 2px; }
QSlider::add-page:horizontal { background: #333c52; border: none; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #bba1ff; border-radius: 2px; }
QSlider::handle:horizontal { background: #eee5ff; width: 15px; margin: -5px 0; border-radius: 7px; }
QSlider::sub-page:horizontal:disabled { background: #51566a; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; color: #909cb3; padding: 12px 4px;
    margin-right: 28px; border-bottom: 2px solid transparent; font-weight: 600; }
QTabBar::tab:selected { color: #decfff; border-bottom: 2px solid #bba1ff; }
QTabBar::tab:hover { color: #decfff; }
QLabel#screen { background: #000; border: 1px solid #343e55; border-radius: 6px;
    color: #657087; }
QProgressBar { background: #242b3c; border: none; border-radius: 6px;
    min-height: 24px; text-align: center; }
QProgressBar::chunk { background: #bba1ff; border-radius: 3px; }
QToolTip { color: #e9edf7; background: #293146; border: 1px solid #46516d; padding: 8px; }
"""


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def confirm_hardware_risk() -> bool:
    settings = QSettings("rks98-control", "rks98-control")
    key = "safety/hideStartupWarning"
    if settings.value(key, False, type=bool):
        return True
    dialog = QDialog()
    dialog.setWindowTitle("RK-S98 Studio - Hardware risk")
    dialog.setStyleSheet(STYLE)
    dialog.setWindowIcon(QIcon(str(files("rks98").joinpath("assets/rks98.svg"))))
    dialog.setMinimumWidth(480)
    layout = QVBoxLayout(dialog)
    layout.addWidget(label("Use at your own risk", "sectionTitle"))
    notice = label(
        "RK-S98 Studio is experimental software that writes directly to your keyboard "
        "using a reverse-engineered protocol.\n\n"
        "We cannot guarantee that it will not corrupt settings, damage the keyboard, "
        'or permanently make it unusable ("brick" it). There is no guaranteed recovery '
        "procedure or automatic rollback.\n\n"
        "The software is provided without warranty. To the fullest extent permitted "
        "by applicable law, the authors and contributors accept no liability for "
        "device damage, data loss, or other loss arising from its use.\n\n"
        "Continue only if you understand and accept these risks.",
        wrap=True,
    )
    notice.setMaximumWidth(520)
    layout.addWidget(notice)
    remember = QCheckBox("Don't show again")
    layout.addWidget(remember)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Continue")
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Quit")
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    if remember.isChecked():
        settings.setValue(key, True)
        settings.sync()
        if settings.status() != settings.Status.NoError:
            logger.error("Could not save startup warning preference")
            QMessageBox.warning(
                dialog,
                "Preference not saved",
                "Could not save your preference. The warning may appear next time.",
            )
    return True


class KeyboardImage(QLabel):
    """The owner's keyboard artwork, scaled to fit without distortion."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(360, 200)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAccessibleName("RK-S98 keyboard")
        self.image = QPixmap()
        data = files("rks98").joinpath("assets/keyboard_rk-s98.png").read_bytes()
        if not self.image.loadFromData(data):
            raise RuntimeError("Could not load the bundled RK-S98 keyboard image.")

    def resizeEvent(self, event: QResizeEvent) -> None:
        self.setPixmap(
            self.image.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        super().resizeEvent(event)


class CropPreview(QLabel):
    zoom_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__("No image yet")
        self.setObjectName("screen")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(DISPLAY_WIDTH + 2, DISPLAY_HEIGHT + 2)
        self.setAccessibleName("Screen crop preview")
        self.setToolTip("Drag to position the image. Scroll or use the slider to zoom.")
        self.image = QImage()
        self.crop = Crop()
        self.fill = True
        self.drag_position: QPointF | None = None

    def show_frame(self, image: QImage) -> None:
        self.image = image
        frame = crop_frame(image, self.crop) if self.fill else fit_frame(image, False)
        self.setPixmap(QPixmap.fromImage(frame))
        self.setCursor(Qt.CursorShape.OpenHandCursor if self.fill else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (
            self.isEnabled()
            and self.fill
            and not self.image.isNull()
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self.drag_position = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.drag_position is None:
            super().mouseMoveEvent(event)
            return
        delta = event.position() - self.drag_position
        self.drag_position = event.position()
        scale = (
            max(
                DISPLAY_WIDTH / self.image.width(),
                DISPLAY_HEIGHT / self.image.height(),
            )
            * self.crop.zoom
        )
        extra_x = max(0.0, self.image.width() * scale - DISPLAY_WIDTH)
        extra_y = max(0.0, self.image.height() * scale - DISPLAY_HEIGHT)
        self.crop = Crop(
            self.crop.zoom,
            min(1.0, max(0.0, self.crop.x - delta.x() / extra_x)) if extra_x > 0 else 0.5,
            min(1.0, max(0.0, self.crop.y - delta.y() / extra_y)) if extra_y > 0 else 0.5,
        )
        self.show_frame(self.image)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = None
            self.setCursor(
                Qt.CursorShape.OpenHandCursor if self.fill else Qt.CursorShape.ArrowCursor
            )
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.isEnabled() and self.fill and not self.image.isNull():
            self.zoom_requested.emit(
                round(self.crop.zoom * 100) + round(event.angleDelta().y() / 120 * 10)
            )
            event.accept()
        else:
            event.ignore()


class Job(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, action: Callable[[], object], parent: QWidget):
        super().__init__(parent)
        self.action = action

    def run(self) -> None:
        try:
            result = self.action()
        except Exception as error:
            logger.exception("Keyboard operation failed")
            self.failed.emit(str(error) or type(error).__name__)
        else:
            self.succeeded.emit(result)


class MainWindow(QMainWindow):
    upload_progress = Signal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("RK-S98 Studio")
        self.setWindowIcon(QIcon(str(files("rks98").joinpath("assets/rks98.svg"))))
        if self.windowIcon().isNull():
            raise RuntimeError("Could not load the bundled application icon.")
        self.settings = QSettings("rks98-control", "rks98-control")
        self.resize(1100, 790)
        self.setMinimumSize(980, 740)
        geometry = self.settings.value("windowGeometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        self.setStyleSheet(STYLE)
        self.job: Job | None = None
        self.background_read = False
        self.background_write = False
        self.edit_revision = 0
        self.pending_actions: deque[Callable[[], None]] = deque()
        self.devices: list[Keyboard] = []
        self.loaded = False
        self.dirty = False
        self.loading = True
        self.picking_color = False
        self.custom_color_available = False
        self.saved_colors: bytes | None = None
        self.color = QColor("#0084ff")
        self.last_lighting: Lighting | None = None
        self.desktop_volume: int | None = None
        self.volume_keyboard: bytes | None = None
        self.volume_attempt: tuple[bytes, int] | None = None
        self.closing = False
        self.user_volume_service = UserVolumeService()
        self.service_ready = False
        self.service_state = ServiceState(False, False)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(32, 24, 32, 22)
        layout.setSpacing(18)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(5)
        titles.addWidget(label("RK-S98 Studio", "title"))
        header.addLayout(titles)
        header.addStretch()
        self.connection = label("Looking for keyboard", "connection")
        header.addWidget(self.connection, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        connection_row = QHBoxLayout()
        self.device_selector = QComboBox()
        self.device_selector.setAccessibleName("Keyboard")
        self.device_selector.setMinimumWidth(200)
        self.device_selector.currentIndexChanged.connect(self._selection_changed)
        connection_row.addWidget(self.device_selector, 1)
        self.refresh = QPushButton("Refresh")
        self.refresh.setObjectName("quiet")
        self.refresh.clicked.connect(self.scan)
        connection_row.addWidget(self.refresh)
        layout.addLayout(connection_row)

        tabs = QTabWidget()
        lighting_page = QWidget()
        content = QHBoxLayout(lighting_page)
        content.setContentsMargins(0, 18, 0, 0)
        content.setSpacing(20)
        preview_card, preview_layout = self._card()
        preview_layout.addWidget(label("RK-S98", "sectionTitle"))
        preview_layout.addStretch()
        self.preview = KeyboardImage()
        preview_layout.addWidget(self.preview)
        self.firmware = label("", "muted")
        self.firmware.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self.firmware)
        preview_layout.addStretch()
        self.preview_title = label("Lighting", "sectionTitle")
        preview_layout.addWidget(self.preview_title)
        self.preview_subtitle = label("", "muted", True)
        preview_layout.addWidget(self.preview_subtitle)
        content.addWidget(preview_card, 1)

        controls, card_layout = self._card()
        card_layout.addWidget(label("Lighting", "sectionTitle"))
        controls_scroll = QScrollArea()
        controls_scroll.setObjectName("controlsScroll")
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        fields = QWidget()
        fields.setObjectName("fields")
        controls_layout = QVBoxLayout(fields)
        controls_layout.setContentsMargins(0, 0, 12, 0)
        controls_layout.setSpacing(10)
        controls_scroll.setWidget(fields)
        card_layout.addWidget(controls_scroll, 1)
        controls_layout.addWidget(label("EFFECT", "eyebrow"))
        self.effect = QComboBox()
        self.effect.setAccessibleName("Lighting effect")
        for value, name in EFFECTS.items():
            self.effect.addItem(name, value)
        controls_layout.addWidget(self.effect)
        self.effect.currentIndexChanged.connect(self._selected_effect_changed)

        self.brightness, self.brightness_slider = self._level(
            controls_layout,
            "BRIGHTNESS",
            20,
            20,
        )
        self.speed, self.speed_slider = self._level(controls_layout, "SPEED", 4, 2)
        color_heading = QHBoxLayout()
        self.color_heading = label("COLOR", "eyebrow")
        color_heading.addWidget(self.color_heading)
        color_heading.addStretch()
        self.use_custom = QCheckBox("Custom color")
        self.use_custom.toggled.connect(self._edited)
        color_heading.addWidget(self.use_custom)
        controls_layout.addLayout(color_heading)
        self.palette = QComboBox()
        self.palette.setAccessibleName("Built-in color")
        for index, name in PALETTE.items():
            self.palette.addItem(name, index)
        self.palette.currentIndexChanged.connect(self._edited)
        controls_layout.addWidget(self.palette)
        self.color_button = QPushButton()
        self.color_button.setAccessibleName("Choose custom RGB color")
        self.color_button.clicked.connect(self._choose_color)
        controls_layout.addWidget(self.color_button)
        self.color_source = label(
            "Custom colors are remembered on this computer.", "colorSource", True
        )
        controls_layout.addWidget(self.color_source)
        self.color_source.setToolTip(
            "The keyboard cannot report custom RGB. Saved colors may differ after "
            "changes in another app. Applying custom RGB can replace other effect colors."
        )
        controls_layout.addStretch()
        self.custom_note = label(
            "Custom RGB uses your saved colors. Unknown effect colors will be initialized.",
            "muted",
            True,
        )
        controls_layout.addWidget(self.custom_note)
        buttons = QHBoxLayout()
        self.revert_button = QPushButton("Discard")
        self.revert_button.setObjectName("quiet")
        self.revert_button.clicked.connect(self.discard_edits)
        buttons.addWidget(self.revert_button)
        self.apply_button = QPushButton("Apply lighting")
        self.apply_button.setObjectName("primary")
        self.apply_button.clicked.connect(self.apply_lighting)
        buttons.addWidget(self.apply_button, 1)
        card_layout.addLayout(buttons)
        content.addWidget(controls, 1)
        tabs.addTab(lighting_page, "Lighting")

        display_page = QWidget()
        display_layout = QHBoxLayout(display_page)
        display_layout.setSpacing(18)
        display_layout.setContentsMargins(0, 18, 0, 0)
        clock_card, clock_layout = self._card()
        clock_layout.addWidget(label("Clock", "sectionTitle"))
        self.clock_preview = label("", "title")
        self.clock_preview.setStyleSheet("font-size: 64px; font-weight: 600;")
        clock_layout.addStretch()
        clock_layout.addWidget(self.clock_preview)
        self.clock_date = label("", "muted")
        clock_layout.addWidget(self.clock_date)
        clock_layout.addWidget(label("Computer's local time", "muted"))
        clock_layout.addStretch()
        clock_layout.addWidget(
            label("Send your computer's date and time to the keyboard.", "muted")
        )
        self.clock_button = QPushButton("Sync clock")
        self.clock_button.setObjectName("primary")
        self.clock_button.clicked.connect(self.sync_clock)
        clock_layout.addWidget(self.clock_button, alignment=Qt.AlignmentFlag.AlignLeft)
        display_layout.addWidget(clock_card, 2)
        display_layout.addWidget(self._screen_card(), 3)
        tabs.addTab(display_page, "Display")
        settings_page = QWidget()
        settings_layout = QVBoxLayout(settings_page)
        settings_layout.setContentsMargins(0, 18, 0, 0)
        settings_card, settings_fields = self._card()
        settings_fields.addWidget(label("Settings", "sectionTitle"))
        volume_option, volume_fields = self._card()
        volume_option.setObjectName("settingOption")
        volume_fields.setSpacing(4)
        self.volume_sync = QCheckBox("Sync desktop volume to keyboard")
        self.volume_sync.setChecked(self.settings.value("volumeSync/enabled", True, type=bool))
        self.volume_sync.toggled.connect(self._volume_sync_toggled)
        volume_fields.addWidget(self.volume_sync)
        volume_details = QVBoxLayout()
        volume_details.setContentsMargins(28, 0, 0, 0)
        volume_details.setSpacing(4)
        volume_details.addWidget(
            label(
                "Sync volume and clock while the app is open.",
                "settingHelp",
                True,
            )
        )
        self.volume_status = label(
            "Waiting for desktop volume..." if self.volume_sync.isChecked() else "Volume sync off.",
            "settingStatus",
            True,
        )
        volume_details.addWidget(self.volume_status)
        volume_fields.addLayout(volume_details)
        settings_fields.addWidget(volume_option)
        background_option, background_fields = self._card()
        background_option.setObjectName("settingOption")
        background_fields.setSpacing(4)
        self.background_sync = QCheckBox("Sync volume in the background and start at login")
        self.background_sync.setEnabled(False)
        self.background_sync.toggled.connect(self._background_sync_toggled)
        background_fields.addWidget(self.background_sync)
        background_details = QVBoxLayout()
        background_details.setContentsMargins(28, 0, 0, 0)
        background_details.setSpacing(4)
        background_details.addWidget(
            label(
                "Sync volume and clock with the app closed. Pauses while it is open.",
                "settingHelp",
                True,
            )
        )
        self.background_status = label("Checking background service...", "settingStatus", True)
        background_details.addWidget(self.background_status)
        background_fields.addLayout(background_details)
        settings_fields.addWidget(background_option)
        settings_fields.addStretch()
        self.application_version = label(f"Version {version('rks98-control')}", "muted")
        settings_fields.addWidget(self.application_version)
        settings_layout.addWidget(settings_card)
        tabs.addTab(settings_page, "Settings")

        def tab_changed(index: int) -> None:
            if tabs.widget(index) is settings_page:
                self._refresh_background_service()

        tabs.currentChanged.connect(tab_changed)
        layout.addWidget(tabs, 1)

        self.status = label("", "status", True)
        self.status.hide()
        layout.addWidget(self.status)
        self.volume_monitor = VolumeMonitor(self)
        self.volume_monitor.changed.connect(self._volume_changed)
        self.volume_monitor.failed.connect(self._volume_failed)
        self.loading = False
        self.firmware_versions: dict[bytes, str] = {}
        self._show_color()
        self._update_enabled()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(REFRESH_INTERVAL_MS)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(1000)
        self._update_clock()
        QTimer.singleShot(0, self.scan)
        QTimer.singleShot(0, self._start_volume_monitor)
        QTimer.singleShot(0, self._refresh_background_service)

    def _screen_card(self) -> QFrame:
        card, layout = self._card()
        layout.addWidget(label("Screen image", "sectionTitle"))
        self.screen_preview = CropPreview()
        layout.addWidget(self.screen_preview, alignment=Qt.AlignmentFlag.AlignLeft)
        self.screen_info = label("A picture or GIF, shown at 320 x 172.", "muted")
        layout.addWidget(self.screen_info)
        row = QHBoxLayout()
        self.choose_image = QPushButton("Choose image...")
        self.choose_image.clicked.connect(self._choose_image)
        row.addWidget(self.choose_image)
        self.fit_mode = QComboBox()
        self.fit_mode.addItem("Fill screen", True)
        self.fit_mode.addItem("Fit inside", False)
        self.fit_mode.setAccessibleName("Image fit")
        self.fit_mode.currentIndexChanged.connect(self._crop_mode_changed)
        row.addWidget(self.fit_mode)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(label("Drag to position. Scroll or use Zoom to crop.", "muted"))
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(label("ZOOM", "eyebrow"))
        self.crop_zoom = QSlider(Qt.Orientation.Horizontal)
        self.crop_zoom.setRange(100, 800)
        self.crop_zoom.setValue(100)
        self.crop_zoom.setAccessibleName("Crop zoom")
        self.crop_zoom.valueChanged.connect(self._crop_zoom_changed)
        self.screen_preview.zoom_requested.connect(self.crop_zoom.setValue)
        zoom_row.addWidget(self.crop_zoom, 1)
        self.crop_reset = QPushButton("Reset")
        self.crop_reset.clicked.connect(self._reset_crop)
        zoom_row.addWidget(self.crop_reset)
        layout.addLayout(zoom_row)
        self.delay_row = QWidget()
        delay_layout = QHBoxLayout(self.delay_row)
        delay_layout.setContentsMargins(0, 0, 0, 0)
        delay_layout.addWidget(label("FRAME DELAY", "eyebrow"))
        self.frame_delay = QSpinBox()
        self.frame_delay.setRange(10, 5000)
        self.frame_delay.setSingleStep(10)
        self.frame_delay.setSuffix(" ms")
        self.frame_delay.setAccessibleName("Frame delay")
        self.frame_delay.valueChanged.connect(self._delay_changed)
        delay_layout.addWidget(self.frame_delay)
        delay_layout.addStretch()
        self.delay_row.hide()
        layout.addWidget(self.delay_row)
        layout.addStretch()
        self.upload_bar = QProgressBar()
        self.upload_bar.setTextVisible(True)
        self.upload_bar.setAccessibleName("Image upload progress")
        self.upload_bar.hide()
        layout.addWidget(self.upload_bar)
        buttons = QHBoxLayout()
        self.upload_button = QPushButton("Upload to keyboard")
        self.upload_button.setObjectName("primary")
        self.upload_button.clicked.connect(self.upload_image)
        buttons.addWidget(self.upload_button)
        self.cancel_upload = QPushButton("Cancel")
        self.cancel_upload.setObjectName("quiet")
        self.cancel_upload.clicked.connect(self._cancel_upload)
        self.cancel_upload.hide()
        buttons.addWidget(self.cancel_upload)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.image_path = ""
        self.animation: Animation | None = None
        self.preview_index = 0
        self.preview_timer = QTimer(self)
        self.preview_timer.timeout.connect(self._next_preview_frame)
        self.upload_stop = threading.Event()
        self.uploading = False
        self.upload_progress.connect(self._upload_progressed)
        return card

    def _card(self) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(10)
        return frame, layout

    def _level(self, layout: QVBoxLayout, title: str, maximum: int, value: int):
        row = QHBoxLayout()
        title_label = label(title, "eyebrow")
        title_label.setFixedWidth(84)
        row.addWidget(title_label)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, maximum)
        slider.setAccessibleName(title.title())
        spin = QSpinBox()
        spin.setRange(0, maximum)
        spin.setAccessibleName(title.title() + " value")
        spin.setFixedWidth(76)
        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(slider.setValue)
        spin.valueChanged.connect(self._edited)
        spin.setValue(value)
        row.addWidget(slider, 1)
        row.addWidget(spin)
        layout.addLayout(row)
        return spin, slider

    def _keyboard(self) -> Keyboard | None:
        index = self.device_selector.currentIndex()
        return self.devices[index] if 0 <= index < len(self.devices) else None

    def _selection_changed(self) -> None:
        if self.loading:
            return
        self.edit_revision += 1
        self.loaded = False
        self.dirty = False
        self.last_lighting = None
        self.saved_colors = None
        self._volume_connection_changed()
        self._update_enabled()
        self.read_lighting()

    def _update_enabled(self) -> None:
        busy = self.job is not None and not (self.background_read or self.background_write)
        keyboard = self._keyboard()
        connected = keyboard is not None and keyboard.supported
        ready = connected and self.loaded and not busy
        self.refresh.setEnabled(not busy and not self.dirty)
        self.device_selector.setEnabled(not busy and not self.dirty)
        self.background_sync.setEnabled(self.service_ready and self.job is None)
        self.effect.setEnabled(ready)
        self.apply_button.setEnabled(ready and self.dirty)
        self.revert_button.setEnabled(not busy and self.dirty)
        self.clock_button.setEnabled(connected and not busy)
        self.upload_button.setEnabled(connected and not busy and self.animation is not None)
        self.choose_image.setEnabled(not self.uploading)
        self.fit_mode.setEnabled(not self.uploading and self.animation is not None)
        self.frame_delay.setEnabled(not self.uploading)
        crop_enabled = not self.uploading and self.animation is not None
        self.screen_preview.setEnabled(crop_enabled)
        self.crop_zoom.setEnabled(crop_enabled and self.fit_mode.currentData())
        self.crop_reset.setEnabled(crop_enabled and self.fit_mode.currentData())
        self.connection.setText(
            "Connected via USB" if connected and self.loaded else "Not connected"
        )
        if self.connection.property("connected") != (connected and self.loaded):
            self.connection.setProperty("connected", connected and self.loaded)
            self.connection.style().unpolish(self.connection)
            self.connection.style().polish(self.connection)
        self._effect_changed()

    def _clear_error(self) -> None:
        self.status.clear()
        self.status.hide()
        self.status.setToolTip("")
        if self.status.property("error") is not False:
            self.status.setProperty("error", False)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)

    def _show_error(self, text: str, details: str = "") -> None:
        self.status.setText(text)
        self.status.setToolTip(details or text)
        self.status.setProperty("error", True)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.show()

    def _error(self, text: str) -> None:
        self.loaded = False
        self._show_error(
            "Couldn't update the keyboard. Check the USB connection. Hover for details.", text
        )
        self._update_enabled()

    def _start(
        self,
        action: Callable[[], object],
        callback: Callable,
        *,
        background: bool = False,
        quiet: bool = False,
        failed: Callable[[str], None] | None = None,
    ) -> None:
        if self.job is not None:
            return
        job = Job(action, self)
        self.job = job
        self.background_read = background
        self.background_write = quiet
        job.succeeded.connect(callback)
        job.failed.connect(failed or self._error)
        job.finished.connect(self._finished)
        if not (background or quiet):
            if failed is None:
                self._clear_error()
            self._update_enabled()
        job.start()

    def _finished(self) -> None:
        if self.uploading:
            self.uploading = False
            self.upload_bar.hide()
            self.cancel_upload.hide()
            self.upload_button.show()
        background = self.background_read or self.background_write
        if self.job is not None:
            self.job.deleteLater()
            self.job = None
        self.background_read = False
        self.background_write = False
        if not background:
            self._update_enabled()
        while self.job is None and self.pending_actions:
            self.pending_actions.popleft()()
        self._sync_pending_volume()
        self.background_sync.setEnabled(self.service_ready and self.job is None)

    def _wait_for_read(self, action: Callable[[], None]) -> bool:
        if self.job is None:
            return False
        if (self.background_read or self.background_write) and action not in self.pending_actions:
            self.pending_actions.append(action)
        return True

    def _poll(self) -> None:
        if self.job is None and not self.dirty and not self.picking_color:
            self.scan(background=True)

    def scan(self, background: bool = False) -> None:
        if self.dirty or self.picking_color or self._wait_for_read(self.scan):
            return
        keyboard = self._keyboard()
        path = keyboard.path if keyboard is not None else None
        revision = self.edit_revision
        versions = dict(self.firmware_versions)

        def read():
            devices = discover()
            index = next(
                (i for i, device in enumerate(devices) if device.path == path and device.supported),
                next((i for i, device in enumerate(devices) if device.supported), -1),
            )
            lighting = None
            version = None
            if index >= 0:
                with HidTransport(devices[index]) as transport:
                    controller = Controller(transport)
                    lighting = controller.read_lighting(0)
                    version = versions.get(devices[index].path)
                    if version is None:
                        try:
                            version = controller.read_firmware_version()
                        except (DeviceError, ProtocolError, OSError) as error:
                            logger.debug("Firmware version unavailable: %s", error)
            return devices, index, lighting, version

        def scanned(result):
            if background and (self.dirty or self.picking_color or revision != self.edit_revision):
                return
            self._scanned(result)

        self._start(read, scanned, background=background)

    def _scanned(
        self,
        result: tuple[list[Keyboard], int, Lighting | None, str | None],
    ) -> None:
        devices, index, lighting, version = result
        present = {device.path for device in devices}
        # Forget unplugged keyboards so a firmware update is picked up on reconnect.
        self.firmware_versions = {
            path: known for path, known in self.firmware_versions.items() if path in present
        }
        if version is not None and index >= 0:
            self.firmware_versions[devices[index].path] = version
        firmware = f"Firmware {version}" if lighting is not None and version else ""
        if self.firmware.text() != firmware:
            self.firmware.setText(firmware)
        self.loading = True
        if self.devices != devices:
            self.devices = devices
            self.device_selector.clear()
            for number, device in enumerate(devices):
                self.device_selector.addItem(f"RK-S98  /  USB {number + 1}")
                self.device_selector.setItemData(number, device.detail, Qt.ItemDataRole.ToolTipRole)
        if self.device_selector.currentIndex() != index:
            self.device_selector.setCurrentIndex(index)
        self.loading = False
        if lighting is None:
            self.loaded = False
            self.last_lighting = None
            self._clear_error()
            self.preview_subtitle.setText("")
            self._update_enabled()
        else:
            self._lighting_read(lighting)
        self._volume_connection_changed()

    def _start_volume_monitor(self) -> None:
        if not self.closing and self.volume_sync.isChecked():
            self.volume_monitor.start()

    def _refresh_background_service(self, error: str = "") -> None:
        if self.job is not None:
            self.pending_actions.append(lambda: self._refresh_background_service(error))
            return

        def loaded(state: ServiceState) -> None:
            self.service_state = state
            self.service_ready = True
            self.background_sync.blockSignals(True)
            self.background_sync.setChecked(state.enabled or state.active)
            self.background_sync.blockSignals(False)
            self.background_status.setText(error or state.text)

        def failed(text: str) -> None:
            self.service_ready = False
            self.background_status.setText(f"Background service unavailable: {text}")

        self._start(self.user_volume_service.state, loaded, quiet=True, failed=failed)

    def _background_sync_toggled(self, enabled: bool) -> None:
        if enabled:
            answer = QMessageBox.question(
                self,
                "Enable background hardware writes?",
                "Enable volume and clock synchronization at login and while this app is closed?\n\n"
                "The service writes directly to the keyboard. Hardware risk remains the same. "
                "It pauses while this app is open; the app's volume checkbox controls that time.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.background_sync.blockSignals(True)
                self.background_sync.setChecked(
                    self.service_state.enabled or self.service_state.active
                )
                self.background_sync.blockSignals(False)
                return
        self.background_sync.setEnabled(False)

        def action() -> None:
            self._set_background_service(enabled)

        if self._wait_for_read(action):
            return
        self._set_background_service(enabled)

    def _set_background_service(self, enabled: bool) -> None:
        self.service_ready = False

        def loaded(state: ServiceState) -> None:
            self.service_state = state
            self.service_ready = True
            self.background_status.setText(state.text)

        def failed(text: str) -> None:
            self.background_status.setText(f"Background service change failed: {text}")
            self.pending_actions.append(
                lambda: self._refresh_background_service(
                    f"Background service change failed: {text}"
                )
            )

        self._start(
            lambda: self.user_volume_service.set_enabled(enabled),
            loaded,
            failed=failed,
        )

    def _volume_sync_toggled(self, enabled: bool) -> None:
        self.settings.setValue("volumeSync/enabled", enabled)
        self.settings.sync()
        if self.settings.status() != self.settings.Status.NoError:
            logger.error("Could not save volume sync preference")
            self._show_error("Could not save the volume sync preference.")
        self.volume_monitor.stop()
        self.desktop_volume = None
        self.volume_attempt = None
        self.volume_status.setText(
            "Waiting for desktop volume..." if enabled else "Volume sync off."
        )
        if enabled:
            self._start_volume_monitor()

    def _volume_connection_changed(self) -> None:
        keyboard = self._keyboard()
        path = keyboard.path if keyboard is not None and keyboard.supported else None
        if path != self.volume_keyboard:
            self.volume_keyboard = path
            self.volume_attempt = None
            self.desktop_volume = None
            if path is not None and self.volume_sync.isChecked():
                self.volume_monitor.request_volume()

    def _volume_changed(self, volume: int) -> None:
        if not self.volume_sync.isChecked():
            return
        self.desktop_volume = volume
        self._sync_pending_volume()

    def _volume_failed(self, text: str) -> None:
        if not self.volume_sync.isChecked():
            return
        self.desktop_volume = None
        self.volume_status.setText(text)

    def _sync_pending_volume(self) -> None:
        if (
            self.closing
            or not self.volume_sync.isChecked()
            or self.job is not None
            or self.desktop_volume is None
        ):
            return
        keyboard = self._keyboard()
        if keyboard is None or not keyboard.supported or not self.loaded:
            return
        attempt = (keyboard.path, self.desktop_volume)
        if attempt == self.volume_attempt:
            return
        self.volume_attempt = attempt

        def send() -> str:
            with HidTransport(keyboard) as transport:
                return Controller(transport).sync_volume(attempt[1])

        def sent(_result: str) -> None:
            if self.volume_sync.isChecked() and self.desktop_volume is not None:
                self.volume_status.setText(f"Volume sent: {attempt[1]}%")

        def failed(text: str) -> None:
            if self.volume_sync.isChecked():
                self.volume_status.setText(f"Volume sync failed: {text}")

        self._start(
            send,
            sent,
            quiet=True,
            failed=failed,
        )

    def _operation(self, action: Callable[[Controller], object], callback: Callable) -> None:
        keyboard = self._keyboard()
        if keyboard is None or not keyboard.supported:
            self._error("No supported keyboard connected.")
            return

        def run() -> object:
            with HidTransport(keyboard) as transport:
                return action(Controller(transport))

        self._start(run, callback)

    def read_lighting(self) -> None:
        if self.dirty or self._wait_for_read(self.read_lighting):
            return
        keyboard = self._keyboard()
        if keyboard is None:
            self.scan()
            return
        self._operation(lambda controller: controller.read_lighting(0), self._lighting_read)

    def _settings_key(self) -> str:
        # Retain the original first-slot key so existing custom colors are preserved.
        return "customRgb/profile0"

    def _load_saved_colors(self) -> bytes | None:
        value = self.settings.value(f"{self._settings_key()}/table")
        if value is None:
            return None
        if not isinstance(value, str):
            raise ProtocolError("Saved RGB history is invalid; expected a hexadecimal table.")
        try:
            colors = bytes.fromhex(value)
        except ValueError as error:
            raise ProtocolError("Saved RGB history is not valid hexadecimal.") from error
        if len(colors) != COLORS_SIZE:
            raise ProtocolError("Saved RGB history has the wrong length.")
        return colors

    def _lighting_read(self, lighting: Lighting) -> None:
        try:
            saved_colors = self._load_saved_colors()
        except ProtocolError as error:
            self._error(str(error))
            return
        if (
            self.background_read
            and self.loaded
            and lighting == self.last_lighting
            and saved_colors == self.saved_colors
        ):
            return
        self.saved_colors = saved_colors
        self.loading = True
        self.last_lighting = lighting
        self.custom_color_available = lighting.color is not None
        self.effect.setCurrentIndex(self.effect.findData(lighting.effect))
        self.brightness.setValue(lighting.brightness)
        self.speed.setValue(lighting.speed)
        index = self.palette.findData(lighting.palette_index)
        if index < 0:
            self.palette.addItem(f"Color slot {lighting.palette_index}", lighting.palette_index)
            index = self.palette.count() - 1
        self.palette.setCurrentIndex(index)
        self._restore_color(lighting.effect, lighting.color)
        saved_mode = self.settings.value(
            f"{self._settings_key()}/effect{lighting.effect}/custom",
            False,
            type=bool,
        )
        self.use_custom.setChecked(
            bool(saved_mode and self.saved_colors is not None and lighting.palette_index == 0)
        )
        self.loaded = True
        self.dirty = False
        self.loading = False
        self._update_enabled()
        self._clear_error()

    def _restore_color(
        self, effect: int, hardware_color: tuple[int, int, int] | None = None
    ) -> None:
        if hardware_color is not None:
            self.color = QColor(*hardware_color)
            self.color_source.setText("Color from keyboard")
        elif self.saved_colors is not None:
            offset = effect * 21
            self.color = QColor(*self.saved_colors[offset : offset + 3])
            self.color_source.setText("Custom color saved on this computer")
        else:
            self.color = QColor("#0084ff")
            self.color_source.setText("No saved custom color.")
        self._show_color()

    def _edited(self) -> None:
        if self.loading:
            return
        self.edit_revision += 1
        if self.loaded:
            self.dirty = True
        self._update_enabled()

    def _selected_effect_changed(self) -> None:
        if not self.loading:
            self._restore_color(self.effect.currentData())
        self._edited()

    def discard_edits(self) -> None:
        if self._wait_for_read(self.discard_edits):
            return
        self.dirty = False
        self.scan()

    def apply_lighting(self) -> None:
        if self._wait_for_read(self.apply_lighting):
            return
        if not self.loaded:
            self._error("Connect the keyboard and load its settings before applying.")
            return
        lighting = Lighting(
            self.effect.currentData(),
            self.brightness.value(),
            self.speed.value(),
            (self.color.red(), self.color.green(), self.color.blue())
            if self._custom_selected()
            else None,
            self.palette.currentData(),
        )
        if lighting.color is not None:
            saved_colors = self.saved_colors
            self._operation(
                lambda controller: controller.apply_custom_color(0, lighting, saved_colors),
                self._custom_applied,
            )
        else:
            self._operation(
                lambda controller: controller.apply_lighting(0, lighting),
                self._lighting_applied,
            )

    def _save_settings(self) -> bool:
        self.settings.sync()
        if self.settings.status() != self.settings.Status.NoError:
            self._error("Keyboard updated, but saving local preferences failed.")
            return False
        return True

    def _lighting_applied(self, lighting: Lighting) -> None:
        self.settings.setValue(
            f"{self._settings_key()}/effect{lighting.effect}/custom",
            False,
        )
        if not self._save_settings():
            return
        self._lighting_read(lighting)

    def _custom_applied(self, result: CustomColorResult) -> None:
        self.settings.setValue(f"{self._settings_key()}/table", result.colors.hex())
        self.settings.setValue(
            f"{self._settings_key()}/effect{result.lighting.effect}/custom",
            True,
        )
        if not self._save_settings():
            return
        self._lighting_read(result.lighting)
        self.custom_color_available = result.color_verified
        self.color_source.setText(
            "Color from keyboard"
            if result.color_verified
            else "Custom color saved on this computer"
        )

    def sync_clock(self) -> None:
        if self._wait_for_read(self.sync_clock):
            return
        volume = self.desktop_volume
        self._operation(
            lambda controller: (
                controller.sync_volume(volume) if volume is not None else controller.sync_clock()
            ),
            lambda _result: None,
        )

    def _choose_image(self) -> None:
        start = str(Path(self.image_path).parent) if self.image_path else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Choose image", start, FILE_FILTER)
        if path:
            self.load_image(path)

    def _crop_mode_changed(self) -> None:
        self.screen_preview.fill = self.fit_mode.currentData()
        self._show_image_preview()
        self._update_enabled()

    def _reset_crop(self) -> None:
        self.screen_preview.crop = Crop()
        self.crop_zoom.setValue(100)
        self._show_image_preview()

    def _crop_zoom_changed(self, value: int) -> None:
        crop = self.screen_preview.crop
        self.screen_preview.crop = Crop(value / 100, crop.x, crop.y)
        self._show_image_preview()

    def _show_image_preview(self) -> None:
        if self.animation is not None:
            self.screen_preview.show_frame(self.animation.frames[self.preview_index])

    def load_image(self, path: str) -> None:
        try:
            animation = load_animation(path, fill=None)
        except ProtocolError as error:
            self._show_error(str(error))
            return
        same_file = path == self.image_path
        self.image_path = path
        self.animation = animation
        self.preview_index = 0
        self._reset_crop()
        animated = len(animation.frames) > 1
        if animated and not same_file:
            self.frame_delay.setValue(max(10, animation.delay_ms))
        self.delay_row.setVisible(animated)
        name = Path(path).name
        if not animated:
            self.screen_info.setText(name)
        elif animation.source_frames > len(animation.frames):
            self.screen_info.setText(
                f"{name}  /  {len(animation.frames)} of {animation.source_frames} frames"
            )
        else:
            self.screen_info.setText(f"{name}  /  {len(animation.frames)} frames")
        self._show_image_preview()
        self._clear_error()
        self._delay_changed()
        self._update_enabled()

    def _delay_changed(self) -> None:
        if self.animation is not None and len(self.animation.frames) > 1:
            self.preview_timer.start(self.frame_delay.value())
        else:
            self.preview_timer.stop()

    def _next_preview_frame(self) -> None:
        if self.animation is not None:
            self.preview_index = (self.preview_index + 1) % len(self.animation.frames)
            self._show_image_preview()

    def upload_image(self) -> None:
        if self.animation is None or self._wait_for_read(self.upload_image):
            return
        frames: list[QImage] = [frame.copy() for frame in self.animation.frames]
        crop = self.screen_preview.crop
        fill = self.fit_mode.currentData()
        delay = self.frame_delay.value() if len(frames) > 1 else 0
        stop = self.upload_stop
        stop.clear()

        def upload(controller: Controller) -> bool:
            data = [
                rgb565(crop_frame(frame, crop) if fill else fit_frame(frame, False))
                for frame in frames
            ]
            try:
                controller.upload_display(
                    data,
                    delay,
                    progress=self.upload_progress.emit,
                    cancelled=stop.is_set,
                )
            except UploadCancelled:
                return False
            return True

        self.uploading = True
        self.upload_bar.setRange(0, 0)
        self.upload_bar.setFormat("Preparing upload...")
        self.upload_bar.show()
        self.upload_button.hide()
        self.cancel_upload.setEnabled(True)
        self.cancel_upload.show()
        self._operation(upload, lambda _completed: None)
        if self.job is None:
            self._finished()

    def _upload_progressed(self, done: int, total: int) -> None:
        if not self.uploading:
            return
        self.upload_bar.setRange(0, total)
        self.upload_bar.setValue(done)
        self.upload_bar.setFormat(
            "Cancelling upload..." if self.upload_stop.is_set() else "Uploading... %p%"
        )

    def _cancel_upload(self) -> None:
        self.upload_stop.set()
        self.cancel_upload.setEnabled(False)
        self.upload_bar.setFormat("Cancelling upload...")

    def _choose_color(self) -> None:
        self.edit_revision += 1
        self.picking_color = True
        try:
            color = QColorDialog.getColor(self.color, self, "Choose color")
        finally:
            self.picking_color = False
        if color.isValid():
            self.color = color
            self._show_color()
            self.use_custom.setChecked(True)
            self._edited()

    def _has_color(self) -> bool:
        return self.effect.currentData() not in FIXED_COLOR_EFFECTS

    def _custom_selected(self) -> bool:
        return self.use_custom.isChecked() and self._has_color() and self.effect.currentData() != 0

    def _effect_changed(self) -> None:
        enabled = (
            self.loaded
            and self.effect.currentData() != 0
            and (self.job is None or self.background_read or self.background_write)
        )
        self.brightness.setEnabled(enabled)
        self.brightness_slider.setEnabled(enabled)
        self.speed.setEnabled(enabled and self.effect.currentData() != 1)
        self.speed_slider.setEnabled(enabled and self.effect.currentData() != 1)
        has_color = self._has_color()
        custom = self.use_custom.isChecked()
        self.color_button.setEnabled(enabled)
        self.use_custom.setEnabled(enabled)
        self.palette.setEnabled(enabled and not custom)
        self.color_heading.setVisible(has_color)
        self.use_custom.setVisible(has_color)
        self.palette.setVisible(has_color and not custom)
        self.color_button.setVisible(has_color and custom)
        self.color_source.setVisible(has_color and custom)
        self.custom_note.setVisible(has_color and custom)
        self._update_preview()

    def _show_color(self) -> None:
        self.color_button.setText(f"{self.color.name().upper()}    Choose color")
        self.color_button.setStyleSheet(f"QPushButton {{ border: 1px solid {self.color.name()}; }}")

    def _update_preview(self) -> None:
        effect = self.effect.currentData()
        self.preview_title.setText(self.effect.currentText())
        if not self._has_color():
            color_text = "Built-in colors"
        elif self.use_custom.isChecked():
            color_text = self.color.name().upper()
        else:
            color_text = self.palette.currentText()
        self.preview_subtitle.setText(
            "Lights off"
            if effect == 0
            else f"{color_text}  /  Brightness {self.brightness.value() * 5}%"
        )

    def _update_clock(self) -> None:
        now = datetime.now().astimezone()
        self.clock_preview.setText(now.strftime("%H:%M"))
        self.clock_date.setText(now.strftime("%A, %d %B %Y"))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.job is not None:
            if self.uploading:
                self._cancel_upload()
            event.ignore()
            return
        self.poll_timer.stop()
        self.closing = True
        self.volume_monitor.stop()
        self.clock_timer.stop()
        self.preview_timer.stop()
        self.settings.setValue("windowGeometry", self.saveGeometry())
        self._save_settings()
        super().closeEvent(event)
