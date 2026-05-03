"""Add the backend package to sys.path so training scripts can reuse parsers.

Importing this module is enough — `import _paths` at the top of each script.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _REPO_ROOT / "backend"

if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

REPO_ROOT = _REPO_ROOT
TRAINING_DIR = _REPO_ROOT / "training"
SYNTHETIC_DIR = TRAINING_DIR / "data" / "synthetic"
PROCESSED_DIR = TRAINING_DIR / "data" / "processed"
