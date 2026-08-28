# State — 2026-08-24

## Current task
D6 finisher-timer + progression audit, triggered by athlete question "why didn't weights increase."

## Decisions made and why
- **D4 finisher: sandbag load-to-utility-seat → slam ball (30lb, EMOM 8/min).** Why: athlete directive, avoid hip-hinge loading while back recovers. Movement 113 renamed in place (no new row), server+seed+live DB all updated. Commit `4fe0f3d`.
- **Finisher payload now exposes `movement_id`.** Why: client's weight-log action needs a real id for `POST /sessions/{id}/finisher/log`; was missing from `build_finisher_payload`. Commit `b7f07cd`.
- **Client finisher timer rewritten** (`IntervalTimerService.kt`): real work/rest (was hardcoded `60-work`), real EMOM, real Tabata multi-block. 2 Opus review rounds — round 1 fixed null-guard/clamp/movement_id-default/tabata-validation findings but introduced a new High (Tabata inter-block rest capped at 59s via the wrong clamp helper, silently truncating D6 jump_rope's real 75s prescription); round 2 fixed with a dedicated floor-only clamp. Merged to `main` (IronLog-V2-Client). **Installed on-device 2026-08-24** (`192.168.1.17:38623`). Not yet exercised live — next D6 jump_rope/EMOM finisher will be the first real-world check.
- **`_confirmation_window()` now always returns 1** (was 1 for T1-labeled groups, 2 for everything else). Why: athlete directive — single-session double progression everywhere, no 2-session accessory confirmation buffer. Verified against 3 real cases (Swiss Bar CG Press, Rear Delt Extension, Wide-Grip Pull-up, all clean 2026-08-16, none advanced). Opus-reviewed clean. Merged + deployed (service restarted 2026-08-24, post-workout). Commit `158215a` (merge `b203720`).
- **Dips [TOWER+TUBES] (movement 98) flagged as misconfigured, NOT yet fixed.** Currently `ASSISTANCE_REDUCTION`/assist_ladder 0-120 (CABLE_LB), but athlete actually uses band resistance (dual purple / purple+black / single purple Draper's Strength bands, added resistance not assistance) at real loads 150-160lb-equivalent for over a month. The streak/confirmation mechanism works fine on it (advances every clean session regardless), but the numeric value it produces is disconnected from reality. Needs reclassification to a band-count/tension ladder mirroring Wide-Grip Pull-up's `TUBE_COUNT` mechanic but in the added-resistance direction — blocked on athlete supplying their actual owned band tension/color progression in order.
- **Session notes used as stopgap for band-tracking** (sessions 42, 52) since `SetLog` has no per-set notes field — adding one is a schema change requiring explicit human sign-off, not yet requested.

## Open questions
- **Wide-Grip Pull-up streak anomaly, unresolved.** `consecutive_advance_count` was 0 after a session (2026-08-16) that replays as clean under current code/data (should be 1, matching CG Press/Rear Delt same-session behavior). No exception found in the write path. Leading hypothesis: sets were corrected/edited after that session was already analyzed (a correction doesn't retroactively re-run progression) — unconfirmed, athlete hasn't answered. Now moot going forward only in the sense that window=1 means today's session alone can trigger an advance regardless of the stale counter — but the underlying mechanism (a correction silently desyncing progression state) could recur elsewhere and hasn't been root-caused.
- **Two Opus-review follow-ups filed, not yet actioned:**
  1. The window=1 change also activates previously-unreachable `FINISHER_DURATION_THEN_ROPE` rope-ladder progression (rope could never advance under window=2 — `_ladder_step` resets streak to 0 at the terminal duration rung). Athlete didn't ask for this; unrequested but not incorrect. Worth confirming intended.
  2. `docs/program/source/2026-08-10-maintenance-block-seed-data-FINAL.md` still specifies per-movement `confirmation_window` values (2 for 19 movements, 1 for 5) that the code never actually parsed and that are now fully superseded. Doc needs reconciling so a future session doesn't "restore" window=2 from it.
- Bicep Curl's logged `actual_load`/`feedback_tap` for 2026-08-23 (uniform 25lb/ON_TARGET×3) does not reflect the real too-easy→too-easy→perfect band progression described live — same class of gap as Dips. Not fixed (would mean editing real SetLog rows, higher-risk than a session note; not requested).

## Next step
1. ~~Install client APK~~ DONE 2026-08-24 (`192.168.1.17:38623`). Verify finisher timer live on next D6.
2. Athlete decision needed: Dips band-ladder redesign (needs their real band inventory/order) and whether to pursue the Pull-up streak-anomaly root cause further.
3. Athlete decision needed: is the rope-ladder progression activation (follow-up #1 above) wanted, or should `FINISHER_DURATION_THEN_ROPE` be excluded from the window=1 change.

---

# State — 2026-08-28

## Current task
D4 live-session feedback: two movements advanced in 5lb steps instead of the intended 2.5lb.

## Decisions made and why
- **PureTorque Pro Rotation (id 69) and Better Fly Rear Delt Extension [FT] (id 143) both had `increment_ladder=[5, 2.5]`** (tiered ladder — tier 0 = 5lb, only drops to 2.5 after a stall). Athlete performed 32.5 on both this session and wants flat 2.5lb steps always, matching most other LADDER movements' convention (`[2.5]` single-value). Changed to `increment_ladder=[2.5]` in `ironlog/seed.py`, applied to the live DB via idempotent migration `deploy/migrations/043_flat_2_5_increment_ladders.sql` (run on myflix against the live `ironlogv2.service` DB — this repo's local `ironlog.db` is the same NFS-mounted file, confirmed by matching mtimes). No service restart needed — movements are read per-request, not cached. Full suite (744 tests) green after. Commit `d48488f`.
- Mirrors an identical prior fix already in this repo's history: `045d4e0 fix(seed): drop 5lb coarse rung from Better Fly Standing Lat Raise ladder` — same failure class, third occurrence now (Standing Lat Raise, PureTorque Rotation, Rear Delt Extension). **Worth a sweep**: grep `ironlog/seed.py` for any other `increment_ladder=[5, 2.5]` movements the athlete hasn't hit yet — see Open questions.

## Open questions
- **Are there other movements still carrying `increment_ladder=[5, 2.5]` that haven't been reported yet?** Not swept this session (fixed reactively, per athlete report, not proactively). A `grep -n "increment_ladder=\[5, 2.5\]" ironlog/seed.py` would find them before the athlete hits each one individually.

## Next step
1. Consider proactively sweeping `ironlog/seed.py` for remaining `[5, 2.5]` tiered ladders (see open question above) rather than waiting for each to surface one at a time.
2. No athlete-facing action needed — fix is already live.

## Session notes
- Pre-existing uncommitted state in this repo (not this session's): modified `.specs/routing-plan.md`, `.superpowers/sdd/task-2-report.md`, `.superpowers/sdd/task-7-report.md`, `docs/build-plan.md`, `docs/program/phase1-warmup-finisher-source.yaml`, `ironlog/generation/live_seed_ramp_and_finishers.py`, plus 118 untracked files (specs, `.db.bak-*` snapshots, `finisher_dump_tmp.py`). Left untouched — not this session's to commit or clean up.
- 5 commits unpushed to `origin/main` as of session end (`d48488f` back through `f362be3`), including this session's fix — see Pushed status in the closing report below.
- Usage snapshot: not captured — `/usage` is a slash command, not available as a tool in this session context.
