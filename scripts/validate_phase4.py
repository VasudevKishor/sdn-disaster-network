"""Preserved Phase 1–3 regressions plus simplified demo offline tests."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.validate_phase2 import main
if __name__=='__main__': sys.exit(main(phase=4))
