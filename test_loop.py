import subprocess
import time
import os
import signal

def run_test(app):
    print(f"Testing {app}")
    ctrl = subprocess.Popen(["ryu-manager", app], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    try:
        output = subprocess.check_output(["sudo", "python3", "topology/run_network.py"], timeout=30).decode('utf-8')
        drops = [line for line in output.split('\n') if "dropped" in line]
        print(drops[0] if drops else "No drop output")
    except Exception as e:
        print(f"Failed: {e}")
    finally:
        os.system("sudo killall ryu-manager 2>/dev/null")
        os.system("sudo mn -c 2>/dev/null")
    time.sleep(1)

run_test("controller/flow_manager_app.py")
