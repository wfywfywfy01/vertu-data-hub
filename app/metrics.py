"""Small in-process Prometheus metrics for the private API."""
from __future__ import annotations

import threading
import time
from collections import Counter


_lock = threading.Lock()
_started_at = time.time()
_in_flight = 0
_requests: Counter[tuple[str, str, int]] = Counter()
_duration_seconds: Counter[tuple[str, str]] = Counter()


def begin_request() -> float:
    global _in_flight
    with _lock:
        _in_flight += 1
    return time.perf_counter()


def end_request(method: str, route: str, status: int, started: float) -> None:
    global _in_flight
    key = (method, route)
    with _lock:
        _in_flight -= 1
        _requests[(method, route, status)] += 1
        _duration_seconds[key] += time.perf_counter() - started


def render() -> str:
    with _lock:
        lines = [
            "# HELP data_hub_uptime_seconds Process uptime.",
            "# TYPE data_hub_uptime_seconds gauge",
            f"data_hub_uptime_seconds {time.time() - _started_at:.3f}",
            "# HELP data_hub_http_requests_in_flight Current HTTP requests.",
            "# TYPE data_hub_http_requests_in_flight gauge",
            f"data_hub_http_requests_in_flight {_in_flight}",
            "# HELP data_hub_http_requests_total HTTP requests by route and status.",
            "# TYPE data_hub_http_requests_total counter",
        ]
        for (method, route, status), value in sorted(_requests.items()):
            lines.append(
                f'data_hub_http_requests_total{{method="{method}",route="{route}",status="{status}"}} {value}'
            )
        lines.extend([
            "# HELP data_hub_http_request_duration_seconds_sum Total HTTP request duration.",
            "# TYPE data_hub_http_request_duration_seconds_sum counter",
        ])
        for (method, route), value in sorted(_duration_seconds.items()):
            lines.append(
                f'data_hub_http_request_duration_seconds_sum{{method="{method}",route="{route}"}} {value:.6f}'
            )
    return "\n".join(lines) + "\n"
