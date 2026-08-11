# Maintenance Block — Complete Weekly Seed Data

**Block**: Maintenance Block Meso 1
**Version**: Wk 1 D1 execution complete, D2-D6 seed data updated with today's changes
**Duration**: 4 weeks

---

## Session Changes Summary (Today)

Applied to the block design during D1 execution:

```yaml
today_changes:
  - t1_rep_range: 6-8 → 4-6  # applied to all T1 primaries
  - core_coverage: mandatory_every_session
  - core_distribution:
      d1: ab_wheel_rollout  # anti-extension
      d2: ab_trainer_decline_situp  # spine flexion (bodyweight)
      d4: ab_trainer_hanging_leg_raise  # anti-extension + hip flexor
      d5: ab_trainer_russian_twist  # rotation
      d6: abmat_ab_bench_pad_cable_crunch  # spine flexion (specialty pad)
  - d1_rear_delt_dropped: covered by D4 (DB RF) + D6 (Face Pull)
  - d1_t3_pull_up_variant: wide_grip_dead_hang  # Wk 1 executed 4/4/4
  - d4_pull_up_dropped: replaced_with_better_fly_lat_pulldown  # user directive
  - nordic_curl_assist_mechanism: ares_cable_weighted_60lb  # NOT monster bands
  - apex_config_conflicts_resolved:
      - stryker_pad_and_matrix_machine_coexist  # both mounted simultaneously
      - ab_trainer_exclusive  # requires own APEX setup
      - fid_better_fly_exclusive  # requires bench in FID angle, no attachments
```

---

## Weekly Volume Check

Target: ~38 exercises/week (maintenance intent, ~20% cut from prior).

```yaml
volume_by_day:
  d1: 8 exercises  # Bench, Pendlay, Seated OHP, Preacher, Lat Raise, Pull-up, Lat Prayer, Ab Wheel
  d2: 7 exercises  # Belt Squat, Matrix Sissy Squat, Nordic Curl, ATG, Hybrid Board Calf, Cable Tib, Ab Trainer Decline Situp
  d4: 8 exercises  # BTN OHP, Better Fly Lat Pulldown, Stryker CSR, Ab Trainer Hanging Leg Raise, Better Fly Pullover, DB Rear Delt Fly, Lying Tricep Ext, Cable Woodchopper
  d5: 8 exercises  # Kickstand RDL, Nordic Max BSS, Nordic Curl, Better Fly Kickback, Reverse Nordic, Hybrid Board Calf, Better Fly Hip Add/Abd, Ab Trainer Russian Twist
  d6: 9 exercises  # Pull-up, Dips, CG Bench, Better Fly Bicep Curl, Stryker CSR Cables, Better Fly Rear Delt Ext, Face Pull, Better Fly OH Tricep Ext, AbMat Cable Crunch
  
weekly_total: 40 exercises
maintenance_target: ~38 exercises
delta_from_target: +2 (acceptable, D6 weak points day carries higher count)
```

---

## D1 Monday — Upper Push (COMPLETED Wk 1)

### Warmup (5 min)
```yaml
- movement_flow (90s): scap_cars 2x5, floor_slides 5, jump_rope 90s
- activation (60s): prone_y_raise_incline_30 2x12, sa_waiters_carry 1x20s/side
- scapular_prep (60s): wall_slide_to_oh_reach 2x8-10  # NEW for OH stability
- specific_ramp (90s): bench_press [45x5, 95x5, 135x3, 155x2]
```

### T1 — Bench Press (Belle Mere BMF Camber Bar, 21" grip) ✓ COMPLETED
```yaml
equipment: [belle_mere_bmf_camber_bar, apex_bench_flat]
grip: 21_inch  # angled, 1.5" camber
apex_config: D  # flat, no attachment
sets: 3
rep_low: 4  # dropped from 6
rep_high: 6  # dropped from 8
rpe_cap: 8
progression_rule: rpe_8_standard
confirmation_window: 1

WK1_LOCKED: 155 × 3×6 RPE 8
```

### T1b — Pendlay Row (Black Diamond DBD, narrow) ✓ COMPLETED
```yaml
equipment: [black_diamond_dbd]
grip: narrow  # inside shoulder-width
apex_config: none
sets: 3
rep_low: 4
rep_high: 6
rpe_cap: 8
progression_rule: hold_load_strain_constraint
current_load: 170  # HOLD

WK1_LOCKED: 170 × 3×8 (over range, held per strain)
```

