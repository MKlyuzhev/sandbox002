"""MT4 .hst history files (format 401) and OANDA → MT4 server time.

OANDA's MT4 server runs on America/New_York + 7h, so 00:00 is the 17:00 NY
daily close. MT4 stamps W1 bars on Sunday 00:00 server time.
"""

from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
HEADER = struct.Struct("<i64s12siiii52s")
RECORD = struct.Struct("<qddddqiq")  # time, open, high, low, close, tick vol, spread, real vol
W1_STAMP_SHIFT = 86400  # OANDA week opens Sat 00:00 server time; MT4 W1 bars are stamped Sunday


def broker_ts(utc_rfc3339: str) -> int:
    utc = datetime.strptime(utc_rfc3339[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return broker_ts_from_utc(utc)


def broker_ts_from_utc(utc: datetime) -> int:
    local = utc.astimezone(NY).replace(tzinfo=None) + timedelta(hours=7)
    return int(local.replace(tzinfo=timezone.utc).timestamp())


def read_hst(path: Path) -> tuple[int, dict[int, tuple]]:
    """Digits and {time: record} from a v401 file (empty if missing)."""
    if not path.exists():
        return 0, {}
    data = path.read_bytes()
    version, _c, _s, _p, digits, *_ = HEADER.unpack_from(data, 0)
    if version != 401:
        raise ValueError(f"{path.name}: unsupported .hst version {version}")
    bars = {}
    for off in range(HEADER.size, len(data) - RECORD.size + 1, RECORD.size):
        rec = RECORD.unpack_from(data, off)
        bars[rec[0]] = rec
    return digits, bars
