"""Read-modify-write keyboard operations with explicit verification."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from .protocol import (
    COLORS_SIZE,
    DISPLAY_ABORT,
    DISPLAY_CHUNK_SIZE,
    DISPLAY_FINISH,
    DISPLAY_FRAME_SIZE,
    DISPLAY_MAX_FRAMES,
    DISPLAY_START,
    EFFECTS,
    FIXED_COLOR_EFFECTS,
    PROFILE_SIZE,
    CommandRejected,
    Lighting,
    ProtocolError,
    clock_packet,
    decode_lighting,
    decode_version,
    default_color_table,
    display_ack,
    display_chunk_packet,
    display_control_packet,
    packet,
    response_payload,
    update_lighting,
    version_packet,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CustomColorResult:
    lighting: Lighting
    colors: bytes
    color_verified: bool


class UploadCancelled(Exception):
    """The user cancelled a display upload."""


ACK_TIMEOUT_S = 3.0
ACK_RETRIES = 3


class Transport(Protocol):
    def send(self, data: bytes) -> None: ...
    def exchange(self, data: bytes) -> bytes: ...
    def read_input(self, timeout_ms: int) -> bytes: ...


class Controller:
    def __init__(self, transport: Transport):
        self.transport = transport

    def _read(self, command: int, length: int, board: int) -> bytes:
        data = self.transport.exchange(packet(command, length, board))
        return response_payload(data, command, length, board)

    def _configuration(self, board: int) -> tuple[bytes, bytes | None]:
        profile = self._read(0x84, PROFILE_SIZE, board)
        try:
            colors = self._read(0x8A, COLORS_SIZE, board)
        except CommandRejected as error:
            if error.command != 0x8A or error.status != 6:
                raise
            logger.debug(
                "%s RGB table is unreadable. Custom RGB requires write-only mode "
                "using locally saved effect colors.",
                error,
            )
            colors = None
        return profile, colors

    def read_firmware_version(self) -> str:
        return decode_version(self.transport.exchange(version_packet()))

    def read_lighting(self, board: int) -> Lighting:
        return decode_lighting(*self._configuration(board))

    def apply_lighting(self, board: int, lighting: Lighting) -> Lighting:
        lighting.validate()
        original_profile, original_colors = self._configuration(board)
        profile, colors = update_lighting(original_profile, original_colors, lighting)
        # Verify the color table before changing the active effect/profile.
        if colors is not None and colors != original_colors:
            self.transport.send(packet(0x0A, COLORS_SIZE, board, colors))
            time.sleep(0.1)
            if self._read(0x8A, COLORS_SIZE, board) != colors:
                raise ProtocolError(
                    "Color-table readback differs after writing. Stopped before updating "
                    "the profile; the keyboard may have partially applied the change."
                )
        if profile != original_profile:
            self.transport.send(packet(0x04, PROFILE_SIZE, board, profile))
            time.sleep(0.1)
        actual_profile, actual_colors = self._configuration(board)
        if actual_profile != profile or actual_colors != colors:
            raise ProtocolError(
                "Settings readback differs after writing. The keyboard may have "
                "partially applied the change; read its settings before trying again."
            )
        return decode_lighting(actual_profile, actual_colors)

    def sync_clock(self) -> str:
        now = datetime.now().astimezone()
        self.transport.send(clock_packet(now))
        return (
            f"Clock packet sent: {now:%Y-%m-%d %H:%M:%S %Z}. "
            "This command has no verified readback; check the keyboard display."
        )

    def apply_custom_color(
        self,
        board: int,
        lighting: Lighting,
        saved_colors: bytes | None = None,
    ) -> CustomColorResult:
        lighting.validate()
        if lighting.color is None or lighting.effect == 0:
            raise ProtocolError("Custom RGB requires a color and a non-Off effect.")
        if lighting.effect in FIXED_COLOR_EFFECTS:
            raise ProtocolError(f"{EFFECTS[lighting.effect]} uses its own colors.")
        if saved_colors is not None and len(saved_colors) != COLORS_SIZE:
            raise ProtocolError("Saved RGB table is incomplete; refusing to overwrite it.")
        original_profile, device_colors = self._configuration(board)
        colors = device_colors if device_colors is not None else saved_colors
        if colors is None:
            colors = default_color_table()
        desired = replace(lighting, palette_index=0)
        profile, updated_colors = update_lighting(original_profile, colors, desired)
        if updated_colors is None:
            raise ProtocolError("Custom RGB table was not generated.")
        # Always send in write-only mode: local history is not hardware readback.
        self.transport.send(packet(0x0A, COLORS_SIZE, board, updated_colors))
        time.sleep(0.2)
        if device_colors is not None:
            if self._read(0x8A, COLORS_SIZE, board) != updated_colors:
                raise ProtocolError(
                    "Custom RGB readback differs after writing; colors may be partially applied."
                )
        self.transport.send(packet(0x04, PROFILE_SIZE, board, profile))
        time.sleep(0.2)
        if self._read(0x84, PROFILE_SIZE, board) != profile:
            raise ProtocolError(
                "Profile verification failed after custom RGB transfer. Colors may have "
                "changed; the previous local history was not updated."
            )
        return CustomColorResult(desired, updated_colors, device_colors is not None)

    def _wait_for_ack(self, timeout_s: float) -> bool | None:
        deadline = time.monotonic() + timeout_s
        while (remaining := deadline - time.monotonic()) > 0:
            ack = display_ack(self.transport.read_input(max(1, int(remaining * 1000))))
            if ack is not None:
                return ack
        return None

    def _send_acknowledged(self, data: bytes) -> None:
        for _attempt in range(1 + ACK_RETRIES):
            self.transport.send(data)
            ack = self._wait_for_ack(ACK_TIMEOUT_S)
            if ack:
                return
            if ack is None:
                raise ProtocolError("The keyboard stopped responding during the image upload.")
        raise ProtocolError("The keyboard rejected part of the image upload.")

    def upload_display(
        self,
        frames: list[bytes],
        delay_ms: int,
        progress: Callable[[int, int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> int:
        """Upload RGB565 frames using the official ACK-paced 0x0D/0x0C sequence."""
        if not 1 <= len(frames) <= DISPLAY_MAX_FRAMES:
            raise ProtocolError(f"Choose between 1 and {DISPLAY_MAX_FRAMES} frames.")
        if any(len(frame) != DISPLAY_FRAME_SIZE for frame in frames):
            raise ProtocolError("Every frame must be a 320x172 RGB565 image.")
        if not 0 <= delay_ms <= 0xFFFF:
            raise ProtocolError("Frame delay must be 0-65535 ms.")
        if cancelled is not None and cancelled():
            raise UploadCancelled()
        # RK's tool subtracts the firmware's per-frame overhead from the delay.
        wire_delay = delay_ms - 25 if delay_ms >= 25 else delay_ms
        for _ in range(64):
            if not self.transport.read_input(0):
                break
        chunks = -(-DISPLAY_FRAME_SIZE // DISPLAY_CHUNK_SIZE)
        total = len(frames) * chunks
        done = 0
        try:
            self._send_acknowledged(display_control_packet(DISPLAY_START, len(frames), wire_delay))
            for frame_index, frame in enumerate(frames):
                for chunk_index in range(chunks):
                    if cancelled is not None and cancelled():
                        raise UploadCancelled()
                    start = chunk_index * DISPLAY_CHUNK_SIZE
                    self._send_acknowledged(
                        display_chunk_packet(
                            frame_index,
                            chunk_index,
                            frame[start : start + DISPLAY_CHUNK_SIZE],
                        )
                    )
                    done += 1
                    if progress is not None:
                        progress(done, total)
            if cancelled is not None and cancelled():
                raise UploadCancelled()
            self.transport.send(display_control_packet(DISPLAY_FINISH, len(frames), wire_delay))
        except BaseException:
            try:
                self.transport.send(display_control_packet(DISPLAY_ABORT, 0, 0))
            except Exception:
                logger.warning("Could not send display upload abort", exc_info=True)
            raise
        self._wait_for_ack(0.5)
        return len(frames)
