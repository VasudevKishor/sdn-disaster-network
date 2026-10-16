#!/usr/bin/env python3
"""
generate_charts.py - Build comparison charts from performance CSV files

Reads baseline + QoS CSV results and produces:
  - throughput_comparison.png
  - latency_comparison.png

Usage:
  python3 performance/generate_charts.py
  python3 performance/generate_charts.py \
      --baseline performance/results/perf_baseline.csv \
      --qos performance/results/perf_qos.csv \
      --output-dir performance/charts
"""

import argparse
import csv
import os
import math
import tempfile
import sys

try:
    import matplotlib.pyplot as plt
except ImportError:
    print("matplotlib required: pip install matplotlib")
    sys.exit(1)


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_csv(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(row)
    return rows


def to_float(val):
    try:
        result = float(val)
        if not math.isfinite(result) or result < 0:
            raise ValueError("Measurement must be finite and nonnegative")
        return result
    except (TypeError, ValueError) as exc:
        raise ValueError("Missing or invalid measurement: %r" % val) from exc


def build_chart_data(baseline_rows, qos_rows):
    labels = []
    baseline_tp, qos_tp = [], []
    baseline_lat, qos_lat = [], []

    def index(rows):
        result = {}
        for row in rows:
            cls = row["flow_class"]
            if cls in result:
                raise ValueError("Duplicate flow class: " + cls)
            result[cls] = row
        if not result:
            raise ValueError("No measurements")
        return result
    baseline_by_class, qos_by_class = index(baseline_rows), index(qos_rows)
    if baseline_by_class.keys() != qos_by_class.keys():
        raise ValueError("Measured flow classes do not match")
    for cls in baseline_by_class:
        if baseline_by_class[cls]["flow_name"] != qos_by_class[cls]["flow_name"]:
            raise ValueError("Workloads do not match for " + cls)
    for row in baseline_rows:
        cls = row["flow_class"]
        name = row.get("flow_name", cls)
        labels.append(name.replace("_", "\n"))
        baseline_tp.append(to_float(row["throughput_mbps"]))
        baseline_lat.append(to_float(row["latency_avg_ms"]))
        qos_row = qos_by_class.get(cls, {})
        qos_tp.append(to_float(qos_row.get("throughput_mbps")))
        qos_lat.append(to_float(qos_row.get("latency_avg_ms")))

    return labels, baseline_tp, qos_tp, baseline_lat, qos_lat


def plot_grouped_bars(labels, baseline_vals, qos_vals, ylabel, title, output_path):
    x = range(len(labels))
    width = 0.35

    fig, ax = plt.subplots(figsize=(10, 6))
    bars1 = ax.bar([i - width / 2 for i in x], baseline_vals, width, label="Baseline (no QoS)", color="#ef4444")
    bars2 = ax.bar([i + width / 2 for i in x], qos_vals, width, label="With QoS", color="#22c55e")

    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    for bars in (bars1, bars2):
        for bar in bars:
            h = bar.get_height()
            ax.annotate(f"{h:.1f}", xy=(bar.get_x() + bar.get_width() / 2, h),
                        xytext=(0, 3), textcoords="offset points", ha="center", fontsize=9)

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"Chart saved -> {output_path}")


def generate_sample_data(output_dir):
    """Create sample CSV files for chart preview when real tests haven't been run."""
    os.makedirs(output_dir, exist_ok=False)
    sample = [
        ["mode", "flow_class", "flow_name", "throughput_mbps", "latency_avg_ms", "timestamp"],
        ["baseline", "high_priority", "Hospital_to_Ambulance", "18.5", "12.3", "sample"],
        ["baseline", "low_priority", "PublicHelpline_to_Ambulance", "17.8", "14.1", "sample"],
        ["qos", "high_priority", "Hospital_to_Ambulance", "22.4", "9.8", "sample"],
        ["qos", "low_priority", "PublicHelpline_to_Ambulance", "4.2", "28.6", "sample"],
    ]
    baseline_path = os.path.join(output_dir, "perf_baseline.csv")
    qos_path = os.path.join(output_dir, "perf_qos.csv")
    for path, mode in ((baseline_path, "baseline"), (qos_path, "qos")):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(sample[0])
            for row in sample[1:]:
                if row[0] == mode:
                    writer.writerow(row)
    return baseline_path, qos_path


def main():
    parser = argparse.ArgumentParser(description="Generate QoS performance comparison charts")
    parser.add_argument(
        "--baseline",
        default=os.path.join(PROJECT_ROOT, "performance", "results", "perf_baseline.csv"),
    )
    parser.add_argument(
        "--qos",
        default=os.path.join(PROJECT_ROOT, "performance", "results", "perf_qos.csv"),
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join(PROJECT_ROOT, "performance", "charts"),
    )
    parser.add_argument(
        "--sample", action="store_true",
        help="Create a separate, labelled synthetic preview directory",
    )
    args = parser.parse_args()

    if args.sample:
        os.makedirs(args.output_dir, exist_ok=True)
        args.output_dir = tempfile.mkdtemp(prefix="synthetic_preview_", dir=args.output_dir)
        args.baseline, args.qos = generate_sample_data(os.path.join(args.output_dir, "synthetic_data"))
        print("SYNTHETIC PREVIEW: " + args.output_dir)
    elif not os.path.isfile(args.baseline) or not os.path.isfile(args.qos):
        parser.error("Both measured CSV files are required; use --sample for a separate preview")
    os.makedirs(args.output_dir, exist_ok=True)

    baseline_rows = load_csv(args.baseline)
    qos_rows = load_csv(args.qos)
    synthetic_flags = {r.get("timestamp") == "sample" for r in baseline_rows + qos_rows}
    if len(synthetic_flags) > 1:
        raise ValueError("Cannot compare synthetic and measured data")
    synthetic = args.sample or synthetic_flags == {True}
    labels, b_tp, q_tp, b_lat, q_lat = build_chart_data(baseline_rows, qos_rows)

    plot_grouped_bars(
        labels, b_tp, q_tp,
        ylabel="Throughput (Mbps)",
        title=("SYNTHETIC PREVIEW — " if synthetic else "") + "Throughput: Baseline vs QoS",
        output_path=os.path.join(args.output_dir, "throughput_comparison.png"),
    )
    plot_grouped_bars(
        labels, b_lat, q_lat,
        ylabel="Latency (ms avg)",
        title=("SYNTHETIC PREVIEW — " if synthetic else "") + "Latency: Baseline vs QoS",
        output_path=os.path.join(args.output_dir, "latency_comparison.png"),
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
