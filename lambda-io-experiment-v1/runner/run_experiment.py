from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import boto3
from botocore.config import Config
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = PROJECT_ROOT / "results"
WRITE_LOCK = threading.Lock()


def parse_int_list(value: str) -> list[int]:
    try:
        result = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use comma-separated integers") from exc

    if not result or any(item <= 0 for item in result):
        raise argparse.ArgumentTypeError("Values must be positive integers")
    return result


def percentile(values: list[float], p: float) -> float:
    if not values:
        return math.nan
    if len(values) == 1:
        return values[0]

    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    lower = math.floor(position)
    upper = math.ceil(position)

    if lower == upper:
        return ordered[lower]

    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def peak_concurrency(intervals: Iterable[tuple[int, int]]) -> int:
    events: list[tuple[int, int]] = []

    for start_ns, end_ns in intervals:
        events.append((start_ns, 1))
        events.append((end_ns, -1))

    # If one interval ends exactly when another starts, process the end first.
    events.sort(key=lambda item: (item[0], item[1]))

    current = 0
    peak = 0

    for _, delta in events:
        current += delta
        peak = max(peak, current)

    return peak


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with WRITE_LOCK:
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        path.write_text("", encoding="utf-8")
        return

    fieldnames: list[str] = []
    seen: set[str] = set()

    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def wait_for_function_ready(client: Any, function_name: str, timeout_seconds: int = 300) -> None:
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        config = client.get_function_configuration(FunctionName=function_name)
        state = config.get("State", "Active")
        update_status = config.get("LastUpdateStatus", "Successful")

        if state == "Active" and update_status == "Successful":
            return

        if state == "Failed" or update_status == "Failed":
            reason = config.get("StateReason") or config.get("LastUpdateStatusReason")
            raise RuntimeError(f"Lambda configuration update failed: {reason}")

        time.sleep(2)

    raise TimeoutError(f"Timed out waiting for {function_name} to become ready")


def update_function_configuration(
    client: Any,
    function_name: str,
    memory_mb: int,
    timeout_seconds: int,
    ephemeral_storage_mb: int,
) -> None:
    current = client.get_function_configuration(FunctionName=function_name)
    current_ephemeral = current.get("EphemeralStorage", {}).get("Size", 512)

    needs_update = (
        current.get("MemorySize") != memory_mb
        or current.get("Timeout") != timeout_seconds
        or current_ephemeral != ephemeral_storage_mb
    )

    if not needs_update:
        print(
            f"  configuration already set: memory={memory_mb}, "
            f"timeout={timeout_seconds}, /tmp={ephemeral_storage_mb}"
        )
        return

    print(
        f"  updating Lambda: memory={memory_mb} MB, "
        f"timeout={timeout_seconds}s, /tmp={ephemeral_storage_mb} MB"
    )

    client.update_function_configuration(
        FunctionName=function_name,
        MemorySize=memory_mb,
        Timeout=timeout_seconds,
        EphemeralStorage={"Size": ephemeral_storage_mb},
    )
    wait_for_function_ready(client, function_name)


def invoke_once(
    client: Any,
    function_name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    client_invoke_started_ns = time.time_ns()

    response = client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )

    client_invoke_finished_ns = time.time_ns()
    raw_payload = response["Payload"].read()
    decoded = raw_payload.decode("utf-8", errors="replace")

    try:
        result = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Lambda returned non-JSON payload: {decoded[:1000]}") from exc

    if response.get("FunctionError"):
        raise RuntimeError(f"Lambda FunctionError: {result}")

    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected Lambda result: {result!r}")

    result["client_invoke_started_ns"] = client_invoke_started_ns
    result["client_invoke_finished_ns"] = client_invoke_finished_ns
    result["client_round_trip_sec"] = (
        client_invoke_finished_ns - client_invoke_started_ns
    ) / 1_000_000_000
    return result


