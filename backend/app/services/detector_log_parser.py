from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

DATE_PATTERNS = [
    "%A, %d %B %Y",
    "%d %B %Y",
    "%Y-%m-%d",
]

APP_DET_RE = re.compile(r"Approach\s*(\d+)\s*,\s*Detector:\s*(\d+)", re.IGNORECASE)
HOUR_HEADER_RE = re.compile(r"(?:^|\s)(\d{2})\s*:")
ROW_RE = re.compile(r"^\s*:(\d{1,2})\s+(.+)$")


def _parse_date(line: str) -> datetime | None:
    cleaned = line.strip()
    for fmt in DATE_PATTERNS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def parse_detector_log(path: str | Path, default_intersection_code: str = "806") -> List[Dict]:
    """Parse SCATS-style 15-minute detector count text logs.

    The ToR sample arranges hours horizontally and interval rows vertically.
    This parser handles one or more Approach/Detector blocks per file.
    """
    path = Path(path)
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    current_date: datetime | None = None
    current_approach: int | None = None
    current_detector: int | None = None
    current_hours: list[int] = []
    rows_seen_in_block = 0
    records: List[Dict] = []

    for line in lines:
        dt = _parse_date(line)
        if dt:
            current_date = dt
            continue

        m = APP_DET_RE.search(line)
        if m:
            current_approach = int(m.group(1))
            current_detector = int(m.group(2))
            current_hours = []
            rows_seen_in_block = 0
            continue

        hour_matches = HOUR_HEADER_RE.findall(line)
        # Header rows normally contain many hour labels and no count values.
        if len(hour_matches) >= 2 and "Total" not in line:
            current_hours = [int(h) for h in hour_matches]
            rows_seen_in_block = 0
            continue

        rm = ROW_RE.match(line)
        if not rm or current_date is None or current_approach is None or current_detector is None or not current_hours:
            continue

        label = int(rm.group(1))
        values = [int(x) for x in re.findall(r"-?\d+", rm.group(2))]
        if not values:
            continue

        # SCATS sample rows are interval-end labels (:15, :30, :45, :60).
        # If a malformed label appears, fall back to row order.
        if label in (15, 30, 45, 60):
            minute_start = 45 if label == 60 else label - 15
        else:
            minute_start = min(rows_seen_in_block, 3) * 15
        rows_seen_in_block += 1

        for hour, count in zip(current_hours, values):
            interval_start = current_date.replace(hour=hour, minute=minute_start, second=0, microsecond=0)
            records.append(
                {
                    "intersection_code": default_intersection_code,
                    "approach_no": current_approach,
                    "detector_no": current_detector,
                    "interval_start": interval_start.isoformat(),
                    "interval_minutes": 15,
                    "vehicle_count": count,
                    "quality_flag": "ok" if count >= 0 else "invalid_negative",
                    "raw_value": str(count),
                }
            )
    return records
