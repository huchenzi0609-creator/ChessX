"""A pinned, canonical receipt contract for subjective memory reflection.

Retrieval decides which evidence is relevant; this projection resolves only
those presented candidates to the owner's actual delivered records. It never
widens the prompt to other archives or treats index/event IDs as citations.
"""

from copy import deepcopy

from chessx.runtime import schemas

from .memory import compact_receipt


def _remove_navigation_references(value):
    """Retain dated spatial facts, removing their competing audit-ID namespace."""
    if isinstance(value, dict):
        for field in ("receipt_id", "source_event_id"):
            value.pop(field, None)
        for item in value.values():
            _remove_navigation_references(item)
    elif isinstance(value, list):
        for item in value:
            _remove_navigation_references(item)


def reflection_request(state, pid, context, *, max_sources=6):
    """Return an independent context, schema and ordered canonical-ID whitelist.

    The display window does not change durable records. Every selected source
    is included completely in ``reflection_sources``, which budget fitting
    preserves across retries. Empty citations remain valid when reflecting on
    initial knowledge or on the previous subjective memory alone.
    """
    if not isinstance(max_sources, int) or isinstance(max_sources, bool) or max_sources < 0:
        raise ValueError("max_sources must be a nonnegative integer")
    projected = deepcopy(context)
    delivered, ambiguous = {}, set()
    for receipt in state.get("receipts", {}).get(pid, []):
        rid = receipt.get("receipt_id")
        if not isinstance(rid, str) or not rid.startswith(f"{pid}:"):
            continue
        sequence = rid[len(pid) + 1 :]
        if not sequence.isascii() or not sequence.isdigit():
            continue
        if rid in delivered and delivered[rid] != receipt:
            ambiguous.add(rid)
        else:
            delivered[rid] = receipt

    candidates = []
    recalled = projected.get("memory_recall", {})
    if isinstance(recalled, dict):
        for entry in recalled.get("long_term", []):
            if not isinstance(entry, dict) or entry.get("owner_id", pid) != pid:
                continue
            ids = entry.get("source_receipt_ids", [])
            if isinstance(ids, list):
                candidates.extend(ids)
        for receipt in recalled.get("archive_evidence", []):
            if isinstance(receipt, dict):
                candidates.append(receipt.get("receipt_id"))
    # Recently delivered context evidence is considered after relevant retrieval.
    for receipt in reversed(projected.get("receipts", [])):
        if isinstance(receipt, dict):
            candidates.append(receipt.get("receipt_id"))

    ids, selected = [], []
    for rid in candidates:
        if len(ids) >= max_sources:
            break
        if not isinstance(rid, str) or rid not in delivered or rid in ambiguous or rid in ids:
            continue
        ids.append(rid)
        selected.append(compact_receipt(delivered[rid]))

    for field in ("receipts", "memory_recall", "evidence_index"):
        projected.pop(field, None)
    _remove_navigation_references(projected.get("navigation"))
    projected["reflection_sources"] = selected
    protocol = projected.setdefault("protocol", {})
    protocol["allowed_source_receipt_ids"] = ids.copy()
    protocol["source_citation_rules"] = (
        "source_receipt_ids may contain ONLY the exact IDs in allowed_source_receipt_ids. "
        "Each ID identifies your delivered full record in reflection_sources. "
        "Do not cite evidence_* index IDs, world/event IDs, other people's receipt IDs, "
        "or historical records absent from this request. An empty array is permitted for "
        "initial knowledge or prior subjective reflection without new event evidence. "
        "A spoken claim proves receipt of the claim, not its truth."
    )
    channels = projected.get("context_channels")
    if isinstance(channels, dict):
        for field in ("receipts", "memory_recall", "evidence_index"):
            channels.pop(field, None)
        channels["reflection_sources"] = "Complete personally delivered evidence; sole citations"
    schema = deepcopy(schemas.MEMORY)
    references = schema["properties"]["source_receipt_ids"]
    references["maxItems"] = len(ids)
    if ids:
        references["items"] = {"type": "string", "enum": ids.copy()}
    return projected, schema, ids