def round_summary(
    *,
    workload: str,
    memory_mb: int,
    requested_concurrency: int,
    round_number: int,
    round_id: str,
    successes: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    max_start_lag_ms: float,
) -> dict[str, Any]:
    throughputs = [float(item["throughput_MiBps"]) for item in successes]
    intervals = [
        (int(item["workload_started_at_ns"]), int(item["workload_finished_at_ns"]))
        for item in successes
    ]
    starts = [start for start, _ in intervals]
    ends = [end for _, end in intervals]
    bytes_total = sum(int(item["bytes_processed"]) for item in successes)

    actual_peak = peak_concurrency(intervals) if intervals else 0
    start_spread_ms = (max(starts) - min(starts)) / 1_000_000 if starts else math.nan
    observed_max_start_lag_ms = (
        max(float(item["start_lag_ms"]) for item in successes)
        if successes
        else math.nan
    )
    makespan_sec = (max(ends) - min(starts)) / 1_000_000_000 if intervals else math.nan
    aggregate_mibps = (
        bytes_total / (1024 * 1024) / makespan_sec
        if intervals and makespan_sec > 0
        else math.nan
    )

    valid = (
        len(successes) == requested_concurrency
        and not errors
        and actual_peak == requested_concurrency
        and observed_max_start_lag_ms <= max_start_lag_ms
    )

    return {
        "workload": workload,
        "memory_mb": memory_mb,
        "requested_concurrency": requested_concurrency,
        "round_number": round_number,
        "round_id": round_id,
        "successful_invocations": len(successes),
        "errors": len(errors),
        "actual_peak_concurrency": actual_peak,
        "unique_execution_environments": len(
            {item.get("execution_environment_id") for item in successes}
        ),
        "cold_starts": sum(bool(item.get("cold_start")) for item in successes),
        "start_spread_ms": start_spread_ms,
        "max_start_lag_ms": observed_max_start_lag_ms,
        "makespan_sec": makespan_sec,
        "mean_per_invocation_MiBps": statistics.mean(throughputs)
        if throughputs
        else math.nan,
        "median_per_invocation_MiBps": statistics.median(throughputs)
        if throughputs
        else math.nan,
        "p95_per_invocation_MiBps": percentile(throughputs, 0.95),
        "min_per_invocation_MiBps": min(throughputs) if throughputs else math.nan,
        "max_per_invocation_MiBps": max(throughputs) if throughputs else math.nan,
        "aggregate_window_MiBps": aggregate_mibps,
        "valid_round": valid,
    }


def build_condition_summary(round_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)

    for row in round_rows:
        key = (
            str(row["workload"]),
            int(row["memory_mb"]),
            int(row["requested_concurrency"]),
        )
        groups[key].append(row)

    output: list[dict[str, Any]] = []

    for (workload, memory_mb, concurrency), rows in sorted(groups.items()):
        valid_rows = [row for row in rows if row["valid_round"]]
        per_invocation = [
            float(row["mean_per_invocation_MiBps"]) for row in valid_rows
        ]
        aggregate = [float(row["aggregate_window_MiBps"]) for row in valid_rows]

        output.append(
            {
                "workload": workload,
                "memory_mb": memory_mb,
                "requested_concurrency": concurrency,
                "total_rounds": len(rows),
                "valid_rounds": len(valid_rows),
                "invalid_rounds": len(rows) - len(valid_rows),
                "total_errors": sum(int(row["errors"]) for row in rows),
                "mean_per_invocation_MiBps": statistics.mean(per_invocation)
                if per_invocation
                else math.nan,
                "stdev_round_mean_MiBps": statistics.stdev(per_invocation)
                if len(per_invocation) > 1
                else 0.0 if per_invocation else math.nan,
                "median_round_mean_MiBps": statistics.median(per_invocation)
                if per_invocation
                else math.nan,
                "mean_aggregate_window_MiBps": statistics.mean(aggregate)
                if aggregate
                else math.nan,
                "mean_actual_peak_concurrency": statistics.mean(
                    float(row["actual_peak_concurrency"]) for row in valid_rows
                )
                if valid_rows
                else math.nan,
                "mean_start_spread_ms": statistics.mean(
                    float(row["start_spread_ms"]) for row in valid_rows
                )
                if valid_rows
                else math.nan,
            }
        )

    return output


