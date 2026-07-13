import matplotlib.pyplot as plt
import numpy as np

# Data
concurrency = np.array([1, 2, 5, 10, 20])

mean = np.array([144.05, 119.61, 84.81, 57.11, 65.78])
std  = np.array([9.29, 5.54, 4.91, 6.87, 10.71])
errors = np.array([0, 0, 0, 0, 69])

plt.figure(figsize=(7,4.5))

plt.errorbar(
    concurrency,
    mean,
    yerr=std,
    fmt='o-',
    linewidth=2,
    markersize=6,
    capsize=5,
    label='Network Throughput'
)

# Error annotation
plt.annotate(
    f"{errors[-1]} errors",
    xy=(20, mean[-1]),
    xytext=(17.5, 82),
    arrowprops=dict(arrowstyle="->"),
    fontsize=10
)

plt.title("AWS Lambda Network Throughput")
plt.xlabel("Concurrency")
plt.ylabel("Throughput (MB/s)")
plt.xticks(concurrency)
plt.grid(True, linestyle='--', alpha=0.5)
plt.legend()

plt.tight_layout()
plt.show()