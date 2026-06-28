"""signature.py — session novelty signature (§7, simple beta form)."""
from typing import Dict, List

_WEIGHTS = {"exercise_set": 0.40, "rep_zone": 0.25, "techniques": 0.20,
            "pattern": 0.10, "order": 0.05}

def compute_signature(slot_movement_ids: List[int], rep_zones: List[str],
                      technique_tags: List[str], patterns: List[str],
                      ordering: List[str]) -> dict:
    return {"exercise_set": sorted(set(slot_movement_ids)),
            "rep_zone": sorted(set(rep_zones)),
            "techniques": sorted(set(technique_tags)),
            "pattern": sorted(set(patterns)),
            "order": list(ordering)}

def _jaccard_distance(a: list, b: list) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    return 1.0 - len(sa & sb) / len(sa | sb)

def _order_distance(a: list, b: list) -> float:
    if not a and not b:
        return 0.0
    n = max(len(a), len(b))
    same = sum(1 for i in range(min(len(a), len(b))) if a[i] == b[i])
    return 1.0 - same / n

def signature_distance(a: dict, b: dict) -> float:
    d = 0.0
    for axis, w in _WEIGHTS.items():
        if axis == "order":
            d += w * _order_distance(a.get(axis, []), b.get(axis, []))
        else:
            d += w * _jaccard_distance(a.get(axis, []), b.get(axis, []))
    return d

def meets_novelty(candidate: dict, recents: List[dict],
                  threshold: float = 0.30) -> bool:
    """Soft (§7): differ >= threshold from EACH recent same-day_role signature."""
    return all(signature_distance(candidate, r) >= threshold for r in recents)
