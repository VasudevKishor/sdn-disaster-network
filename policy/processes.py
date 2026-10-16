"""Stop only a process group created and owned by this session."""
import os
import signal
import subprocess


def stop_group(process):
    try: os.killpg(process.pid,signal.SIGTERM)
    except ProcessLookupError: pass
    try: process.wait(timeout=5)
    except subprocess.TimeoutExpired: pass
    # A CLI child may still be alive after its parent exited. The new-session
    # group is ours; never use a process-name/global kill as cleanup.
    try: os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError: pass
    process.wait(timeout=5)
