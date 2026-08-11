# IronLog V2 — Maintenance Block Seed Data

**Block name**: Maintenance Block Meso 1
**Block duration**: 4 weeks
**Design intent**: Maintenance volume + new equipment integration + overhead stability emphasis + injury constraints (lower back/upper glute strain, knee health priority)

**Version**: Full replacement of prior maintenance program structure. Retains progression engine rules and independent-track keying from prior architecture.

---

## Block-Level Constraints

```yaml
block_constraints:
  injuries:
    - active_strain: lower_back_upper_glute
    - persistent_priority: knee_health  # #1 priority always
  
  removed_movements_this_block:
    - hip_thrust  # any variant, any day (glute contraction near strained tissue)
    - rdl_bilateral  # replaced with kickstand RDL (unilateral spine load)
    - kb_swing  # removed from finisher rotation (hip hinge under load)
    - sandbag_load  # removed from finisher rotation (repeated hip hinge)
    - dragon_flag  # already replaced with cable_woodchopper in prior update
    - nordic_curl_hyper_pro  # replaced with nordic_curl_max (Nordic Max attachment)
  
  removed_equipment_this_block:
    - gmwd_hip_thrust_station  # no HT this block; equipment stays owned but unused
    - blackwing_bench  # replaced with apex_bench (retirement decision pending James use)
  
  overhead_stability_emphasis: true
    weekly_vertical_press_frequency: 3  # D1, D4, D6
    warmup_scapular_prep: true  # Wall Slide to OH Reach on push days
```

---

## Equipment Inventory Additions

New equipment now in active rotation:

```yaml
equipment_additions:
  - apex_adjustable_bench:
      description: Firmer + narrower than BlackWing, foundation for all APEX attachments
      replaces: blackwing_bench (pending final retirement decision)
  
  - belle_mere_bmf_camber_bar:
      description: 4-grip camber bar (7", 14", 21", 28")
      grip_details:
        - grip_7in: neutral (parallel), tricep-focused
        - grip_14in: angled, close-grip
        - grip_21in: angled, standard grip, 1.5" camber
        - grip_28in: neutral (parallel), wide grip, 3" camber
      note: SAME as prior "BMF Pro Camber Bar" in memory — not a separate bar
  
  - stryker_pad:
      description: Multi-position pad for row/press/HT/tricep work
      exercises_available: [csr_barbell, csr_cables, seated_ohp, seated_oh_triceps, hip_thrust]
  
  - matrix_machine:
      description: Multi-mode attachment
      exercises_available: [preacher_curl, bulgarian_split_squat, barbell_hip_thrust, sissy_squat]
  
  - nordic_max:
      description: Foot anchor pad for Nordic curls (also functions as BSS foot holder + lat pulldown leg holder)
      exercises_available: [nordic_curls, bulgarian_split_squat, lat_pulldown_leg_holder]
      assist_mechanism_this_block: monster_bands_overhead
  
  - hybrid_board:
      description: Ankle/lower leg platform
      exercises_available: [calf_raises, tib_raises, belt_squat_platform]
  
  - ab_trainer:
      description: Decline ab station attachment
      exercises_available: [decline_situps, weighted_situps, decline_crunches, hanging_leg_raises, hanging_knee_raises, russian_twists, cable_crunches]
  
  - better_fly:
      description: 2× grip-free cuffs (elbows/knees) for cable isolation
      exercises_available: 
        [fid_seated_chest_fly, standing_fly, low_pec_fly, lat_pulldown, straight_arm_lat_pulldown, pullover, 
         keenan_flap, kickbacks, knee_drives, hip_adduction, hip_abduction, cable_crunches, 
         lat_raise, front_raise, rear_delt_ext, tricep_pushdowns, oh_tricep_ext, bicep_curl]
  
  - abmat_ab_bench_pad:
      description: Cushioning pad for ab bench cable crunches
      exercises_available: [cable_crunches]
  
  - abmat_rom_pad:
      description: ROM adjuster for INCLINE bench only (unstable on flat)
      exercises_available: [incline_bench_variations]
      note: NOT USED THIS BLOCK, banked for future rotation
  
  - abmat_zercher_pad:
      description: Elbow padding for Zercher squat
      exercises_available: [zercher_squat]
      note: NOT USED THIS BLOCK, banked for future rotation

equipment_clarifications:
  - andreoni_bar:
      description: Multi-grip cable attachment (NOT a loadable barbell)
      correct_uses: [seated_cable_row, cable_pullover, cable_pressing_patterns]
      incorrect_uses: [bench_press, ohp, pendlay_row, any_bilateral_barbell_movement]
  
  - straight_bars_available:
      - black_diamond_dbd  # primary straight bar
      - gladiator_stainless  # backup straight bar (previously marked for sale, still owned)

  - camber_bar_confirmation:
      belle_mere_bmf_camber_bar: same_as_prior_bmf_pro_camber_bar  # NOT two separate bars

  - monster_bands:
      description: Rogue Monster Bands (heavy, medium, light)
      new_use: Nordic Max assist mechanism (looped over pull-up bar, under armpits)
```

