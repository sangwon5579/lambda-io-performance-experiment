import json
import os
import platform
import time
import uuid
from pathlib import Path
from typing import Any


_ENV_ID_PATH = Path("/tmp/lambda_io_v1_environment_id")
_COLD_START = True


def _execution_environment_id() -> str:
    """Return an ID that persists only for the lifetime of this execution environment."""
    try:
        if _ENV_ID_PATH.exists():
            return _ENV_ID_PATH.read_text(encoding="utf-8").strip()

        env_id = str(uuid.uuid4())
        _ENV_ID_PATH.write_text(env_id, encoding="utf-8")
        return env_id
    except OSError:
        # The ID is only diagnostic data. Do not fail the benchmark if it cannot be stored.
        return "unavailable"


def _wait_until_epoch_ms(target_epoch_ms: int) -> None:
    """Wait until a common wall-clock time supplied by the local runner."""
    target_ns = target_epoch_ms * 1_000_000

    while True:
        remaining_ns = target_ns - time.time_ns()
        if remaining_ns <= 0:
            return

        # Sleep for most of the remaining period and use short sleeps near the barrier.
        if remaining_ns > 20_000_000:
            time.sleep((remaining_ns - 5_000_000) / 1_000_000_000)
        elif remaining_ns > 1_000_000:
            time.sleep(0.0005)
        else:
            # The final sub-millisecond wait reduces barrier overshoot.
            pass


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    written = 0

    while written < len(view):
        count = os.write(fd, view[written:])
        if count <= 0:
            raise OSError("os.write() returned zero bytes")
        written += count


def _buffered_write(path: str, total_bytes: int, block_bytes: int) -> None:
    block = b"x" * block_bytes
    remaining = total_bytes

    with open(path, "wb") as file:
        while remaining > 0:
            chunk_size = min(remaining, block_bytes)
            file.write(block[:chunk_size])
            remaining -= chunk_size

        file.flush()
        os.fsync(file.fileno())


def _synchronous_write(path: str, total_bytes: int, block_bytes: int) -> None:
    block = b"x" * block_bytes
    flags = os.O_CREAT | os.O_WRONLY | os.O_TRUNC

    # O_DSYNC makes each write wait for data persistence where supported.
    if hasattr(os, "O_DSYNC"):
        flags |= os.O_DSYNC
    else:
        flags |= os.O_SYNC

    fd = os.open(path, flags, 0o600)
    remaining = total_bytes

    try:
        while remaining > 0:
            chunk_size = min(remaining, block_bytes)
            _write_all(fd, block[:chunk_size])
            remaining -= chunk_size

        if hasattr(os, "fdatasync"):
            os.fdatasync(fd)
        else:
            os.fsync(fd)
    finally:
        os.close(fd)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    global _COLD_START

    cold_start = _COLD_START
    _COLD_START = False

    experiment_id = str(event.get("experiment_id", "manual"))
    round_id = str(event.get("round_id", "manual"))
    invocation_index = int(event.get("invocation_index", 0))
    scheduled_start_at_ms = int(event.get("start_at_epoch_ms", time.time_ns() // 1_000_000))

    mode = str(event.get("mode", "buffered")).lower()
    size_mb = int(event.get("size_mb", 128))
    block_kb = int(event.get("block_kb", 1024))

    if mode not in {"buffered", "sync"}:
        raise ValueError("mode must be 'buffered' or 'sync'")
    if size_mb <= 0:
        raise ValueError("size_mb must be positive")
    if block_kb <= 0:
        raise ValueError("block_kb must be positive")

    total_bytes = size_mb * 1024 * 1024
    block_bytes = block_kb * 1024
    path = f"/tmp/io-{experiment_id}-{round_id}-{invocation_index}-{uuid.uuid4().hex}.bin"

    received_at_ns = time.time_ns()

    _wait_until_epoch_ms(scheduled_start_at_ms)

    workload_started_at_ns = time.time_ns()
    perf_started_ns = time.perf_counter_ns()

    try:
        if mode == "buffered":
            _buffered_write(path, total_bytes, block_bytes)
        else:
            _synchronous_write(path, total_bytes, block_bytes)
    finally:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    perf_finished_ns = time.perf_counter_ns()
    workload_finished_at_ns = time.time_ns()

    elapsed_sec = (perf_finished_ns - perf_started_ns) / 1_000_000_000
    throughput_mibps = (total_bytes / (1024 * 1024)) / elapsed_sec
    start_lag_ms = workload_started_at_ns / 1_000_000 - scheduled_start_at_ms

    return {
        "schema_version": 1,
        "workload": "disk",
        "experiment_id": experiment_id,
        "round_id": round_id,
        "invocation_index": invocation_index,
        "aws_request_id": getattr(context, "aws_request_id", None),
        "execution_environment_id": _execution_environment_id(),
        "cold_start": cold_start,
        "python_version": platform.python_version(),
        "kernel": platform.release(),
        "memory_mb": int(os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "0")),
        "mode": mode,
        "size_mb": size_mb,
        "block_kb": block_kb,
        "scheduled_start_at_ms": scheduled_start_at_ms,
        "received_at_ns": received_at_ns,
        "workload_started_at_ns": workload_started_at_ns,
        "workload_finished_at_ns": workload_finished_at_ns,
        "start_lag_ms": start_lag_ms,
        "bytes_processed": total_bytes,
        "elapsed_sec": elapsed_sec,
        "throughput_MiBps": throughput_mibps,
    }
