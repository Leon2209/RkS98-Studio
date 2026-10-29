"""RK-S98 wired packet encoding, independent of USB and Qt."""

import struct
from dataclasses import dataclass
from datetime import datetime

REPORT_ID = 9
REPORT_SIZE = 520
PROFILE_SIZE = 128
COLORS_SIZE = 420


def default_color_table() -> bytes:
    """Initialize unknown effect entries with seven standard RGB palette colors."""
    palette = bytes(
        (
            255,
            0,
            0,
            0,
            255,
            0,
            0,
            0,
            255,
            255,
            255,
            0,
            255,
            0,
            255,
            0,
            255,
            255,
            255,
            255,
            255,
        )
    )
    return palette * 20


PALETTE = {
    0: "Red",
    1: "Green",
    2: "Blue",
    3: "Yellow",
    4: "Pink",
    5: "Cyan",
    6: "White",
    7: "Multicolor / rainbow",
}

EFFECTS = {
    0: "Off",
    1: "Static",
    2: "Breathing",
    3: "Rainbow",
    4: "Flash away",
    5: "Raindrops",
    6: "Rainbow wheel",
    7: "Ripple",
    8: "Twinkling stars",
    9: "Fading trail",
    10: "Snake",
    11: "Neon stream",
    12: "Reactive",
    13: "Sine wave",
    14: "Side scan",
    15: "Windmill",
    16: "Waterfall",
    17: "Blossoming",
}


# Rainbow, Windmill and Waterfall always use their built-in colors.
FIXED_COLOR_EFFECTS = frozenset({3, 15, 16})


class ProtocolError(ValueError):
    """The device response or requested configuration is not supported."""


class CommandRejected(ProtocolError):
    def __init__(self, command: int, status: int):
        self.command = command
        self.status = status
        super().__init__(f"Firmware rejected command 0x{command:02x} (status 0x{status:02x}).")


def packet(command: int, length: int, board: int = 0, payload: bytes = b"") -> bytes:
    if command not in (0x84, 0x04, 0x8A, 0x0A, 0x0B):
        raise ProtocolError("Unsupported command.")
    if board not in (0, 1, 2):
        raise ProtocolError("Profile must be 1, 2, or 3.")
    if not 0 <= length <= 512 or (payload and len(payload) != length):
        raise ProtocolError("Invalid packet payload length.")
    if command < 0x80 and len(payload) != length:
        raise ProtocolError("A write command requires a complete payload.")
    data = bytearray(REPORT_SIZE)
    data[:8] = struct.pack("<BBBBBBH", REPORT_ID, command, 0, board, 1, 0, length)
    data[8 : 8 + len(payload)] = payload
    return bytes(data)


def response_payload(data: bytes, command: int, length: int, board: int) -> bytes:
    if (
        8 <= len(data) <= REPORT_SIZE
        and data[0] == REPORT_ID
        and data[1] == command & 0x7F
        and data[2] != 0
        and data[6:8] == b"\x00\x00"
    ):
        # Error replies can include stale trailing bytes and do not echo the profile.
        raise CommandRejected(command, data[2])
    if not 8 + length <= len(data) <= REPORT_SIZE:
        raise ProtocolError(
            f"Command 0x{command:02x}: expected {8 + length}-{REPORT_SIZE} response "
            f"bytes; received {len(data)}. Header: {data[:8].hex(' ') or '(empty)'}. "
            "Disconnect other keyboard configurators before retrying."
        )
    if data[0] != REPORT_ID or data[1] != command:
        raise ProtocolError(
            f"Unexpected response to command 0x{command:02x}: {data[:8].hex(' ')}. "
            "Disconnect other keyboard configurators before retrying."
        )
    if data[2] != 0 or data[3] != board or data[4:6] != b"\x01\x00":
        raise ProtocolError("Unexpected profile or multi-packet response.")
    if struct.unpack_from("<H", data, 6)[0] != length:
        raise ProtocolError("Unexpected payload length in response.")
    return data[8 : 8 + length]


