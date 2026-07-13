import os
import platform
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


_ENV_ID_PATH = Path("/tmp/lambda_io_v1_environment_id")
_COLD_START = True


def _execution_environment_id() -> str:
    try:
        if _ENV_ID_PATH.exists():
            return _ENV_ID_PATH.read_text(encoding="utf-8").strip()

        env_id = str(uuid.uuid4())
        _ENV_ID_PATH.write_text(env_id, encoding="utf-8")
        return env_id
    except OSError:
        return "unavailable"


def _wait_until_epoch_ms(target_epoch_ms: int) -> None:
    target_ns = target_epoch_ms * 1_000_000

    while True:
        remaining_ns = target_ns - time.time_ns()
        if remaining_ns <= 0:
            return

        if remaining_ns > 20_000_000:
            time.sleep((remaining_ns - 5_000_000) / 1_000_000_000)
        elif remaining_ns > 1_000_000:
            time.sleep(0.0005)
        else:
            pass


def _cache_busting_url(url: str, token: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}lambda_io_run={urllib.parse.quote(token)}"


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    global _COLD_START

    cold_start = _COLD_START
    _COLD_START = False

    experiment_id = str(event.get("experiment_id", "manual"))
    round_id = str(event.get("round_id", "manual"))
    invocation_index = int(event.get("invocation_index", 0))
    scheduled_start_at_ms = int(event.get("start_at_epoch_ms", time.time_ns() // 1_000_000))

    url = str(event.get("url") or os.environ.get("NETWORK_TEST_URL", "")).strip()
    timeout_seconds = int(event.get("timeout_seconds", 180))
    expected_size_mb = event.get("expected_size_mb")
    chunk_kb = int(event.get("chunk_kb", 1024))

    if not url:
        raise ValueError("A network test URL is required")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if chunk_kb <= 0:
        raise ValueError("chunk_kb must be positive")

    received_at_ns = time.time_ns()
    request_url = _cache_busting_url(
        url,
        f"{experiment_id}-{round_id}-{invocation_index}-{uuid.uuid4().hex}",
    )

    request = urllib.request.Request(
        request_url,
        headers={
            "User-Agent": "lambda-io-experiment-v1",
            "Cache-Control": "no-cache",
            "Connection": "close",
            "Accept-Encoding": "identity",
        },
        method="GET",
    )

    _wait_until_epoch_ms(scheduled_start_at_ms)

    workload_started_at_ns = time.time_ns()
    perf_started_ns = time.perf_counter_ns()
    total_bytes = 0
    status_code = None
    content_length = None

    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        status_code = getattr(response, "status", None)
        content_length_header = response.headers.get("Content-Length")
        if content_length_header:
            content_length = int(content_length_header)

        while True:
            chunk = response.read(chunk_kb * 1024)
            if not chunk:
                break
            total_bytes += len(chunk)

    perf_finished_ns = time.perf_counter_ns()
    workload_finished_at_ns = time.time_ns()

    if expected_size_mb is not None:
        expected_bytes = int(expected_size_mb) * 1024 * 1024
        if total_bytes != expected_bytes:
            raise ValueError(
                f"Downloaded {total_bytes} bytes, expected {expected_bytes} bytes"
            )

    elapsed_sec = (perf_finished_ns - perf_started_ns) / 1_000_000_000
    throughput_mibps = (total_bytes / (1024 * 1024)) / elapsed_sec
    start_lag_ms = workload_started_at_ns / 1_000_000 - scheduled_start_at_ms

    return {
        "schema_version": 1,
        "workload": "network",
        "experiment_id": experiment_id,
        "round_id": round_id,
        "invocation_index": invocation_index,
        "aws_request_id": getattr(context, "aws_request_id", None),
        "execution_environment_id": _execution_environment_id(),
        "cold_start": cold_start,
        "python_version": platform.python_version(),
        "kernel": platform.release(),
        "memory_mb": int(os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "0")),
        "url": url,
        "http_status": status_code,
        "content_length": content_length,
        "scheduled_start_at_ms": scheduled_start_at_ms,
        "received_at_ns": received_at_ns,
        "workload_started_at_ns": workload_started_at_ns,
        "workload_finished_at_ns": workload_finished_at_ns,
        "start_lag_ms": start_lag_ms,
        "bytes_processed": total_bytes,
        "downloaded_MiB": total_bytes / (1024 * 1024),
        "elapsed_sec": elapsed_sec,
        "throughput_MiBps": throughput_mibps,
    }
