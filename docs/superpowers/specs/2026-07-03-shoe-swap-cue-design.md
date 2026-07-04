# Shoe-Swap Cue — Design

**Date:** 2026-07-03
**Repos:** server `~/projects/IronLog-V2` (FastAPI/SQLModel) + client `~/projects/IronLog-V2-Client` (Kotlin/Compose)
**Status:** Approved design → spec for implementation planning

## Goal

Show which shoe each session calls for and prompt a clear **mid-session swap** when it changes — display-only base-program metadata, so the athlete resets their feet *before* a block, not mid-set.

## Scope

| THIS chunk | NOT this chunk |
|---|---|
| Per-**tier** shoe label surfaced in the session graph | Per-movement shoe (per-tier suffices for the program) |
| Client displays the shoe + a swap-cue banner at transitions | Engine involvement — shoes are pure display, never touched by the progression engine or the two-writer boundary |
| Seed the D1–D6 shoe values | The Z2 `z2_swap` (Cloud X4) — deferred with the rest of Z2 (not in-app) |

**Display-only.** No `current_load`/state writes; the progression engine and Option-C boundary are untouched.

## Server (IronLog-V2)

### S1 — `Tier.shoe` + `ExerciseGroup.shoe` fields + migration
- Add `Tier.shoe: Optional[str] = None` (`ironlog/models/program.py`) and `ExerciseGroup.shoe: Optional[str] = None` (the session-graph model), holding a shoe label (`"Metcon 9"` / `"Adipower II"` / `"Cloud X4"`).
- Migration `018_shoe.sql`: two additive `ALTER TABLE … ADD COLUMN shoe VARCHAR;` (one for `tier`, one for `exercisegroup`) — a purely-additive-schema file, allowed multi-statement per the migration README's additive carve-out. Parity keystone `test_chain_matches_create_all` stays green.

### S2 — Assembler propagates + DTO
- The assembler already sets `ExerciseGroup.rest_seconds`/`label` from the `Tier`. Propagate `Tier.shoe → ExerciseGroup.shoe` at the 3 group-build sites (same spots that set `rest_seconds`/`label`).
- Extend `GroupOut` (`schemas_capture.py`) with `shoe: Optional[str] = None`; `_serialize_session` populates it from `ExerciseGroup.shoe`.

### S3 — Seed the shoe values
Seed `Tier.shoe` per the base-program footwear metadata (live DB + seed source):
- **D1, D2, D4, D6:** every tier `"Metcon 9"`.
- **D5:** `"Metcon 9"` for T1/T1b/T2; **`"Adipower II"` for T3** (the mid-session swap between T2 and T3).

## Client (IronLog-V2-Client)

### C1 — DTO + display
- Add `shoe: String? = null` to the Kotlin `GroupOut` DTO (field-for-field with the Pydantic model — the crossing artifact).
- **Session header:** show the starting shoe (`👟 Metcon 9`) — the first group's shoe.
- **Per-group transition cue:** as groups render in order, when a group's `shoe` is non-null and **differs from the previous group's shoe**, render a prominent banner at that group: **`👟 Swap to Adipower II`**. No banner when the shoe is unchanged (or null). Pure display — extract the "did the shoe change vs the previous group" decision as a small pure helper (`shoeTransition(prevShoe, thisShoe): String?`) and unit-test it.

## Crossing contract

`GroupOut.shoe` (Pydantic `schemas_capture.py` ↔ Kotlin `CaptureModels.kt`) — snake/camel matched to the JSON, like the existing `GroupOut` fields.

## Build order

Server-stable-before-client: S1 (field+migration) → S2 (assembler+DTO) → S3 (seed) → server pytest green → C1 (client DTO + display + cue) → build + install + phone check.

## Verification

- **Server pytest:** assembler propagates `Tier.shoe` → `GroupOut.shoe`; migration parity green.
- **Client:** unit-test `shoeTransition` (change → label, same → null, null-safe); build on workstation gradlew + `adb -s 192.168.1.17:36231 install -r`.
- **Phone check:** D5 shows `👟 Metcon 9` at the top and a `👟 Swap to Adipower II` banner at T3; D1/D2/D4/D6 show Metcon 9 with no swap banner.

## Global constraints

- Server: NO `from __future__ import annotations`; migration additive/single-statement + parity keystone; display-only (no engine/`current_load` involvement).
- Client: no new Gradle dependency; `SERVER_BASE_URL=http://192.168.1.7:8000` local-uncommitted (leave it).
- Two-repo: server stable before client; `GroupOut.shoe` is the crossing artifact.
