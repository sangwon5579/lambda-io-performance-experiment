import os
import time
import platform


def read_file(path):
    try:
        with open(path, "r") as f:
            return f.read()
    except Exception as e:
        return str(e)


def io_test(size_mb=128):
    path = "/tmp/test.bin"
    block = b"x" * (1024 * 1024)  # 1MB

    start = time.time()

    with open(path, "wb") as f:
        for _ in range(size_mb):
            f.write(block)
        f.flush()
        os.fsync(f.fileno())

    end = time.time()

    os.remove(path)

    elapsed = end - start

    return {
        "elapsed_sec": round(elapsed, 4),
        "throughput_MBps": round(size_mb / elapsed, 2)
    }


def lambda_handler(event, context):

    result = {
        "python_version": platform.python_version(),
        "kernel": platform.release(),
        "memory_limit_MB": os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE"),

        "cpuinfo": read_file("/proc/cpuinfo")[:500],
        "meminfo": read_file("/proc/meminfo")[:500],
        "cgroup": read_file("/proc/self/cgroup"),
        "uptime": read_file("/proc/uptime"),

        "io_result": io_test(
            event.get("size_mb", 128)
        )
    }

    return result