VERSION_COMMAND = 0x82
VERSION_LENGTH = 10


def version_packet() -> bytes:
    """Read-only info query used by RK's web app; byte 2 is a request flag, not a profile."""
    data = bytearray(REPORT_SIZE)
    data[:8] = struct.pack("<BBBBBBH", REPORT_ID, VERSION_COMMAND, 1, 0, 1, 0, VERSION_LENGTH)
    return bytes(data)


def decode_version(data: bytes) -> str:
    """Firmware version bytes are shown as hex digits, e.g. 21 17 -> "2117"."""
    if (
        len(data) < 8 + VERSION_LENGTH
        or data[0] != REPORT_ID
        or data[1] != VERSION_COMMAND
        or data[4:6] != b"\x01\x00"
        or struct.unpack_from("<H", data, 6)[0] != VERSION_LENGTH
    ):
        raise ProtocolError("Unexpected firmware version response.")
    high, low = data[16], data[17]
    if (high, low) in ((0, 0), (0xFF, 0xFF)):
        raise ProtocolError("Keyboard did not report a firmware version.")
    return f"{high:02X}{low:02X}"


@dataclass(frozen=True)
class Lighting:
    effect: int
    brightness: int
    speed: int
    color: tuple[int, int, int] | None
    palette_index: int = 0

    def validate(self) -> None:
        if self.effect not in EFFECTS:
            raise ProtocolError("This lighting effect is not supported.")
        if not 0 <= self.brightness <= 20 or not 0 <= self.speed <= 4:
            raise ProtocolError("Brightness must be 0-20 and speed must be 0-4.")
        if not 0 <= self.palette_index <= 15:
            raise ProtocolError("Palette index must be 0-15.")
        if self.color is not None and (
            len(self.color) != 3 or any(not 0 <= c <= 255 for c in self.color)
        ):
            raise ProtocolError("Color requires three values between 0 and 255.")


def decode_lighting(profile: bytes, colors: bytes | None) -> Lighting:
    if len(profile) != PROFILE_SIZE or (colors is not None and len(colors) != COLORS_SIZE):
        raise ProtocolError("Incomplete lighting configuration.")
    effect = profile[10]
    if effect not in EFFECTS:
        raise ProtocolError(f"Effect {effect} is not editable in this version.")
    if profile[5] not in (0, 1):
        raise ProtocolError(f"Unknown lighting parameter format: {profile[5]}.")
    if effect == 0:
        # OFF has no lighting parameters; firmware may store FF sentinels here.
        return Lighting(0, 0, 0, (0, 0, 0) if colors is not None else None)
    if profile[5] == 0:
        offset = 56 + effect * 2
        brightness, packed = profile[offset : offset + 2]
        speed, color_index = packed >> 4, packed & 15
    elif profile[5] == 1:
        brightness, speed, color_index = profile[6:9]
    offset = effect * 21
    result = Lighting(
        effect,
        brightness,
        speed,
        (colors[offset], colors[offset + 1], colors[offset + 2]) if colors is not None else None,
        color_index,
    )
    result.validate()
    return result


def update_lighting(
    profile: bytes,
    colors: bytes | None,
    lighting: Lighting,
) -> tuple[bytes, bytes | None]:
    decode_lighting(profile, colors)
    lighting.validate()
    if lighting.effect in FIXED_COLOR_EFFECTS and lighting.color is not None:
        raise ProtocolError(f"{EFFECTS[lighting.effect]} uses its own colors.")
    if colors is None and lighting.color is not None:
        raise ProtocolError(
            "Custom color is unavailable: the firmware rejected the color-table query."
        )
    updated_profile = bytearray(profile)
    updated_colors = bytearray(colors) if colors is not None else None
    updated_profile[9] = 0  # Select built-in effects instead of per-key lighting.
    updated_profile[10] = lighting.effect
    if lighting.effect == 0:
        return bytes(updated_profile), bytes(updated_colors) if updated_colors is not None else None
    color_index = lighting.palette_index
    if lighting.effect in FIXED_COLOR_EFFECTS:
        # Keep whatever color byte the keyboard already stores for these effects.
        color_index = profile[57 + lighting.effect * 2] & 15 if profile[5] == 0 else profile[8]
    if profile[5] == 0:
        offset = 56 + lighting.effect * 2
        updated_profile[offset : offset + 2] = bytes(
            (lighting.brightness, lighting.speed << 4 | color_index)
        )
    else:
        updated_profile[6:9] = bytes((lighting.brightness, lighting.speed, color_index))
    offset = lighting.effect * 21
    if updated_colors is not None and lighting.color is not None:
        updated_colors[offset : offset + 3] = bytes(lighting.color)
    return bytes(updated_profile), bytes(updated_colors) if updated_colors is not None else None