### T2 GS — 3 items, 90s rest, 3 rounds (APEX Config A) ✓ COMPLETED
```yaml
apex_config: A  # Stryker Pad + Matrix Machine coexist

exercises:
  - stryker_pad_seated_ohp:
      equipment: [stryker_pad, apex_bench, mx100_dbs]
      sets: 3
      rep_low: 8
      rep_high: 12
      rpe_cap: 8
      progression_rule: rpe_8_standard
      confirmation_window: 2
      WK1_LOCKED: 65 × 3×12 (possibly RPE 6-7, verify Wk 2)
  
  - matrix_machine_preacher_curl:
      equipment: [matrix_machine, apex_bench, kyoto_ez_curl_bar]
      grip: narrow_inside
      sets: 3
      rep_low: 8
      rep_high: 12
      rpe_cap: 8
      progression_rule: rpe_8_standard
      confirmation_window: 2
      WK1_LOCKED: 55 × 3×12 RPE 8
  
  - better_fly_standing_lateral_raise:
      equipment: [better_fly, ares_cable, ares_low_pulley]
      sets: 3
      rep_low: 10
      rep_high: 15
      rpe_cap: 8
      progression_rule: rpe_8_standard
      confirmation_window: 2
      WK1_LOCKED: 20 × 3×12 RPE 8
```

### T3 GS — 3 items, 75s rest, 3 rounds (No APEX use) ✓ COMPLETED
```yaml
exercises:
  - pull_up_d1_wide_grip:
      equipment: [pull_up_bar]
      grip: wide  # dead hang, unassisted
      sets: 3
      rep_low: 4  # calibrated to variant
      rep_high: 6
      rpe_cap: 8
      progression_rule: pull_up_rolling_max
      WK1_LOCKED: 4/4/4 unassisted dead hang wide-grip
      variant_notes: |
        NEW variant for D1 (was assisted narrow-grip in prior version).
        Wide grip = harder than standard; 4 reps consistent baseline.
        Progression: 5+ reps Set 1 → advance rep target.
  
  - lat_prayer:
      equipment: [ares_cable, ares_dual_pulleys]
      sets: 3
      rep_low: 10
      rep_high: 15
      rpe_cap: 8
      progression_rule: rpe_8_standard
      confirmation_window: 2
      WK1_LOCKED: 70 × 3×12 (RPE 6-7, still too easy — significant under-load)
      wk2_action: JUMP_TO_85-95_LB  # significant progression to reach RPE 8
  
  - ab_wheel_rollout:
      equipment: [ab_wheel]
      sets: 3
      rep_low: 8
      rep_high: 12
      rpe_cap: 8
      progression_rule: rep_ladder_at_cap  # bodyweight
      core_pattern: anti_extension
      WK1_LOCKED: 3×8 bodyweight
      wk2_target: 3×9-10 bodyweight
```

### Finisher — Jump Rope EMOM ✓ COMPLETED
```yaml
type: EMOM
duration: 6_min
exercise: jump_rope
rope: crossrope_quarter_lb
WK1_STATUS: completed
notes: work_seconds_per_minute_not_logged  # future sessions log 30-40s/min
```

### Z2 — SKIPPED Wk 1
```yaml
duration: 15_min  # scheduled
WK1_STATUS: SKIPPED
reason: session_ran_long_from_new_movement_testing
wk2_action: resume_normal_z2_protocol
```

### D1 Wk 1 Complete Session Data
```yaml
d1_wk1_baselines_final:
  t1_bench_press: 155 × 3×6 RPE 8
  t1b_pendlay_row: 170 × 3×8 (held)
  t2_stryker_seated_ohp: 65 × 3×12 (verify RPE 8 next session)
  t2_matrix_preacher_curl: 55 × 3×12 RPE 8
  t2_better_fly_lateral_raise: 20 × 3×12 RPE 8
  t3_pull_up_wide_grip: 4/4/4 unassisted dead hang
  t3_lat_prayer: 70 × 3×12 (well below RPE 8, jump to 85-95 Wk 2)
  t3_ab_wheel_rollout: 3×8 bodyweight
  finisher_jump_rope: 6 min completed
  z2: SKIPPED (session ran long)
  session_duration: extended by ~15-20 min due to new movement testing
```

