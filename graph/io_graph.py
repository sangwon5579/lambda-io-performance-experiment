import matplotlib.pyplot as plt
import numpy as np

# Concurrency
concurrency = np.array([1, 2, 5, 10, 20])

# Mean throughput (MB/s)
mean_128 = np.array([97.97, 96.82, 97.38, 97.25, 97.54])
std_128  = np.array([2.33, 1.35, 1.54, 1.28, 0.65])
err_128  = np.array([0, 0, 0, 0, 27])

mean_512 = np.array([383.14, 403.15, 402.01, 402.64, 415.73])
std_512  = np.array([65.20, 35.83, 29.71, 34.35, 5.00])
err_512  = np.array([0, 0, 0, 0, 11])

mean_1024 = np.array([394.27, 419.24, 403.88, 405.33, 419.65])
std_1024  = np.array([69.16, 35.97, 40.55, 34.25, 2.95])
err_1024  = np.array([0, 0, 0, 0, 0])

plt.figure(figsize=(8,5))

# Throughput
plt.errorbar(concurrency, mean_128, yerr=std_128,
             marker='o', capsize=4, linewidth=2, label='128 MB')

plt.errorbar(concurrency, mean_512, yerr=std_512,
             marker='s', capsize=4, linewidth=2, label='512 MB')

plt.errorbar(concurrency, mean_1024, yerr=std_1024,
             marker='^', capsize=4, linewidth=2, label='1024 MB')

plt.xlabel("Concurrency")
plt.ylabel("Throughput (MB/s)")
plt.title("AWS Lambda Disk I/O Throughput")
plt.xticks(concurrency)
plt.grid(True, linestyle='--', alpha=0.5)
plt.legend()

plt.tight_layout()
plt.show()

# --------------------------------------------------------
# Error 개수도 같이 보고 싶다면 별도 그래프
# --------------------------------------------------------

x = np.arange(len(concurrency))
width = 0.25

plt.figure(figsize=(8,4))

plt.bar(x - width, err_128, width, label="128 MB")
plt.bar(x,         err_512, width, label="512 MB")
plt.bar(x + width, err_1024, width, label="1024 MB")

plt.xticks(x, concurrency)
plt.xlabel("Concurrency")
plt.ylabel("Number of Errors")
plt.title("Lambda Invocation Errors")
plt.grid(axis='y', linestyle='--', alpha=0.5)
plt.legend()

plt.tight_layout()
plt.show()