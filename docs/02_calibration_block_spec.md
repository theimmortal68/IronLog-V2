# Calibration Block — 2-Week Microcycle (V2 Entry Spec)

**Placement:** this week's deload → **2-week calibration block** (replaces the scheduled Test week) → P1 proper with the engine live.
**Format:** your normal 5-day split, full exercise selection, giant sets, Z2 + knee work all unchanged. Only two things change — intensity sits at the **bottom of the RPE-8 band**, and **every working set is a logged data point** (per-set tap).
**Goal:** flip each lift's inherited (RPE-noisy) e1RM to a clean **measured** baseline, seat the per-set RPE capture, and baseline new/re-specced movements — before the engine gets authority to push load.

> This is **not** a second deload. You already deloaded this week. If sets feel like RPE 5, the taps carry no signal and the two weeks are wasted. When unsure, err **slightly heavy** — the engine is learning what your true 7–8 feels like.

---

## 1. How submaximal sets calibrate

You don't go to failure. A set of `W × R` reps tapped at RPE 7–8 implies reps-in-reserve (RPE 8 ≈ 2 RIR, RPE 7 ≈ 3), so:

```
est. reps-to-failure = R + RIR
e1RM ≈ W × (1 + (R + RIR)/30)      [Epley]
```

Two weekly estimates that agree (within ~5%) → that lift flips to **measured** and the engine takes over. Logging all three sets at one load also teaches the engine your normal **RPE drift** across sets, so it won't later misread a fatigued set-3 as a stall.

---

## 2. Intensity scheme (primaries)

| | Load (% inherited e1RM) | Sets × Reps | RPE target |
|---|---|---|---|
| **Week 1** | ~73–75% | 3 × 5–8 | 7 (full reps, never grinding) |
| **Week 2** | ~78–80% | 3 × 5–8 | 7 → low-8, confirm |

*Worked example, squat (inherited e1RM ~278):* Wk1 ≈ 205 lb, Wk2 ≈ 220 lb. Plug your own current e1RM per lift into the same two percentages.

---

## 3. The five days (template)

Each lifting day, unchanged in structure:
1. **Primary as straight sets (T1) first**, at the calibration load above — this is the measurement, give it focused effort.
2. **Giant sets (3×3)** touched at calibration intensity — one honest tapped set per movement to seat capture.
3. **Z2 15 min + knee work** exactly as normal. Wed/Sun rest + sauna unchanged.

| Day | Primary (calibrated) |
|---|---|
| Upper A | Bench |
| Lower A | Squat (+ HT if scheduled here) |
| Upper B | OHP |
| Lower B | Hinge (RDL or DL) (+ HT) |
| Day 5 | accessory / knee-hip / conditioning + Day-5 HT (2×12 light) |

Accessory *selection* follows your existing program — the only change is intensity + tapping.

---

## 4. Per-lift calibration protocol

| Lift | Tier | Protocol | Exit criterion |
|---|---|---|---|
| Bench, Squat, OHP, Hinge | **Primary** | §2 scheme, T1 straight sets, tap all 3 sets | 2 weekly e1RM estimates within ~5% → **measured** |
| Hip Thrust | **Composite** | Not %e1RM. Dial plates + band under the 220 bottom clamp to an RPE-feel target; log **felt-peak**. See HT composite-load spec. | Repeatable plates+band combo + felt-peak anchor logged |
| Re-specced (reverse Nordic now loaded by weight; anything changed in the verification sweep) | **From scratch** | No valid history — find the RPE-7 load for the rep target, start conservative, ramp | 2 sessions establishing a stable RPE-7 load |
| Accessories / giant-set work | **Light touch** | 1 honest tapped set per movement per session | Capture seated; autoregulates live in P1 |

---

## 5. Variations rotated in — starting weights

**Principle:** a variant's start = **parent's measured e1RM × ratio** (barbell variants) or a **direct RPE-7 dial-in** (DB/cable/small-load). The first tapped set on first exposure overrides the estimate — so **start at the low end; the tap corrects upward fast.** Precision of the ratio doesn't matter, only that it's in the right neighborhood and conservative.

### 5a. Same baseline as parent — no separate calibration
Grip / stance / attachment rotations ride the parent's calibrated load directly; expect RPE to wobble ±~0.5 and let the tap handle it:
- Squat stance width (narrow / medium / wide)
- Bench grip width
- Lat-pulldown grip (wide pronated / medium neutral / close neutral)
- Pendlay-row and T-Bar-row grip widths
- EZ-curl grip; triceps pushdown attachment (rope / V-bar / EZ)

### 5b. Ratio-derived — barbell variants of the primaries (confirm on first exposure)
Start ≈ the listed fraction of the **parent's measured** e1RM. Estimate only; the first tapped set sets the real baseline.

| Variant | Start ≈ | Parent | Note |
|---|---|---|---|
| Front Squat | 80% | Back Squat | typically settles 80–85% |
| Box Squat | 90% | Back Squat | box-height dependent |
| Close-grip / Swiss-bar CG Press | 90% | Bench | |
| Z-Press | 85% | Standing OHP | strict, no leg drive |
| Conventional DL / Sumo DL ↔ RDL | individual | Hinge primary | least reliable to estimate — start conservative, confirm. Direction depends on which is your hinge primary (a full DL is heavier than an RDL; an RDL is lighter than a DL). |

### 5c. DB / small-load variants — dial directly, don't ratio
Incline DB press, seated DB press, goblet/Bulgarian/reverse lunge, DB raises, hammer curl, KB work: just find the RPE-7 load on the first set. Ratioing a per-hand DB off a barbell e1RM is noisier than simply feeling it, and the loads are small enough to dial in one set.

---

## 6. Exit criteria (per lift → `calibration_status`)

- **Primaries:** two weekly e1RM estimates within ~5% → `measured`; engine gets progression authority + the increment ladder. Disagreement >5% → stays `calibrating`, held conservative, third reading early in P1.
- **HT:** flips when a plates+band combo and felt-peak anchor are logged and repeatable under the clamp.
- **Variants:** flip on first tapped exposure — they inherit the parent's measured anchor (5a) or confirm the ratio estimate (5b/5c).

---

## 7. Dependencies / open

- The **definitive variant roster** locks after the verification sweep + the six policy answers — especially: is the hinge primary RDL or conventional DL (drives §5b direction), are curls kept, and the OHP increment (5 vs 2.5).
- Week-1 loads are % of **inherited** e1RM (your current, RPE-noisy numbers). That's expected — replacing them with measured values is the whole job of the block.
