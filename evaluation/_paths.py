"""Add the backend package to sys.path so evaluation scripts can reuse parsers."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _REPO_ROOT / "backend"

if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

REPO_ROOT = _REPO_ROOT
EVAL_DIR = _REPO_ROOT / "evaluation"
REPORTS_DIR = EVAL_DIR / "reports"
