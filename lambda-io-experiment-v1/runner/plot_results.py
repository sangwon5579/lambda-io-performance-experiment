from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot Lambda I/O experiment summary.")
    parser.add_argument("result_dir", type=Path)
    args = parser.parse_args()

    summary_path = args.result_dir / "summary.csv"
    if not summary_path.exists():
        raise FileNotFoundError(f"Cannot find {summary_path}")

    rows = read_rows(summary_path)
    graph_dir = args.result_dir / "graphs"
    graph_dir.mkdir(parents=True, exist_ok=True)

    per_memory: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if int(row["valid_rounds"]) > 0:
            per_memory[int(row["memory_mb"])].append(row)

    plt.figure(figsize=(8, 5))
    for memory, items in sorted(per_memory.items()):
        items.sort(key=lambda row: int(row["requested_concurrency"]))
        x = [int(row["requested_concurrency"]) for row in items]
        y = [float(row["mean_per_invocation_MiBps"]) for row in items]
        plt.plot(x, y, marker="o", label=f"{memory} MB")

    plt.xlabel("Requested concurrency")
    plt.ylabel("Mean per-invocation throughput (MiB/s)")
    plt.title("AWS Lambda per-invocation throughput")
    plt.xticks(sorted({int(row["requested_concurrency"]) for row in rows}))
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.legend()
    plt.tight_layout()
    per_invocation_path = graph_dir / "per_invocation_throughput.png"
    plt.savefig(per_invocation_path, dpi=160)
    plt.close()

    plt.figure(figsize=(8, 5))
    for memory, items in sorted(per_memory.items()):
        items.sort(key=lambda row: int(row["requested_concurrency"]))
        x = [int(row["requested_concurrency"]) for row in items]
        y = [float(row["mean_aggregate_window_MiBps"]) for row in items]
        plt.plot(x, y, marker="o", label=f"{memory} MB")

    plt.xlabel("Requested concurrency")
    plt.ylabel("Aggregate window throughput (MiB/s)")
    plt.title("AWS Lambda aggregate throughput")
    plt.xticks(sorted({int(row["requested_concurrency"]) for row in rows}))
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.legend()
    plt.tight_layout()
    aggregate_path = graph_dir / "aggregate_throughput.png"
    plt.savefig(aggregate_path, dpi=160)
    plt.close()

    print(f"Created: {per_invocation_path}")
    print(f"Created: {aggregate_path}")


if __name__ == "__main__":
    main()