---

## D2 Tuesday — Lower Squat (SEED DATA)

### Warmup (5 min)
```yaml
- movement_flow (90s): cat_cow 5, worlds_greatest 2/side, cossack_squat 3/side
- activation (60s): glute_bridge_2s 1x10, banded_clamshell 1x10/side
- specific_ramp (2-2.5 min): belt_squat [45x5, 135x3, 185x2, 225x1]
```

### T1 — Belt Squat
```yaml
equipment_option_a: [hyper_pro, fa_belt_squat_attachment]  # current, 260 pin cap
equipment_option_b: [hybrid_board]  # NEW test
apex_config: none
sets: 3
rep_low: 4  # dropped from 6
rep_high: 6  # dropped from 8
rpe_cap: 8
progression_rule: rep_ladder_at_cap  # if at 260 Hyper Pro cap
wk1_action: test_both_platforms
wk1_test_notes: |
  Compare Hybrid Board vs Hyper Pro belt squat.
  Test factors: loading ceiling, foot position, ROM, comfort.
  Winner = D2 T1 primary for the block.
current_load: 260  # Hyper Pro baseline
spine_safety: offloaded  # safe for strain
```

### T2 GS — 2-item pair, 90s rest, 3 rounds
```yaml
apex_config: none_or_config_C_for_nordic  # depends on Nordic Max placement

exercises:
  - matrix_machine_sissy_squat:
      equipment: [matrix_machine, apex_bench]
      apex_config: A_matrix  # need to swap if Nordic Curl uses Nordic Max
      sets: 3
      rep_low: 8
      rep_high: 12
      rpe_cap: 8
      progression_rule: rpe_8_standard
      confirmation_window: 2
      current_load: null  # bodyweight first, add DBs if easy
      wk1_calibration_estimate: bodyweight_max_reps
      aspect: [hypertrophy, athleticism]
      knee_health_note: sissy_squat_trains_vmo_deep_knee_flexion
  
  - nordic_curl_max_d2:  # UPDATED — Ares assist, NOT bands
      equipment: [apex_bench, nordic_max_attachment, ares_cable, sled_harness_or_strap]
      apex_config: nordic_max_attachment  # own config
      assist_mechanism: ares_cable_weighted  # CHANGED from monster_bands
      current_assist_load: 60  # lb, LOCKED from today's testing
      assist_attach_point: upper_body  # shoulder/chest, not hip
      sets: 3
      rep_low: 6
      rep_high: 8
      rpe_cap: 8
      progression_rule: assistance_reduction
      confirmation_window: 2
      assist_ladder: [60, 55, 50, 45, 40, 35, 30, 25, 20, 15, 10, 5, 0]
      # Terminal state = unassisted BW Nordic
      wk1_baseline: 60_lb_ares_assist
      notes: |
        Ares cable at 60 lb — quantifiable, constant tension.
        NOT monster bands (mechanically different, band recommendation superseded).
        Attach point: chest/shoulder height (NOT low back — proved inefficient in testing).
        Independent load track from D5.

apex_config_note: |
  If Nordic Max attachment can coexist with Matrix Machine (both use APEX), 
  T2 works as pair. If exclusive, split into T2 (Sissy) + T3 add (Nordic Curl).
  User to verify Nordic Max/Matrix compatibility.
```

### T3 GS — 3 items, 60s rest, 3 rounds
```yaml
apex_config: matches_t2_or_swap

exercises:
  - atg_split_squat:
      equipment: [mx100_dbs, utility_seat]
      elevation: 16_inches
      sets: 3
      rep_low: 8
      rep_high: 12
      rpe_cap: 8
      unilateral: true
      progression_rule: rpe_8_standard
      current_load: 30  # per hand
      ankle_mobility_note: at_edge_hold_elevation
  
  - hybrid_board_calf_raise_d2:
      equipment: [hybrid_board]
      sets: 3
      rep_low: 10
      rep_high: 15
      rpe_cap: 8
      progression_rule: rpe_8_standard
      wk1_action: test_loadability
  
  - cable_tib_raise_d2:
      equipment: [ares_low_pulley, ankle_strap]
      sets: 3
      rep_low: 10
      rep_high: 15
      rpe_cap: 8
      progression_rule: rpe_8_standard
      current_load: 25
```

