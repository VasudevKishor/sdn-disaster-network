import subprocess
import time
import os

# Start controller
ctrl = subprocess.Popen(["ryu-manager", "controller/flow_manager_app.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)

# Run network and get output
output = subprocess.check_output(["sudo", "python3", "topology/run_network.py"]).decode('utf-8')
print(output)

ctrl.terminate()