def clock_packet(now: datetime) -> bytes:
    # SYSTEMTIME uses a little-endian year and Sunday-based weekday.
    payload = b"\x00\x00\x00" + struct.pack(
        "<HBBBBBB",
        now.year,
        now.month,
        now.day,
        now.hour,
        now.minute,
        now.second,
        (now.weekday() + 1) % 7,
    )
    return packet(0x0B, len(payload), payload=payload)


def volume_packet(volume: int, now: datetime) -> bytes:
    if type(volume) is not int or not 0 <= volume <= 100:
        raise ProtocolError("Volume must be an integer percentage between 0 and 100.")
    payload = bytes((volume, 0, 0)) + clock_packet(now)[11:19] + bytes(3)
    return packet(0x0B, len(payload), payload=payload)


DISPLAY_WIDTH = 320
DISPLAY_HEIGHT = 172
DISPLAY_FRAME_SIZE = DISPLAY_WIDTH * DISPLAY_HEIGHT * 2
DISPLAY_MAX_FRAMES = 100
DISPLAY_CHUNK_SIZE = 512
DISPLAY_FINISH, DISPLAY_START, DISPLAY_ABORT = 0, 1, 2


def display_control_packet(mode: int, frame_count: int, delay_ms: int) -> bytes:
    """Command 0x0D starts, commits, or aborts an RGB565 display upload."""
    if mode not in (DISPLAY_FINISH, DISPLAY_START, DISPLAY_ABORT):
        raise ProtocolError("Unknown display upload mode.")
    if not 0 <= frame_count <= DISPLAY_MAX_FRAMES or not 0 <= delay_ms <= 0xFFFF:
        raise ProtocolError("Invalid display frame count or delay.")
    data = bytearray(REPORT_SIZE)
    data[:8] = struct.pack("<BBBBBBH", REPORT_ID, 0x0D, 0, 0, 1, 0, 5)
    data[8] = mode << 6
    data[9] = frame_count
    data[11:13] = struct.pack("<H", delay_ms)
    return bytes(data)


def display_chunk_packet(frame_index: int, chunk_index: int, chunk: bytes) -> bytes:
    """Command 0x0C carries up to 512 frame bytes with an 8-bit additive checksum."""
    if not 0 <= frame_index < DISPLAY_MAX_FRAMES or not 0 <= chunk_index <= 0xFF:
        raise ProtocolError("Invalid display frame or chunk index.")
    if not 0 < len(chunk) <= DISPLAY_CHUNK_SIZE:
        raise ProtocolError("Invalid display chunk length.")
    data = bytearray(REPORT_SIZE)
    data[:8] = struct.pack(
        "<BBBBBBH",
        REPORT_ID,
        0x0C,
        0,
        frame_index,
        chunk_index,
        sum(chunk) & 0xFF,
        len(chunk),
    )
    data[8 : 8 + len(chunk)] = chunk
    return bytes(data)


def display_ack(report: bytes) -> bool | None:
    """Return True/False for an upload acknowledgement, or None for unrelated input."""
    if len(report) < 4 or report[0] != REPORT_ID or report[2] != 6:
        return None
    return report[3] == 1