### T4 straight — Ab Trainer Decline Sit-up (core)
```yaml
apex_config: B  # Ab Trainer requires exclusive setup (swap from Matrix)
exercise: ab_trainer_decline_situp
equipment: [ab_trainer, apex_bench]
sets: 3
rep_low: 10
rep_high: 15
rpe_cap: 8
progression_rule: rep_ladder_at_cap  # bodyweight first, add weight later
current_load: null  # bodyweight
wk1_calibration: bodyweight_max_reps
core_pattern: spine_flexion_bodyweight
notes: |
  D2 core requirement.
  Bodyweight first. Add plate on chest if 3×15 clean.
```

### Finisher
```yaml
type: EMOM
duration: 6_min
exercise: sled_push
location: dreadmill
resistance_level: 8
work_seconds_per_minute: 30-40
spine_safety: safe  # upright push
```

### Z2
```yaml
duration: 15_min
adaptive_pace: true
recovery_gap: 3_min
```

---

## D3 Wednesday — REST

```yaml
type: full_rest
optional: neighborhood_walk, saunabox
```

---

## D4 Thursday — Upper Pull + Vertical Press (SEED DATA — Pull-up dropped)

### Warmup (5 min)
```yaml
- movement_flow (90s): scapular_pulls 2x5, open_book 5/side, jump_rope 90s
- activation (60s): prone_y_raise_incline_30 2x12, sa_waiters_carry 1x20s/side
- scapular_prep (60s): wall_slide_to_oh_reach 2x8-10  # OH stability
- specific_ramp (90s): btn_ohp [45x5, 65x5, 85x3, 95x2]
```

### T1 — Seated BTN OHP (Black Diamond DBD)
```yaml
equipment: [black_diamond_dbd, apex_bench_upright]
apex_config: D  # flat/upright, no attachment
sets: 3
rep_low: 4  # dropped from 6
rep_high: 6  # dropped from 8
rpe_cap: 8
progression_rule: rpe_8_standard
confirmation_window: 1
wk1_action: ramp_session_find_rpe_8
wk1_ramp: [45x5, 65x5, 85x3, working_sets_to_rpe_8]
aspect: [strength, stability]
vertical_press_exposure: true
form_safety_notes: |
  - Test shoulder mobility first (broomstick BTN + elbows below shoulders, no pain)
  - Bar to base of neck (upper traps), not lower spine, not tapping neck
  - Elbows below shoulders throughout ROM
  - Neutral head/chin, don't push head forward
  - Stop if impingement signal (pinching, sharp pain, dizziness)
  - Full seated position on APEX Bench upright (reduces trunk stability demand)
  - Swap to front seated OHP that session if BTN feels off
```

### T1b — Better Fly Lat Pulldown (REPLACES Pull-up)
```yaml
equipment: [better_fly, ares_cable, ares_high_pulley]
apex_config: none
sets: 3
rep_low: 6
rep_high: 8
rpe_cap: 8
progression_rule: rpe_8_standard
confirmation_window: 1
load_type: cable
aspect: [strength, hypertrophy]
wk1_calibration_estimate: 60-80  # test to find RPE 8

replaces_this_block: pull_up_d4  # dropped per user directive

pull_up_weekly_frequency:
  d1_t3: pull_up_wide_grip_deadhang  # kept
  d6_gs1: pull_up_weekly_max_tracker  # kept
  d4_t1b: better_fly_lat_pulldown  # was pull_up, now dropped
  total: 2×/week direct pull-up (down from 3×/week)

notes: |
  Better Fly cuff on elbows, cable at high pulley.
  Grip-free vertical pull isolation.
  Cable version provides continuous tension throughout ROM.
```

