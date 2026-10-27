import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from rks98.display import Crop, crop_frame, fit_frame, load_animation, rgb565
from rks98.protocol import (
    DISPLAY_FRAME_SIZE,
    ProtocolError,
    display_ack,
    display_chunk_packet,
    display_control_packet,
)


@pytest.fixture(scope="module", autouse=True)
def app():
    return QApplication.instance() or QApplication([])


def image(path, width, height, color):
    picture = QImage(width, height, QImage.Format.Format_RGB32)
    picture.fill(QColor(color))
    assert picture.save(str(path))
    return str(path)


def test_rgb565_is_big_endian_like_rk_web_app(tmp_path):
    frames = load_animation(image(tmp_path / "red.png", 640, 344, "#ff0000")).frames
    data = rgb565(frames[0])
    assert len(data) == DISPLAY_FRAME_SIZE
    assert data[:4] == bytes((0xF8, 0, 0xF8, 0))


def test_fit_inside_letterboxes_and_fill_covers(tmp_path):
    path = image(tmp_path / "tall.png", 100, 400, "#0084ff")
    fit = rgb565(load_animation(path, fill=False).frames[0])
    fill = rgb565(load_animation(path, fill=True).frames[0])
    assert fit[:2] == b"\0\0"
    assert fill[:2] == bytes((0x04, 0x3F))


def test_still_image_has_one_frame_and_no_delay(tmp_path):
    animation = load_animation(image(tmp_path / "a.jpg", 50, 50, "white"))
    assert len(animation.frames) == 1 and animation.delay_ms == 0


def gif(path, frame_count):
    # A generated 1x1 GIF: red/blue palette, 100 ms delay, one LZW pixel per frame.
    data = bytearray(b"GIF89a" + bytes.fromhex("01000100800000ff00000000ff"))
    for index in range(frame_count):
        data.extend(bytes.fromhex("21f904000a0000002c0000000001000100000202"))
        data.extend(bytes((0x44 if index % 2 == 0 else 0x4C, 1, 0)))
    data.append(0x3B)
    path.write_bytes(data)
    return str(path)


def test_gif_loads_frames(tmp_path):
    animation = load_animation(gif(tmp_path / "a.gif", 2))
    assert len(animation.frames) == 2 and animation.delay_ms == 100
    assert animation.frames[0].pixelColor(0, 0) == QColor("red")
    assert animation.frames[1].pixelColor(0, 0) == QColor("blue")


def test_long_gif_is_sampled_with_adjusted_delay(tmp_path):
    animation = load_animation(gif(tmp_path / "long.gif", 101))
    assert animation.source_frames == 101
    assert len(animation.frames) == 51 and animation.delay_ms == 200


def test_unreadable_file_is_reported(tmp_path):
    path = tmp_path / "bad.png"
    path.write_bytes(b"not an image")
    with pytest.raises(ProtocolError):
        load_animation(str(path))


def test_display_packets_match_official_layout():
    assert display_control_packet(2, 0, 0)[:13] == bytes(
        (9, 13, 0, 0, 1, 0, 5, 0, 0x80, 0, 0, 0, 0)
    )
    chunk = display_chunk_packet(3, 200, bytes((200, 100)))
    assert chunk[:10] == bytes((9, 12, 0, 3, 200, 44, 2, 0, 200, 100))
    with pytest.raises(ProtocolError):
        display_chunk_packet(0, 0, bytes(513))
    with pytest.raises(ProtocolError):
        display_control_packet(3, 1, 0)


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        (bytes.fromhex("090a060101"), True),
        (bytes.fromhex("090a060001"), False),
        (bytes.fromhex("010a060101"), None),
        (bytes.fromhex("090a050101"), None),
    ],
)
def test_display_ack(report, expected):
    assert display_ack(report) is expected


@pytest.mark.parametrize(("width", "height"), [(1, 1), (20, 800), (2000, 10), (320, 172)])
def test_crop_accepts_small_large_portrait_and_landscape_images(width, height):
    source = QImage(width, height, QImage.Format.Format_RGB32)
    source.fill(QColor("red"))
    for crop in (Crop(), Crop(8, 0, 0), Crop(8, 1, 1)):
        result = crop_frame(source, crop)
        assert result.size().width() == 320 and result.size().height() == 172
        assert rgb565(result) == bytes((0xF8, 0)) * (320 * 172)


def test_crop_selects_part_of_original_without_losing_offscreen_pixels(tmp_path):
    source = QImage(960, 172, QImage.Format.Format_RGB32)
    painter = QPainter(source)
    for x, color in enumerate(("red", "lime", "blue")):
        painter.fillRect(x * 320, 0, 320, 172, QColor(color))
    painter.end()
    path = str(tmp_path / "stripes.png")
    assert source.save(path)
    original = load_animation(path, fill=None).frames[0]
    assert original.size() == source.size()
    for x, color in ((0, "red"), (0.5, "lime"), (1, "blue")):
        result = crop_frame(original, Crop(1, x, 0.5))
        assert result.pixelColor(160, 86) == QColor(color)


def test_zoom_selects_small_detail_instead_of_entire_image():
    source = QImage(320, 172, QImage.Format.Format_RGB32)
    source.fill(QColor("blue"))
    painter = QPainter(source)
    painter.fillRect(140, 65, 40, 42, QColor("red"))
    painter.end()
    assert crop_frame(source, Crop()).pixelColor(0, 0) == QColor("blue")
    assert crop_frame(source, Crop(8)).pixelColor(0, 0) == QColor("red")


@pytest.mark.parametrize("crop", [Crop(0), Crop(9), Crop(1, -1), Crop(1, 0, 2), Crop(float("nan"))])
def test_invalid_crops_are_rejected(crop):
    source = QImage(320, 172, QImage.Format.Format_RGB32)
    with pytest.raises(ProtocolError, match="Crop requires"):
        crop_frame(source, crop)


def test_empty_images_are_rejected():
    with pytest.raises(ProtocolError, match="empty image"):
        crop_frame(QImage(), Crop())
    with pytest.raises(ProtocolError, match="empty image"):
        fit_frame(QImage(), True)
