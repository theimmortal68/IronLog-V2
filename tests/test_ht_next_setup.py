from ironlog.engine.band_composite import (
    Band, _all_configs, config_bottom, config_peak, ht_next_setup,
    ht_performed_floor, ht_scaled_setup,
)

INV = [Band(0,18,45), Band(1,36,90), Band(2,60,150), Band(3,80,200), Band(4,130,325), Band(5,190,475)]

def test_raise_plates_within_config_when_room():
    # 180 + Orange (bottom 198) -> +5 plates, same config (no reconfigure)
    assert ht_next_setup(180, [0], INV, 5, 220) == (185, [0])

def test_add_band_when_plates_capped():
    # Orange caps at 202 plates (bottom 220). From 202+Orange (peak 247), next needs a reconfigure.
    plates, config = ht_next_setup(202, [0], INV, 5, 220)
    assert (plates + sum(b.peak for b in INV if b.id in config)) > 247   # peak advanced
    assert plates + sum(b.rest for b in INV if b.id in config) <= 220    # legal bottom
    assert len(set(config)) == len(config)                              # each band once

def test_never_exceeds_bottom_clamp():
    plates, config = ht_next_setup(202, [0], INV, 5, 220)
    assert plates + sum(b.rest for b in INV if b.id in config) <= 220

def test_smallest_peak_step():
    # from a capped Orange, the chosen next peak is the least peak strictly greater than current
    plate_step, clamp = 5, 220
    by_id = {b.id: b for b in INV}
    cur_peak = 202 + 45
    plates, config = ht_next_setup(202, [0], INV, plate_step, clamp)
    nxt = plates + sum(b.peak for b in INV if b.id in config)
    assert nxt > cur_peak

    # enumerate every feasible (plates, config) under the same params
    # ht_next_setup uses internally, and confirm `nxt` is truly the smallest
    # feasible peak strictly above cur_peak — not just *a* larger one.
    feasible_peaks = []
    for cfg in _all_configs(INV):
        srest = sum(by_id[b].rest for b in cfg)
        if srest > clamp:
            continue
        max_plates = int((clamp - srest) // plate_step) * plate_step
        p = 0.0
        while p <= max_plates:
            if config_bottom(p, cfg, by_id) <= clamp:
                feasible_peaks.append(config_peak(p, cfg, by_id))
            p += plate_step

    # no feasible setup has a peak strictly between cur_peak and nxt
    assert not any(cur_peak < pk < nxt for pk in feasible_peaks)


def test_band_defaults_usable_true():
    assert Band(0, 18, 45).usable is True


def test_search_skips_retired_band():
    # Red (id 1) retired. From a capped Orange the reconfigure must not pick Red.
    inv = [Band(0, 18, 45), Band(1, 36, 90, False), Band(2, 60, 150),
           Band(3, 80, 200), Band(4, 130, 325), Band(5, 190, 475)]
    plates, config = ht_next_setup(202, [0], inv, 5, 220)
    assert 1 not in config


def test_current_config_with_retired_band_reconfigures_off_it():
    # Orange (id 0) is the current config but is now retired -> skip the
    # raise-plates shortcut, reconfigure to a usable band (drop Orange).
    inv = [Band(0, 18, 45, False), Band(1, 36, 90), Band(2, 60, 150),
           Band(3, 80, 200), Band(4, 130, 325), Band(5, 190, 475)]
    plates, config = ht_next_setup(180, [0], inv, 5, 220)
    assert 0 not in config


def test_all_bands_retired_falls_back_to_plates_only():
    # No usable band -> the only usable config is the empty (plates-only) one.
    inv = [Band(0, 18, 45, False), Band(1, 36, 90, False)]
    plates, config = ht_next_setup(100, [0], inv, 5, 220)
    assert config == []          # no retired band prescribed
    assert plates >= 100


def test_performed_floor_raises_to_implied_plates():
    by_id = {b.id: b for b in INV}
    assert ht_performed_floor(170, [1], 265, by_id) == 175


def test_performed_floor_never_regresses_when_felt_peak_lower():
    by_id = {b.id: b for b in INV}
    assert ht_performed_floor(175, [1], 260, by_id) == 175


def test_performed_floor_is_idempotent_when_felt_peak_matches():
    by_id = {b.id: b for b in INV}
    assert ht_performed_floor(170, [1], 260, by_id) == 170


def test_ht_scaled_setup_real_bands_uses_nearest_peak_then_fewest_bands():
    by_id = {b.id: b for b in INV}

    plates, config = ht_scaled_setup(216, INV)

    assert (plates, config) == (215.0, [])
    assert config_peak(plates, config, by_id) == 215.0
    assert abs(config_peak(plates, config, by_id) - 216) == 1.0

    tied_one_band_setups = [
        (170.0, [0]),  # Orange
        (125.0, [1]),  # Red
        (65.0, [2]),   # Blue
        (15.0, [3]),   # Green
    ]
    for tied_plates, tied_config in tied_one_band_setups:
        assert config_bottom(tied_plates, tied_config, by_id) <= 225
        assert config_peak(tied_plates, tied_config, by_id) == 215.0


def test_ht_scaled_setup_never_exceeds_bottom_clamp():
    by_id = {b.id: b for b in INV}

    for target_peak in range(0, 801):
        plates, config = ht_scaled_setup(float(target_peak), INV)
        assert config_bottom(plates, config, by_id) <= 225


def test_ht_scaled_setup_tiebreak_prefers_plates_only_over_one_band():
    inv = [Band(10, 5, 10)]

    plates, config = ht_scaled_setup(20, inv)

    assert (plates, config) == (20.0, [])
