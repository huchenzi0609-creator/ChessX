"""Shared, sanitized validation feedback for serial and speculative requests."""


def validation_feedback(
    code: str, details: dict, maximum: int, *, stage: str | None = None
) -> dict:
    if stage == "bard":
        return {
            "code": code,
            "details": details,
            "combined_output_token_allowance": maximum + 1000,
            "instruction": "Correct the indicated failure using the same supplied sources. Bard reasoning has no fixed length limit; use the request's combined output allowance and retain enough room for complete schema JSON. Expose reasoning separately, never in story prose. Cite only original supplied event IDs. Do not alter world facts or invent missing information.",
        }
    instruction = (
        "Correct the indicated failure and return one complete JSON object with ONLY the supplied schema fields. "
        "Shorten text to fit the ENTIRE formal output budget; keep exposed reasoning well below 1000 tokens. "
        "Do not change world facts or use a fallback action."
    )
    repairs = {
        "COT_BUDGET_EXCEEDED": "Your previous exposed reasoning exceeded 1000 tokens. Regenerate the SAME decision from the SAME supplied facts with a brief reasoning trace: aim for at most 250 tokens, only the few relevant points. Do not enumerate all rules, schema branches, board cells or past events. Then return complete minimal schema JSON. Do not omit the reasoning trace, truncate it, or change world facts.",
        "SPEECH_INTERNAL_REFERENCE": "Rewrite the intended utterance in natural spoken language without source IDs, citation numbers, footnotes or technical labels. Do not reproduce the offending marker.",
        "SPEECH_REPETITIVE": "Do not repeat the previous long utterance or paraphrase it again. Respond briefly to a new point; if there is nothing new, choose pass or leave when permitted by this stage.",
    }
    return {
        "code": code,
        "details": details,
        "formal_output_token_limit": maximum,
        "instruction": repairs.get(code, instruction),
    }
