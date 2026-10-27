import struct
from datetime import datetime

import pytest

from rks98.protocol import (
    COLORS_SIZE,
    EFFECTS,
    FIXED_COLOR_EFFECTS,
    PROFILE_SIZE,
    Lighting,
    ProtocolError,
    clock_packet,
    decode_lighting,
    packet,
    response_payload,
    update_lighting,
)


def configuration(format_type=0):
    profile = bytearray((i * 13) % 256 for i in range(PROFILE_SIZE))
    profile[5] = format_type
    profile[10] = 1
    profile[58:60] = bytes((4, 0x20))
    profile[6:9] = bytes((4, 2, 0))
    colors = bytes(i % 256 for i in range(COLORS_SIZE))
    return bytes(profile), colors


def test_packet_exact_wire_layout():
    data = packet(0x84, 128, 2)
    assert len(data) == 520
    assert data[:8] == bytes((9, 0x84, 0, 2, 1, 0, 128, 0))
    assert data[8:] == bytes(512)
    data = packet(0x0A, 420, 1, bytes([42]) * 420)
    assert data[:8] == bytes((9, 0x0A, 0, 1, 1, 0, 164, 1))
    assert data[8:428] == bytes([42]) * 420
    assert data[428:] == bytes(92)


@pytest.mark.parametrize(
    "command,length,board,payload",
    [
        (0x11, 1, 0, b"\x00"),  # Factory reset must not be exposed.
        (0x84, 513, 0, b""),
        (0x84, 128, 3, b""),
        (0x04, 128, 0, b""),
        (0x04, 128, 0, b"\x00"),
    ],
)
def test_invalid_packets_rejected(command, length, board, payload):
    with pytest.raises(ProtocolError):
        packet(command, length, board, payload)


def test_response_validation():
    payload = bytes(range(128))
    data = bytearray(packet(0x84, 128, 2, payload))
    assert response_payload(data, 0x84, 128, 2) == payload
    for offset in (0, 1, 2, 3, 4, 5, 6, 7):
        corrupted = bytearray(data)
        corrupted[offset] ^= 0xFF
        with pytest.raises(ProtocolError):
            response_payload(corrupted, 0x84, 128, 2)
    assert response_payload(data[:136], 0x84, 128, 2) == payload
    for shortened in (data[:135], data[:8], b""):
        with pytest.raises(ProtocolError):
            response_payload(shortened, 0x84, 128, 2)


@pytest.mark.parametrize("format_type", [0, 1])
@pytest.mark.parametrize("effect", EFFECTS)
def test_lighting_roundtrip_preserves_unrelated_bytes(format_type, effect):
    profile, colors = configuration(format_type)
    fixed = effect in FIXED_COLOR_EFFECTS
    lighting = Lighting(effect, 3, 4, None if fixed else (1, 254, 128), 7)
    new_profile, new_colors = update_lighting(profile, colors, lighting)
    expected = lighting if effect else Lighting(0, 0, 0, (0, 0, 0))
    if fixed:
        index = profile[57 + effect * 2] & 15 if format_type == 0 else profile[8]
        expected = Lighting(effect, 3, 4, tuple(colors[effect * 21 : effect * 21 + 3]), index)
    assert decode_lighting(new_profile, new_colors) == expected
    allowed = {9, 10}
    if format_type == 0:
        allowed.update((56 + effect * 2, 57 + effect * 2))
    else:
        allowed.update((6, 7, 8))
    assert all(a == b for i, (a, b) in enumerate(zip(profile, new_profile)) if i not in allowed)
    assert all(
        a == b
        for i, (a, b) in enumerate(zip(colors, new_colors))
        if i not in range(effect * 21, effect * 21 + 3)
    )
    assert len(new_profile) == 128 and len(new_colors) == 420


