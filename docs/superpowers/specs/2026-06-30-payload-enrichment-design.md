# Muscle-Aware Reasoned Deviation Payload — Design

**Date:** 2026-06-30
**Repo:** `~/projects/IronLog-V2` (Python / FastAPI / SQLModel; server-only — no client changes)
**Status:** Approved design → spec for implementation planning

---

## Goal

Give the runtime proposer enough information — and explicit instructions — to make the best possible deviation decision, instead of choosing blind from bare integer IDs with no guidance.

## Motivation (the audit)

The model that proposes deviations (`GeminiProposer`, `gemini-3.1-flash-lite`) currently receives, per `gemini.py` `propose()`:

```python
body = {"contents": [{"role": "user", "parts": [{"text": json.dumps(payload)}]}],
        "generationConfig": {"responseJsonSchema": SELECTIONS_JSON_SCHEMA,
                             "thinkingConfig": {"thinkingBudget": 0}}}
```

Three structural gaps:

- **A — no instructions.** No system prompt, no task description, no coaching policy, no field glossary. The model infers its job from a raw JSON blob plus the output schema.
- **B — bare candidate IDs.** `build_context_payload` emits `"candidates": [35, 62, 71]` (`context.py:375`) — integers with no name, muscle, equipment, or pattern. The model cannot reason about alternatives it cannot see; the path of least resistance is to echo `candidates[0]` (always the program anchor).
- **C — `thinkingBudget: 0`.** The runtime call runs with reasoning disabled.

A prior cross-vendor eval (Gemini 3.1 Pro, Claude Opus/Sonnet 4.6, GPT-OSS 120B vs flash-lite) accidentally tested an *enriched* payload (the eval harness added movement names): models converged (Opus 9/9 with flash-lite, 7/9 unanimous). That proved the models choose well **once they can see the candidates** — i.e. the lever is the payload, not the model. This chunk moves that enrichment into the production payload.

## Resolved design decisions (forks)

1. **Deviation policy = stall-type-driven.** On a stall: a **failed-progression** stall (missing reps at load) → keep the movement, let loading adjust; a **trend plateau** (e1RM flat over the window despite hitting reps) → swap for a novel same-pattern stimulus. Scale the response to severity. Bias toward the limiter muscle. No fixed keep-vs-swap bias.
2. **Stall detail = typed + severity + limiter** (the policy depends on it, so it is core, not an add-on).
3. **Limiter source = movement→muscle tagging** (new library data). The system has no muscle data today — only `region` (UPPER/LOWER/CORE), `lift_category` (ROW/BENCH/…), and slot `pattern`. A true muscle-level limiter requires tagging movements.
4. **Muscle taxonomy = moderate (~17), primary + secondary.**
5. **Candidate descriptor = full** (`id, name, primary_muscle, secondary_muscles, lift_category, pattern, equipment_tags, is_program_anchor`).
6. **Instruction prompt = full coaching instruction** in Gemini's native `systemInstruction`.
7. **Thinking = dynamic** (`thinkingBudget: -1`); revisit a fixed budget only if it becomes problematic.
8. **Extra payload fields = phase-intent + per-slot rep-scheme** (bodyweight excluded — it is a loading input, not a selection input).
9. **Verification gate = re-run the agy cross-vendor slate against the real enriched payload** + full pytest green.

## Grounded facts (verified against the code)

- `detect_stall(progress_anchor_e1rms, consecutive_failed, objective)` returns `StallSignal(trend_stalled, failed_stalled, stalled)`; the failed arm fires on `consecutive_failed_progressions >= STALL_FAILED_THRESHOLD` regardless of e1RM history; PROGRESS-objective-gated.
- `Movement` has `region`, `lift_category`, `knee_modality`, `equipment_tags` (`List[str]` JSON), `base_name`, `is_primary`. **No muscle field.**
- `PhasePolicy` has `default_objective`, `rpe_band_low/high`, `hard_cap`, `top_set_rpe`, `progression_attempted`, `volume_posture` (str). No single "intent" string — phase-intent is composed from these.
- `TierExercise` has `rep_low`, `rep_high`, `scheme`. `SlotSpec` does **not** carry a rep range → the payload build needs a slot→`TierExercise` lookup.
- `build_candidate_menu` returns `List[int]` (IDs); `check_menu_membership` validates against those IDs. The menu stays ID-based; only the **payload** enriches IDs → descriptors.
- `GeminiProposer.propose` serializes `json.dumps(payload)` as the sole user message; `generateContent` supports a top-level `systemInstruction`.

---

## Units

### Unit 1 — Muscle taxonomy (foundation)

- New `Muscle` enum in `ironlog/models/enums.py` (~17 values): `UPPER_CHEST, MID_LOWER_CHEST, LATS, MID_BACK, UPPER_TRAPS, FRONT_DELT, SIDE_DELT, REAR_DELT, BICEPS, TRICEPS, FOREARMS, QUADS, HAMSTRINGS, GLUTES, ADDUCTORS, CALVES, ABS, SPINAL_ERECTORS`.
- `Movement` gains `primary_muscle: Optional[Muscle] = None` and `secondary_muscles: List[str] = Field(default_factory=list, sa_column=Column(JSON))` (mirrors `equipment_tags`).
- Migration: additive-nullable columns (the safe pattern). Single-statement-atomic or idempotent (`IF NOT EXISTS`) per the project migration rule; include the parity keystone (`test_chain_matches_create_all`).

