"""Save reproducible offline evidence; never starts Mininet or claims live gates."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main(phase=2):
    parser = argparse.ArgumentParser(description="Save reproducible Phase %d offline evidence" % phase)
    parser.add_argument("--ryu-python", type=Path, default=ROOT.parent / "ryu/ryu-env/bin/python")
    parser.add_argument("--output", type=Path, default=ROOT / (
        "monitoring/evidence/phase%d_offline_%s.json" % (phase, datetime.now(timezone.utc).date())))
    args = parser.parse_args()
    data = {"started_utc": datetime.now(timezone.utc).isoformat(), "scope": "Phase %d offline checks" % phase,
            "live_validated": False, "checks": [],
            "limits": ["No Mininet/kernel dataplane executed; workload process check uses loopback",
                       "Usable-path monitor and new fault scenarios require privileged validation",
                       "No new privileged topology/resource/failure validation",
                       "Phase 3 OVS integration uses isolated dummy ports; kernel HTB and Mininet services need the live gate" if phase>=3 else "No Phase 3 service-policy engine",
                       *(["Phase 4 congestion degradation/recovery and full demo resets require privileged live validation"] if phase>=4 else [])]}
    commands = [
        [sys.executable, "-m", "unittest", "tests.test_foundation", "tests.test_phase1_measurements",
         "tests.test_phase1_workload_processes",
         *(["tests.test_phase2_model", "tests.test_path_monitor", "tests.test_phase2_reconnect",
            "tests.test_expanded_model", "tests.test_expanded_evidence", "tests.test_expanded_load"] if phase >= 2 else []),
         *(["tests.test_incident_model","tests.test_incident_deploy","tests.test_incident_backend","tests.test_incident_ovs","tests.test_service_probe"] if phase>=3 else []),
         *(["tests.test_demos"] if phase>=4 else []), "-v"],
        [str(args.ryu_python), "-m", "unittest", "tests.test_matches", "tests.test_controller", "tests.test_protocol",
         *(["tests.test_phase2_controller", "tests.test_phase2_protocol", "tests.test_expanded_controller",
            "tests.test_expanded_wire"] if phase >= 2 else []),
         *(["tests.test_incident_controller"] if phase>=3 else []), "-v"],
    ]
    for cmd in commands:
        try:
            result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=180 if phase>=3 else 90,
                                    env=dict(os.environ, MPLBACKEND="Agg", PYTHONDONTWRITEBYTECODE="1"))
            record = {"command": cmd, "returncode": result.returncode,
                      "stdout": result.stdout, "stderr": result.stderr,
                      "passed": result.returncode == 0 and "skipped=" not in result.stderr}
        except (OSError, subprocess.TimeoutExpired) as exc:
            record = {"command": cmd, "passed": False, "error": str(exc)}
        data["checks"].append(record)
    # Compile bytes in memory: never execute ad hoc tests or write root-owned
    # __pycache__ directories left by older lab sessions.
    tracked = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, text=True)
    sources = sorted(set(tracked.splitlines()))
    python_files = [s for s in sources if s.endswith(".py")]
    shell_files = [s for s in sources if s.endswith(".sh")]
    errors = []
    for name in python_files:
        try:
            compile((ROOT / name).read_bytes(), name, "exec")
        except (SyntaxError, OSError) as exc:
            errors.append(str(exc))
    data["checks"].append({"check": "in-memory Python compile (no execution)", "files": python_files,
                           "passed": not errors, "errors": errors})
    for name in shell_files:
        result = subprocess.run(["bash", "-n", name], cwd=ROOT, capture_output=True, text=True, timeout=10)
        data["checks"].append({"check": "shell syntax", "file": name,
                               "passed": result.returncode == 0, "stderr": result.stderr})
    for name in ("config/acceptance.json", "config/expanded_acceptance.json", "qos/queue_config.json"):
        try:
            json.loads((ROOT / name).read_text())
            record = {"check": "JSON parse", "file": name, "passed": True}
        except (OSError, ValueError) as exc:
            record = {"check": "JSON parse", "file": name, "passed": False, "error": str(exc)}
        data["checks"].append(record)
    diff = subprocess.run(["git", "diff", "--check"], cwd=ROOT, capture_output=True, text=True, timeout=10)
    data["checks"].append({"check": "tracked diff whitespace", "passed": diff.returncode == 0,
                           "output": diff.stdout + diff.stderr})
    data["passed"] = all(check["passed"] for check in data["checks"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n")
    print(("PASS" if data["passed"] else "FAIL") + ": offline evidence saved to " + str(args.output))
    return 0 if data["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
