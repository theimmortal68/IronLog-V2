# 130-Movement Library Specification (Task B) — Design

**Date:** 2026-06-27
**Repo:** `~/projects/IronLog-V2`
**Status:** DRAFT — 4 marked blanks pending user input (see §13); everything else banked
**Scope:** Specify the full movement library so the seed import is mechanical. This is a **library-structure design**, not an import task — the xlsx is a verification/decision sheet, not a data source. Once the blanks are filled, the import runs deterministically off this spec (no decisions left), via the user-authorized subagent exception.

---

## 1. Governing definition (the line that decides everything)

**A `Movement` is something the engine prescribes, loads, progresses, or logs.** Mobility/warmup-flow items that have none of those are NOT Movements — they're handled elsewhere (warmups auto-generated; mobility is session-flow). This definition resolved Fork 1's PREP split and governs the whole library.

---

## 2. Source & the seeded set (Fork 1 — Keep? → Status) — BANKED

Source: `docs/exercise_verification.xlsx`, sheet "Verification" (130 data rows). The `Keep?` column maps to `Status` by prefix:

| Keep? | count | → |
|---|---|---|
| blank | 91 | `Status.ACTIVE` |
| `inactive *` | 8 | `Status.INACTIVE` (kept, dormant, eligible for future blocks) |
| `prep *` with a progression mode (Band Pull-Aparts, `protocol`) | 1 | `Status.PREP` |
| `prep (keep, non-progressed)` — 6 pure-mobility (Couch Stretch, Foam Roll ×2, Hip Circles, Patellar Mobilizations, Band Pull-Aparts + Hip Circles) | 6 | **EXCLUDE** (mobility, `prog=none`, no load) |
| `drop *` (auto-ramp warmups, UI artifacts, 7 dups, gear-replaced) | 24 | **EXCLUDE** |

**→ 100 movements seeded** (91 ACTIVE + 8 INACTIVE + 1 PREP); 30 excluded. Rule is prefix-based and robust to the parenthetical variants.

Plus **3 knee movements added beyond the sheet** (§6 — Sissy Squat, Cable Tibialis Raise, Poliquin Step-up — to close the docs/06 §4 frequency gap) → **103 total seeded** (all ACTIVE).

---

## 3. Equipment (Fork 2) — BANKED (one confirm: KLEVA)

The `Equipment` table already exists (seeded from the `EQUIPMENT` constant: name, load_floor, min_step, load_unit). Each Movement carries `equipment_tags` (all bracket codes, split on `+`) + one `load_equipment_id` (the single load-governing FK that drives floor/step).

**Code → Equipment dictionary (2a, banked):**
`PB`→Barbell - Double Black Diamond · `OB`→Barbell - Gladiator WL · `SB`→BMF Camber Bar · `EZ`→Kyoto EZ Curl Bar · `DB`→Dumbbells (MX100) · `FT`→Ares cable (single) · `ANDREONI`→Ares cable (dual) · `GHR`→Hyper Pro belt attach · `HIP_THRUST`→GMWD hip thrust · `REV_HYPER`→Scout reverse hyper · `TOWER`→Pull-up tower · `TUBES`→Tubes · `KB`→Kettlebell.

**Load-bearing vs support rule (2c, banked):** `equipment_tags` = all codes; `load_equipment_id` = the single load-governing code. **Support/attachment codes are tag-only, never load-bearing:** `BENCH`, `UTIL_SEAT`, `D-handle`, `WHEEL`, `BALL`, `FARMER HANDLES`/`FARMER`, **`LM`** (landmine — barbell governs), **`KLEVA`** (T-bar handle — barbell governs; **tag-only, CONFIRMED**). The barbell/DB/cable/machine in the bracket is the load-bearing FK.

