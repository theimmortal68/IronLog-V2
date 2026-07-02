import json
from ironlog.generation.gemini import GeminiProposer
from ironlog.generation.proposer import PROPOSER_SYSTEM_INSTRUCTION, SELECTIONS_JSON_SCHEMA


class _CapturingClient:
    def __init__(self):
        self.body = None
    def post(self, url, json=None, headers=None):
        self.body = json
        class _R:
            def raise_for_status(self_): pass
            def json(self_):
                return {"candidates": [{"content": {"parts": [{"text":
                    '{"ordering": [], "slots": [], "rationale": "ok"}'}]}}]}
        return _R()


def test_propose_sends_system_instruction_and_dynamic_thinking():
    cap = _CapturingClient()
    gp = GeminiProposer(api_key="x", http=cap)
    gp.propose({"day_role": "D1", "slots": []})
    assert cap.body["systemInstruction"]["parts"][0]["text"] == PROPOSER_SYSTEM_INSTRUCTION
    assert cap.body["generationConfig"]["thinkingConfig"]["thinkingBudget"] == -1


def test_system_instruction_encodes_policy_c_keywords():
    t = PROPOSER_SYSTEM_INSTRUCTION.lower()
    assert "failed" in t and "trend" in t and "limiter" in t
    assert "load" in t  # selections-only boundary mentions never computing loads
