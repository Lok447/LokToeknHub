"""Small dependency-free Prometheus exporter for gateway health signals."""

from collections import Counter
from threading import RLock


_lock = RLock()
_requests = Counter()


def observe_request(path: str, method: str, status_code: int) -> None:
    with _lock:
        _requests[(path, method, str(status_code))] += 1


def render_prometheus() -> str:
    lines = [
        "# HELP loktoken_http_requests_total Total HTTP requests handled by this instance.",
        "# TYPE loktoken_http_requests_total counter",
    ]
    with _lock:
        items = list(_requests.items())
    for (path, method, status), count in sorted(items):
        safe_path = path.replace('\\', '\\\\').replace('"', '\\"')
        lines.append(f'loktoken_http_requests_total{{path="{safe_path}",method="{method}",status="{status}"}} {count}')
    return "\n".join(lines) + "\n"


def reset_metrics() -> None:
    with _lock:
        _requests.clear()
