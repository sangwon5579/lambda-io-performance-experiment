from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def parse_memories(value: str) -> set[int]:
    return {
        int(item.strip())
        for item in value.split(",")
        if item.strip()
    }


def is_true(value: str) -> bool:
    return str(value).strip().lower() in {
        "true",
        "1",
        "yes",
    }


def safe_mean(values: list[float]) -> float:
    return statistics.mean(values) if values else math.nan


def safe_median(values: list[float]) -> float:
    return statistics.median(values) if values else math.nan


def safe_stdev(values: list[float]) -> float:
    if len(values) >= 2:
        return statistics.stdev(values)

    if len(values) == 1:
        return 0.0

    return math.nan


def main() -> None:
    parser = argparse.ArgumentParser(
        description="중간 종료된 Lambda 실험의 rounds.csv로 summary.csv를 생성합니다."
    )
    parser.add_argument(
        "result_dir",
        type=Path,
        help="결과 폴더 경로",
    )
    parser.add_argument(
        "--memories",
        default="128,512,1024",
        help="분석에 포함할 메모리 값",
    )
    args = parser.parse_args()

    rounds_path = args.result_dir / "rounds.csv"
    summary_path = args.result_dir / "summary.csv"

    if not rounds_path.exists():
        raise FileNotFoundError(
            f"rounds.csv가 없습니다: {rounds_path}"
        )

    selected_memories = parse_memories(args.memories)

    with rounds_path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        rows = list(csv.DictReader(file))

    # 1769MB처럼 중간 실행된 조건은 여기서 제외합니다.
    rows = [
        row
        for row in rows
        if int(row["memory_mb"]) in selected_memories
    ]

    groups: dict[
        tuple[str, int, int],
        list[dict[str, str]],
    ] = defaultdict(list)

    for row in rows:
        key = (
            row["workload"],
            int(row["memory_mb"]),
            int(row["requested_concurrency"]),
        )
        groups[key].append(row)

    summaries: list[dict[str, Any]] = []

    for (
        workload,
        memory_mb,
        concurrency,
    ), group_rows in sorted(groups.items()):

        valid_rows = [
            row
            for row in group_rows
            if is_true(row["valid_round"])
        ]

        per_invocation = [
            float(row["mean_per_invocation_MiBps"])
            for row in valid_rows
        ]

        aggregate = [
            float(row["aggregate_window_MiBps"])
            for row in valid_rows
        ]

        peak_concurrency = [
            float(row["actual_peak_concurrency"])
            for row in valid_rows
        ]

        start_spread = [
            float(row["start_spread_ms"])
            for row in valid_rows
        ]

        summaries.append(
            {
                "workload": workload,
                "memory_mb": memory_mb,
                "requested_concurrency": concurrency,
                "total_rounds": len(group_rows),
                "valid_rounds": len(valid_rows),
                "invalid_rounds": (
                    len(group_rows) - len(valid_rows)
                ),
                "total_errors": sum(
                    int(row["errors"])
                    for row in group_rows
                ),
                "mean_per_invocation_MiBps": safe_mean(
                    per_invocation
                ),
                "stdev_round_mean_MiBps": safe_stdev(
                    per_invocation
                ),
                "median_round_mean_MiBps": safe_median(
                    per_invocation
                ),
                "mean_aggregate_window_MiBps": safe_mean(
                    aggregate
                ),
                "mean_actual_peak_concurrency": safe_mean(
                    peak_concurrency
                ),
                "mean_start_spread_ms": safe_mean(
                    start_spread
                ),
            }
        )

    if not summaries:
        raise RuntimeError(
            "선택한 메모리에 해당하는 결과가 없습니다."
        )

    with summary_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=list(summaries[0].keys()),
        )
        writer.writeheader()
        writer.writerows(summaries)

    print(f"생성 완료: {summary_path}")

    for row in summaries:
        print(
            f"memory={row['memory_mb']:4d} MB, "
            f"concurrency={row['requested_concurrency']:2d}, "
            f"valid={row['valid_rounds']}/{row['total_rounds']}, "
            f"per_inv={row['mean_per_invocation_MiBps']:.2f} MiB/s, "
            f"aggregate={row['mean_aggregate_window_MiBps']:.2f} MiB/s"
        )


if __name__ == "__main__":
    main()