### T2 GS — 3 items, 90s rest, 3 rounds
```yaml
apex_config: B  # Ab Trainer for Hanging Leg Raise mid-tier

exercises:
  - stryker_pad_csr_barbell:
      equipment: [stryker_pad, apex_bench, black_diamond_dbd]
      apex_config: needs_stryker_but_ab_trainer_active
      # NOTE: Stryker Pad + Ab Trainer incompatible per D1 finding.
      # Option: swap Stryker/Ab Trainer between rounds, or restructure T2.
      sets: 3
      rep_low: 8
      rep_high: 12
      rpe_cap: 8
      progression_rule: rpe_8_standard
      confirmation_window: 2
      wk1_calibration_estimate: 95-115
      strain_safety: chest_supported_offloads_lower_back
  
  - ab_trainer_hanging_leg_raise:
      equipment: [ab_trainer, apex_bench]
      apex_config: B
      sets: 3
      rep_low: 8
      rep_high: 12
      rpe_cap: 8
      progression_rule: rep_ladder_at_cap  # bodyweight
      current_load: null
      core_pattern: anti_extension_hip_flexion
  
  - better_fly_cable_pullover:
      equipment: [better_fly, ares_cable, ares_mid_pulley]
      apex_config: none
      sets: 3
      rep_low: 10
      rep_high: 15
      rpe_cap: 8
      progression_rule: rpe_8_standard
      confirmation_window: 2
      wk1_calibration_estimate: 40-45
      load_type: cable

apex_conflict_flag: |
  Stryker Pad + Ab Trainer cannot coexist on APEX Bench.
  T2 GS as designed may not work as pure giant set.
  Options for Wk 1 execution:
    A. Run T2 as 3 straight-set blocks (like D1 T4 modification)
    B. Restructure: split Stryker and Ab Trainer across separate tiers
    C. Configure GS to alternate rounds (setup change is de facto rest)
  User to test tonight/Thursday and decide.
```

### T3 GS — 3 items, 75s rest, 3 rounds
```yaml
apex_config: C_or_flat_for_lying_tricep_ext

exercises:
  - db_rear_delt_fly:
      equipment: [mx100_dbs, apex_bench_incline]
      apex_config: C  # bench in incline angle
      sets: 3
      rep_low: 10
      rep_high: 15
      rpe_cap: 8
      progression_rule: rpe_8_standard
      current_load: 10  # per hand
  
  - lying_tricep_extension_camber_7:
      equipment: [belle_mere_bmf_camber_bar, apex_bench_flat]
      grip: 7_inch  # neutral, tricep-focused
      apex_config: D  # flat
      # NOTE: swap bench angle between Rear Delt Fly (incline) and Tricep Ext (flat)
      sets: 3
      rep_low: 8
      rep_high: 12
      rpe_cap: 8
      progression_rule: rpe_8_standard
      confirmation_window: 2
      wk1_calibration_estimate: 60
  
  - cable_woodchopper:
      equipment: [ares_high_pulley, puretorque_pro]
      apex_config: none
      direction: high_to_low
      sets: 3
      rep_low: 8
      rep_high: 12
      rpe_cap: 8
      unilateral: true
      progression_rule: rpe_8_standard
      current_load: 30

apex_swap_note: |
  T3 GS requires bench angle change between Rear Delt Fly (incline) and Tricep Ext (flat).
  Could run as straight sets to avoid mid-round bench angle changes.
```

### Finisher — Jump Rope (replaces Sandbag)
```yaml
type: EMOM
duration: 6_min
exercise: jump_rope
rope: crossrope_quarter_lb
work_seconds_per_minute: 30-40
replaces_this_block: sandbag_load
reason: strain_safety
```

### Z2
```yaml
duration: 15_min
adaptive_pace: true
recovery_gap: 5_min  # longer gap after finisher
```

---

## D5 Friday — Lower Hinge (SEED DATA)

### Warmup (5 min)
```yaml
- movement_flow (90s): cat_cow 5, worlds_greatest 2/side, dead_bug 5/side
- activation (60s): glute_bridge_2s 1x10, banded_clamshell 1x10/side
- specific_ramp: kickstand_rdl_light_ramp
```

### T1 — Kickstand RDL (MX100 DBs)
```yaml
equipment: [mx100_dbs]
apex_config: none
sets: 3
rep_low: 4  # dropped from 6
rep_high: 6  # dropped from 8
rpe_cap: 8
unilateral: true
progression_rule: rpe_8_standard
confirmation_window: 1
wk1_calibration_estimate: 35-40  # per hand
rest_between_sides: 60-90s
rest_between_rounds: 150s
setup: b_stance_front_foot_flat_back_foot_on_ball_for_balance
strain_safety: unilateral_reduces_spinal_load_50pct
replaces_this_block: rdl_bilateral
```

