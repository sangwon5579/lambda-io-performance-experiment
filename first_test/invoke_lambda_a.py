import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.config import Config


REGION = "ap-northeast-2"
FUNCTION_NAME = "test"

CONCURRENCIES = [1, 2, 5, 10, 20]
ROUNDS = 10
SIZE_MB = 128

config = Config(
    read_timeout=120,
    connect_timeout=10,
    retries={"max_attempts": 3, "mode": "standard"},
)

lambda_client = boto3.client("lambda", region_name=REGION, config=config)


def invoke_once():
    response = lambda_client.invoke(
        FunctionName=FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps({"size_mb": SIZE_MB}),
    )

    result = json.loads(response["Payload"].read())

    if "FunctionError" in response:
        raise RuntimeError(result)

    return result["io_result"]["throughput_MBps"]


def run_one_round(concurrency):
    throughputs = []
    errors = 0

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(invoke_once) for _ in range(concurrency)]

        for future in as_completed(futures):
            try:
                throughputs.append(future.result())
            except Exception as e:
                errors += 1
                print(f"  error: {type(e).__name__}: {e}")

    return throughputs, errors


def run_experiment():
    summary = []

    for concurrency in CONCURRENCIES:
        round_avgs = []
        total_errors = 0

        print(f"\n=== concurrency {concurrency} ===")

        for r in range(1, ROUNDS + 1):
            throughputs, errors = run_one_round(concurrency)
            total_errors += errors

            if throughputs:
                avg = statistics.mean(throughputs)
                round_avgs.append(avg)
                print(f"round {r}: avg={avg:.2f} MB/s, ok={len(throughputs)}, errors={errors}, each={throughputs}")
            else:
                print(f"round {r}: all failed, errors={errors}")

            time.sleep(3)

        if round_avgs:
            mean = statistics.mean(round_avgs)
            stdev = statistics.stdev(round_avgs) if len(round_avgs) > 1 else 0
            summary.append((concurrency, mean, stdev, min(round_avgs), max(round_avgs), total_errors))

    print("\n\n===== SUMMARY =====")
    print("concurrency, mean_MBps, stdev, min_round_avg, max_round_avg, errors")

    for row in summary:
        print(f"{row[0]}, {row[1]:.2f}, {row[2]:.2f}, {row[3]:.2f}, {row[4]:.2f}, {row[5]}")


if __name__ == "__main__":
    run_experiment()