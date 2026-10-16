"""Run Phase 1 foundation, real workload-process, and controller/wire checks."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.validate_phase2 import main


if __name__ == "__main__":
    sys.exit(main(phase=1))
