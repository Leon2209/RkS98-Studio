"""Convert still images and animations into RK-S98 RGB565 display frames."""

import sys
from dataclasses import dataclass
from math import ceil, isfinite

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter

from .protocol import DISPLAY_HEIGHT, DISPLAY_MAX_FRAMES, DISPLAY_WIDTH, ProtocolError

DEFAULT_DELAY_MS = 100
FILE_FILTER = "Images and GIFs (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"


@dataclass(frozen=True)
class Animation:
    frames: list[QImage]
    delay_ms: int
    source_frames: int


@dataclass(frozen=True)
class Crop:
    zoom: float = 1.0
    x: float = 0.5
    y: float = 0.5


def crop_frame(image: QImage, crop: Crop) -> QImage:
    """Render a movable screen-shaped crop without scaling the entire source."""
    if image.isNull():
        raise ProtocolError("Can't crop an empty image.")
    if (
        not all(isfinite(value) for value in (crop.zoom, crop.x, crop.y))
        or not 1 <= crop.zoom <= 8
        or not 0 <= crop.x <= 1
        or not 0 <= crop.y <= 1
    ):
        raise ProtocolError("Crop requires zoom 1-8 and positions between 0 and 1.")
    scale = max(DISPLAY_WIDTH / image.width(), DISPLAY_HEIGHT / image.height()) * crop.zoom
    width, height = DISPLAY_WIDTH / scale, DISPLAY_HEIGHT / scale
    source = QRectF(
        (image.width() - width) * crop.x,
        (image.height() - height) * crop.y,
        width,
        height,
    )
    frame = QImage(DISPLAY_WIDTH, DISPLAY_HEIGHT, QImage.Format.Format_RGB32)
    frame.fill(QColor("black"))
    painter = QPainter(frame)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(QRectF(frame.rect()), image, source)
    painter.end()
    return frame.convertToFormat(QImage.Format.Format_RGB16)


def fit_frame(image: QImage, fill: bool) -> QImage:
    """Scale to 320x172: fill crops the center; otherwise letterbox on black."""
    if image.isNull():
        raise ProtocolError("Can't resize an empty image.")
    mode = (
        Qt.AspectRatioMode.KeepAspectRatioByExpanding
        if fill
        else Qt.AspectRatioMode.KeepAspectRatio
    )
    scaled = image.scaled(
        DISPLAY_WIDTH,
        DISPLAY_HEIGHT,
        mode,
        Qt.TransformationMode.SmoothTransformation,
    )
    frame = QImage(DISPLAY_WIDTH, DISPLAY_HEIGHT, QImage.Format.Format_RGB32)
    frame.fill(QColor("black"))
    painter = QPainter(frame)
    painter.drawImage(
        QRect(
            (DISPLAY_WIDTH - scaled.width()) // 2,
            (DISPLAY_HEIGHT - scaled.height()) // 2,
            scaled.width(),
            scaled.height(),
        ),
        scaled,
    )
    painter.end()
    return frame.convertToFormat(QImage.Format.Format_RGB16)


def load_animation(path: str, fill: bool | None = True) -> Animation:
    reader = QImageReader(path)
    reader.setDecideFormatFromContent(True)
    reader.setAutoTransform(True)
    images: list[QImage] = []
    delays: list[int] = []
    while True:
        image = reader.read()
        if image.isNull():
            break
        images.append(image)
        delays.append(reader.nextImageDelay())
        if not reader.supportsAnimation() or len(images) >= 1000:
            break
    if not images:
        raise ProtocolError(f"Can't open this image: {reader.errorString()}")
    source_frames = len(images)
    # Keep the playback length when a long animation is reduced to the firmware's limit.
    step = ceil(source_frames / DISPLAY_MAX_FRAMES)
    images = images[::step]
    positive = [delay for delay in delays if delay > 0]
    delay = round(sum(positive) / len(positive)) if positive else DEFAULT_DELAY_MS
    return Animation(
        images if fill is None else [fit_frame(image, fill) for image in images],
        min(0xFFFF, delay * step) if source_frames > 1 else 0,
        source_frames,
    )


def rgb565(frame: QImage) -> bytes:
    """Return big-endian RGB565 pixels, row by row, as RK's web app sends them."""
    if frame.format() != QImage.Format.Format_RGB16:
        frame = frame.convertToFormat(QImage.Format.Format_RGB16)
    if frame.width() != DISPLAY_WIDTH or frame.height() != DISPLAY_HEIGHT:
        raise ProtocolError("Display frames must be 320x172 pixels.")
    row = DISPLAY_WIDTH * 2
    native = b"".join(bytes(frame.constScanLine(y))[:row] for y in range(DISPLAY_HEIGHT))
    if sys.byteorder == "big":
        return native
    swapped = bytearray(len(native))
    swapped[0::2] = native[1::2]
    swapped[1::2] = native[0::2]
    return bytes(swapped)
