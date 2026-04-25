from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

SIGNAL_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+INT\s+(?P<int>\S+)\s+PHASE\s+(?P<phase>\d+)\s+(?P<state>.+?)\s*$",
    re.IGNORECASE,
)


def parse_signal_log(path: str | Path) -> List[Dict]:
    path = Path(path)
    records: List[Dict] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip().replace("\u00a0", " ")
        if not line:
            continue
        m = SIGNAL_RE.match(line)
        if not m:
            continue
        state = " ".join(m.group("state").upper().split())
        records.append(
            {
                "event_time": m.group("ts"),
                "intersection_code": m.group("int"),
                "phase_no": int(m.group("phase")),
                "signal_state": state,
                "raw_line": line,
            }
        )
    return records
