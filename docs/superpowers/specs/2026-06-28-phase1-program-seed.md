# IronLog V2 — Phase 1 Program Seed Data

> **Role in v0.6:** this is the authoritative data source for the *evolving-seed prior* (spec `2026-06-27-v0.6-generation-design.md` §3A). Task 2 seeds the **main-work** tiers (T1 + giant-set/accessory + knee slots) from this document into the program tables. **Warmups, finishers (EMOM), and Z2 are deferred to v0.7** — they appear here for completeness and for the user to run from this doc during beta, but v0.6 does NOT seed or emit them (beta emits main-work only, with an in-app marker). Working weights are NOT seeded (MovementState owns them). Richer progression rules (single-session, rep-ladder, band/tube-reduction) are an analysis-layer concern, not generation.

## Program Metadata

| Field | Value |
|---|---|
| Program Name | Post-HGC Phase 1 (Pre-APEX Bridge) |
| Phase | P1 Equipment / CUT Body Comp |
| Duration | 4 weeks (~Jul–early Aug 2026) |
| Split | 5-day (3 upper / 2 lower / 2 rest) |
| Goal | Strength maintenance + conditioning + weak-point targeting |
| Body weight target | Trending toward 213 lb |
| Priority Stack | (1) Knee health, (2) Conditioning, (3) Strength, (4) Glute floor maintenance |

## Weekly Schedule

| Day | Session | Focus |
|---|---|---|
| Mon (D1) | Upper Push | Bench primary + horizontal pull + accessories |
| Tue (D2) | Lower A | Belt Squat + HT primary + Nordic + ATG split squat + tib |
| Wed | REST | Optional sauna |
| Thu (D4) | Upper Pull | Pull-up primary + row volume + rear delt |
| Fri (D5) | Lower B | RDL primary + HT + Bulgarian + Poliquin + sissy + tib + calf |
| Sat (D6) | Weak Points | Pull-up max test + accessory giant sets |
| Sun | REST | Optional sauna |

## Knee-frequency placement (the §4 satisfiability check — confirmed)

| Modality | Target | Days | Result |
|---|---|---|---|
| NORDIC | 2× | D2 + D5 | ✓ |
| KOT | 2× | D2 (ATG split squat) + D5 (Poliquin step-up) | ✓ |
| SISSY | 1× | D5 | ✓ |
| TIB | 2× | D2 + D5 | ✓ |
| Calf (direct) | 1× | D5 | ✓ |

> Note vs the earlier draft: tib is on **both** lower days (D2 + D5), satisfying tib 2×. D2's knee block = Nordic + ATG (KOT) + Cable Tib; D5's = Nordic + Poliquin (KOT) + Sissy + Cable Tib + Calf.

---

## Main-work tiers (the v0.6 seed — by day)

Slot `tier_role`: **anchor** (T1, deterministic), **semi** (held across meso, rotate loading not exercise), **free** (LLM may evolve). `pattern` drives the Fork-3 menu filter. Meso-rotation slots noted.

