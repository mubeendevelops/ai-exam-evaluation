"""The LLM scorer (P19): a second opinion per rubric criterion, off by default.

* ``prompts``: one prompt per criterion; the student's answer is data, never instructions.
* ``reply``: the strict JSON ``{"credit": 0 | 0.5 | 1, "reason": "..."}``.
* ``groq``: the Groq chat client with key rotation and a rate limit (development keys only),
  retries, a timeout and token accounting.
* ``scorer``: ``LlmScorer``, the ``Scorer`` the scoring service asks when a college has it on.

Nothing here logs a prompt, an answer or a reply: counts, ids and status codes only."""