---

## Day-by-Day Structure

### D1 Monday — Upper Push

```yaml
day: d1
day_name: Upper Push
day_type: upper_push_with_ohp_stability
shoes:
  primary: metcon_9
  z2_swap: cloud_x4

warmup:
  duration_minutes: 5
  additions_this_block:
    - wall_slide_to_oh_reach: 2x8-10  # scapular pattern prep for OH stability

tiers:
  - tier: T1
    scheme: STRAIGHT
    rest_seconds: 120
    exercises:
      - movement_id: bench_press_camber_21
        equipment: [belle_mere_bmf_camber_bar, apex_bench_flat]
        grip: 21_inch  # 1.5" camber, angled
        sets: 3
        rep_low: 6
        rep_high: 8
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 1  # T1
        wk1_calibration_estimate: 155-165  # slightly lighter than straight bar due to camber

  - tier: T1b
    scheme: STRAIGHT
    rest_seconds: 120
    exercises:
      - movement_id: pendlay_row_narrow
        equipment: [black_diamond_dbd]  # straight bar, NOT Bruno Bar
        sets: 3
        rep_low: 6
        rep_high: 8
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 1
        wk1_hold_at: 170  # strain healing — no push, maintain load
        notes: |
          HOLD 170 lb this block. Do not push progression while lower back
          strain healing. Advance only when strain fully resolved.

  - tier: T2
    scheme: GIANT_SET
    rest_seconds: 90
    rounds: 3
    exercises:
      - movement_id: stryker_pad_seated_ohp
        equipment: [stryker_pad, apex_bench, mx100_dbs]
        sets: 3
        rep_low: 8
        rep_high: 12
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        wk1_calibration_estimate: 35-45  # per hand
        aspect: [strength, hypertrophy, stability]
        vertical_press_exposure: true
      
      - movement_id: matrix_machine_preacher_curl
        equipment: [matrix_machine, apex_bench, kyoto_ez_curl]
        sets: 3
        rep_low: 8
        rep_high: 12
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        wk1_baseline_locked: 55  # from equipment shakedown
        aspect: [hypertrophy]
        reintroduces_bicep_work: true  # curls back in program after long absence
      
      - movement_id: ab_trainer_cable_crunch
        equipment: [ab_trainer, apex_bench, ares_cable]
        sets: 3
        rep_low: 10
        rep_high: 15
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        wk1_calibration_estimate: 40-60
        aspect: [hypertrophy]
        strain_safe: true  # spine flexion controlled cable load

  - tier: T3
    scheme: GIANT_SET
    rest_seconds: 75
    rounds: 3
    exercises:
      - movement_id: pull_up_d1
        equipment: [pull_up_bar, mingmc_sling, red_band]
        sets: 3
        rep_low: 6
        rep_high: 8
        rpe_cap: 8
        progression_rule: pull_up_rolling_max
      
      - movement_id: better_fly_fid_seated_chest_fly
        equipment: [better_fly, ares_cable, apex_bench]
        sets: 3
        rep_low: 10
        rep_high: 15
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        wk1_calibration_estimate: 40-45  # RPE 6 was 30 lb; target working weight higher
        load_type: cable
      
      - movement_id: better_fly_standing_lat_front_raise
        equipment: [better_fly, ares_cable]
        sets: 3
        rep_low: 10
        rep_high: 15
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        wk1_calibration_estimate: 15-20
        load_type: cable

finisher:
  type: EMOM
  duration_minutes: 6
  exercise: jump_rope
  rope: crossrope_quarter_lb
  work_seconds_per_minute: 30-40
  location: gym

z2:
  duration_minutes: 15
  adaptive_pace: true
  recovery_gap_minutes: 3
```

---

### D2 Tuesday — Lower Squat

