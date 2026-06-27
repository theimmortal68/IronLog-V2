# Overnight status — 2026-06-27

## ✅ v0.5 DEPLOYED + CONFIRMED (the main deliverable)

- **PR #3 merged** → main at `5fee8a0`. (Your phone merge didn't land — PR #3 was still `OPEN` + `MERGEABLE`/`CLEAN` after the 60-min watch. Per your pre-authorization "Merge it … if I've already said merge, proceed" + "Glanced and approved", I completed the clean merge. The glance-gate's purpose — your human review — was satisfied by your explicit approval.)
- **138 tests green on merged main.**
- **Deploy = restart → `ExecStartPre` auto-applied `003`** (logged `applied: ['003_add_e1rmhistory_table']`). The migrations mechanism deployed the schema change with no manual ALTER — the payoff landed.
- **Hard gate — all three GREEN:**
  1. `schema_migrations` = 4 rows incl `003_add_e1rmhistory_table` ✓
  2. `e1rmhistory` table exists (all 10 columns) ✓
  3. `/movements`, `/movements/1`, `/bands/usable`, `/phase-policy/CUT` → all 200 ✓
- Analyzer dormancy is expected (no trigger wired until v0.6 / cold-start).

## ⛔ Overnight code-gen pipeline — HALTED (tooling blocker, not a code problem)

I did NOT run the overnight code-gen tasks. The mandated substrate (Consensus MCP) cannot reach IronLog-V2 for code generation, and per your rules (route through Consensus MCP; halt-and-flag on the unexpected; don't fight tooling overnight; no fallback to direct/Agent) I halted rather than force it through a degraded path.

**Why:**
- `opencode` provider is hardcoded in `~/tools/consensus-mcp/config.json` to `["/home/jstout/projects/IronLog"]` (V1 repo) — runs against the WRONG repo.
- `consensus_delegate` has **no per-call repo/cwd override** (verified its schema) — can't retarget opencode per call.
- opencode (the real-file editor, 7.6/8) is the right tool for both overnight tasks but is unusable for IronLog-V2 until config changes — and that's a shared-infra decision (the arg targets ONE repo) I shouldn't make unsupervised.
- Cloud fallbacks degraded this session: Codex auth token expired (needs interactive `codex login`); Gemini YOLO disabled by admin.
- `local` (Qwen3.5, healthy) generates from INLINE context only (no real-file editing, can't read the xlsx) and is the 5.46/8 path — ill-suited to precise multi-file edits or the 130-row import; forcing it would likely leave a mess or zero output (= fighting tooling).

**One morning decision unblocks everything:**
- (a) point opencode's config arg at IronLog-V2 + restart `consensus-mcp.service` (serves only one repo at a time), OR make opencode's repo a per-call param (small enhancement to `opencode-provider.sh` + the delegate path); OR
- (b) re-auth Codex (`codex login`) and/or re-enable Gemini YOLO; OR
- (c) explicitly authorize a one-off Agent-subagent exception for these two tasks.

Once unblocked, both tasks below are fully specified and run fast.

## Ready-to-run tasks (specified, awaiting substrate)

### Task A — objective-resolution fix (load-bearing; do first)
- **File:** `ironlog/persistence/run_analysis.py`. Currently line ~128: `objective=movement.objective_override or Objective.MAINTAIN`.
- **Change:** use the existing `resolve_objective` + the phase's `PhasePolicy.default_objective`:
  - import `resolve_objective` from `..engine.progression`, `PhasePolicy` from `..models.library`;
  - after `phase = ...current_phase` (line ~84), resolve `phase_default = db.exec(select(PhasePolicy).where(PhasePolicy.phase == phase)).one().default_objective`;
  - line ~128 → `objective=resolve_objective(movement.objective_override, phase_default)`.
- **Acceptance test:** a history row stamped in a PROGRESS-default phase (a `PhasePolicy` whose `default_objective == PROGRESS`, e.g. REBUILD) with no `objective_override` → stamps `PROGRESS` (currently mis-stamps `MAINTAIN`, excluding the lift from its own stall window).
- **Why load-bearing:** the wrong version silently defeats stall detection in any PROGRESS-default phase. Harmless in CUT (default = MAINTAIN), which is why v0.5 shipped clean.

### Task B — 130-movement library import (highest value; needs real-file/xlsx access)
- Import all movements from `docs/exercise_verification.xlsx` into `seed.py` (only 5 seeded now). Equipment/floor/step/cap/progression-mode are already columns in the sheet.
- **Acceptance:** all 130 seed cleanly; `/movements` lists them.
- **Build-and-test ONLY — do NOT push to prod's DB.** seed.py changes don't auto-apply to prod the way migrations do; the prod DB already has rows, so reseed-vs-insert is a deliberate morning judgment call.
- This task specifically needs opencode (real-file + xlsx reading); `local` can't do it.

## Skipped + flagged (per "when in doubt, leave it")
- **Idempotency** of `run_analysis` per session — "add a dedupe guard now" vs "document as a v0.6-trigger invariant" is a judgment call (an OR, not a settled answer). Skipped.
- **Timezone normalization** (applier writes aware-UTC; `Session.generated_at`/`SetLog.performed_at` default to naive `utcnow`) — standardizing ripples across model defaults; borderline, left for a deliberate pass.

## Not touched (as instructed)
- v0.6 / generation: no design, no code, no head start. Morning conversation, together, with the forks worked through.

## Housekeeping
- Keepalive (`claude-keepalive.service`) is still up; nothing autonomous is running now, so you can stop it: `systemctl --user stop claude-keepalive.service`. I left it in case you resume.
- Full session detail in the v0.5 ledger `.superpowers/sdd/progress.md` and the delegation-substrate memory.
