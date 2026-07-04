# HT Band-Composite Loading (v0.7) — Design

**Date:** 2026-07-04
**Repos:** server `~/projects/IronLog-V2` (FastAPI/SQLModel) + client `~/projects/IronLog-V2-Client` (Kotlin/Compose)
**Status:** Approved design → spec for implementation planning

## Goal

Model Hip Thrust as **plates + accommodating bands**, where the **peak** effective load (the stimulus at lockout) can exceed the GMWD frame's **220 bottom cap** while the bottom stays under it. Progression at/near the cap comes from adding bands (which raise peak far more than bottom) rather than the interim rep-ladder-at-cap. Deterministic — no AI.

## Effective-load model

Both-sides, on the GMWD weight pegs (no bar; plates ≥ 0):
- **bottom** = `plates + Σ(band rest)` — must stay **≤ 220** (frame-flex safety clamp, already validated).
- **peak** = `plates + Σ(band peak)` — the stimulus the engine maximizes.

A **configuration** is a **subset** of the six-band inventory (each band ≤ 1 — the athlete owns one pair of each), so there are **64 configs** (including the empty set = plates only). Tensions add.

### Band inventory (formula-derived, both-sides)

`rest = rated_per_side × 2`, `peak = rated_per_side × 5` (≈ 2.5× rest — matches the frame geometry). All six `usable`, `calibration_status = MODELED`.

| # | Color | rated/side | rest | peak | max plates (220−rest) |
|---|---|---|---|---|---|
| 0 | Orange | 9 | 18 | 45 | 202 |
| 1 | Red | 18 | 36 | 90 | 184 |
| 2 | Blue | 30 | 60 | 150 | 160 |
| 3 | Green | 40 | 80 | 200 | 140 |
| 4 | Black | 65 | 130 | 325 | 90 |
| 5 | Purple | 95 | 190 | 475 | 30 |

Consistent with the seeded baselines: D2 HT = 180 plates + Orange → bottom 198 / peak 225; D5 = 205 + Orange → bottom 223 / peak 250.

## Scope

| IN | OUT (deferred) |
|---|---|
| `ht_band_config` (band-set) state + prescription | Multi-band felt-peak *auto-refinement* (only single-band configs auto-refine here) |
| Engine peak-max config search (replaces HT rep-ladder-at-cap) | Non-HT band use |
| Assembler prescribes plates + config | The band-inventory calibration *UI* (seed-only + gym felt-peak this chunk) |
| Validator sum-of-rests 220 clamp (extend the existing check) | |
| Client: setup display + reconfigure cue + felt-peak capture | |
| Inventory seed (all six, MODELED) | |

## Data model

- **`BandPair`** (exists): reseed all six from the formula (`bottom_lb`/`peak_lb`, `usable=true`, `MODELED`).
- **`MovementState.ht_band_config`** (new): JSON list of band ids — the current HT configuration (replaces the single `ht_band_pair_id` for HT; keep the old field or migrate it — plan decides). `ht_plates` (exists) holds the plate load.
- **`PlannedSet.band_config`** (new): JSON list of band ids for the prescribed set (alongside the existing `target_plates`/`target_felt_peak`).
- **Migration** `019_ht_band_config.sql`: additive `ADD COLUMN` for the two JSON fields (additive-schema carve-out; parity keystone green).
- **`GroupOut`/`PlannedSetOut` DTO:** surface `band_config` (list) + `target_plates` + `target_felt_peak` (some already present) so the client can render the setup.

## Engine (deterministic peak-max search)

HT stays `RULE_DRIVEN` (RPE-exempt, advances every performed session), but its advancement becomes a **peak step** instead of `+plates`/rep-ladder:

- **Pure helper** `ht_next_setup(current_plates, current_config, inventory, plate_step) -> (plates, config)`: enumerate all feasible `(plates, config)` where `config ⊆ inventory` (each band ≤ 1), `bottom = plates + Σrest ≤ 220`, plates in `plate_step` increments ≥ 0; drop dominated setups; from those with `peak > current_peak`, pick the **smallest peak step** (smoothest progression). If plates can still rise within the current config, that wins (no reconfigure); only when it can't does the search add a band and drop plates.
- This **replaces the HT `RULE_DRIVEN → REP_LADDER-at-cap` handoff** from the progression-engine chunk (the 220 rep-ladder was the interim; band progression is the real model).
- **Option-C alignment:** the peak-max search is the engine's *decision* (pure); it feeds `prospective` at generation and `commit_session` persists the setup at approval — `current_load`/`ht_plates`/`ht_band_config` are never written by `run_analysis`. `run_analysis` only records the earned advance (session performed).
- **`plate_step`:** the HT plate increment (confirm with user; default from the current HT +5 convention).

## Validator (extend the existing gate)

`_check_ht_safety` already emits `HT_BOTTOM_OVER_LIMIT` at `ht_bottom_clamp=220` and `HT_BAND_NOT_REGISTERED`. Update it to compute `bottom = target_plates + Σ(rest of each band in band_config)` (sum the config, not one band) and validate every band id in the config is registered. Keep the clamp + fail-loud behavior.

## Assembler

Prescribe HT as `target_plates` + `band_config` (+ `target_felt_peak` = the config's modeled peak + plates) at the HT slot — resolved via the peak-max search from the movement's current setup.

## Client

- **Setup display:** render the HT prescription as `205 plates + Orange · peak ~250` (plates + band names + modeled peak, "~" while MODELED).
- **Reconfigure cue:** when the config or plate count differs from the prior session (same pattern as the shoe/rest cues), a prominent banner: `⚠ Reconfigure: 166 plates + Orange+Red (was 205 + Orange) · peak ~301`.
- **Felt-peak capture:** during HT logging, capture the athlete's felt-peak per working set (`SetLog.felt_peak` — exists). Display the modeled peak as the reference.
- **Single-band refinement:** when a logged HT config has exactly **one** band, `felt_peak − plates` is that band's observed peak → feed a running estimate into `BandPair.peak_lb` and flip `calibration_status → MEASURED` after N consistent readings. Multi-band configs: capture only (can't isolate individual bands — deferred).

## Testing

- **Server (pure, TDD):** `ht_next_setup` — advances plates within a config until the bottom cap, then adds a band + drops plates for the smallest peak step; never exceeds 220 bottom; respects each-band-once; picks smallest peak increase. Validator sum-of-rests clamp (a 2-band config over 220 fails; a legal one passes). Inventory seed values. Migration parity keystone green. Option-C: `run_analysis` never writes `ht_plates`/`ht_band_config`/`current_load`.
- **Client (unit):** the reconfigure-cue decision (config/plates changed vs prior → banner) and single-band felt-peak → observed-peak math. Build + install.
- **Phone check:** HT shows plates + band + peak; a forced config change shows the reconfigure banner; felt-peak input records.

## Build order

Server-stable-before-client: inventory reseed + `ht_band_config`/`band_config` fields + migration 019 → `ht_next_setup` engine helper + wire into the HT rule (replace rep-ladder-at-cap) → validator sum-rest → assembler prescribes plates+config → DTO → server pytest green → client (setup display + reconfigure cue + felt-peak capture + single-band refinement) → build + install + phone check.

## Global constraints

- Server: NO `from __future__ import annotations`; migration additive/single-statement-or-carve-out + parity keystone; **Option-C two-writer boundary preserved** (engine decides, `commit_session` sole writer of the setup at approval, `run_analysis` bookkeeping only); `engine/` stays pure.
- Client: no new Gradle dependency; `SERVER_BASE_URL` local-uncommitted.
- Independent of the config-seed reconciliation (which lights up the engine generally); this chunk is the HT-specific loading upgrade and can build/merge on its own timeline.