```yaml
day: d2
day_name: Lower Squat
day_type: lower_squat_no_ht
shoes:
  primary: metcon_9
  optional_swap:
    from: metcon_9
    to: adipower_ii
    at: between_t2_t3
  z2_swap: cloud_x4

warmup:
  duration_minutes: 5

tiers:
  - tier: T1
    scheme: STRAIGHT
    rest_seconds: 120
    exercises:
      - movement_id: belt_squat
        equipment_option_a: [hyper_pro, fa_belt_squat_attachment]  # current setup, 260 pin cap
        equipment_option_b: [hybrid_board]  # NEW — test Wk 1
        sets: 3
        rep_low: 6
        rep_high: 8
        rpe_cap: 8
        progression_rule: rep_ladder_at_cap  # if using Hyper Pro at 260 cap
        wk1_action: test_both_platforms
        wk1_test_notes: |
          Compare Hybrid Board belt squat vs Hyper Pro belt squat.
          Winner becomes primary for the block. Loser retires from D2 T1 slot.
          Test factors: loading ceiling, foot position, ROM, comfort.
        spine_safety: offloaded  # good for strain

  # NO T1b THIS BLOCK — Hip Thrust removed due to strain

  - tier: T2
    scheme: GIANT_SET
    rest_seconds: 90
    rounds: 3
    exercises:
      - movement_id: matrix_machine_sissy_squat
        equipment: [matrix_machine, apex_bench]
        sets: 3
        rep_low: 8
        rep_high: 12
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        wk1_calibration_estimate: bodyweight  # first exposure
        aspect: [hypertrophy, athleticism]
        knee_health_note: sissy squat trains VMO, deep knee flexion capacity
      
      - movement_id: nordic_curl_max_d2
        equipment: [apex_bench, nordic_max_attachment, pull_up_bar, monster_bands]
        assist_mechanism: monster_bands_overhead
        sets: 3
        rep_low: 6
        rep_high: 8
        rpe_cap: 8
        progression_rule: assistance_reduction
        confirmation_window: 2
        wk1_action: calibrate_band_stage
        assist_ladder:
          stage_1: [heaviest_band, heaviest_band]  # most assist
          stage_2: [heaviest_band, medium_band]
          stage_3: [medium_band, medium_band]
          stage_4: [heaviest_band]
          stage_5: [medium_band]
          stage_6: [light_band]
          stage_7: unassisted  # terminal
        setup_notes: |
          Band(s) looped over pull-up bar, positioned to catch under armpits/chest 
          at shoulder height when standing. Band(s) stretch as you descend, 
          providing scaling assist matched to Nordic strength curve.

  - tier: T3
    scheme: GIANT_SET
    rest_seconds: 60
    rounds: 3
    exercises:
      - movement_id: atg_split_squat
        equipment: [mx100_dbs, utility_seat]
        elevation: 16_inches
        sets: 3
        rep_low: 8
        rep_high: 10
        rpe_cap: 8
        unilateral: true
        progression_rule: rpe_8_standard
        confirmation_window: 2
        current_load: 30  # per hand, from Wk 1 baseline
        ankle_mobility_status: at_edge
        elevation_note: hold at 16" while mobility work in progress
      
      - movement_id: hybrid_board_calf_raise_d2
        equipment: [hybrid_board]
        sets: 3
        rep_low: 10
        rep_high: 15
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        wk1_action: test_loadability  # bodyweight vs DB vs weight vest
        aspect: [hypertrophy]
      
      - movement_id: cable_tib_raise_d2
        equipment: [ares_low_pulley, ankle_strap]
        sets: 3
        rep_low: 10
        rep_high: 15
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        current_load: 25  # locked from Wk 1

finisher:
  type: EMOM
  duration_minutes: 6
  exercise: sled_push
  location: dreadmill
  resistance_level: 8
  work_seconds_per_minute: 30-40
  spine_safety: safe  # standing push, no hinging

z2:
  duration_minutes: 15
  adaptive_pace: true
  recovery_gap_minutes: 3
```

---

### D3 Wednesday — Rest

```yaml
day: d3
type: full_rest
optional_activities:
  - neighborhood_walk  # ~55% Z2 verified
  - saunabox
```

---

### D4 Thursday — Upper Pull + Vertical Press (BTN OHP)