def build_parser() -> argparse.ArgumentParser:
    load_dotenv(PROJECT_ROOT / ".env")

    parser = argparse.ArgumentParser(
        description="Run synchronized AWS Lambda disk or network I/O experiments."
    )
    parser.add_argument("workload", choices=["disk", "network"])
    parser.add_argument(
        "--region",
        default=os.getenv("AWS_REGION", "ap-northeast-2"),
    )
    parser.add_argument(
        "--function-name",
        help="Overrides DISK_FUNCTION_NAME or NETWORK_FUNCTION_NAME from .env",
    )
    parser.add_argument(
        "--memories",
        type=parse_int_list,
        default=parse_int_list(os.getenv("MEMORY_SIZES", "128,512,1769")),
        help="Comma-separated memory values, for example 128,512,1769",
    )
    parser.add_argument(
        "--concurrencies",
        type=parse_int_list,
        default=parse_int_list(os.getenv("CONCURRENCIES", "1,5,10")),
        help="Comma-separated requested concurrency values",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=int(os.getenv("ROUNDS", "5")),
    )
    parser.add_argument(
        "--barrier-seconds",
        type=float,
        default=float(os.getenv("BARRIER_SECONDS", "10")),
    )
    parser.add_argument(
        "--round-gap-seconds",
        type=float,
        default=float(os.getenv("ROUND_GAP_SECONDS", "5")),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.getenv("LAMBDA_TIMEOUT_SECONDS", "300")),
    )
    parser.add_argument(
        "--ephemeral-storage-mb",
        type=int,
        default=int(os.getenv("EPHEMERAL_STORAGE_MB", "1024")),
    )
    parser.add_argument(
        "--max-start-lag-ms",
        type=float,
        default=float(os.getenv("MAX_START_LAG_MS", "250")),
        help="A round is invalid if any workload starts this late after the barrier",
    )
    parser.add_argument(
        "--skip-config-update",
        action="store_true",
        help="Do not update Lambda memory, timeout, or ephemeral storage",
    )

    # Disk-specific arguments
    parser.add_argument(
        "--disk-mode",
        choices=["buffered", "sync"],
        default=os.getenv("DISK_MODE", "buffered"),
    )
    parser.add_argument(
        "--size-mb",
        type=int,
        default=int(os.getenv("DISK_SIZE_MB", "128")),
    )
    parser.add_argument(
        "--block-kb",
        type=int,
        default=int(os.getenv("DISK_BLOCK_KB", "1024")),
    )

    # Network-specific arguments
    parser.add_argument(
        "--urls",
        default=os.getenv("NETWORK_URLS", ""),
        help="Comma-separated URLs; invocations are assigned round-robin",
    )
    parser.add_argument(
        "--expected-size-mb",
        type=int,
        default=int(os.getenv("NETWORK_EXPECTED_SIZE_MB", "100")),
    )
    parser.add_argument(
        "--network-timeout-seconds",
        type=int,
        default=int(os.getenv("NETWORK_TIMEOUT_SECONDS", "180")),
    )
    parser.add_argument(
        "--network-chunk-kb",
        type=int,
        default=int(os.getenv("NETWORK_CHUNK_KB", "1024")),
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.rounds <= 0:
        parser.error("--rounds must be positive")
    if args.barrier_seconds < 2:
        parser.error("--barrier-seconds must be at least 2 seconds")
    if not 512 <= args.ephemeral_storage_mb <= 10240:
        parser.error("--ephemeral-storage-mb must be between 512 and 10240")

    env_function_name = (
        os.getenv("DISK_FUNCTION_NAME")
        if args.workload == "disk"
        else os.getenv("NETWORK_FUNCTION_NAME")
    )
    function_name = args.function_name or env_function_name

    if not function_name:
        parser.error(
            "Set the function name with --function-name or in .env "
            "(DISK_FUNCTION_NAME / NETWORK_FUNCTION_NAME)"
        )

    urls = [item.strip() for item in args.urls.split(",") if item.strip()]
    if args.workload == "network" and not urls:
        parser.error("Network workload requires NETWORK_URLS or --urls")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = args.disk_mode if args.workload == "disk" else "download"
    experiment_id = f"{args.workload}-{suffix}-{timestamp}-{uuid.uuid4().hex[:6]}"
    result_dir = RESULTS_ROOT / experiment_id
    raw_path = result_dir / "raw_invocations.jsonl"
    error_path = result_dir / "errors.jsonl"

    client_config = Config(
        connect_timeout=10,
        read_timeout=max(args.timeout_seconds + 60, 360),
        max_pool_connections=max(64, max(args.concurrencies) * 2),
        retries={"total_max_attempts": 1, "mode": "standard"},
    )
    lambda_client = boto3.client(
        "lambda",
        region_name=args.region,
        config=client_config,
    )

    try:
        function_config = lambda_client.get_function_configuration(
            FunctionName=function_name
        )
    except Exception as exc:
        print(f"Cannot read Lambda function {function_name}: {exc}", file=sys.stderr)
        return 1

    metadata = {
        "experiment_id": experiment_id,
        "created_at": datetime.now().isoformat(),
        "region": args.region,
        "function_name": function_name,
        "function_runtime": function_config.get("Runtime"),
        "function_architecture": function_config.get("Architectures"),
        "workload": args.workload,
        "memories": args.memories,
        "concurrencies": args.concurrencies,
        "rounds": args.rounds,
        "barrier_seconds": args.barrier_seconds,
        "round_gap_seconds": args.round_gap_seconds,
        "timeout_seconds": args.timeout_seconds,
        "ephemeral_storage_mb": args.ephemeral_storage_mb,
        "max_start_lag_ms": args.max_start_lag_ms,
        "disk_mode": args.disk_mode if args.workload == "disk" else None,
        "size_mb": args.size_mb if args.workload == "disk" else None,
        "block_kb": args.block_kb if args.workload == "disk" else None,
        "urls": urls if args.workload == "network" else None,
        "expected_size_mb": args.expected_size_mb
        if args.workload == "network"
        else None,
    }

    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\nExperiment: {experiment_id}")
    print(f"Function:   {function_name}")
    print(f"Results:    {result_dir}\n")

    round_rows: list[dict[str, Any]] = []

    for memory_mb in args.memories:
        print(f"\n##### MEMORY {memory_mb} MB #####")

        if not args.skip_config_update:
            try:
                update_function_configuration(
                    lambda_client,
                    function_name,
                    memory_mb,
                    args.timeout_seconds,
                    args.ephemeral_storage_mb,
                )
            except Exception as exc:
                print(f"Configuration update failed: {exc}", file=sys.stderr)
                return 1

            # Allow the control-plane change to settle before starting requests.
            time.sleep(3)

        for concurrency in args.concurrencies:
            print(f"\n=== requested concurrency {concurrency} ===")

            for round_number in range(1, args.rounds + 1):
                round_id = (
                    f"m{memory_mb}-c{concurrency}-r{round_number:02d}-"
                    f"{uuid.uuid4().hex[:6]}"
                )
                start_at_epoch_ms = int(
                    (time.time() + args.barrier_seconds) * 1000
                )

                payloads: list[dict[str, Any]] = []

                for invocation_index in range(concurrency):
                    payload: dict[str, Any] = {
                        "experiment_id": experiment_id,
                        "round_id": round_id,
                        "invocation_index": invocation_index,
                        "start_at_epoch_ms": start_at_epoch_ms,
                    }

                    if args.workload == "disk":
                        payload.update(
                            {
                                "mode": args.disk_mode,
                                "size_mb": args.size_mb,
                                "block_kb": args.block_kb,
                            }
                        )
                    else:
                        payload.update(
                            {
                                "url": urls[invocation_index % len(urls)],
                                "expected_size_mb": args.expected_size_mb,
                                "timeout_seconds": args.network_timeout_seconds,
                                "chunk_kb": args.network_chunk_kb,
                            }
                        )

                    payloads.append(payload)

                successes: list[dict[str, Any]] = []
                errors: list[dict[str, Any]] = []

                with ThreadPoolExecutor(max_workers=concurrency) as executor:
                    futures = {
                        executor.submit(
                            invoke_once,
                            lambda_client,
                            function_name,
                            payload,
                        ): payload
                        for payload in payloads
                    }

                    for future in as_completed(futures):
                        payload = futures[future]
                        try:
                            result = future.result()
                            successes.append(result)
                            append_jsonl(raw_path, result)
                        except Exception as exc:
                            error = {
                                "experiment_id": experiment_id,
                                "round_id": round_id,
                                "memory_mb": memory_mb,
                                "requested_concurrency": concurrency,
                                "round_number": round_number,
                                "invocation_index": payload["invocation_index"],
                                "error_type": type(exc).__name__,
                                "error_message": str(exc),
                                "recorded_at": datetime.now().isoformat(),
                            }
                            errors.append(error)
                            append_jsonl(error_path, error)

                row = round_summary(
                    workload=args.workload,
                    memory_mb=memory_mb,
                    requested_concurrency=concurrency,
                    round_number=round_number,
                    round_id=round_id,
                    successes=successes,
                    errors=errors,
                    max_start_lag_ms=args.max_start_lag_ms,
                )
                round_rows.append(row)
                write_csv(result_dir / "rounds.csv", round_rows)

                validity = "VALID" if row["valid_round"] else "INVALID"
                print(
                    f"round {round_number:02d}: {validity}, "
                    f"ok={row['successful_invocations']}/{concurrency}, "
                    f"peak={row['actual_peak_concurrency']}, "
                    f"mean={row['mean_per_invocation_MiBps']:.2f} MiB/s, "
                    f"aggregate={row['aggregate_window_MiBps']:.2f} MiB/s, "
                    f"start_spread={row['start_spread_ms']:.2f} ms, "
                    f"max_lag={row['max_start_lag_ms']:.2f} ms"
                )

                if errors:
                    for error in errors:
                        print(
                            f"  error[{error['invocation_index']}]: "
                            f"{error['error_type']}: {error['error_message']}"
                        )

                if round_number < args.rounds:
                    time.sleep(args.round_gap_seconds)

    condition_rows = build_condition_summary(round_rows)
    write_csv(result_dir / "summary.csv", condition_rows)

    print("\n===== CONDITION SUMMARY (valid rounds only) =====")
    for row in condition_rows:
        print(
            f"memory={row['memory_mb']:4d}, "
            f"concurrency={row['requested_concurrency']:2d}, "
            f"valid={row['valid_rounds']}/{row['total_rounds']}, "
            f"per_inv={row['mean_per_invocation_MiBps']:.2f} MiB/s, "
            f"aggregate={row['mean_aggregate_window_MiBps']:.2f} MiB/s, "
            f"errors={row['total_errors']}"
        )

    print(f"\nSaved results to: {result_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