### T2 GS — 3 items, 90s rest, 3 rounds
```yaml
exercises:
  - nordic_max_bulgarian_split_squat:
      equipment: [apex_bench, nordic_max_attachment, mx100_dbs]
      apex_config: nordic_max
      sets: 3
      rep_low: 8
      rep_high: 12
      rpe_cap: 8
      unilateral: true
      progression_rule: rpe_8_standard
      confirmation_window: 2
      wk1_calibration_estimate: 25-30  # per hand
  
  - nordic_curl_max_d5:
      equipment: [apex_bench, nordic_max_attachment, ares_cable, sled_harness_or_strap]
      apex_config: nordic_max
      assist_mechanism: ares_cable_weighted  # SAME as D2
      current_assist_load: 60  # LOCKED, same starting point as D2
      assist_attach_point: upper_body
      sets: 3
      rep_low: 6
      rep_high: 8
      rpe_cap: 8
      progression_rule: assistance_reduction
      confirmation_window: 2
      assist_ladder: [60, 55, 50, 45, 40, 35, 30, 25, 20, 15, 10, 5, 0]
      independent_track: from_d2_nordic_curl_max
  
  - better_fly_kickback:
      equipment: [better_fly, ares_cable, ares_low_pulley]
      apex_config: none
      sets: 3
      rep_low: 10
      rep_high: 15
      rpe_cap: 8
      unilateral: true
      progression_rule: rpe_8_standard
      confirmation_window: 2
      wk1_calibration_estimate: 20-30
      load_type: cable
      strain_safety: glute_isolation_no_spinal_load
```

### T3 GS — 3 items, 60s rest, 3 rounds
```yaml
exercises:
  - reverse_nordic_d5:
      equipment: [hyper_pro, shorty_monster_bands]
      apex_config: none
      sets: 3
      rep_low: 8
      rep_high: 12
      rpe_cap: 8
      progression_rule: assistance_reduction
      current_assist_level: 20  # lb bands
      assist_ladder: [20, 15, 10, 5, 0]
  
  - hybrid_board_calf_raise_d5:
      equipment: [hybrid_board]
      apex_config: none
      sets: 3
      rep_low: 10
      rep_high: 15
      rpe_cap: 8
      progression_rule: rpe_8_standard
      independent_track: from_d2_hybrid_board_calf
  
  - better_fly_hip_adduction:  # rotate with abduction weekly
      equipment: [better_fly, ares_cable]
      apex_config: none
      sets: 3
      rep_low: 10
      rep_high: 15
      rpe_cap: 8
      unilateral: true
      progression_rule: rpe_8_standard
      confirmation_window: 2
      wk1_calibration_estimate: 15-25
      rotation_partner: better_fly_hip_abduction
```

### T4 straight — Ab Trainer Russian Twist (core)
```yaml
apex_config: B  # Ab Trainer setup
exercise: ab_trainer_russian_twist
equipment: [ab_trainer, apex_bench]
sets: 3
rep_low: 10
rep_high: 15  # per side counted as 1
rpe_cap: 8
progression_rule: rep_ladder_at_cap  # bodyweight then add DB
current_load: null
core_pattern: rotation
notes: |
  D5 core requirement — rotational pattern.
  Differs from Cable Woodchopper (D4) in mechanics (Ab Trainer decline vs standing cable).
```

### Finisher — Heavy Farmer Carry (unchanged, spine-safe)
```yaml
type: EMOM
duration: 6_min
exercise: heavy_farmer_carry
weight: 55  # per hand
work: 40s
rest: 20s
location: dreadmill
spine_safety: upright_walk_minimal_stress
```

### Z2
```yaml
duration: 15_min
adaptive_pace: true
recovery_gap: 3_min
```

---

## D6 Saturday — Weak Points + Isolation (SEED DATA)

### Warmup (3-4 min abbreviated)
```yaml
- movement_flow: scap_cars, open_book, banded_pull_apart 15
- activation: prone_y_raise 1x12
```

