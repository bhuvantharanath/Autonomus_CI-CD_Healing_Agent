"""Global determinism settings.

Every component that introduces randomness (LLM calls, file-system
iteration, hash-set ordering) MUST import from this module and use
these constants so that identical inputs always produce identical
``results.json`` output (timestamps excluded).
"""

# Fixed seed used for LLM calls and any future PRNG usage.
SEED: int = 42

# LLM sampling parameters — fully deterministic.
LLM_TEMPERATURE: float = 0.0
LLM_TOP_P: float = 1.0  # Gemini requires top_p > 0; 1.0 is default / neutral

# Convenience dict to spread into every OpenAI-compatible payload.
# NOTE: Gemini's OpenAI-compatible API does not support the ``seed``
# parameter and rejects ``top_p=0.0``.  We use ``temperature=0`` for
# determinism and omit ``seed``.
LLM_DETERMINISTIC_PARAMS: dict[str, object] = {
    "temperature": LLM_TEMPERATURE,
    "top_p": LLM_TOP_P,
}