```yaml
day: d4
day_name: Upper Pull + Vertical Press
day_type: upper_pull_with_btn_ohp
shoes:
  primary: metcon_9
  z2_swap: cloud_x4

warmup:
  duration_minutes: 5
  additions_this_block:
    - wall_slide_to_oh_reach: 2x8-10  # scapular pattern prep for OH stability

tiers:
  - tier: T1
    scheme: STRAIGHT
    rest_seconds: 120
    exercises:
      - movement_id: seated_btn_ohp
        equipment: [black_diamond_dbd, apex_bench_upright]
        # NOT andreoni_bar (that's a cable attachment)
        # NOT belle_mere_camber_bar (camber wrong bar path for BTN)
        sets: 3
        rep_low: 6
        rep_high: 8
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 1  # T1
        wk1_action: ramp_session_find_rpe_8
        wk1_ramp: [45x5, 65x5, 85x3]  # then working sets ramping to RPE 8
        aspect: [strength, stability]
        vertical_press_exposure: true
        form_safety_notes: |
          - Test shoulder mobility first (broomstick BTN with elbows below shoulders, no pain)
          - Bar to base of neck (upper traps), NOT lower spine, NOT tapping neck
          - Elbows below shoulders throughout ROM
          - Neutral head/chin (do NOT push head forward)
          - Stop if any impingement signal (pinching, sharp pain, dizziness)
          - Full seated position on APEX Bench (reduces trunk stability demand for safety)
          - If BTN feels off, swap to front seated OHP that session

  - tier: T1b
    scheme: STRAIGHT
    rest_seconds: 180
    exercises:
      - movement_id: pull_up_d4
        equipment: [pull_up_bar]
        # Sling and band DROPPED — all sets unassisted per milestone clearance
        sets: 3
        rep_low: 6
        rep_high: 8
        rpe_cap: 8
        progression_rule: pull_up_rolling_max
        protocol_notes: |
          All 3 sets pure unassisted max attempts. No sling, no band.
          Wk 1 baseline going into this block: 7/6/6 unassisted (fresh Set 1 max).
          Next milestone: 8-10 strict → weighted (dip belt + plate)

  - tier: T2
    scheme: GIANT_SET
    rest_seconds: 90
    rounds: 3
    exercises:
      - movement_id: stryker_pad_csr_barbell
        equipment: [stryker_pad, apex_bench, black_diamond_dbd]
        # Or use Belle Mere Camber Bar 21" grip — either works
        sets: 3
        rep_low: 8
        rep_high: 12
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        wk1_calibration_estimate: 95-115
        aspect: [hypertrophy]
        strain_safety: chest_supported_offloads_lower_back
      
      - movement_id: ab_trainer_hanging_leg_raise
        equipment: [ab_trainer, apex_bench]
        sets: 3
        rep_low: 8
        rep_high: 12
        rpe_cap: 8
        progression_rule: rep_ladder_at_cap  # bodyweight, rep progression
        current_load: null
        wk1_calibration: bodyweight_max_reps
        aspect: [hypertrophy]
      
      - movement_id: better_fly_cable_pullover
        equipment: [better_fly, ares_cable, ares_mid_pulley]
        sets: 3
        rep_low: 10
        rep_high: 15
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        wk1_calibration_estimate: 40-45
        load_type: cable
        aspect: [hypertrophy]

  - tier: T3
    scheme: GIANT_SET
    rest_seconds: 75
    rounds: 3
    exercises:
      - movement_id: db_rear_delt_fly
        equipment: [mx100_dbs, apex_bench_incline]
        sets: 3
        rep_low: 10
        rep_high: 15
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        current_load: 10  # per hand, from Wk 1 baseline
      
      - movement_id: lying_tricep_extension_camber_7
        equipment: [belle_mere_bmf_camber_bar, apex_bench_flat]
        grip: 7_inch  # neutral, tricep-focused
        sets: 3
        rep_low: 8
        rep_high: 12
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        wk1_calibration_estimate: 60
      
      - movement_id: cable_woodchopper
        equipment: [ares_high_pulley, puretorque_pro]
        direction: high_to_low
        sets: 3
        rep_low: 8
        rep_high: 12
        rpe_cap: 8
        unilateral: true
        progression_rule: rpe_8_standard
        confirmation_window: 2
        current_load: 30  # from prior D4 baseline

finisher:
  type: EMOM
  duration_minutes: 6
  exercise: jump_rope
  rope: crossrope_quarter_lb
  work_seconds_per_minute: 30-40
  location: gym
  replaces: sandbag_load  # removed for strain safety this block

z2:
  duration_minutes: 15
  adaptive_pace: true
  recovery_gap_minutes: 3
```

---

### D5 Friday — Lower Hinge (Kickstand RDL)