@pytest.mark.parametrize(
    "lighting",
    [
        Lighting(18, 2, 2, (0, 0, 0)),
        Lighting(1, -1, 2, (0, 0, 0)),
        Lighting(1, 21, 2, (0, 0, 0)),
        Lighting(1, 2, 5, (0, 0, 0)),
        Lighting(1, 2, 2, (256, 0, 0)),
        Lighting(1, 2, 2, None, -1),
        Lighting(1, 2, 2, None, 16),
    ],
)
def test_invalid_lighting_rejected(lighting):
    with pytest.raises(ProtocolError):
        update_lighting(*configuration(), lighting)


def test_unknown_format_and_incomplete_state_rejected():
    with pytest.raises(ProtocolError):
        decode_lighting(*configuration(2))
    profile, colors = configuration()
    with pytest.raises(ProtocolError):
        update_lighting(profile[:-1], colors, Lighting(1, 2, 2, (0, 0, 0)))


def test_off_sentinels_are_not_treated_as_lighting_parameters():
    profile, colors = configuration()
    profile = bytearray(profile)
    profile[10] = 0
    profile[56:58] = b"\xff\xff"
    assert decode_lighting(profile, colors) == Lighting(0, 0, 0, (0, 0, 0))
    desired = Lighting(1, 19, 4, (255, 0, 0))
    new_profile, new_colors = update_lighting(profile, colors, desired)
    assert decode_lighting(new_profile, new_colors) == desired


def test_full_brightness_range_includes_factory_value_twenty():
    profile, colors = configuration()
    for brightness in range(21):
        desired = Lighting(1, brightness, 4, (255, 0, 0))
        assert decode_lighting(*update_lighting(profile, colors, desired)) == desired


def test_missing_color_table_is_explicit_and_never_fabricated():
    profile, _ = configuration()
    current = decode_lighting(profile, None)
    assert current.color is None
    desired = Lighting(2, 19, 0, None, 7)
    new_profile, new_colors = update_lighting(profile, None, desired)
    assert new_colors is None
    assert decode_lighting(new_profile, new_colors) == desired
    with pytest.raises(ProtocolError, match="Custom color is unavailable"):
        update_lighting(profile, None, Lighting(1, 19, 0, (0, 0, 0)))


@pytest.mark.parametrize("format_type", [0, 1])
@pytest.mark.parametrize("palette_index", range(16))
def test_palette_indices_are_preserved_exactly_without_rgb_table(format_type, palette_index):
    profile, _ = configuration(format_type)
    desired = Lighting(1, 20, 1, None, palette_index)
    updated_profile, updated_colors = update_lighting(profile, None, desired)
    assert updated_colors is None
    assert decode_lighting(updated_profile, None) == desired
    if format_type == 0:
        assert updated_profile[59] == 0x10 | palette_index
    else:
        assert updated_profile[8] == palette_index
    assert update_lighting(updated_profile, None, desired) == (updated_profile, None)


@pytest.mark.parametrize(
    "name,packed,index",
    [
        ("red", 0x10, 0),
        ("green", 0x11, 1),
        ("blue", 0x12, 2),
    ],
)
def test_display_selected_static_color_captures(name, packed, index):
    from rks98.protocol import PALETTE

    profile, _ = configuration()
    profile = bytearray(profile)
    profile[9] = 0
    profile[58:60] = bytes((20, packed))
    result = decode_lighting(profile, None)
    assert result == Lighting(1, 20, 1, None, index)
    assert PALETTE[index].lower() == name
    assert update_lighting(profile, None, result) == (bytes(profile), None)


def test_user_confirmed_palette_order():
    from rks98.protocol import PALETTE

    assert list(PALETTE.values()) == [
        "Red",
        "Green",
        "Blue",
        "Yellow",
        "Pink",
        "Cyan",
        "White",
        "Multicolor / rainbow",
    ]


