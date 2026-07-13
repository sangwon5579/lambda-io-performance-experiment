import os
import time
import platform
import urllib.request


def load_env(path=".env"):
    if not os.path.exists(path):
        return

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()

EC2_URL = os.environ["EC2_URL"]


def read_file(path):
    try:
        with open(path, "r") as f:
            return f.read()
    except Exception as e:
        return str(e)


def io_test(size_mb=128):
    path = "/tmp/test.bin"
    block = b"x" * (1024 * 1024)

    start = time.time()

    with open(path, "wb") as f:
        for _ in range(size_mb):
            f.write(block)
        f.flush()
        os.fsync(f.fileno())

    elapsed = time.time() - start
    os.remove(path)

    return {
        "elapsed_sec": round(elapsed, 4),
        "throughput_MBps": round(size_mb / elapsed, 2)
    }


def network_test(url=EC2_URL):
    start = time.time()
    total_bytes = 0
    chunk_size = 1024 * 1024  # 1MB

    with urllib.request.urlopen(url, timeout=120) as response:
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            total_bytes += len(chunk)

    elapsed = time.time() - start
    size_mb = total_bytes / (1024 * 1024)

    return {
        "url": url,
        "downloaded_MB": round(size_mb, 2),
        "elapsed_sec": round(elapsed, 4),
        "throughput_MBps": round(size_mb / elapsed, 2)
    }


def lambda_handler(event, context):
    size_mb = event.get("size_mb", 128)
    url = event.get("url", EC2_URL)

    return {
        "python_version": platform.python_version(),
        "kernel": platform.release(),
        "memory_limit_MB": os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE"),
        "cpuinfo": read_file("/proc/cpuinfo")[:500],
        "meminfo": read_file("/proc/meminfo")[:500],
        "cgroup": read_file("/proc/self/cgroup"),
        "uptime": read_file("/proc/uptime"),
        "io_result": io_test(size_mb),
        "network_result": network_test(url)
    }