```yaml
day: d5
day_name: Lower Hinge
day_type: lower_hinge_no_ht_no_bilateral_rdl
shoes:
  primary: metcon_9
  mid_session_swap:
    from: metcon_9
    to: adipower_ii
    at: between_t2_t3
  z2_swap: cloud_x4

warmup:
  duration_minutes: 5

tiers:
  - tier: T1
    scheme: STRAIGHT
    rest_seconds: 150
    exercises:
      - movement_id: kickstand_rdl
        equipment: [mx100_dbs]
        # DBs, NOT barbell — reduces spinal moment arm, better form focus for strain
        sets: 3
        rep_low: 6
        rep_high: 8
        rpe_cap: 8
        unilateral: true
        progression_rule: rpe_8_standard
        confirmation_window: 1  # T1
        wk1_calibration_estimate: 35-40  # per hand
        setup: |
          B-stance: front foot flat, back foot on ball for balance only.
          Load through front hip only. Back leg is stability, not load-bearing.
        rest_between_sides_seconds: 60-90
        strain_safety: |
          Unilateral hinge reduces spinal load ~50% vs bilateral RDL.
          DBs at sides reduce moment arm vs barbell at hips.
          Watch for: hip rotation, back arching, grinding reps.
        replaces_this_block: rdl_bilateral

  # NO T1b THIS BLOCK — Hip Thrust removed due to strain

  - tier: T2
    scheme: GIANT_SET
    rest_seconds: 90
    rounds: 3
    exercises:
      - movement_id: nordic_max_bulgarian_split_squat
        equipment: [apex_bench, nordic_max_attachment, mx100_dbs]
        # Nordic Max holds rear foot for BSS
        sets: 3
        rep_low: 8
        rep_high: 12
        rpe_cap: 8
        unilateral: true
        progression_rule: rpe_8_standard
        confirmation_window: 2
        wk1_calibration_estimate: 25-30  # per hand, lower than D2 track pending BSS calibration
      
      - movement_id: nordic_curl_max_d5
        equipment: [apex_bench, nordic_max_attachment, pull_up_bar, monster_bands]
        assist_mechanism: monster_bands_overhead
        sets: 3
        rep_low: 6
        rep_high: 8
        rpe_cap: 8
        progression_rule: assistance_reduction
        confirmation_window: 2
        assist_ladder: same_as_d2_nordic_curl_max  # independent track
        # Independent load track from D2 — separate MovementState row
        wk1_action: calibrate_band_stage
      
      - movement_id: better_fly_kickback
        equipment: [better_fly, ares_cable, ares_low_pulley]
        sets: 3
        rep_low: 10
        rep_high: 15
        rpe_cap: 8
        unilateral: true
        progression_rule: rpe_8_standard
        confirmation_window: 2
        wk1_calibration_estimate: 20-30
        load_type: cable
        aspect: [hypertrophy]
        strain_safety: glute_isolation_no_spinal_load

  - tier: T3
    scheme: GIANT_SET
    rest_seconds: 60
    rounds: 3
    exercises:
      - movement_id: reverse_nordic_d5
        equipment: [hyper_pro, shorty_monster_bands]
        # Kept on Hyper Pro — Nordic Max is for Nordic Curl, not Reverse Nordic
        sets: 3
        rep_low: 8
        rep_high: 12
        rpe_cap: 8
        progression_rule: assistance_reduction
        current_assist_level: 20
        assist_ladder: [20, 15, 10, 5, 0]
        knee_health_note: trains VMO + hip flexor at end range
      
      - movement_id: hybrid_board_calf_raise_d5
        equipment: [hybrid_board]
        sets: 3
        rep_low: 10
        rep_high: 15
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        # Independent track from D2 Hybrid Board Calf Raise
      
      - movement_id: better_fly_hip_adduction
        equipment: [better_fly, ares_cable]
        sets: 3
        rep_low: 10
        rep_high: 15
        rpe_cap: 8
        unilateral: true
        progression_rule: rpe_8_standard
        confirmation_window: 2
        wk1_calibration_estimate: 15-25
        load_type: cable
        alternates_with: better_fly_hip_abduction  # rotate weekly

finisher:
  type: EMOM
  duration_minutes: 6
  exercise: heavy_farmer_carry
  weight_lb: 55
  work_seconds_per_minute: 40
  rest_seconds_per_minute: 20
  location: dreadmill
  spine_safety: upright_walk_minimal_stress

z2:
  duration_minutes: 15
  adaptive_pace: true
  recovery_gap_minutes: 3
```

---

### D6 Saturday — Weak Points + Isolation

