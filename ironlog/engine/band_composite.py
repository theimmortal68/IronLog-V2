"""
band_composite.py — pure peak-max band-config search for HT (hand-tension) band setups.

`ht_next_setup` finds the next `(plates, config)` progression step: prefer
raising plates within the current band config (no reconfigure) when that
stays under the bottom-position clamp; otherwise search all subsets of the
band inventory for the setup with the smallest peak strictly above the
current peak (tiebreak: fewest bands). Pure — no DB, no HTTP.

The default bottom-position clamp is 225 lb, matching the validator's
ValidationContext.ht_bottom_clamp (raised 220 -> 225, user decision
2026-07-06) so the generator's advancement ceiling matches what validate()
accepts.
"""
from collections import namedtuple
from itertools import combinations
from typing import List, Optional, Tuple

Band = namedtuple("Band", "id rest peak usable", defaults=(True,))


def config_bottom(plates, config, by_id):
    return plates + sum(by_id[b].rest for b in config)


def config_peak(plates, config, by_id):
    return plates + sum(by_id[b].peak for b in config)


def resolved_band_config(band_config: Optional[list], band_pair_id: Optional[int]) -> Optional[List[int]]:
    """A logged set's band configuration: band_config first, falling back to
    the older singular band_pair_id field. None if unresolvable.

    Pure -- takes plain values, not ORM objects, so it works for both a
    PlannedSet-then-SetLog fallback chain and a plain (band_config,
    band_pair_id) pair."""
    if band_config:
        return list(band_config)
    if band_pair_id is not None:
        return [band_pair_id]
    return None


def ht_performed_floor(plates: float, config: list, felt_peak: float, by_id: dict) -> float:
    """Floor plates up to what's needed to explain a logged felt_peak for the
    same config. Never regresses below the stored plates."""
    implied_plates = felt_peak - sum(by_id[b].peak for b in config)
    return max(plates, implied_plates)


def _all_configs(inventory):
    ids = [b.id for b in inventory if b.usable]
    for k in range(len(ids) + 1):
        for combo in combinations(ids, k):     # each usable band at most once
            yield list(combo)


def ht_next_setup(plates, config, inventory: List[Band], plate_step=5, clamp=225) -> Tuple[float, list]:
    by_id = {b.id: b for b in inventory}     # ALL bands: prices the current config even if retired
    cur_peak = config_peak(plates, config, by_id)
    # 1) prefer raising plates within the current config — only if it uses no
    #    retired band (don't keep loading a band the athlete retired).
    if all(by_id[b].usable for b in config) and config_bottom(plates + plate_step, config, by_id) <= clamp:
        return (plates + plate_step, list(config))
    # 2) search USABLE subsets for the smallest peak strictly above current
    best = None
    for cfg in _all_configs(inventory):
        srest = sum(by_id[b].rest for b in cfg)
        if srest > clamp:
            continue
        max_plates = int((clamp - srest) // plate_step) * plate_step
        p = 0.0
        while p <= max_plates:
            pk = p + sum(by_id[b].peak for b in cfg)
            if pk > cur_peak:
                key = (pk, len(cfg))   # smallest peak, then fewest bands
                if best is None or key < best[0]:
                    best = (key, (p, list(cfg)))
            p += plate_step
    return best[1] if best else (plates, list(config))


def ht_scaled_setup(target_peak: float, inventory: List[Band], plate_step=5, clamp=225) -> Tuple[float, list]:
    """Find the setup whose peak is closest to target_peak under the bottom clamp.

    Pure -- no DB, no HTTP. Mirrors ht_next_setup's exhaustive search, but
    optimizes for closest-to-target instead of smallest-strictly-above.
    Tiebreak: fewest bands.
    """
    by_id = {b.id: b for b in inventory}
    best = None
    for cfg in _all_configs(inventory):
        srest = sum(by_id[b].rest for b in cfg)
        if srest > clamp:
            continue
        max_plates = int((clamp - srest) // plate_step) * plate_step
        p = 0.0
        while p <= max_plates:
            pk = p + sum(by_id[b].peak for b in cfg)
            dist = abs(pk - target_peak)
            key = (dist, len(cfg))
            if best is None or key < best[0]:
                best = (key, (p, list(cfg)))
            p += plate_step
    return best[1] if best else (0.0, [])