### GS1 — 3 items, 90s rest, 3 rounds
```yaml
apex_config: D  # flat for CG Bench

exercises:
  - pull_up_d6:
      equipment: [pull_up_bar, mingmc_sling, red_band]
      apex_config: none
      sets: 3
      rep_low: 5
      rep_high: 8
      rpe_cap: 8
      progression_rule: pull_up_rolling_max
      protocol: weekly_max_tracker  # Set 1 unassisted max
      current_baseline: 7_unassisted_set_1
  
  - dips:
      equipment: [andreoni_bar, multifunction_tower, ares_cable]
      apex_config: none
      load_type: cable
      sets: 3
      rep_low: 8
      rep_high: 12
      rpe_cap: 8
      progression_rule: rpe_8_standard
      confirmation_window: 2
      current_load: 150
  
  - close_grip_bench_camber_14:
      equipment: [belle_mere_bmf_camber_bar, apex_bench_flat]
      grip: 14_inch  # angled close-grip
      apex_config: D
      sets: 3
      rep_low: 4  # T1-like rep range for CG Bench compound
      rep_high: 6
      rpe_cap: 8
      progression_rule: rpe_8_standard
      confirmation_window: 1
      wk1_calibration_estimate: 135-145
```

### GS2 — 3 items, 90s rest, 3 rounds
```yaml
apex_config: A  # Stryker Pad (Matrix not needed here)

exercises:
  - better_fly_cable_bicep_curl:
      equipment: [better_fly, ares_cable]
      apex_config: none
      sets: 3
      rep_low: 10
      rep_high: 15
      rpe_cap: 8
      progression_rule: rpe_8_standard
      confirmation_window: 2
      wk1_calibration_estimate: 30-40
      load_type: cable
      second_bicep_exposure: true
  
  - stryker_pad_csr_cables:
      equipment: [stryker_pad, apex_bench, ares_cable]
      apex_config: A_stryker
      sets: 3
      rep_low: 8
      rep_high: 12
      rpe_cap: 8
      progression_rule: rpe_8_standard
      confirmation_window: 2
      wk1_calibration_estimate: 60-80
      differs_from_d4: cable_resistance_curve
  
  - better_fly_rear_delt_ext:
      equipment: [better_fly, ares_cable]
      apex_config: none
      sets: 3
      rep_low: 10
      rep_high: 15
      rpe_cap: 8
      progression_rule: rpe_8_standard
      confirmation_window: 2
      wk1_calibration_estimate: 15-20
```

### GS3 — 3 items, 60s rest, 3 rounds
```yaml
apex_config: B_or_variant  # for AbMat cable crunch

exercises:
  - face_pull:
      equipment: [ares_high_pulley, rope_attachment]
      apex_config: none
      sets: 3
      rep_low: 10
      rep_high: 15
      rpe_cap: 8
      progression_rule: rpe_8_standard
      current_load: 30
  
  - better_fly_oh_tricep_ext:
      equipment: [better_fly, ares_cable, ares_high_pulley]
      apex_config: none
      sets: 3
      rep_low: 8
      rep_high: 12
      rpe_cap: 8
      progression_rule: rpe_8_standard
      confirmation_window: 2
      wk1_calibration_estimate: 30-40
      vertical_press_exposure: true  # OH endurance + stability
  
  - abmat_ab_bench_pad_cable_crunch:
      equipment: [abmat_ab_bench_pad, ares_cable, ab_trainer]
      apex_config: B_ab_trainer
      sets: 3
      rep_low: 10
      rep_high: 15
      rpe_cap: 8
      progression_rule: rpe_8_standard
      confirmation_window: 2
      wk1_calibration_estimate: 40-60
      core_pattern: spine_flexion_specialty_pad
```

### Finisher — Jump Rope
```yaml
type: EMOM
duration: 6_min
exercise: jump_rope
rope: crossrope_quarter_lb
work_seconds_per_minute: 35
```

### Z2
```yaml
duration: 15_min
adaptive_pace: true
```

---

## D7 Sunday — REST

```yaml
type: full_rest
```

---

## Key Nordic Curl Update — Ares Weighted Assist

```yaml
nordic_curl_max_setup:
  attachment: nordic_max_on_apex_bench  # foot anchor
  assist_mechanism: ares_cable_weighted  # CHANGED — was: monster_bands_overhead
  current_assist_load: 60  # lb, LOCKED both D2 and D5
  attach_point: upper_body  # chest or shoulder height, NOT hip/low back
  attach_hardware: sled_harness_or_chest_strap
  
progression:
  ladder: [60, 55, 50, 45, 40, 35, 30, 25, 20, 15, 10, 5, 0]
  decrement: 5_lb  # standard step
  confirmation: 2_sessions_clean_at_rpe_8_before_reducing_assist
  terminal_state: unassisted_bodyweight_nordic

benefits_of_cable_over_bands:
  - quantifiable_load_progression (5 lb decrements vs band swaps)
  - constant_tension_predictable
  - clean_setup_no_band_swap_time
  - independent_left_right_symmetry

tradeoffs:
  - constant_tension_doesnt_match_nordic_strength_curve (bands would be biomechanically better)
  - accept_this_tradeoff_for_progression_tracking_precision

setup_evolution_log:
  test_1: monster_bands_overhead  # recommended by Claude, superseded
  test_2: ares_sled_harness_low_back_90lb  # inefficient, too much load
  test_3_final: ares_cable_upper_body_60lb  # LOCKED — right attach point + right load
```