```yaml
day: d6
day_name: Weak Points + Isolation
day_type: accessory_full_body
shoes:
  primary: metcon_9
  z2_swap: cloud_x4

warmup:
  duration_minutes: 3-4  # abbreviated

tiers:
  - tier: GS1
    scheme: GIANT_SET
    rest_seconds: 90
    rounds: 3
    exercises:
      - movement_id: pull_up_d6
        equipment: [pull_up_bar, mingmc_sling, red_band]
        sets: 3
        rep_low: 5
        rep_high: 8
        rpe_cap: 8
        progression_rule: pull_up_rolling_max
        protocol: weekly_max_tracker  # Set 1 pure unassisted max
        wk1_baseline: 7_unassisted_set_1  # from prior Wk 1
      
      - movement_id: dips
        equipment: [andreoni_bar, multifunction_tower, ares_cable]
        load_type: cable
        sets: 3
        rep_low: 8
        rep_high: 12
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        current_load: 150  # from Wk 1 baseline
      
      - movement_id: close_grip_bench_camber_14
        equipment: [belle_mere_bmf_camber_bar, apex_bench_flat]
        grip: 14_inch  # angled close-grip
        sets: 3
        rep_low: 6
        rep_high: 8
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 1  # borderline T1/T2, treat as T1 confirmation
        wk1_calibration_estimate: 135-145

  - tier: GS2
    scheme: GIANT_SET
    rest_seconds: 90
    rounds: 3
    exercises:
      - movement_id: better_fly_cable_bicep_curl
        equipment: [better_fly, ares_cable]
        sets: 3
        rep_low: 10
        rep_high: 15
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        wk1_calibration_estimate: 30-40
        load_type: cable
        aspect: [hypertrophy]
        second_bicep_exposure: true  # first is D1 Matrix Machine Preacher Curl
      
      - movement_id: stryker_pad_csr_cables
        equipment: [stryker_pad, apex_bench, ares_cable]
        sets: 3
        rep_low: 8
        rep_high: 12
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        wk1_calibration_estimate: 60-80
        differs_from_d4: cable_resistance_curve  # vs D4 barbell CSR
      
      - movement_id: better_fly_rear_delt_ext
        equipment: [better_fly, ares_cable]
        sets: 3
        rep_low: 10
        rep_high: 15
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        wk1_calibration_estimate: 15-20
        load_type: cable

  - tier: GS3
    scheme: GIANT_SET
    rest_seconds: 60
    rounds: 3
    exercises:
      - movement_id: face_pull
        equipment: [ares_high_pulley, rope_attachment]
        sets: 3
        rep_low: 10
        rep_high: 15
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        current_load: 30  # from Wk 1 baseline
      
      - movement_id: better_fly_oh_tricep_ext
        equipment: [better_fly, ares_high_pulley]
        sets: 3
        rep_low: 8
        rep_high: 12
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        wk1_calibration_estimate: 30-40
        load_type: cable
        aspect: [hypertrophy, oh_endurance, stability]
        vertical_press_exposure: true  # third OH exposure this week
      
      - movement_id: abmat_ab_bench_pad_cable_crunch
        equipment: [abmat_ab_bench_pad, ares_cable, ab_trainer]
        sets: 3
        rep_low: 10
        rep_high: 15
        rpe_cap: 8
        progression_rule: rpe_8_standard
        confirmation_window: 2
        wk1_calibration_estimate: 40-60

finisher:
  type: EMOM
  duration_minutes: 6
  exercise: jump_rope
  rope: crossrope_quarter_lb
  work_seconds_per_minute: 35
  location: gym

z2:
  duration_minutes: 15
  adaptive_pace: true
```

---

### D7 Sunday — Rest

```yaml
day: d7
type: full_rest
```

---

## Cross-Program Additions

### Warmup Updates (D1 + D4)

Add scapular stability prep to push days:

```yaml
warmup_addition_d1_d4:
  wall_slide_to_oh_reach:
    sets: 2
    reps: 8-10
    duration_seconds: 60
    purpose: scapular_upward_rotation_prep
    when: after_movement_flow_before_specific_ramp
```

### Finisher Rotation (Full Week)

```yaml
finishers_maintenance_block:
  d1_monday:
    exercise: jump_rope
    rope: crossrope_quarter_lb
    work: 30-40s/min
    duration: 6_min
    replaces_this_block: kb_swing_emom
  
  d2_tuesday:
    exercise: sled_push
    location: dreadmill
    resistance: level_8
    work: 30-40s/min
    duration: 6_min
    unchanged: true
  
  d4_thursday:
    exercise: jump_rope
    rope: crossrope_quarter_lb
    work: 30-40s/min
    duration: 6_min
    replaces_this_block: sandbag_load_emom
  
  d5_friday:
    exercise: heavy_farmer_carry
    weight: 55  # per hand
    work: 40s
    rest: 20s
    duration: 6_min
    location: dreadmill
    unchanged: true
  
  d6_saturday:
    exercise: jump_rope
    rope: crossrope_quarter_lb
    work: 35s/min
    duration: 6_min
    unchanged: true

jump_rope_frequency_note: |
  3× per week (D1, D4, D6) — may cause calf/ankle overuse.
  Monitor for tightness. If develops, swap D4 to Better Fly circuit or 
  Farmer Carry variant.
```

### Rep Range Standardization (Applies to All Movements)

```yaml
rep_range_bands:
  strength_low: 3_to_6
  strength_moderate: 6_to_8  # T1 primaries default
  hypertrophy: 8_to_12  # accessory strength/hypertrophy
  volume_hypertrophy: 10_to_15  # higher-rep accessory + isolation
  endurance_recovery: 15_to_20  # recovery, high-rep, calf/tib

all_movements_must_fit_band: true
```

---

## Wk 1 Calibration Queue

All movements requiring baseline calibration on first exposure:

