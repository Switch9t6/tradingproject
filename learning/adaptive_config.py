import os
import sys
import time
import threading
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from learning.self_learning_engine import get_optimized_scoring_weights

# =============================================================================
# ADAPTIVE CONFIGURATION BRIDGE
# -----------------------------------------------------------------------------
# Single choke-point through which the scanners consume the anti-overfit learned
# thresholds. Values are cached for CACHE_TTL seconds so the scanners never
# re-read / re-parse the learning state file on every candidate evaluation, and
# so the learning engine can't be disrupted by scanner read churn.
# =============================================================================

CACHE_TTL = 60.0

_cache: dict = {"ts": 0.0, "data": None}
_lock = threading.Lock()


def _adaptive() -> dict:
    with _lock:
        now = time.time()
        if _cache["data"] is None or (now - _cache["ts"]) > CACHE_TTL:
            try:
                _cache["data"] = dict(get_optimized_scoring_weights())
            except Exception:
                _cache["data"] = {
                    "min_volume_ratio": 2.0,
                    "score_threshold": 75.0,
                    "take_profit_pct": 0.25,
                    "stop_loss_pct": 0.12,
                }
            _cache["ts"] = now
        return _cache["data"]


def adaptive_score_threshold() -> float:
    """Minimum composite score for a candidate to qualify (75.0 base + learned boost)."""
    return float(_adaptive().get("score_threshold", 75.0))


def adaptive_min_volume_ratio() -> float:
    """Minimum volume-surge ratio required (baseline 2.0; learned value only tightens)."""
    return float(_adaptive().get("min_volume_ratio", 2.0))


def adaptive_take_profit_pct() -> float:
    return float(_adaptive().get("take_profit_pct", 0.25))


def adaptive_stop_loss_pct() -> float:
    return float(_adaptive().get("stop_loss_pct", 0.12))


def invalidate_adaptive_cache() -> None:
    """Force the next read to reload from the learning engine (used after a trade closes)."""
    with _lock:
        _cache["data"] = None
        _cache["ts"] = 0.0
