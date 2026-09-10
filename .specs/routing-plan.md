## Routing Plan
Generated: 2026-09-03 (revised after /verify-plan FAIL — see Verification history below)

- .specs/01-periodization-data-model.md → codex, worktree wt-01, depends on: none
- .specs/02-resolution-chain-microcycle-parity.md → gemini, worktree wt-02, depends on: 01
- .specs/03-policy-resolver.md → codex, worktree wt-03, depends on: 01
- .specs/04-generation-wiring-prescription-snapshot.md → codex, worktree wt-04, depends on: 01, 03
- .specs/06-phase-cutover-migration.md → gemini, worktree wt-06, depends on: 01, 03
- .specs/05-read-api-endpoints.md → gemini, worktree wt-05, depends on: 01, 03, 04

Delegation ratio: 6/6 (100%)

Merge order (serial, build/test gate between each, per CLAUDE.md):
1. 01 (foundational — schema + migration; everything else blocked until this merges)
2. 02, 03 — independent of each other (disjoint files: skeleton.py/program.py vs. new periodization_resolver.py), can be *dispatched* concurrently once 01 merges, but still *merged* one at a time with a build/test pass between each per the serial-merge rule
3. 04, 06 — both need 03 merged first (04 needs `resolve_envelope`/`ResolvedEnvelope` directly; 06 needs it for its shadow-validation pass). 04 and 06 touch disjoint files and don't depend on each other — can dispatch concurrently once 03 is in, merge serially.
4. 05 — after 01, 03, and 04 are all merged (reuses spec 04's current-Microcycle lookup helper rather than duplicating it; does not depend on 06).

## Verification history
- 2026-09-03, first `/verify-plan` pass: **FAIL**. Two issues found: (a) spec 06 was grouped parallel with 03 and only listed `01` as a dependency, but design doc §10's required shadow-validation pass needs spec 03's `resolve_envelope()` directly — resequenced 06 to depend on 03. (b) spec 06 was missing the `MesoRotation.meso_number → mesocycle_id` backfill design doc §10 explicitly requires — added to spec 06's Changes/Verification. Also fixed, as a HUMAN-GATE-adjacent completeness gap: spec 02's class rename (`WeekParityRotation` → `MicrocycleParityRotation`) would have silently renamed the live DB table (no `__tablename__` pins exist anywhere in `ironlog/models/program.py`) — spec 02 now requires pinning `__tablename__ = "weekparityrotation"` explicitly, avoiding any DB-level rename.
- HUMAN GATEs raised by that pass (schema changes in 01/02/06-conditional, public API surface in 05) — **user-authorized, all of them**, 2026-09-03. Not re-litigated on re-verification; each was already covered by the approved design doc.

## Notes
- Providers alternated codex/gemini per subtask for load distribution, not a hard requirement — both are cross-module-capable per CLAUDE.md's provider guidance (opencode is retired per project memory, not used).
- Spec 06 explicitly does not execute anything against the live DB (see its "Explicit non-goal") — its merge is just landing the tooling + tests, no production action attached to this routing plan.
- Given today's earlier session saw both codex (transient backend outage) and gemini (dispatch timeout with the underlying process still alive) misbehave at least once, follow this project's established playbook per dispatch: verify via `git log`/`git status` in the worktree before treating any "timed out" report as a real failure, and fall back to a Claude subagent per this session's own precedent if a provider fails twice on the same subtask.
- Given the size of spec 01 (7 new tables + 2 model files + 1 migration), consider Fable review mandatory for it even though it's schema/additive — it's the shared foundation every other spec depends on, an error here propagates everywhere. Specs 03 and 04 (real new deterministic logic, invariant-touching per CLAUDE.md's Review Gate) also default to review, not the mechanical-exemption path.