```yaml
wk1_calibration_movements:
  # D1 Push
  - movement: bench_press_camber_21
    day: d1
    tier: T1
    estimate: 155-165
    priority: high  # T1 primary
  
  - movement: stryker_pad_seated_ohp
    day: d1
    tier: T2
    estimate: 35-45  # per hand
  
  - movement: ab_trainer_cable_crunch
    day: d1
    tier: T2
    estimate: 40-60
  
  - movement: better_fly_fid_seated_chest_fly
    day: d1
    tier: T3
    estimate: 40-45
  
  - movement: better_fly_standing_lat_front_raise
    day: d1
    tier: T3
    estimate: 15-20
  
  # D2 Squat
  - movement: belt_squat_hybrid_board  # vs Hyper Pro test
    day: d2
    tier: T1
    action: compare_platforms
  
  - movement: matrix_machine_sissy_squat
    day: d2
    tier: T2
    estimate: bodyweight_first_test_reps
  
  - movement: nordic_curl_max_d2
    day: d2
    tier: T2
    action: find_band_stage_at_rpe_8
  
  - movement: hybrid_board_calf_raise_d2
    day: d2
    tier: T3
    action: test_loadability_then_calibrate
  
  # D4 Pull + BTN OHP
  - movement: seated_btn_ohp
    day: d4
    tier: T1
    action: ramp_session
    ramp: [45x5, 65x5, 85x3, working_sets_to_rpe_8]
    priority: high  # T1, form-critical
  
  - movement: stryker_pad_csr_barbell
    day: d4
    tier: T2
    estimate: 95-115
  
  - movement: ab_trainer_hanging_leg_raise
    day: d4
    tier: T2
    action: bodyweight_max_reps_test
  
  - movement: better_fly_cable_pullover
    day: d4
    tier: T2
    estimate: 40-45
  
  - movement: lying_tricep_extension_camber_7
    day: d4
    tier: T3
    estimate: 60
  
  # D5 Hinge
  - movement: kickstand_rdl
    day: d5
    tier: T1
    estimate: 35-40  # per hand DBs
    priority: high  # T1 primary, strain-critical
  
  - movement: nordic_max_bulgarian_split_squat
    day: d5
    tier: T2
    estimate: 25-30  # per hand
  
  - movement: nordic_curl_max_d5
    day: d5
    tier: T2
    action: find_band_stage_at_rpe_8
    independent_track: from_d2_nordic_curl_max
  
  - movement: better_fly_kickback
    day: d5
    tier: T2
    estimate: 20-30
  
  - movement: better_fly_hip_adduction  # rotate with abduction
    day: d5
    tier: T3
    estimate: 15-25
  
  - movement: hybrid_board_calf_raise_d5
    day: d5
    tier: T3
    action: calibrate_after_d2_baseline
    independent_track: from_d2_hybrid_board_calf_raise
  
  # D6 Weak Points
  - movement: close_grip_bench_camber_14
    day: d6
    tier: GS1
    estimate: 135-145
  
  - movement: better_fly_cable_bicep_curl
    day: d6
    tier: GS2
    estimate: 30-40
  
  - movement: stryker_pad_csr_cables
    day: d6
    tier: GS2
    estimate: 60-80
  
  - movement: better_fly_rear_delt_ext
    day: d6
    tier: GS2
    estimate: 15-20
  
  - movement: better_fly_oh_tricep_ext
    day: d6
    tier: GS3
    estimate: 30-40
  
  - movement: abmat_ab_bench_pad_cable_crunch
    day: d6
    tier: GS3
    estimate: 40-60
```

---

## Movements Retired from Program This Block

```yaml
retired_movements:
  # Removed for injury safety (return post-strain heal)
  - hip_thrust_d2
  - hip_thrust_d5
  - hip_thrust_d6
  - rdl_bilateral  # replaced with kickstand_rdl
  - kb_swing_finisher_d1  # replaced with jump_rope
  - sandbag_load_finisher_d4  # replaced with jump_rope
  
  # Removed due to equipment upgrade
  - nordic_curl_hyper_pro_d2  # replaced with nordic_curl_max_d2
  - assisted_nordic_curl_d5  # replaced with nordic_curl_max_d5 (band assist)
  
  # Previously retired (confirm still retired)
  - dragon_flag  # replaced with cable_woodchopper
  - nordic_curl_d2_hyper_pro  # from earlier update, now Nordic Max
```

---

## Program Structural Summary

```yaml
weekly_structure_summary:
  training_days: 5
  rest_days: 2 (D3 Wed, D7 Sun)
  
  vertical_press_frequency: 3
    d1_t2: stryker_pad_seated_ohp  # moderate load, seated
    d4_t1: seated_btn_ohp  # heavy load, BTN
    d6_gs3: better_fly_oh_tricep_ext  # light load, overhead endurance
  
  finisher_frequency: 5  # every training day
  z2_frequency: 5  # every training day
  
  total_exercises_weekly: 38
  new_exercises_count: 24
  
  aspect_coverage:
    strength: covered  # all primaries
    hypertrophy: covered  # all accessories
    conditioning: covered  # Z2 + finishers
    stability: emphasized  # 3× vertical press + scapular prep
    mobility: warmup_only  # accept gap for maintenance
    power: minimal_gap  # accept gap for maintenance
    athleticism: minimal_gap  # accept gap for maintenance
```

