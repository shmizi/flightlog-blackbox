"""File-level validation that runs before parsing (E1) and the file hash used for E4."""

import hashlib
import re
import struct

# Every DataFlash record starts with 0xA3 0x95; the first record is FMT (0x80).
HEADER = b"\xa3\x95\x80"
MAVLINK_MAGIC = (0xFE, 0xFD)                 # MAVLink v1, v2
YEAR_2000, YEAR_2100 = 946684800, 4102444800

DATAFLASH, TELEMETRY, RAW_LINK, PARAMS = "dataflash", "telemetry", "raw", "params"

# Mission Planner "NAME,VALUE" (or space/tab separated) and QGroundControl "sys comp NAME VALUE type".
PARAM_LINE = re.compile(r"^(?:\d+\s+\d+\s+)?[A-Z][A-Z0-9_]{1,16}[\s,]+[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?:\s+\d+)?\s*$")


def log_kind(path):
    """E1: what kind of log the bytes are, judged by content, not file extension.

    dataflash  .bin from the flight controller's SD card
    telemetry  .tlog: 8-byte big-endian receive time (us) before each MAVLink packet
    raw        .rlog: the same MAVLink bytes with no timestamps
    None       not a flight log
    """
    with open(path, "rb") as f:
        head = f.read(4096)
    if head[:3] == HEADER:
        return DATAFLASH
    if len(head) >= 9 and head[8] in MAVLINK_MAGIC:
        ts = struct.unpack(">Q", head[:8])[0] / 1e6
        if YEAR_2000 <= ts <= YEAR_2100:
            return TELEMETRY
    # An .rlog can open with serial noise or boot text ("Init Gyro**") before the first frame.
    if _has_mavlink_frames(head):
        return RAW_LINK
    if _is_param_file(head):
        return PARAMS
    return None


def _is_param_file(head):
    """A parameter list saved from Mission Planner or QGroundControl (plain text)."""
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        return False
    lines = text.splitlines()
    if len(head) >= 4096:          # only a full read can end in a line cut in half
        lines = lines[:-1]
    lines = [ln.strip() for ln in lines]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    return len(lines) >= 3 and all(PARAM_LINE.match(ln) for ln in lines[:20])


def _has_mavlink_frames(buf, chain=5):
    """True if some offset starts `chain` back-to-back MAVLink frames of one version.

    Five chained frames with valid v2 flags will not turn up by chance in a JPEG or
    other compressed file, which a shorter chain sometimes would.
    """
    for start in range(len(buf)):
        magic, i = buf[start], start
        if magic not in MAVLINK_MAGIC:
            continue
        for _ in range(chain):
            if i + 3 > len(buf) or buf[i] != magic:
                break
            n = buf[i + 1]
            if magic == 0xFE:
                i += 6 + n + 2
            else:
                incompat = buf[i + 2]
                if incompat > 1:            # only MAVLINK_IFLAG_SIGNED is defined
                    break
                i += 10 + n + 2 + (13 if incompat else 0)
        else:
            if i <= len(buf):
                return True
    return False


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
