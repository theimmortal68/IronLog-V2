"""
generation.py — the between-session brain (interface only, not yet implemented).

Per the generation-algorithm spec this is the propose -> validate -> approve loop:
a deterministic skeleton is laid, the LLM fills the adaptive layer by calling tools
against real data, and a deterministic validator clamps/rejects before approval.

Implementing it requires the LLM integration and the validator; it is scaffolded
here so the package structure is complete. See docs/06_generation_algorithm_spec.md.
"""


def generate_session(day_role: str):
    raise NotImplementedError(
        "Generation is specified in docs/06_generation_algorithm_spec.md. "
        "Build order: validator (deterministic) first, then the LLM proposal loop."
    )