### D1 — Upper Push
| Tier | Slot | Movement (Meso-1 prior) | tier_role | pattern | scheme | reps | RPE cap |
|---|---|---|---|---|---|---|---|
| T1 | d1_t1 | Bench Press [PB] *(meso: straight→BMF 21")* | anchor | bench | TOPSET_BACKOFF | 5-8 | 8 |
| T2 GS | d1_t2a | Pendlay Row Narrow | semi | horizontal_pull | DOUBLE_PROGRESSION | 5-8 | — |
| T2 GS | d1_t2b | Incline DB Press | free | vertical_push | DOUBLE_PROGRESSION | 8-10 | — |
| T2 GS | d1_t2c | Face-Up Incline Knee Raise | free | core | — | 8-12 | — |
| T3 GS | d1_t3a | Pull-up (2-phase) | free | vertical_pull | REP_RATIO | 6-8 | — |
| T3 GS | d1_t3b | Cross-Body Lateral Raise | free | lateral_raise | DOUBLE_PROGRESSION | 10-12 | — |
| T3 GS | d1_t3c | Cross-Body Rear Delt Fly | free | rear_delt | DOUBLE_PROGRESSION | 10-12 | — |
| T4 GS | d1_t4a | Seated Cable Row | semi | horizontal_pull | DOUBLE_PROGRESSION | 10-12 | — |
| T4 GS | d1_t4b | Ab Wheel Rollout | free | core | — | 8-12 | — |
| T4 GS | d1_t4c | Lat Prayer | free | lat | DOUBLE_PROGRESSION | 10-12 | — |

### D2 — Lower A
| Tier | Slot | Movement (Meso-1 prior) | tier_role | pattern / knee | scheme | reps | RPE cap |
|---|---|---|---|---|---|---|---|
| T1 | d2_t1 | **Belt Squat** *(meso-rotation ↔ Back Squat)* | anchor (semi-rotation) | squat | TOPSET_BACKOFF | 5-8 | 8 |
| T1b | d2_t1b | Barbell Hip Thrust (220 cap) | semi | hip_thrust | COMPOSITE | 8 | 8 |
| T2 GS | d2_t2a | Assisted Nordic | — | knee:NORDIC | ASSISTED | 6-10 | — |
| T2 GS | d2_t2b | Scout Reverse Hyper (180 cap) | free | reverse_hyper | REP_AT_CAP | 15-25 | — |
| T3 | d2_t3a | ATG Split Squat | — | knee:KOT | DOUBLE_PROGRESSION | 8-10 | — |
| T3 | d2_t3b | Cable Tib Raise | — | knee:TIB | DOUBLE_PROGRESSION | 12-15 | — |

### D4 — Upper Pull
| Tier | Slot | Movement (Meso-1 prior) | tier_role | pattern | scheme | reps | RPE cap |
|---|---|---|---|---|---|---|---|
| T1 | d4_t1 | Assisted Pull-up (2-phase) | anchor | vertical_pull | REP_RATIO | 6-8 | — |
| T2 GS | d4_t2a | **Meadows Row** *(meso ↔ Pendlay Row)* | semi (rotation) | horizontal_pull | DOUBLE_PROGRESSION | 8-10 | — |
| T2 GS | d4_t2b | Andreoni Cable Pullover | free | lat | DOUBLE_PROGRESSION | 10-12 | — |
| T2 GS | d4_t2c | Face-Up Incline Knee Raise | free | core | — | 8-12 | — |
| T3 GS | d4_t3a | Cross-Body Rear Delt Fly | free | rear_delt | DOUBLE_PROGRESSION | 10-12 | — |
| T3 GS | d4_t3b | **Meadows SA Row** *(meso ↔ SA DB Row)* | free (rotation) | horizontal_pull | DOUBLE_PROGRESSION | 8-10 | — |
| T3 GS | d4_t3c | Dragon Flag | free | core | — | 3-6 | — |

### D5 — Lower B
| Tier | Slot | Movement (Meso-1 prior) | tier_role | pattern / knee | scheme | reps | RPE cap |
|---|---|---|---|---|---|---|---|
| T1 | d5_t1 | **RDL** *(meso-rotation ↔ Staggered RDL)* | anchor (semi-rotation) | rdl | TOPSET_BACKOFF | 4-6 | 8 |
| T1b | d5_t1b | Barbell Hip Thrust (220 cap, independent track) | semi | hip_thrust | COMPOSITE | 8 | 8 |
| T2 GS | d5_t2a | Bulgarian Split Squat | free | lunge | DOUBLE_PROGRESSION | 8-10 | — |
| T2 GS | d5_t2b | Scout Reverse Hyper *(meso bi↔single-leg)* | free (rotation) | reverse_hyper | DOUBLE_PROGRESSION | 12-15 | — |
| T2 GS | d5_t2c | Assisted Nordic (eccentric) | — | knee:NORDIC | ASSISTED | 5-8 | — |
| T3 GS | d5_t3a | Poliquin Step-up | — | knee:KOT | DOUBLE_PROGRESSION | 8-10 | — |
| T3 GS | d5_t3b | Sissy Squat | — | knee:SISSY | DOUBLE_PROGRESSION | 8-12 | — |
| T3 GS | d5_t3c | Cable Tib Raise | — | knee:TIB | DOUBLE_PROGRESSION | 12-15 | — |
| T3 GS | d5_t3d | Hyper Pro Calf Raise | free | calf | DOUBLE_PROGRESSION | 10-15 | — |

### D6 — Weak Points
| Tier | Slot | Movement (Meso-1 prior) | tier_role | pattern | scheme | reps | RPE cap |
|---|---|---|---|---|---|---|---|
| GS1 | d6_g1a | Pull-up (Set 1 unassisted max test) | anchor | vertical_pull | REP_RATIO | 5-8 | — |
| GS1 | d6_g1b | Dips | free | vertical_push | DOUBLE_PROGRESSION | 8-12 | — |
| GS1 | d6_g1c | Hip Thrust (D5 × 0.80, FIXED) | free | hip_thrust | FIXED | 12 | — |
| GS2 | d6_g2a | T-Bar Row Wide | semi | horizontal_pull | DOUBLE_PROGRESSION | 8-10 | — |
| GS2 | d6_g2b | DB Seal Row | free | horizontal_pull | DOUBLE_PROGRESSION | 10-12 | — |
| GS2 | d6_g2c | Lateral Raise | free | lateral_raise | DOUBLE_PROGRESSION | 12-15 | — |
| GS3 | d6_g3a | Cross-Body Rear Delt Fly | free | rear_delt | DOUBLE_PROGRESSION | 12-15 | — |
| GS3 | d6_g3b | Cable V-Bar Pushdown | semi | triceps | SINGLE_SESSION | 8-12 | — |
| GS3 | d6_g3c | Reverse Hyper Recovery | free | reverse_hyper | FIXED | 15-20 | — |

## Meso rotation (Phase 1, 2-week mesos)

| Slot | Meso 1 | Meso 2 |
|---|---|---|
| d1_t1 (bench bar) | straight bar | BMF Pro 21" |
| d2_t1 | Belt Squat | Back Squat |
| d5_t1 | RDL | Staggered RDL |
| d4_t2a | Meadows Bruno | Pendlay Row |
| d4_t3b | Meadows SA Bruno | Single-Arm DB Row |
| d5_t2b | Scout RH bilateral | Scout RH single-leg |

## Phase rules → existing mechanisms / out of scope

| Rule | v0.6 handling |
|---|---|
| HT 220 lb bar cap | validator `ht_bottom_clamp = 220` (exists) |
| RPE 8 cap on Bench/Squat/OHP/RDL | `phase_hard_cap` / PhasePolicy (exists) |
| Scout RH 180 cap | movement `cap` (library) |
| Double / single-session / rep-ladder / band-reduction / tube-reduction progression | **analysis-layer (state-advance), NOT generation** — generation reads `current_load` only |

## NOT seeded by v0.6 (deferred — v0.7 / state / out of scope)

Warmups (movement flow / activation / knee-prep / ramps), finishers (EMOM + power-slot rotation), Z2 protocol, working weights, rep/set logs, e1RM, bodyweight, deload-week specifics, REBUILD-phase modifications.
