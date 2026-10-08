"""
Compatibility shim. Date extraction now lives in
backend/civicsignal/detectors/staleness/dates.py (same API, plus ISO dates,
year-wrapping ranges, and an ambiguity flag). The original prototype is kept
in _backup/prototype-2026-09-29/.
"""

import sys
from pathlib import Path

_BACKEND = str(Path(__file__).resolve().parent / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from civicsignal.detectors.staleness.dates import (  # noqa: E402,F401
    MONTH_NAMES,
    MONTH_PATTERN,
    DateClaim,
    extract_date_claims,
)
