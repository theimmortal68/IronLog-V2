"""
band_composite.py — pure peak-max band-config search for HT (hand-tension) band setups.

`ht_next_setup` finds the next `(plates, config)` progression step: prefer
raising plates within the current band config (no reconfigure) when that
stays under the bottom-position clamp; otherwise search all subsets of the
band inventory for the setup with the smallest peak strictly above the
current peak (tiebreak: fewest bands). Pure — no DB, no HTTP.
"""
from collections import namedtuple
from itertools import combinations
from typing import List, Tuple

Band = namedtuple("Band", "id rest peak usable", defaults=(True,))


def config_bottom(plates, config, by_id):
    return plates + sum(by_id[b].rest for b in config)


def config_peak(plates, config, by_id):
    return plates + sum(by_id[b].peak for b in config)


def _all_configs(inventory):
    ids = [b.id for b in inventory if b.usable]
    for k in range(len(ids) + 1):
        for combo in combinations(ids, k):     # each usable band at most once
            yield list(combo)


def ht_next_setup(plates, config, inventory: List[Band], plate_step=5, clamp=220) -> Tuple[float, list]:
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
