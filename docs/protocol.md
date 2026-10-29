# RK-S98 wired protocol notes

This is a record of observed behavior, not a complete specification. Commands
were investigated using the [official RK web app](https://drive.rkgaming.com/)
(bundle `index-CTQPyNpU.js`) and tested on a `258a:01af` keyboard with firmware
`2117`. No official JavaScript is distributed.

## Transport and discovery

The app uses the Linux `hidraw` binding supplied by the `hidapi` package.
It checks vendor-defined interfaces and requires feature report **9** with a
**519-byte payload**, or 520 bytes including the report ID. The VID/PID may
be shared with other firmware; matching the descriptor is not a guarantee of
behavioral compatibility.

Feature reports contain an ID, a seven-byte header, and up to 512 data bytes.
Profile responses may be 136 bytes rather than padded to 520. Successful
responses require matching headers and a complete declared payload.

## Lighting and colors

| Command | Purpose | Payload length |
| --- | --- | --- |
| `84` | Read profile | 128 |
| `04` | Write profile | 128 |
| `8a` | Read effect color table | 420 |
| `0a` | Write effect color table | 420 |

The header addresses one of three profile slots (wire indices 0-2).
The desktop app always uses wire index 0 and exposes no profile selector.
Profile byte 10 selects the effect; per-effect parameters start at byte 56.
The low nibble of each effect parameter byte is a palette index, not a
multicolor boolean: Red=0, Green=1, Blue=2, Yellow=3, Pink=4, Cyan=5,
White=6, Multicolor=7. Unknown valid indices are preserved. Brightness 20 is
present in factory settings, despite the inspected web slider exposing 0-19.
The app accepts 0-20. Rainbow, Windmill, and Waterfall retain their stored
color byte because they use built-in colors.

RGB triples are spaced 21 bytes apart in the color table. Static's first
triple is at offset 21. Custom-color traffic from the official color wheel
confirmed that a full `0a` table write is followed by a profile write.

### Rejected color-table reads

The development firmware rejects `8a` through both HIDAPI and the official
page's WebHID. A rejection looks like `09 0a 06 00 ... 00 00`; the profile/count
may not be echoed, and stale trailing bytes can occur (including a captured
101-byte response). Error classification therefore checks command, status,
and zero payload length, not just transfer size. Other malformed responses stop
the operation; no alternate commands are guessed.

When table readback is unavailable, custom RGB uses the last successfully sent
420-byte table from QSettings. With no history, it initializes seven standard
palette colors per effect. It replaces the selected effect's first triple,
sets palette index 0, writes the full table, and verifies the profile.
This can overwrite unknown colors from other software.

History updates only after successful transfer and profile verification.
Built-in palette changes do not write the table. Read/refresh operations never
send local colors. History is per local profile, not per physical keyboard.
Palette index 0 cannot distinguish Red from custom RGB; outside changes can
make the saved custom-mode preference stale. No safe partial-table update or
alternative read has been verified.

## Firmware version

The web app's wired `getPassword` query sends:

```text
09 82 01 00 01 00 0a 00
```

Response bytes 16-17 are formatted as hex digits: `21 17` displays as `2117`.
Version-query failure is non-fatal. The cache is cleared on disconnection.
This query does not flash firmware.

## Clock

The app's `0b` clock command has an 11-byte payload: three zero bytes,
little-endian year, month, day, hour, minute, second, and Sunday-based weekday.
This follows the
[community clock documentation](https://github.com/nai-tro/rk-s98-mac-time-sync),
adapted to descriptor-matched report 9 rather than that project's report ID.
No automatic clock readback has been verified.

### Volume feedback

Command `0b` also supports a 14-byte screen-status payload containing volume
and local date/time. For report ID 9 and profile 0:

```text
09 0b 00 00 01 00 0e 00
VV 00 00 YL YH MM DD hh mm ss ww 00 00 00
```

The remainder of the 520-byte report is zero padding.

| Payload offset | Field |
| --- | --- |
| 0 | Integer volume percentage, 0-100 (`25` hex means 37%) |
| 1-2 | CPU usage and memory load can be sent, but are not used by the RK-S98 |
| 3-4 | Little-endian year |
| 5-9 | Month, day, hour, minute, second |
| 10 | Sunday-based weekday (0-6) |
| 11-13 | Zero |

Volume feedback includes current local time. No volume-only short packet,
"preserve clock" sentinel, separate mute encoding, or status readback has been
verified.

On firmware `2117`, a single packet with volume 65% visibly set the keyboard
indicator to 65%. Profile 0 readback remained unchanged and desktop volume
remained 65%. Other volume values, the clock's physical result for this form,
mute behavior, and persistence have not been tested.

The legacy 11-byte clock packet sends zero in the volume field; whether that
resets the displayed volume has not been tested. Manual clock sync uses the
14-byte form when desktop volume is available. Automatic volume
feedback is enabled by default and can be disabled in Settings. While enabled
and the app is open, connection triggers an initial volume/time
write, and PulseAudio/PipeWire events trigger subsequent updates. Lighting
discovery and refresh remain read-only; the separate volume synchronization
operation writes status, not lighting configuration. Updates wait for other
USB operations and coalesce to the latest volume. Transfer failures are
reported without automatic retries.

An optional, default-off user service can continue volume/time synchronization
after the app closes and at graphical login. A process lock pauses service
USB operations for the entire foreground app session, preventing overlapping
app/service transfers. The service requests fresh audio state after handoff or
keyboard reconnection. Its five-second discovery/ownership check does not poll
volume; volume changes still come from OS audio events. Transfer errors stop
the service without retrying the write or automatically restarting.

## Display images

Frames are 320 x 172, big-endian RGB565: 110,080 bytes, or 215 chunks of 512.
Up to 100 frames are supported.

1. Send `0d` with header `09 0d 00 00 01 00 05 00`.
   Byte 8 is mode shifted left six bits; byte 9 is frame count;
   bytes 11-12 hold the little-endian delay. Mode 1 starts the transfer.
   The wire delay is requested delay minus 25 ms when the request is at least 25 ms.
2. Wait for the start acknowledgement.
3. Send `0c` chunks with header
   `[9, 0x0c, 0, frame_index, chunk_index, sum(data) & 0xff, length_low, length_high]`.
   Each chunk must be acknowledged before advancing.
4. Send `0d` mode 0 to finish. Mode 2 aborts.

Input acknowledgements observed on hardware start with `09 0a 06 01` followed
by a counter. Status other than 1 is a NAK; the current packet may be resent
up to three times. A missing acknowledgement times out after three seconds.
Timeouts are not retried. Cancellation and transfer errors attempt an abort;
an abort does not restore the previous image.

The app sends a length of 512 for the final full chunk. The inspected web tool
used `frame_size % 512` there, which yields zero; the app's complete-length
transfer was acknowledged by the development keyboard.

HIDAPI `read(..., timeout_ms=0)` blocks indefinitely; input polling must use
a positive timeout. Upload speed measured around 13 seconds per frame.

## Hardware evidence

- All three profile slots were read successfully.
- Brightness was reduced by one step, verified, and restored.
- All 17 effects plus Off were written and verified; visible effect changes
  were user-confirmed. Original profile bytes were restored.
- Static Red, Green, and Blue were identified and user-confirmed.
- Static custom `RGB(0,132,255)` was visibly confirmed; saved local history
  restored the app's selected color afterward. This is not hardware readback.
- Clock sync visibly updated local date/time.
- A single 14-byte `0b` volume/time packet set the volume indicator to 65%,
  user-confirmed on firmware `2117`; profile 0 readback remained unchanged.
- Firmware `2117` matched RK's web tool.
- A still-image transfer completed with all 215 chunks acknowledged.
  GIF playback has not yet been physically verified.

Writes to other profile slots, speed settings, custom RGB on every effect, and
other firmware revisions have not all been individually hardware-tested.
Profile verification cannot guarantee that a partly failed write was harmless.
There is no automatic rollback.
