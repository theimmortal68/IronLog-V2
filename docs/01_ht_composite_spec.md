# Hip Thrust — Composite-Load Model (V2 Engine Spec)

**Lift:** Hip Thrust · **Station:** GMWD hip thrust machine
**Load type:** composite — static plates **+** a matched pair of Rogue Shorty Monster Bands (one per side, accommodating resistance)
**Status:** band peak calibrated to one field data point; band bottom modeled (conservative). RPE-cap exempt, always-progress.

---

## 1. Load definition

Total resistance is position-dependent. Two reference points matter:

- **Bottom** (start of the thrust, hips down) — lowest band tension. **Safety-critical** (see clamp).
- **Peak** (lockout, hips up) — highest band tension. This is the working-max reference.

```
bottom_total = plates + band_bottom
peak_total   = plates + band_peak
```

The band is a **pair** (left + right). All values below are for the pair as mounted; lateral forces cancel by symmetry, vertical resistance adds.

---

## 2. Calibrated band table

Per **pair**, in lb. Anchored to the field point "two #0 + 220 plates felt ~250 at peak" (band peak ≈ 30). Geometric model scaled ×1.15 to match.

| Band pair | Bottom (lb) | Peak (lb) | Confidence |
|---|---|---|---|
| #0 Orange | 14 | **30** | peak = measured anchor |
| #1 Red | 29 | 60 | peak good, bottom modeled |
| #2 Blue | 47 | 100 | peak good, bottom modeled |
| #3 Green | 63 | 133 | peak good, bottom modeled |
| #4 Black | 102 | 217 | both modeled — respect |
| #5 Purple | 151 | 317 | both modeled — respect |

**Trust notes**
- **Peak** is anchored at #0 and carries reasonably across bands — all six share the 12″ resting length and hit the same strain at the top, so the same correction applies.
- **Bottom** has one corroborating point: 220 plates + #0 pair (~234 bottom) flexed the latch, consistent with the #0 pair adding ~14 down low. Still treat as estimates, most cautiously on the heavy bands; firm up via calibration accrual (§6).

---

## 3. Derivation basis (auditable)

Confirmed against Rogue's product guide and held assumptions:

- Resting length **12″** flat → **24″** loop perimeter (`P0`). *(Rogue confirmed.)*
- Ratings measured "at 100% stretch / 24″." *(Rogue confirmed.)* Rating = **felt loop resistance**, not single-strand tension — confirmed by the field point (single-strand would have felt ~270, not 250).
- Triangular mount: pegs 12″ apart; band loops both pegs + horn. Horn-to-peg 15″ (bottom) → 24″ (top). Loop path **42″ → 60″**.
- Strain: **75% at bottom, 150% at peak**. Force model: `F = R · strain · cos(half-angle)`, ×2 for the pair, ×1.15 empirical anchor.
- **Peak : bottom ≈ 2.1×** — geometry-driven, near-invariant to band choice.

---

## 4. Hard constraints (validator — non-negotiable)

1. **Bottom clamp:** `bottom_total ≤ 220 lb` (plates + band-at-bottom). Above this the GMWD lap-bar latch flexes. Validator **rejects** any prescription exceeding it. **Confirmed by field test:** 220 plates + #0 pair = ~234 bottom-total produced observable flex — so the ceiling has near-zero margin (flex seen only ~14 lb over). Sit a few lb under 220; do not treat 220 as a soft target.
2. **Stretch cap:** the top geometry already sits at **2.5× resting length (30″ flat)** — Rogue's stated do-not-exceed stretch. **No progression axis may increase band elongation** (no moving the horn farther, no setup that stretches more). Bigger bands store *more* energy at this same limit, so "more band" is not a safer lever — it's a more violent failure mode.
3. **Wear gate:** high tension-to-length ratio → risk of **sudden** failure. Engine tracks band age / inspection date and prompts a check; replace proactively on any feathering or nicks rather than running to failure.

---

## 5. Progression (three axes)

HT is linear / always-progress (exempt from the RPE cap): push toward ceiling each session, nominal **+5 lb/session at peak**. The agent selects the axis:

1. **Plates** — primary lever for peak. Bounded by the bottom clamp (§4.1). Prefer this for adding peak; adding plate raises bottom and peak equally.
2. **Band size** — adds peak **and** widens the accommodating spread, but consumes bottom budget and raises stored energy at the stretch cap. Use to shape the resistance curve, not as the default peak driver.
3. **Sets/reps (volume bridge)** — used when the next available discrete step (a band swap, or coarse plate availability) would jump peak **> 5 lb**. Bridge the gap with volume until the next clean load step lands within +5 lb.

**Deload:** 2×8 (or 2×12 on Day 5) at ~60% of last working weight, no progression rule.

**Peak reachability under the 220 clamp** (max peak = 220 + band spread): #0 tops at ~236, #1 at ~251 — **neither reaches the 260 peak target.** #2 Blue (≤273), #3 Green (≤290) and up do. So as peak approaches 260, the band must step up to #2+ and load shifts from plate toward band to keep the bottom under the clamp (e.g. peak 260 = Blue pair + 160 plates → 207 bottom).

---

## 6. Calibration accrual (no equipment required)

Each HT session logs `{plates, band_pair, felt_peak}`. Every entry drops an anchor on the curve; over a few band/plate combinations the modeled table firms into a measured one — no scale needed.

- Add a **`felt_peak`** field to HT logging (optional per set, prompted at lockout).
- Each new anchor updates the band table's scaling for that band; values carry a **`calibration_status`** flag (`measured` / `modeled`).
- While a band is `modeled`, the validator applies the bottom clamp with extra margin; once `measured`, the clamp uses the real value.

---

## 7. Data-model implications (V2)

- **HT load entry is composite:** `{ plates_lb, band_pair_id, band_bottom_lb, band_peak_lb, calibration_status }`.
- **Band reference table** as its own small entity: one row per band/pair with bottom, peak, calibration_status, inspection_date.
- **Validator input:** `bottom_total = plates_lb + band_bottom_lb`; reject if `> 220` (apply margin while modeled).
- **Exercise metadata flags on HT:** `composite_load = true`, `always_progress = true`, `rpe_cap_exempt = true`, `stretch_capped = true`, `wear_tracked = true`.

---

## Open items
- [x] ~~Confirm the "220" reference~~ — **resolved: 220 is bottom-total (plates + band), confirmed by observed flex at ~234.**
- [ ] Bottom values uncalibrated — accrue via §6, prioritize the band(s) actually in rotation.
- [ ] Rev Hyper (Scout) follows the same pattern at a 180 cap with set/rep progression above it; spec separately when its calibration is needed.
