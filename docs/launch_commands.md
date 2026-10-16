# SDN Disaster Network: Comprehensive Launch Guide

This document contains all the commands needed to launch and interact with every component of the SDN Disaster Network project.

> **Note:** Ensure you are in the root directory of your project before running these commands:
> `cd /home/rithvikmukka/sdn-lab/sdn-project/`

You will need to open **three separate terminal windows** to run the different components simultaneously.

---

## 1. Launching the Network (Core Setup)

### Terminal 1: Start the Ryu Controller
First, launch the SDN controller. We include the REST API module so you can manually manage flows later.
```bash
# Set QoS variable to 1 to enable QoS flow rules
DISASTER_QOS=1 ../ryu/ryu-env/bin/ryu-manager ryu.app.ofctl_rest controller/flow_manager_app.py
```
*(Wait until the controller is fully started before moving to Terminal 2)*

### Terminal 2: Start Mininet
Now, build the disaster topology network using your custom Python script.
```bash
sudo python3 topology/run_network.py
```
*When this launches, it should automatically run `pingall` and report 0% packet loss.*

---

## 2. Setting Up QoS (Quality of Service)

### Terminal 3: Apply QoS Queues
Now that the switches are running in Mininet, configure the Open vSwitch (OVS) HTB queues.
```bash
sudo bash qos/qos_setup.sh
```
*Verify the queues are active:*
```bash
sudo ovs-vsctl list Queue
sudo ovs-ofctl -O OpenFlow13 dump-queues s1
```

*(If you ever need to remove QoS for baseline testing, run: `sudo bash qos/qos_teardown.sh`)*

---

## 3. Flow Rule Management (REST API)

### Terminal 3: Manage Flow Rules
You can interact with the controller's REST API using the manual script you created.

* **List current flows:**
  ```bash
  bash demo_scripts/manual_flow_insertion.sh list
  ```
* **Add a manual flow** (e.g., Police → Hospital):
  ```bash
  bash demo_scripts/manual_flow_insertion.sh add
  ```
* **Delete a manual flow:**
  ```bash
  bash demo_scripts/manual_flow_insertion.sh delete
  ```

---

## 4. Traffic Monitoring & Evidence Capture

### Terminal 3: Network Snapshots and Traffic Capture
Take a snapshot of the network state **before** generating traffic:
```bash
python3 monitoring/stats_reader.py --save monitoring/evidence/before.json --print
```

### Terminal 2 (Mininet CLI): Generate Competing Traffic
Run these commands inside the Mininet (`mininet>`) prompt to simulate heavy traffic:
```bash
# Start servers
h4 iperf -s -p 5001 &
h1 iperf -s -p 8080 &

# Start clients (generating 30Mbps each)
h2 iperf -c 10.0.0.4 -p 5001 -t 20 -b 30M &
h7 iperf -c 10.0.0.1 -p 8080 -t 20 -b 30M &
```

### Terminal 3: Snapshot and Packet Capture (While Traffic is Running)
While the traffic is flowing, you can capture packets and take an "after" snapshot:
```bash
# Capture tcpdump (Optional, saves pcaps to evidence folder)
sudo bash monitoring/capture_traffic.sh

# Snapshot AFTER traffic
python3 monitoring/stats_reader.py --save monitoring/evidence/after.json --print

# Compare before and after stats
python3 monitoring/stats_reader.py --compare monitoring/evidence/before.json monitoring/evidence/after.json
```

---

## 5. Automated Performance Testing

If you want to run the automated baseline vs. QoS comparison scripts, follow these steps.

### Baseline Test (No QoS):
1. **Terminal 1:** `DISASTER_QOS=0 ../ryu/ryu-env/bin/ryu-manager controller/flow_manager_app.py`
2. **Terminal 2:** `sudo python3 topology/run_network.py`
3. **Terminal 3:** `sudo bash qos/qos_teardown.sh`
4. **Terminal 3:** `python3 performance/run_tests.py --mode baseline`

### QoS Test (With QoS):
1. **Terminal 1:** `DISASTER_QOS=1 ../ryu/ryu-env/bin/ryu-manager ryu.app.ofctl_rest controller/flow_manager_app.py`
2. **Terminal 2:** `sudo python3 topology/run_network.py`
3. **Terminal 3:** `sudo bash qos/qos_setup.sh`
4. **Terminal 3:** `python3 performance/run_tests.py --mode qos`

### Generate Charts:
Once both tests are complete, you can generate the performance graphs:
```bash
python3 performance/generate_charts.py
```
*(The generated PNG graphs will be saved in `performance/charts/`)*
