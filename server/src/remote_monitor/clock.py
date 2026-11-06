"""Single time base for the whole system.

All media timestamps are wall-clock microseconds since the Unix epoch (UTC). Using wall-clock
time (instead of a per-stream counter) lets every viewer compute the end-to-end latency
(capture -> display) after a simple NTP-style clock-offset estimation.
"""

import time


def now_us() -> int:
    return time.time_ns() // 1_000


def now_ms() -> int:
    return time.time_ns() // 1_000_000