---

## Application Priority for IronLog V2

Suggested application order:

**Before Wk 1 Monday D1**:
1. D1 Pendlay Row equipment tag update (straight bar, not Bruno Bar)
2. D1 Bench Press equipment change (Belle Mere BMF Camber Bar 21" grip)
3. D1 T2 GS composition (Stryker Pad Seated OHP + Matrix Preacher + Ab Trainer Cable Crunch)
4. D1 T3 GS composition (Pull-up + Better Fly Chest Fly + Better Fly Lat/Front Raise)
5. D1 Finisher swap (Jump Rope replaces KB Swing)
6. D1 Warmup addition (Wall Slide)

**Before Wk 1 Tuesday D2**:
7. D2 T1 Belt Squat test protocol (Hybrid Board vs Hyper Pro)
8. D2 T1b removal (Hip Thrust dropped)
9. D2 T2 GS composition (Matrix Sissy Squat + Nordic Max Nordic Curl)
10. D2 T3 GS composition (ATG + Hybrid Board Calf + Cable Tib)
11. Nordic Curl assist mechanism change (Monster Bands overhead, not Hyper Pro incline)

**Before Wk 1 Thursday D4**:
12. D4 T1 change (Seated BTN OHP, ramp session)
13. D4 T1 equipment (Black Diamond DBD, not Andreoni Bar)
14. D4 T2 GS composition (Stryker Pad CSR + Ab Trainer Hanging Leg Raise + Better Fly Pullover)
15. D4 T3 GS composition (DB Rear Delt Fly + Lying Tricep Ext + Cable Woodchopper)
16. D4 Finisher swap (Jump Rope replaces Sandbag Load)
17. D4 Warmup addition (Wall Slide)

**Before Wk 1 Friday D5**:
18. D5 T1 change (Kickstand RDL DB, replaces bilateral RDL)
19. D5 T1b removal (Hip Thrust dropped)
20. D5 T2 GS composition (Nordic Max BSS + Nordic Max Nordic Curl + Better Fly Kickback)
21. D5 T3 GS composition (Reverse Nordic + Hybrid Board Calf + Better Fly Hip Add/Abd)

**Before Wk 1 Saturday D6**:
22. D6 GS1 composition (Pull-up + Dips + Camber Bar CG Bench)
23. D6 GS2 composition (Better Fly Bicep Curl + Stryker Pad CSR Cables + Better Fly Rear Delt Ext)
24. D6 GS3 composition (Face Pull + Better Fly OH Tricep Ext + AbMat Ab Bench Pad Cable Crunch)

**Any time (reference/structural)**:
25. Equipment inventory updates (APEX Bench, all attachments, Belle Mere BMF Camber Bar clarification, Andreoni Bar clarification, Nordic Max, Better Fly, etc.)
26. Rep range standardization (all movements fit 5 bands)
27. Injury constraint documentation

---

## Key Design Notes

**Why this block is maintenance, not progression**:
- Volume reduced ~20% (~38 vs ~42 exercises)
- Two T1b primaries removed (D2 and D5 Hip Thrust)
- Loads held on strain-affected movements (Pendlay 170 hold, HT removed)
- New movement calibrations at conservative estimates
- Overhead stability emphasis > absolute strength push

**Why injuries drove specific choices**:
- Kickstand RDL (unilateral hinge) replaces bilateral RDL (spinal load reduction)
- Seated BTN OHP (reduces standing bracing) replaces standing front OHP
- Cable Crunches (controlled) replace weighted sit-ups (peak spinal flexion)
- Jump Rope (upright) replaces KB Swing (hip hinge) and Sandbag Load (repeated hinge)
- All Hip Thrust removed (glute contraction near strained tissue)

**Why new equipment gets meaningful slots**:
- Every new attachment used at least 1×/wk
- Multiple attachments used 2-3×/wk (Stryker Pad, Better Fly, Matrix Machine, Hybrid Board, Nordic Max)
- Belle Mere BMF Camber Bar featured across 3 grip widths
- Only ROM Pad and Zercher Pad not yet integrated (banked for future)

**Why overhead stability is emphasized**:
- User specifically requested Seated OHP + BTN OHP for stability improvement
- Weekly vertical press frequency increased to 3× (from 1×)
- Load variety: moderate seated (D1), heavy BTN (D4), light OH endurance (D6)
- Scapular pattern prep in warmups (Wall Slide to OH Reach)

---

## End of Seed Data Package

Wk 1 execution begins Monday. Log actual weights + reps + RPE per session to lock Wk 2 progression baselines. New movement calibrations become the Wk 1 baseline data.

Post-Wk 1, batch memory edits + full seed data locked baseline updates.
