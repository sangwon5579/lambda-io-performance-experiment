from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import threading
import time
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = PROJECT_ROOT / "results"


def parse_int_list(value: str) -> list[int]:
    result = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not result or any(item <= 0 for item in result):
        raise argparse.ArgumentTypeError("Use positive comma-separated integers")
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


def download_once(
    url: str,
    barrier: threading.Barrier,
    expected_size_mb: int,
    timeout_seconds: int,
    invocation_index: int,
) -> dict[str, Any]:
    token = uuid.uuid4().hex
    separator = "&" if "?" in url else "?"
    request_url = (
        f"{url}{separator}ec2_baseline={urllib.parse.quote(token)}"
    )
    request = urllib.request.Request(
        request_url,
        headers={
            "User-Agent": "lambda-io-ec2-baseline-v1",
            "Cache-Control": "no-cache",
            "Connection": "close",
            "Accept-Encoding": "identity",
        },
        method="GET",
    )

    barrier.wait()
    started_ns = time.time_ns()
    perf_started_ns = time.perf_counter_ns()
    total_bytes = 0

    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total_bytes += len(chunk)

    perf_finished_ns = time.perf_counter_ns()
    finished_ns = time.time_ns()

    expected_bytes = expected_size_mb * 1024 * 1024
    if total_bytes != expected_bytes:
        raise ValueError(
            f"Downloaded {total_bytes} bytes, expected {expected_bytes}"
        )

    elapsed_sec = (perf_finished_ns - perf_started_ns) / 1_000_000_000
    return {
        "invocation_index": invocation_index,
        "started_at_ns": started_ns,
        "finished_at_ns": finished_ns,
        "bytes_processed": total_bytes,
        "elapsed_sec": elapsed_sec,
        "throughput_MiBps": expected_size_mb / elapsed_sec,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure the maximum HTTP throughput of the EC2/nginx test server."
    )
    parser.add_argument("--url", required=True)
    parser.add_argument(
        "--concurrencies",
        type=parse_int_list,
        default=[1, 2, 5, 10],
    )
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--expected-size-mb", type=int, default=100)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    experiment_id = (
        f"ec2-http-baseline-{datetime.now().strftime('%Y%m%d-%H%M%S')}-"
        f"{uuid.uuid4().hex[:6]}"
    )
    result_dir = RESULTS_ROOT / experiment_id
    result_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "experiment_id": experiment_id,
        "url": args.url,
        "concurrencies": args.concurrencies,
        "rounds": args.rounds,
        "expected_size_mb": args.expected_size_mb,
        "created_at": datetime.now().isoformat(),
    }
    (result_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    raw_rows: list[dict[str, Any]] = []
    round_rows: list[dict[str, Any]] = []

    for concurrency in args.concurrencies:
        print(f"\n=== EC2 baseline concurrency {concurrency} ===")

        for round_number in range(1, args.rounds + 1):
            barrier = threading.Barrier(concurrency)
            successes: list[dict[str, Any]] = []
            errors: list[str] = []

            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = [
                    executor.submit(
                        download_once,
                        args.url,
                        barrier,
                        args.expected_size_mb,
                        args.timeout_seconds,
                        invocation_index,
                    )
                    for invocation_index in range(concurrency)
                ]

                for future in as_completed(futures):
                    try:
                        successes.append(future.result())
                    except Exception as exc:
                        errors.append(f"{type(exc).__name__}: {exc}")

            for row in successes:
                raw_rows.append(
                    {
                        "requested_concurrency": concurrency,
                        "round_number": round_number,
                        **row,
                    }
                )

            if successes:
                starts = [int(row["started_at_ns"]) for row in successes]
                ends = [int(row["finished_at_ns"]) for row in successes]
                throughputs = [
                    float(row["throughput_MiBps"]) for row in successes
                ]
                makespan_sec = (max(ends) - min(starts)) / 1_000_000_000
                total_bytes = sum(
                    int(row["bytes_processed"]) for row in successes
                )
                aggregate = (
                    total_bytes / (1024 * 1024) / makespan_sec
                    if makespan_sec > 0
                    else math.nan
                )
                start_spread_ms = (max(starts) - min(starts)) / 1_000_000
            else:
                throughputs = []
                aggregate = math.nan
                start_spread_ms = math.nan

            valid = len(successes) == concurrency and not errors
            round_row = {
                "requested_concurrency": concurrency,
                "round_number": round_number,
                "successful_downloads": len(successes),
                "errors": len(errors),
                "start_spread_ms": start_spread_ms,
                "mean_per_download_MiBps": statistics.mean(throughputs)
                if throughputs
                else math.nan,
                "median_per_download_MiBps": statistics.median(throughputs)
                if throughputs
                else math.nan,
                "p95_per_download_MiBps": percentile(throughputs, 0.95),
                "aggregate_window_MiBps": aggregate,
                "valid_round": valid,
            }
            round_rows.append(round_row)

            print(
                f"round {round_number:02d}: "
                f"{'VALID' if valid else 'INVALID'}, "
                f"ok={len(successes)}/{concurrency}, "
                f"mean={round_row['mean_per_download_MiBps']:.2f} MiB/s, "
                f"aggregate={aggregate:.2f} MiB/s"
            )
            for error in errors:
                print(f"  {error}")

            time.sleep(3)

    write_csv(result_dir / "raw_downloads.csv", raw_rows)
    write_csv(result_dir / "rounds.csv", round_rows)
    print(f"\nSaved results to: {result_dir}")


if __name__ == "__main__":
    main()