### Unit 2 — Tag the 108 movements (data pass)

- Semi-automated proposal: a worker proposes `primary_muscle` + `secondary_muscles` for each movement from `name`/`base_name`/`lift_category`/`pattern`/`region`.
- **User review gate**: tagging accuracy drives the limiter — the proposed tags are reviewed/corrected before commit.
- Tags committed to the `MOVEMENTS` seed source (source of truth) **and** a data migration backfills the live DB's existing rows (idempotent: only set where null).

### Unit 3 — Enriched candidate descriptors (gap B)

- `build_candidate_menu` unchanged (returns IDs; membership validation unchanged).
- `build_context_payload` enriches each candidate ID → `{id, name, primary_muscle, secondary_muscles, lift_category, pattern, equipment_tags, is_program_anchor}`. `is_program_anchor = (id == slot.program_movement_id)`.

### Unit 4 — Typed/severity/limiter stall record (gap D + the muscle limiter)

- `build_weak_point_hints` (rename/return-shape change) returns per stalled movement:
  ```
  {stall_type: "failed" | "trend" | "both",
   failed_count: int,
   e1rm_window: {sessions: int, peak: float | null, latest: float | null},
   limiter: {primary_muscle: str | null, secondary_muscles: [str]}}
  ```
- Derived from `detect_stall` (type), `consecutive_failed_progressions` (failed_count), the e1RM window (peak/latest/sessions), and the movement's Unit-1 tags (limiter).
- `slot_has_deviation_signal` / `should_invoke_llm` still trigger on presence of a record for the slot's program movement (behavior preserved; only the payload-facing shape is richer). `GenerationContext.weak_point_hints` type changes from `Dict[int, str]` to `Dict[int, dict]`.

### Unit 5 — Phase-intent + per-slot rep-scheme (gap G)

- Payload adds `phase_intent`: `{objective: <default_objective>, rpe_band: [low, high], volume_posture: <str>}` from `PhasePolicy`.
- Each slot in the payload adds `rep_scheme: {rep_low, rep_high, scheme}` via a slot→`TierExercise` lookup (keyed by `program_movement_id` within the program/day).
- **Informational only.** Does not alter the assembler. Finding B (assembler ignores seeded `rep_low/high`) remains a separate v0.7 item.

### Unit 6 — Instruction prompt + proposer call (gaps A + C)

- New versioned constant `PROPOSER_SYSTEM_INSTRUCTION` (in `proposer.py` or `gemini.py`) encoding: the role (strength coach); **policy (c)** verbatim; the program-is-default principle (the `is_program_anchor` candidate stands unless an alternative better addresses the limiter); a field glossary (`owed`, the stall record, `candidates` + `is_program_anchor`, `phase_intent`, `rep_scheme`); and the selections-only boundary (pick movements/variants/technique — never compute loads or reps).
- `GeminiProposer.propose`: add `"systemInstruction": {"parts": [{"text": PROPOSER_SYSTEM_INSTRUCTION}]}` to the body; set `thinkingConfig.thinkingBudget = -1` (dynamic). The injected adapter constructor stays test-friendly (no real network in tests).

### Unit 7 — Verification (the gate)

- Re-run the agy cross-vendor slate (`agy --sandbox --model "<model>" -p "<prompt>"`, OAuth, free) against the **real enriched payload** built by the new `build_context_payload`, on D1 + D4 + D6 stall scenarios (throwaway DB copy, never prod).
- Pass criteria: flash-lite produces on-menu, structurally valid, reasoned picks (rationale references stall type + limiter), and converges with the frontier models on the enriched input.
- Full pytest suite green (server-side on myflix: `ssh myflix 'cd ~/projects/IronLog-V2 && .venv/bin/pytest -q'`).

## Build order

1 → 2 → (3, 4, 5 in parallel) → 6 → 7. Units 3/4/5 depend on Unit 1; Unit 4 also depends on Unit 2 (tags); Unit 6 depends on the final payload shape.

## Global constraints

- **NO `from __future__ import annotations`** (project-wide).
- **BUILD-AND-TEST-ONLY**: never run `python -m ironlog.seed` against prod; server tests run in-memory/on-myflix; the live DB is touched only by gated, backup-first migrations.
- **Migration rule**: single-statement-atomic OR idempotent (`IF NOT EXISTS`); add the parity keystone test.
- **Two-writer boundary**: this work is read-only context construction + a library schema/data addition; it must not write `current_load`/outcome fields.
- **Tests on myflix** via SSH (workstation venv lacks deps).

## Out of scope

- **F — user weak-point/goal profile** (the athlete's personal weak areas). Separate later chunk; Unit 1's movement tags are its foundation.
- **Finding B** — assembler ignoring seeded `rep_low/high`. Stays v0.7.
- **Few-shot examples** in the prompt — held in reserve; added only if Unit 7 shows the model misreads the task.

## Verification summary

The chunk is "done" when: the enriched payload is built by `build_context_payload`; the proposer call carries `systemInstruction` + dynamic thinking; the agy slate against the real enriched payload shows flash-lite making reasoned, convergent picks; and the full pytest suite is green.
