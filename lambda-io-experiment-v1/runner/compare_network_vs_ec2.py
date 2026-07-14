from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def is_true(value: str | None) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def as_float(value: str | None) -> float:
    if value is None or value == "":
        return math.nan
    return float(value)


def build_ec2_summary(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    groups: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if is_true(row.get("valid_round")):
            groups[int(row["requested_concurrency"])].append(row)

    summary: list[dict[str, Any]] = []
    for concurrency, items in sorted(groups.items()):
        per_download = [as_float(item["mean_per_download_MiBps"]) for item in items]
        aggregate = [as_float(item["aggregate_window_MiBps"]) for item in items]
        summary.append(
            {
                "requested_concurrency": concurrency,
                "valid_rounds": len(items),
                "mean_per_download_MiBps": statistics.mean(per_download),
                "mean_aggregate_window_MiBps": statistics.mean(aggregate),
            }
        )
    return summary


def write_comparison_csv(
    path: Path,
    lambda_rows: list[dict[str, str]],
    ec2_rows: list[dict[str, Any]],
) -> None:
    output: list[dict[str, Any]] = []

    for row in lambda_rows:
        if int(row.get("valid_rounds", "0")) <= 0:
            continue
        output.append(
            {
                "source": "Lambda",
                "memory_mb": int(row["memory_mb"]),
                "requested_concurrency": int(row["requested_concurrency"]),
                "valid_rounds": int(row["valid_rounds"]),
                "mean_per_invocation_MiBps": as_float(row["mean_per_invocation_MiBps"]),
                "mean_aggregate_window_MiBps": as_float(row["mean_aggregate_window_MiBps"]),
            }
        )

    for row in ec2_rows:
        output.append(
            {
                "source": "EC2 baseline",
                "memory_mb": "",
                "requested_concurrency": row["requested_concurrency"],
                "valid_rounds": row["valid_rounds"],
                "mean_per_invocation_MiBps": row["mean_per_download_MiBps"],
                "mean_aggregate_window_MiBps": row["mean_aggregate_window_MiBps"],
            }
        )

    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "source",
                "memory_mb",
                "requested_concurrency",
                "valid_rounds",
                "mean_per_invocation_MiBps",
                "mean_aggregate_window_MiBps",
            ],
        )
        writer.writeheader()
        writer.writerows(output)


def plot_aggregate(
    output_path: Path,
    lambda_rows: list[dict[str, str]],
    ec2_rows: list[dict[str, Any]],
) -> None:
    by_memory: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in lambda_rows:
        if int(row.get("valid_rounds", "0")) > 0:
            by_memory[int(row["memory_mb"])].append(row)

    plt.figure(figsize=(9, 5.5))
    for memory_mb, rows in sorted(by_memory.items()):
        rows.sort(key=lambda item: int(item["requested_concurrency"]))
        x = [int(item["requested_concurrency"]) for item in rows]
        y = [as_float(item["mean_aggregate_window_MiBps"]) for item in rows]
        plt.plot(x, y, marker="o", label=f"Lambda {memory_mb} MB")

    x = [int(item["requested_concurrency"]) for item in ec2_rows]
    y = [float(item["mean_aggregate_window_MiBps"]) for item in ec2_rows]
    plt.plot(x, y, marker="s", linestyle="--", label="EC2 server baseline")

    plt.xlabel("Requested concurrency")
    plt.ylabel("Aggregate throughput (MiB/s)")
    plt.title("Lambda network throughput vs EC2 server baseline")
    plt.xticks(sorted(set(x) | {int(r["requested_concurrency"]) for r in lambda_rows}))
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()


def plot_per_invocation(
    output_path: Path,
    lambda_rows: list[dict[str, str]],
    ec2_rows: list[dict[str, Any]],
) -> None:
    by_memory: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in lambda_rows:
        if int(row.get("valid_rounds", "0")) > 0:
            by_memory[int(row["memory_mb"])].append(row)

    plt.figure(figsize=(9, 5.5))
    for memory_mb, rows in sorted(by_memory.items()):
        rows.sort(key=lambda item: int(item["requested_concurrency"]))
        x = [int(item["requested_concurrency"]) for item in rows]
        y = [as_float(item["mean_per_invocation_MiBps"]) for item in rows]
        plt.plot(x, y, marker="o", label=f"Lambda {memory_mb} MB")

    x = [int(item["requested_concurrency"]) for item in ec2_rows]
    y = [float(item["mean_per_download_MiBps"]) for item in ec2_rows]
    plt.plot(x, y, marker="s", linestyle="--", label="EC2 client baseline")

    plt.xlabel("Requested concurrency")
    plt.ylabel("Mean per-download throughput (MiB/s)")
    plt.title("Per-download throughput: Lambda vs EC2 baseline")
    plt.xticks(sorted(set(x) | {int(r["requested_concurrency"]) for r in lambda_rows}))
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lambda network 결과와 EC2 HTTP baseline 결과 비교"
    )
    parser.add_argument("lambda_result_dir", type=Path)
    parser.add_argument("ec2_result_dir", type=Path)
    args = parser.parse_args()

    lambda_rows = read_csv(args.lambda_result_dir / "summary.csv")
    ec2_round_rows = read_csv(args.ec2_result_dir / "rounds.csv")
    ec2_summary = build_ec2_summary(ec2_round_rows)

    if not ec2_summary:
        raise RuntimeError("EC2 baseline에 유효한 라운드가 없습니다.")

    output_dir = args.lambda_result_dir / "comparison_with_ec2"
    output_dir.mkdir(parents=True, exist_ok=True)

    aggregate_path = output_dir / "aggregate_lambda_vs_ec2.png"
    per_invocation_path = output_dir / "per_download_lambda_vs_ec2.png"
    csv_path = output_dir / "comparison_summary.csv"

    plot_aggregate(aggregate_path, lambda_rows, ec2_summary)
    plot_per_invocation(per_invocation_path, lambda_rows, ec2_summary)
    write_comparison_csv(csv_path, lambda_rows, ec2_summary)

    print(f"생성 완료: {aggregate_path}")
    print(f"생성 완료: {per_invocation_path}")
    print(f"생성 완료: {csv_path}")


if __name__ == "__main__":
    main()