def test_firmware_rejection_is_distinct_from_malformed_response():
    from rks98.protocol import CommandRejected

    with pytest.raises(CommandRejected) as caught:
        response_payload(bytes.fromhex("09 0a 06 00 01 00 00 00"), 0x8A, 420, 0)
    assert caught.value.command == 0x8A and caught.value.status == 6
    with pytest.raises(CommandRejected):
        response_payload(bytes.fromhex("09 0a 06 00 02 00 00 00"), 0x8A, 420, 2)
    with pytest.raises(ProtocolError):
        response_payload(bytes.fromhex("09 0a 06 00 02 00 01 00"), 0x8A, 420, 2)


@pytest.mark.parametrize("size", [8, 101, 136, 428, 520])
def test_rejection_uses_header_not_stale_trailing_bytes(size):
    from rks98.protocol import CommandRejected

    data = bytes.fromhex("09 0a 06 00 01 00 00 00") + bytes([0xA5]) * (size - 8)
    with pytest.raises(CommandRejected) as caught:
        response_payload(data, 0x8A, 420, 0)
    assert caught.value.command == 0x8A and caught.value.status == 6


@pytest.mark.parametrize(
    "header",
    [
        "08 0a 06 00 01 00 00 00",
        "09 04 06 00 01 00 00 00",
        "09 0a 00 00 01 00 00 00",
        "09 0a 06 00 01 00 01 00",
    ],
)
def test_other_short_responses_are_not_misclassified_as_color_rejection(header):
    from rks98.protocol import CommandRejected

    with pytest.raises(ProtocolError) as caught:
        response_payload(bytes.fromhex(header) + bytes(93), 0x8A, 420, 0)
    assert not isinstance(caught.value, CommandRejected)


@pytest.mark.parametrize(
    "date,weekday",
    [
        (datetime(2026, 10, 25, 23, 59, 58), 0),
        (datetime(2026, 10, 26, 0, 0, 0), 1),
        (datetime(2028, 2, 29, 12, 34, 56), 2),
    ],
)
def test_clock_exact_payload_and_sunday_based_weekday(date, weekday):
    data = clock_packet(date)
    assert len(data) == 520
    assert data[:11] == bytes((9, 11, 0, 0, 1, 0, 11, 0, 0, 0, 0))
    assert data[11:19] == struct.pack(
        "<HBBBBBB",
        date.year,
        date.month,
        date.day,
        date.hour,
        date.minute,
        date.second,
        weekday,
    )
    assert data[19:] == bytes(501)


def version_response(high=0x21, low=0x17):
    data = bytearray(520)
    data[:18] = bytes((9, 0x82, 1, 0, 1, 0, 10, 0, 6, 0, 0, 0, 0, 0x23, 0, 0, high, low))
    return bytes(data)


def test_version_query_matches_official_read_only_request():
    from rks98.protocol import version_packet

    data = version_packet()
    assert len(data) == 520
    assert data[:8] == bytes((9, 0x82, 1, 0, 1, 0, 10, 0))
    assert not any(data[8:])


def test_version_decodes_bytes_as_hex_digits():
    from rks98.protocol import decode_version

    assert decode_version(version_response()) == "2117"
    assert decode_version(version_response(0x1A, 0x05)) == "1A05"


@pytest.mark.parametrize(
    "data",
    [
        b"",
        version_response()[:17],
        bytes((9, 0x84)) + version_response()[2:],
        version_response(0, 0),
        version_response(0xFF, 0xFF),
    ],
)
def test_invalid_version_responses_are_rejected(data):
    from rks98.protocol import decode_version

    with pytest.raises(ProtocolError):
        decode_version(data)


@pytest.mark.parametrize("effect", sorted(FIXED_COLOR_EFFECTS))
def test_fixed_color_effects_reject_custom_rgb(effect):
    profile, colors = configuration()
    with pytest.raises(ProtocolError, match="own colors"):
        update_lighting(profile, colors, Lighting(effect, 3, 4, (1, 2, 3), 0))