---

## Progression Priorities Wk 2

```yaml
significant_progressions_needed:
  - d1_lat_prayer: 70 → 85-95 lb JUMP  # RPE 6-7 signal, significant under-load
  - d1_stryker_seated_ohp: verify RPE 8 at 65 lb OR advance to 70

standard_progressions:
  - d1_bench: 155 confirmation OR 160
  - d1_pendlay: HOLD 170 (strain)
  - d1_preacher_curl: 55 → 60 (2-session confirmation)
  - d1_lateral_raise: 20 → 22.5 (2-session confirmation)
  - d1_pull_up_wide_grip: attempt 5+ Set 1
  - d1_ab_wheel: 3×8 → 3×9-10

new_movement_calibrations:
  - d2_belt_squat: test Hybrid Board vs Hyper Pro
  - d2_matrix_sissy_squat: calibrate bodyweight
  - d2_nordic_curl_max: 60 lb Ares assist, hit RPE 8
  - d2_hybrid_board_calf: test loadability
  - d2_ab_trainer_decline_situp: calibrate bodyweight
  - d4_seated_btn_ohp: RAMP SESSION to find RPE 8
  - d4_better_fly_lat_pulldown: calibrate ~60-80 lb
  - d4_stryker_csr_barbell: calibrate ~95-115
  - d4_ab_trainer_hanging_leg_raise: bodyweight max reps
  - d4_better_fly_pullover: calibrate ~40-45
  - d4_lying_tricep_ext_camber_7: calibrate ~60
  - d5_kickstand_rdl: calibrate ~35-40 per hand DBs
  - d5_nordic_max_bss: calibrate ~25-30 per hand
  - d5_nordic_curl_max: 60 lb Ares assist
  - d5_better_fly_kickback: calibrate ~20-30
  - d5_hybrid_board_calf_d5: calibrate
  - d5_better_fly_hip_adduction: calibrate ~15-25
  - d5_ab_trainer_russian_twist: bodyweight test
  - d6_close_grip_bench_camber_14: calibrate ~135-145
  - d6_better_fly_bicep_curl: calibrate ~30-40
  - d6_stryker_csr_cables: calibrate ~60-80
  - d6_better_fly_rear_delt_ext: calibrate ~15-20
  - d6_better_fly_oh_tricep_ext: calibrate ~30-40
  - d6_abmat_ab_bench_pad_cable_crunch: calibrate ~40-60
```

---

## Critical Design Notes

**Volume**: 40 exercises weekly = maintenance target ~38 (+2 acceptable, D6 weak-points day).

**Core distribution**: 5 different patterns/equipment across 5 days.

**APEX conflicts**: Stryker + Matrix coexist; Ab Trainer requires exclusive setup; FID Better Fly (bench in FID angle) requires exclusive.

**Bar assignments**:
- Belle Mere BMF Camber Bar: D1 Bench (21"), D4 Lying Tricep Ext (7"), D6 CG Bench (14")
- Black Diamond DBD: D1 Pendlay, D4 BTN OHP, D5 backup for Kickstand RDL
- MX100 DBs: primary DB source across all days
- Kyoto EZ Curl: Matrix Preacher Curl only

**Nordic Curl assist locked**: Ares cable, 60 lb, upper body attach point.

**D4 Pull-up removed**: replaced with Better Fly Lat Pulldown. Pull-up frequency drops to 2×/wk (D1, D6).

**Strain constraints active**:
- No Hip Thrust any day
- No bilateral RDL (Kickstand only)
- Pendlay hold at 170
- No KB Swing finisher
- No Sandbag Load finisher
- Seated BTN OHP (not standing)
- Cable Crunches / bodyweight core preferred over heavy weighted sit-ups
