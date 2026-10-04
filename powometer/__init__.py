"""POW-O-METER data pipeline.

Rebuilds the station record from raw data (raw/) and configuration
(config/), following docs/DATA_PIPELINE_DESIGN.md. Run with:

    python -m powometer build      # writes out/
    python -m powometer validate   # checks config files
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
CONFIG = ROOT / "config"
OUT = ROOT / "out"
APPROVED = ROOT / "approved"

__all__ = ["ROOT", "RAW", "CONFIG", "OUT", "APPROVED"]
