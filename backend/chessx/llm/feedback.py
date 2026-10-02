"""Shared, sanitized validation feedback for serial and speculative requests."""


def validation_feedback(
    code: str, details: dict, maximum: int, *, stage: str | None = None
) -> dict:
    if stage == "bard":
        instruction = "Correct the indicated failure using the same supplied sources. Bard reasoning has no fixed length limit; use the request's combined output allowance and retain enough room for complete schema JSON. Expose reasoning separately, never in story prose. Cite only original supplied event IDs. Do not alter world facts or invent missing information."
        if details.get("reason") == "RESPONSE_TRUNCATED":
            instruction += " The previous generation stopped at the combined API output limit, so it was incomplete. Replan the scope of this source-grounded passage, avoiding padding; ensure final JSON and its closing delimiters complete before that limit."
        return {
            "code": code,
            "details": details,
            "combined_output_token_allowance": maximum + 1000,
            "instruction": instruction,
        }
    instruction = (
        "Correct the indicated failure and return one complete JSON object with ONLY the supplied schema fields. "
        "Shorten text to fit the ENTIRE formal output budget; keep exposed reasoning well below 1000 tokens. "
        "Do not change world facts or use a fallback action."
    )
    repairs = {
        "MEMORY_SOURCE_INVALID": "Durable entries must cite exact IDs from the provided source_batch only. Do not cite prior entries or other people's sources. Spoken assertions alone are heard_claim/location_report/hypothesis, never observed_fact; retain speaker attribution and uncertainty.",
        "COT_BUDGET_EXCEEDED": "Your previous exposed reasoning exceeded 1000 tokens. Regenerate the SAME decision from the SAME supplied facts with a brief reasoning trace: aim for at most 250 tokens, only the few relevant points. Do not enumerate all rules, schema branches, board cells or past events. Then return complete minimal schema JSON. Do not omit the reasoning trace, truncate it, or change world facts.",
        "SPEECH_INTERNAL_REFERENCE": "Rewrite the intended utterance in natural spoken language without source IDs, citation numbers, footnotes or technical labels. Do not reproduce the offending marker.",
        "SPEECH_REPETITIVE": "Do not repeat the previous long utterance or paraphrase it again. Respond briefly to a new point; if there is nothing new, choose pass or leave when permitted by this stage.",
    }
    if stage == "memory":
        # Reflection uses received evidence, not the optional compactor's batch.
        source_repair = "For source_receipt_ids, copy exact values only from protocol.allowed_source_receipt_ids and reflection_sources[].receipt_id. evidence_* keys and world:event IDs are different namespaces, not receipt citations. Do not invent, infer, or cite another owner's or an omitted source. If no received evidence supports the reflection, use [] rather than fabricate a source. Preserve the same supplied knowledge and goal permissions."
        repairs["MEMORY_SOURCE_INVALID"] = source_repair
        if details.get("reason") == "SCHEMA_MISMATCH" and "enum" in details.get(
            "schema_keywords", []
        ):
            instruction += " " + source_repair
    if details.get("reason") == "RESPONSE_TRUNCATED":
        instruction = "The previous generation reached the combined API output limit before completion. Regenerate with a short exposed reasoning trace (aim at most 250 tokens) and ONLY the minimal complete schema JSON. Do not enumerate history, rules or alternatives, append an essay, or copy/repair the incomplete response. Keep speech within 100 tokens and the entire formal JSON within its supplied budget. Preserve the same facts and stage permissions."
    return {
        "code": code,
        "details": details,
        "formal_output_token_limit": maximum,
        "instruction": repairs.get(code, instruction),
    }
