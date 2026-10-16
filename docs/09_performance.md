# Document 9 — Performance Analysis

## 9.1 Why Performance Analysis?

The monitoring in Document 8 proves QoS is active. The performance analysis in
Requirement 5 goes further: it **quantifies exactly how much better** the critical
flows perform with QoS compared to without.

The workflow is:
1. Run the full test suite **without QoS** → record baseline numbers
2. Run the same test suite **with QoS** → record QoS numbers
3. Plot both sets of numbers side by side → chart that tells the story

---

## 9.2 `performance/run_tests.py` — Data Collection

### The Test Definition

```python
IPERF_DURATION = 10       # Run each iperf flow for 10 seconds
IPERF_BANDWIDTH = "25M"   # Each flow requests 25 Mbps (creates contention on 50 Mbps link)
PING_COUNT = 20           # Send 20 pings and average the latency

FLOWS = {
    "high_priority": {
        "name": "Hospital_to_Ambulance",
        "server_host": "h4",  "client_host": "h2",
        "server_ip": "10.0.0.4",  "port": 5001,
        "ping_target": "10.0.0.4",
    },
    "low_priority": {
        "name": "PublicHelpline_to_CommandCenter",
        "server_host": "h1",  "client_host": "h7",
        "server_ip": "10.0.0.1",  "port": 8080,
        "ping_target": "10.0.0.1",
    },
}
```

**Why 25 Mbps per flow on a 50 Mbps link?**
Two flows × 25 Mbps = 50 Mbps total — exactly saturating the shared access link.
This creates **real contention**, which is where QoS differences become visible.
If both flows requested only 10 Mbps on a 50 Mbps link, there would be no
contention and QoS would have nothing to demonstrate.

**Why these specific flows?**
- `h2→h4 on port 5001`: Matches the Ambulance TCP/5001 proactive rule → gets Queue 0.
- `h7→h1 on port 8080`: Matches the PublicHelpline rule → gets Queue 1.

This is the exact contention point the QoS was designed for.

### `run_iperf_test()` — Throughput Measurement

```python
def run_iperf_test(flow_key, flow):
    host_exec(server, f"iperf -s -p {port} -D")  # Start server in background
    time.sleep(1)
    out = host_exec(client, f"iperf -c {ip} -p {port} -t {IPERF_DURATION} -b {IPERF_BANDWIDTH}")
    mbps = parse_iperf_throughput_mbps(out)
    return {"raw": out, "throughput_mbps": mbps}
```

`iperf -s -p 5001 -D` — Start an iperf server on port 5001, daemonized (`-D`).

`iperf -c 10.0.0.4 -p 5001 -t 10 -b 25M` — Send TCP traffic to 10.0.0.4 on
port 5001 for 10 seconds at 25 Mbps requested bandwidth.

`parse_iperf_throughput_mbps()` — Uses regex to extract the final summary line
from iperf output (the line containing `Mbits/sec`).

### `run_ping_test()` — Latency Measurement

```python
def run_ping_test(flow_key, flow):
    out = host_exec(client, f"ping -c {PING_COUNT} {ping_target}")
    avg_ms = parse_ping_avg_ms(out)
    return {"raw": out, "latency_avg_ms": avg_ms}
```

`ping -c 20 10.0.0.4` — Send 20 ICMP echo requests and get the round-trip times.

`parse_ping_avg_ms()` — Uses regex to extract the average from the final
`rtt min/avg/max/mdev` line in the ping output.

### `save_csv()` — Storing Results

```python
writer.writerow(["mode", "flow_class", "flow_name", "throughput_mbps", "latency_avg_ms", "timestamp"])
```

Results are saved to `performance/results/perf_baseline.csv` and
`performance/results/perf_qos.csv`. This CSV format is:
- Human-readable (open in any spreadsheet)
- Machine-readable (fed directly into `generate_charts.py`)
- Reproducible (same format every run, so charts can be regenerated anytime)

### Running the Tests

```bash
# Baseline (no QoS):
DISASTER_QOS=0 ryu-manager controller/flow_manager_app.py  # Terminal 1
sudo bash qos/qos_teardown.sh                              # Remove OVS queues
sudo python3 topology/run_network.py                       # Terminal 2
python3 performance/run_tests.py --mode baseline           # Terminal 3

# With QoS:
DISASTER_QOS=1 ryu-manager ryu.app.ofctl_rest controller/flow_manager_app.py
sudo bash qos/qos_setup.sh
python3 performance/run_tests.py --mode qos
```

---

## 9.3 `performance/generate_charts.py` — Visualization

### Reading the CSVs

```python
def load_csv(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(row)
    return rows
```

`csv.DictReader` reads each row as a dictionary where column names are keys.
So `row["throughput_mbps"]` gives you the throughput value for that row.

### Building Chart Data

```python
def build_chart_data(baseline_rows, qos_rows):
    qos_by_class = {r["flow_class"]: r for r in qos_rows}
    for row in baseline_rows:
        cls = row["flow_class"]                      # "high_priority" or "low_priority"
        labels.append(row["flow_name"])
        baseline_tp.append(to_float(row["throughput_mbps"]))
        qos_tp.append(to_float(qos_by_class.get(cls, {}).get("throughput_mbps")))
```

This pairs up baseline and QoS data by `flow_class` key, building parallel lists
that matplotlib can plot as grouped bars.

### Generating the Charts

```python
def plot_grouped_bars(labels, baseline_vals, qos_vals, ylabel, title, output_path):
    x = range(len(labels))
    width = 0.35

    fig, ax = plt.subplots(figsize=(10, 6))
    bars1 = ax.bar([i - width/2 for i in x], baseline_vals, width,
                   label="Baseline (no QoS)", color="#ef4444")
    bars2 = ax.bar([i + width/2 for i in x], qos_vals, width,
                   label="With QoS", color="#22c55e")
```

**How the grouped bar chart is constructed:**
- Each flow (Hospital, Helpline) gets TWO bars side by side.
- Left bar = baseline value (red).
- Right bar = QoS value (green).
- The chart title and y-axis label are passed as parameters.

Two charts are generated:
- `throughput_comparison.png` — Mbps achieved by each flow.
- `latency_comparison.png` — Average RTT in milliseconds for each flow.

### The `--sample` Flag for Preview

```python
if args.sample or not os.path.exists(args.baseline):
    args.baseline, args.qos = generate_sample_data(results_dir)
```

If no real CSV files exist yet (you haven't run `run_tests.py`), this flag
generates fake but plausible numbers and creates charts anyway. Useful for
previewing what the charts look like before the actual Mininet test.

---

## 9.4 Expected Results and What They Prove

| Metric | Baseline | With QoS | What it proves |
|--------|----------|----------|----------------|
| Hospital throughput (Mbps) | ~17.5 | ~22–25 | QoS protects high-priority bandwidth |
| Helpline throughput (Mbps) | ~17.5 | ~4–5 | QoS rate-limits low-priority traffic |
| Hospital latency (ms) | ~25 | ~18 | High-priority queue reduces queuing delay |
| Helpline latency (ms) | ~25 | ~35–50 | Rate-limiting causes buffering in low queue |

**The key insight:**
Without QoS, both flows get approximately equal bandwidth (~17.5 Mbps each). This
"fairness" is actually harmful in a disaster — the hospital gets no more service than
the helpline. With QoS, the hospital gets priority access, and the helpline is strictly
limited. That's the desired behavior.