**Non-loaded movements (2d, banked):** `BW` (bodyweight), `BAND` (load via BandPair), and conditioning implements `SANDBAG`, `BALL`, `JR` (Jump Rope), `FARMER` → `load_equipment_id = None`, the implement recorded in `equipment_tags`. (Conditioning movements aren't load-progressed; no Equipment-row or FK needed. `KB` keeps its existing Kettlebell row.)

**JR resolved:** = Jump Rope (conditioning), from the movement names. **LM banked tag-only.**

---

## 4. base_name (Fork 3) — BANKED

`base_name` = the Exercise name with the trailing `[…]` bracket stripped (equipment-only strip). Every name is a clean trailing-suffix case (verified: zero odd cases). Grip/width qualifiers are KEPT (e.g. `T-Bar Row - Medium [OB + KLEVA + LM]` → `base_name="T-Bar Row - Medium"`); grouping grip variants is `family`'s job (§7), keeping `base_name` a pure mechanical strip. No-bracket names → `base_name = name`.

---

## 5. progression_mode + scheme (Fork 4) — RESOLVED

**`progression_mode`** — "Progression Type" column → `ProgressionMode`:
- `ladder`→LADDER · `protocol`→PROTOCOL · `conditioning`→CONDITIONING · `assisted (reduce assist)`→ASSISTED · `composite`→COMPOSITE (1:1)
- `accessory (single-session)` (11) → **LADDER** (scheme DOUBLE_PROGRESSION — single-session double-progression: set 3 hits top reps at RPE≤8 → load up next session)
- `rev-hyper` (2) → **LADDER, `cap=180`** (progress reps above the cap)
- `protocol (BW, +load opt)` (1, Dips) → **PROTOCOL** (note: Dips becomes weighted later via dip belt at a rep threshold — PROTOCOL is right *now*, flagged not to calcify)

**`scheme`** (not in sheet — derived rule, BANKED):
- ASSISTED → REP_RATIO · COMPOSITE → STRAIGHT · non-primary LADDER / accessory-single-session → DOUBLE_PROGRESSION · PROTOCOL/CONDITIONING/rev-hyper → STRAIGHT
- **T1 top-set lifts → TOPSET_BACKOFF (RESOLVED).** Exactly these **6**: **Bench Press, Back Squat, Front Squat, Belt Squat, Standing OHP, RDL** (rotating T1 squat slot = Back/Front/Belt; plus Bench/OHP/RDL).
- **Box Squat, Conventional DL, Sumo DL, Bent Over Row → STRAIGHT (CONFIRMED)** — heavy-straight or accessory work, not T1 top-set slots.

Principle: TOPSET_BACKOFF = T1-slot lifts (the rotating squat, bench, OHP, RDL), not every heavy compound.

---

## 6. knee_modality (Fork 6) — RESOLVED (incl. tib/sissy gap closed)

Only knee-prioritized lifts get a modality (rest = `None`):
- `Nordic Curl`, `Nordic Curl - Volume` → **NORDIC**
- `Reverse Nordic Curl` → **KOT** (quad/knee-extension eccentric, knees-forward — the KOT pattern)
- `ATG Split Squat` (+`[BW]`), `ATG Squat Hold` → **KOT**
- `Calf Raise [GHR]` → **None** (plantarflexion, not tibialis)

**GAP CLOSED — three knee movements ADDED (RESOLVED).** docs/06 §4 mandates knee frequencies **tib 2×/wk, sissy 1×/wk**, which the seeded sheet couldn't satisfy (no tib/sissy rows — verified absent from xlsx, V1 `SeedData.kt`, and the V1 repo). The user trains all three; they are added as real Movements (beyond the 100 sheet rows → **103 total**):

| Movement | region | knee_modality | progression_mode / scheme | equipment | load_equipment_id | tags | load_floor | min_step | increment_ladder |
|---|---|---|---|---|---|---|---|---|---|
| **Sissy Squat** | LOWER | **SISSY** | LADDER / DOUBLE_PROGRESSION | added-load (see note) | **None** | `["BW"]` | **0** | 2.5 | `[2.5]` |
| **Cable Tibialis Raise** | LOWER | **TIB** | LADDER / DOUBLE_PROGRESSION | Ares cable (single) | `eq["Ares cable (single)"]` | `["FT"]` | 10 | 2.5 | `[2.5]` |
| **Poliquin Step-up** | LOWER | **KOT** | LADDER / DOUBLE_PROGRESSION | Dumbbells (MX100) | `eq["Dumbbells (MX100)"]` | `["DB"]` | 10 | 2.5 | `[2.5]` |

**Sissy Squat — critical modeling note (single continuous load track):** it is ONE movement with a **continuous total-added-load track from bodyweight (0) upward** — plate held to ~15 lb, then DB/KB above that, but it's the **same total added load, different object**. The implement is an **informational tag, NOT a load-track break**: do **not** split into two movements, and do **not** reset the load track / e1RM history at the plate→DB/KB switch. `load_equipment_id=None` (no single Equipment row governs an added-load-from-zero track), `load_floor=0`, `min_step=2.5` set directly on the movement; the engine tracks one continuous load/e1RM history across the whole BW→plate→DB/KB range. (Modeled like the existing Lateral Raise: LADDER `[2.5]` + DOUBLE_PROGRESSION, but floored at 0.)

These three make §4's tib (2×/wk) and sissy (1×/wk) frequencies **satisfiable by movements the user actually trains** — no phantom-movement false-positives.

---

## 7. family / is_family_anchor / derived_from_id / start_ratio (Fork 5) — RESOLVED

**Pattern:** anchor sets `family` + `is_family_anchor=True`; a ratio-variant sets `derived_from_id` (→ anchor) + `start_ratio`. Grip rotations ride the parent at `start_ratio=1.0` (docs/02 §5a).

**Governing principle (BANKED):** `start_ratio` links are only for movements that progress **by e1RM**. Movements that progress by reps-at-cap (rev-hyper) or are individually calibrated (hinges) get **own baselines** even if pattern-related — don't force a ratio onto a lift whose progression model isn't e1RM.

**Ratio-derived families (docs/02 §5b):**
- **`back_squat`** — anchor Back Squat. Front Squat **0.80**, Box Squat **0.90**.
- **`bench`** — anchor Bench Press. Swiss Bar CG Press **0.90**. (JM Press → standalone accessory, `family=None`, DOUBLE_PROGRESSION — it's a triceps accessory, not a bench-ratio press.)
- **`ohp`** — anchor Standing OHP. Z-Press **0.85**.

**Same-baseline grip groups (§5a, `start_ratio=1.0`, anchor = Medium):**
- `pendlay_row`: Medium (anchor) · Narrow, Wide
- `t_bar_row`: Medium (anchor) · Narrow, Wide
- `ez_curl`: Medium (anchor) · Narrow, Wide *(INACTIVE, still grouped)*

**Other groups:**
- `hip_thrust` (composite, HT spec): Hip Thrust (anchor) · Banded Hip Thrust, Banded BW Hip Thrust *(ratios N/A)*
- `nordic`: Nordic Curl (anchor) · Nordic Curl - Volume *(1.0 volume variant)*
- `reverse_hyper`: Reverse Hyper (anchor) · Light Reverse Hyper

**Banked residue:**
- **Belt Squat → own standalone anchor** (different gear, plate-loaded) — and it's TOPSET_BACKOFF (anchor-ness and scheme are independent).
- **Swiss Bar CG Press → `bench` 0.90; JM Press → standalone accessory.**

**Standalone (`family=None`):** all dial-direct accessories (§5c) — split squats, lunges, goblet, DB presses/raises, all cable accessories, Meadows/Chest-Supported/Seal rows, core, conditioning.

- **Conventional DL / Sumo DL → own baselines (CONFIRMED)** — hinges individually calibrated (Deadlift e1RM tracked independently at 365); no ratio link to RDL.
- **Light Reverse Hyper → own baseline (CONFIRMED)** — rev-hyper is cap-and-reps, not e1RM, so no forced ratio (per the principle above).
(Grip anchor = Medium and accessories-are-standalone: BANKED.)

---

## 8. Remaining defaults (Fork 7) — BANKED

- `band_eligible` → **True** for `BAND`-tag or HT family (band tension part of the load model): Hip Thrust, Banded Hip Thrust, Banded BW Hip Thrust, Band Pull-Aparts. Else default False.
- `rpe_capped` → **True for the T1 TOPSET_BACKOFF primaries** (RPE-8 cap); else False. *(ties to the 6 TOPSET_BACKOFF lifts.)*
- `rpe_cap_exempt` → **True for HT/composite** (load rule-driven, push to ceiling); else False.
- **Assertion (in spec + a test):** a movement is `rpe_capped` XOR `rpe_cap_exempt` — never both.
- `assist_subtype` / `assist_unit` → **default `None`** (follows the existing assisted Pull-up; ASSISTED + REP_RATIO carries the behavior).

---

## 9. Import mechanics

`seed.py` is constant-driven (`EQUIPMENT`, `PHASES`, `BANDS`, `TAXONOMY`, then `Movement(...)`). The import adds a **`MOVEMENTS` data constant** — one entry per seeded movement with every field resolved by this spec — and a loop:
1. First pass: create all Movement rows (anchors and standalone) from `MOVEMENTS`.
2. Second pass: resolve `derived_from_id` links (variant → anchor id) after anchors exist (anchors carry `family`+`is_family_anchor`; variants carry `family-or-derived_from` + `start_ratio`).
The 5 existing hand-written Movement blocks are absorbed into `MOVEMENTS` (no duplication). Equipment-code → `load_equipment_id` resolved via the §3 dictionary against the `eq` lookup. Any equipment referenced but absent from `EQUIPMENT` is added to the `EQUIPMENT` constant (none expected beyond existing rows, given §3 + tag-only handling).

**Generation:** the `MOVEMENTS` constant is produced by applying this spec's rules to the 130 xlsx rows — a mechanical transform once the blanks are filled. Done via the user-authorized subagent exception (opencode non-viable for edits).

---

## 10. Tests (acceptance)

- **All 100 seed cleanly** (`seed.py` runs without error on a fresh DB).
- `/movements` lists the ACTIVE set (the API filters by status as it does today); INACTIVE/PREP present in the table.
- **Status counts:** 91 ACTIVE, 8 INACTIVE, 1 PREP; the 30 excluded are absent.
- **Scheme:** the TOPSET_BACKOFF set is exactly the resolved 6 lifts; no other movement is TOPSET_BACKOFF.
- **Family links resolve:** every `derived_from_id` points to an existing anchor; every ratio-variant has a `start_ratio`; anchors have `is_family_anchor=True`.
- **knee_modality:** ≥1 ACTIVE movement exists for each required modality — NORDIC (Nordic Curl), TIB (Cable Tibialis Raise), SISSY (Sissy Squat), KOT (ATG / Reverse Nordic / Poliquin Step-up) — so docs/06 §4 frequencies (tib 2×, sissy 1×, KOT 2×, Nordic 2×) are satisfiable, not phantom.
- **Sissy Squat continuous track:** `load_floor=0`, `load_equipment_id=None`, single movement (no split at the plate→DB/KB switch).
- **`rpe_capped` XOR `rpe_cap_exempt`** holds for every movement (the §8 assertion as a test).
- **Equipment:** every `load_equipment_id` resolves to an Equipment row; tag-only codes (LM, KLEVA, supports, conditioning implements) never become a `load_equipment_id`.

---

## 11. Out of scope
- MovementState seeding for the new movements (calibration/loads) — separate (the library is definitions; state/calibration is the calibration block's job).
- Pushing to prod's DB — build-and-test-only; reseed-vs-insert into the populated prod DB is a separate deliberate step (the prod DB already has rows).
- v0.6 generation.

---

## 12. Architecture invariants honored
- Definition-vs-state: this spec is the static `Movement` definitions only; state untouched.
- Locked reference data: equipment floors/steps come from the Legend's locked table; not invented.
- The governing definition (§1) keeps the library "everything is genuinely trainable" — no mobility noise for analyzers to special-case.

---

## 13. Blanks status — ALL CLOSED

1. ✅ **Fork 4 TOPSET_BACKOFF subset** — RESOLVED: the 6 (Bench, Back Squat, Front Squat, Belt Squat, OHP, RDL); Box Squat / Conv DL / Sumo DL / Bent Over Row → STRAIGHT.
3. ✅ **KLEVA** — RESOLVED: tag-only attachment. (JR = Jump Rope; LM tag-only.)
4. ✅ **Fork 5 residue** — RESOLVED: Conv DL / Sumo DL own-baselines; Light Reverse Hyper own-baseline.
2. ✅ **tib/sissy GAP — CLOSED.** Three knee movements added (§6): **Sissy Squat** (SISSY, single continuous added-load track from 0, `load_equipment_id=None`), **Cable Tibialis Raise** (TIB, Ares single/FT), **Poliquin Step-up** (KOT, DB — verified absent from the sheet, so added new). All DOUBLE_PROGRESSION. docs/06 §4 frequencies now satisfiable.

**ALL BLANKS CLOSED — zero ⬜ remain. Final movement count: 103 (100 from sheet + 3 knee).**

---

## 14. Approvals

| Item | Status | Date |
|---|---|---|
| Fork 1 — Keep?→Status (100 seeded) + governing definition | banked | 2026-06-27 |
| Fork 2 — equipment mapping (dict, load-bearing rule, non-loaded, LM+KLEVA tag-only, JR=Jump Rope) | banked | 2026-06-27 |
| Fork 3 — base_name equipment-only strip | banked | 2026-06-27 |
| Fork 4 — progression_mode mappings + scheme rule | banked | 2026-06-27 |
| Fork 4 — TOPSET_BACKOFF subset (6; others STRAIGHT) | ✅ resolved | 2026-06-27 |
| Fork 5 — family structure + e1RM-only-ratio principle + residue 1,3,5,6 | banked | 2026-06-27 |
| Fork 5 — residue 2 (DLs own-baseline) + 4 (Light Rev Hyper own-baseline) | ✅ resolved | 2026-06-27 |
| Fork 6 — knee classifications | banked | 2026-06-27 |
| Fork 6 — tib/sissy GAP (3 knee movements added → 103) | ✅ resolved | 2026-06-27 |
| Fork 7 — defaults + capped-XOR-exempt assertion | banked | 2026-06-27 |
| **Spec COMPLETE — zero blanks; 103 movements** | this commit | 2026-06-27 |
| User spec-review gate | pending | — |
| writing-plans → import via subagent exception (build-and-test-only, not prod) | pending | — |
