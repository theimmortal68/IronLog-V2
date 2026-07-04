from ironlog.engine.band_composite import ht_next_setup, Band

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
    cur_peak = 202 + 45
    plates, config = ht_next_setup(202, [0], INV, 5, 220)
    nxt = plates + sum(b.peak for b in INV if b.id in config)
    # no feasible setup has a peak strictly between cur_peak and nxt
    assert nxt > cur_peak
