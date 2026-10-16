#!/usr/bin/env python3
"""Record installed versions and errors without starting/stopping lab services."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import sys


def capture(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return {"command": args, "returncode": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": args, "error": str(exc)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ryu-python", default=str(Path(__file__).resolve().parents[2] / "ryu/ryu-env/bin/python"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"timestamp": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
              "system_python": sys.version, "commands": []}
    commands = [["mn", "--version"], ["ovs-vsctl", "--version"], ["iperf", "--version"],
                ["tcpdump", "--version"], ["tc", "-V"], ["ip", "-V"],
                [args.ryu_python, "-m", "pip", "freeze", "--all"],
                ["git", "status", "--short"], ["git", "diff", "--stat"],
                [sys.executable, "-c", "import matplotlib; print(matplotlib.__version__)"]]
    report["commands"] = [capture(c) for c in commands]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as fh:
        json.dump(report, fh, indent=2)
    print(args.output)


if __name__ == "__main__":
    main()
