"""Lossless owner-receipt indexing without a model call or character reflection.

The durable additions contain attributed source text, not synthesized beliefs.
The small summary is only an index; omitting an old record from that display
does not discard its addition or change a character's goals.
"""

import hashlib
import json
import re

from .memory import HIGH_SIGNAL, NOISE, compact_receipt

SPEECH_CHANNELS = {"ConversationMessage", "NegotiationMessage", "SettlementMessage"}
ACCOUNT_CHANNELS = {"BattleNarrated", "BattleNarrative"}
OBSERVATION_CHANNELS = {
    "MoveCommitted",
    "MovementCommitted",
    "PieceMoved",
    "ObjectPicked",
    "ObjectPickedUp",
    "ObjectDropped",
    "ObjectTransferred",
    "ObjectTransferAccepted",
    "ConversationCreated",
    "ConversationJoined",
    "ConversationLeft",
    "ConversationMerged",
    "ConversationClosed",
    "MembershipChanged",
    "GovernanceResolved",
    "BattleStarted",
    "BattleJoined",
    "BattleMerged",
    "BattlesMerged",
    "BattleOutcomeCommitted",
    "CombatContinued",
    "SettlementStarted",
    "LoserReleased",
    "SurrenderDeclared",
    "Death",
    "PieceDied",
    "DeathExecuted",
    "DeathCommitted",
    "ColorChanged",
    "PieceWaited",
}
LOCATION_WORDS = re.compile(
    r"[东西南北]{1,2}(?:边|面|侧|方|向|角)|"
    r"(?:向|往|朝)[东西南北]{1,2}(?:走|去|行|移|找|搜|探|[，。！？\s]|$)|"
    r"\b(?:north|south|east|west|northeast|northwest|southeast|southwest)\b|"
    r"坐标|(?:row|col)\s*[:=]|\[\s*\d+\s*,\s*\d+\s*\]",
    re.IGNORECASE,
)
SUMMARY_LIMIT = 350
SUMMARY_HEADER = "本人回执摘录索引。发言及传来叙述未经核实；较早记录仍在长期账本和来源回执。"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _entry(pid, receipt):
    source = compact_receipt(receipt)
    receipt_id = source["receipt_id"]
    if not isinstance(receipt_id, str) or not receipt_id:
        return None
    channel, payload = source["channel"], source["payload"]
    if channel in NOISE:
        return None
    turn = source.get("turn")
    # IDs/names are already present in this owner's delivered evidence. Never
    # look up another piece in global state to fill an unknown identity.
    speaker_id = payload.get("speaker_id", source.get("actor_id"))
    speaker_name = payload.get("speaker_name") or speaker_id or "未标明发言者"
    speech = payload.get("speech")
    if not isinstance(speech, str) and channel in SPEECH_CHANNELS:
        speech = payload.get("text")
    if isinstance(speech, str):
        kind = "location_report" if LOCATION_WORDS.search(speech) else "heard_claim"
        text = f"{speaker_name}的发言（未经核实），原话：{_json(speech)}"
    else:
        account = payload.get("public_narrative")
        if not isinstance(account, str) and channel in ACCOUNT_CHANNELS:
            account = payload.get("text")
        if isinstance(account, str):
            kind = "received_account"
            text = f"收到的叙述（非亲见事实），原文：{_json(account)}"
        else:
            kind = "observed_fact" if channel in OBSERVATION_CHANNELS else "received_event"
            text = f"{channel}规则回执：{_json(payload)}"
    key = "evidence_" + hashlib.sha256(f"{pid}\0{receipt_id}".encode()).hexdigest()[:24]
    return {
        "id": key,
        "owner_id": pid,
        "kind": kind,
        "text": text,
        # This tag controls retrieval only. It does not validate a claim or
        # promote dialogue about death into a rule-domain death event.
        "salience": "important" if HIGH_SIGNAL.search(text) else "normal",
        "source_receipt_ids": [receipt_id],
        "source_event_id": receipt.get("source_event_id"),
        "learned_turn": turn if isinstance(turn, int) else None,
        "speaker_id": speaker_id,
        "speaker_name": speaker_name if isinstance(speech, str) else None,
    }


def _summary(entries, previous, counter):
    lines = [SUMMARY_HEADER]
    chosen = []
    # Prefer the newest complete evidence. A long utterance gets a reference,
    # never a cut-off quotation that could change the speaker's meaning.
    for entry in reversed(entries):
        turn = entry["learned_turn"]
        date = f"第{turn}回合" if isinstance(turn, int) else "回合未标明"
        reference = f"{date} {entry['kind']} [{entry['id']}]"
        complete = f"{reference}：{entry['text']}"
        if counter.count("\n".join(lines + chosen + [complete])) > SUMMARY_LIMIT:
            complete = f"{reference}（完整原文见来源回执）"
        if counter.count("\n".join(lines + chosen + [complete])) <= SUMMARY_LIMIT:
            chosen.append(complete)
    lines.extend(reversed(chosen))
    if isinstance(previous, str) and previous:
        old = f"此前索引：\n{previous}"
        if counter.count("\n".join(lines + [old])) <= SUMMARY_LIMIT:
            lines.append(old)
    summary = "\n".join(lines)
    # A supported tokenizer can fit the fixed short header. If an injected
    # counter cannot, do not manufacture a truncated Chinese fragment.
    return summary if counter.count(summary) <= SUMMARY_LIMIT else ""


def extractive_compaction(state, pid, start, end, counter):
    """Index a contiguous prefix of this owner's delivered archive, purely.

    Entries are keyed by owner plus receipt, so repeated words from different
    dates/speakers retain separate provenance. Text is complete and may exceed
    the model-output schema's short-text bound; this is not a model response.
    Prompt projections and the summary have their own display bounds.
    """
    receipts = state.get("receipts", {}).get(pid, [])
    if not 0 <= start <= end <= len(receipts):
        raise ValueError("Compaction range must be within the owner's archive")
    additions = {}
    for receipt in receipts[start:end]:
        entry = _entry(pid, receipt)
        if entry is not None:
            additions[entry["id"]] = entry
    own = state.get("private", {}).get(pid, {})
    return {
        "summary": _summary(
            list(additions.values()),
            own.get("rolling_summary", "")
            if own.get("compaction_provenance") == "extractive_receipts_v1"
            else "",
            counter,
        ),
        "additions": list(additions.values()),
        "goal_decision": "keep",
        "goal_status": "active",
        "goals_markdown": "",
        "reason_summary": "",
    }
