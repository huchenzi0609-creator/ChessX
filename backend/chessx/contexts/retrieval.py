"""Lexical expansion and social evidence indexes over one owner's delivered data.

Aliases help find an existing source; they never establish identities, relations,
promises, truth or another role's current location.
"""

import re

SYNONYM_GROUPS = (
    ("去世", "死亡", "死讯", "身亡", "亡故", "died", "death", "dead"),
    ("父亲", "父王", "生父", "father"),
    ("母亲", "生母", "mother"),
    ("出生", "身世", "血缘", "出身", "identity", "born", "birth"),
    ("答应", "承诺", "约定", "保证", "promise", "promised", "commitment"),
    ("交给", "转交", "送到", "递交", "transfer", "deliver"),
    ("位置", "何处", "哪里", "所在", "方位", "where", "location"),
    ("信任", "可信", "相信", "trust"),
    ("怀疑", "不信", "疑虑", "suspect", "distrust"),
)
SOCIAL = re.compile(
    r"父亲|母亲|亲人|兄弟|姐妹|朋友|敌人|信任|怀疑|背叛|承诺|答应|约定|保证|"
    r"father|mother|friend|enemy|trust|suspect|promise|commitment",
    re.I,
)
COMMITMENT = re.compile(
    r"答应|承诺|约定|保证|我会|我将|promise|promised|will\s+(?:go|bring|give|meet)", re.I
)


def alias_groups(own: dict, receipts: list[dict], observation: dict) -> list[set[str]]:
    groups = []
    # Only identities already delivered to this owner or currently visible.
    for person in observation.get("pieces", []):
        if person.get("name") and person.get("id"):
            groups.append({person["name"], person["id"]})
    for pid, person in own.get("spatial_memory", {}).get("seen_people", {}).items():
        if person.get("name"):
            groups.append({pid, person["name"]})
    for receipt in receipts:
        event = receipt.get("payload", {})
        payload = event.get("payload", event)
        if not isinstance(payload, dict):
            continue
        for id_field, name_field in (("speaker_id", "speaker_name"), ("piece_id", "piece_name")):
            if payload.get(id_field) and payload.get(name_field):
                groups.append({payload[id_field], payload[name_field]})
    # Author-supplied name pairs, e.g. 张三（Zhang San）, remain descriptive data.
    persona = own.get("persona", "")
    for name, aliases in re.findall(
        r"([\w\u4e00-\u9fff]{2,24})[（(]([^()（）\n]{2,64})[）)]", persona
    ):
        values = {name, *(s.strip() for s in re.split(r"[,，、/|]", aliases))}
        groups.append({s for s in values if 1 < len(s) <= 32})
    # An optional owner-authored index is useful for imported character designs.
    for name, aliases in own.get("retrieval_aliases", {}).items():
        if isinstance(name, str) and isinstance(aliases, list):
            groups.append({name, *(a for a in aliases if isinstance(a, str) and len(a) <= 32)})
    return groups


def expanded_terms(text: str, tokenizer, groups=()) -> set[str]:
    found = tokenizer(text)
    lowered = text.casefold()

    def present(word):
        word = word.casefold()
        return (
            bool(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", lowered))
            if word.isascii()
            else word in lowered
        )

    for group in (*SYNONYM_GROUPS, *groups):
        if any(present(word) for word in group if len(word) > 1):
            for word in group:
                found.update(tokenizer(word))
    return found


def social_evidence(records: list[dict], query_terms: set[str], tokenizer) -> dict:
    """Pointers plus complete delivered speech, not a generated social graph."""
    matches = []
    for index, record in enumerate(records):
        text = record.get("payload", {}).get("speech", "")
        if SOCIAL.search(text):
            matches.append((len(tokenizer(text) & query_terms), index, record, text))
    matches.sort(key=lambda item: (-item[0], -item[1]))
    return {
        "relations": [record for _, _, record, text in matches if not COMMITMENT.search(text)][:3],
        "commitments": [record for _, _, record, text in matches if COMMITMENT.search(text)][:3],
        "scope": "Personally received statements only; relations and promises are attributed claims, not certified states. Later denial or fulfillment must be evaluated from its own evidence.",
    }
