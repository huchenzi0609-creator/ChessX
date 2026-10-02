"""Programmatic memory maintenance preserves evidence without role inference."""

import json
from copy import deepcopy

import pytest
from chessx.contexts.extractive import SUMMARY_LIMIT, extractive_compaction
from chessx.llm.validation import TokenCounter


def receipt(index, *, channel="ConversationMessage", payload=None, owner="a", turn=None):
    return {
        "receipt_id": f"{owner}:{index}",
        "source_event_id": f"world:{index}",
        "channel": channel,
        "payload": {
            "turn": index if turn is None else turn,
            "actor_id": "speaker",
            "payload": payload or {"speaker_name": "甲", "speech": f"消息{index}。"},
        },
    }


def state(receipts):
    return {
        "receipts": {
            "a": receipts,
            "b": [receipt(999, owner="b", payload={"speech": "OTHER_RECEIVED_SECRET"})],
        },
        "private": {
            "a": {"goals": "KEEP_MY_GOAL", "rolling_summary": ""},
            "b": {"initial_memory": "OTHER_PRIVATE_SECRET"},
        },
        "pieces": {"speaker": {"name": "GLOBAL_UNKNOWN_NAME"}},
    }


@pytest.fixture
def counter():
    return TokenCounter("cl100k_base")


def test_compaction_is_pure_and_reads_only_delivered_owner_evidence(counter):
    current = state([receipt(1, payload={"speech": "本人收到的消息。"})])
    before = deepcopy(current)
    result = extractive_compaction(current, "a", 0, 1, counter)
    serialized = json.dumps(result, ensure_ascii=False)
    assert current == before
    assert "本人收到的消息" in serialized
    for forbidden in ("OTHER_RECEIVED_SECRET", "OTHER_PRIVATE_SECRET", "GLOBAL_UNKNOWN_NAME"):
        assert forbidden not in serialized
    assert result["goal_decision"] == "keep"
    assert result["goals_markdown"] == result["reason_summary"] == ""
    assert result["additions"][0]["source_receipt_ids"] == ["a:1"]
    assert result["additions"][0]["owner_id"] == "a"
    assert result["additions"][0]["source_event_id"] == "world:1"


def test_death_news_is_preserved_as_attributed_claim_not_rule_death(counter):
    current = state(
        [receipt(1, payload={"speaker_id": "messenger", "speech": "我听说旧王去世了。"})]
    )
    entry = extractive_compaction(current, "a", 0, 1, counter)["additions"][0]
    assert entry["kind"] == "heard_claim"
    assert entry["salience"] == "important"
    assert entry["speaker_id"] == "messenger"
    assert "messenger的发言（未经核实）" in entry["text"]
    assert entry["learned_turn"] == 1
    assert json.loads(entry["text"].split("原话：", 1)[1]) == "我听说旧王去世了。"


@pytest.mark.parametrize(
    "speech",
    ["我在东北角见过他。", "往南走。", "His house is north of here.", "他说坐标是[2, 3]。"],
)
def test_direction_is_a_report_without_inventing_verified_coordinates(counter, speech):
    current = state([receipt(1, payload={"speaker_id": "speaker", "speech": speech})])
    entry = extractive_compaction(current, "a", 0, 1, counter)["additions"][0]
    assert entry["kind"] == "location_report"
    assert json.loads(entry["text"].split("原话：", 1)[1]) == speech
    assert "position" not in entry and "target" not in entry


def test_actual_move_fields_stay_as_delivered_and_hidden_fields_are_not_indexed(counter):
    current = state(
        [
            receipt(
                1,
                channel="MoveCommitted",
                payload={
                    "piece_id": "speaker",
                    "path": [{"row": 1, "col": 2}],
                    "path_visible_only": True,
                    "last_visible_position": {"row": 1, "col": 2},
                    "private": "HIDDEN",
                    "goals": "SECRET_GOAL",
                },
            )
        ]
    )
    entry = extractive_compaction(current, "a", 0, 1, counter)["additions"][0]
    assert entry["kind"] == "observed_fact"
    delivered = json.loads(entry["text"].split("规则回执：", 1)[1])
    assert delivered["path_visible_only"] is True
    assert delivered["path"] == [{"row": 1, "col": 2}]
    assert "position" not in delivered
    assert "HIDDEN" not in entry["text"] and "SECRET_GOAL" not in entry["text"]


def test_narrative_and_unknown_events_do_not_become_observed_facts(counter):
    current = state(
        [
            receipt(1, channel="BattleNarrated", payload={"public_narrative": "一人转身离开。"}),
            receipt(2, channel="UnrecognizedNotice", payload={"description": "未知来源消息"}),
        ]
    )
    result = extractive_compaction(current, "a", 0, 2, counter)
    assert [e["kind"] for e in result["additions"]] == ["received_account", "received_event"]


def test_identical_words_keep_distinct_owner_receipt_identity_and_dates(counter):
    payload = {"speaker_id": "speaker", "speech": "相同的话。"}
    current = state([receipt(1, payload=payload), receipt(2, payload=payload)])
    entries = extractive_compaction(current, "a", 0, 2, counter)["additions"]
    assert entries[0]["id"] != entries[1]["id"]
    assert entries[0]["text"] == entries[1]["text"]
    assert [entry["learned_turn"] for entry in entries] == [1, 2]
    repeated = extractive_compaction(current, "a", 0, 1, counter)["additions"][0]
    assert repeated["id"] == entries[0]["id"]
    current["receipts"]["b"] = [receipt(1, payload=payload, owner="b")]
    other = extractive_compaction(current, "b", 0, 1, counter)["additions"][0]
    assert other["id"] != entries[0]["id"]


def test_long_quote_is_lossless_in_ledger_and_only_referenced_in_summary(counter):
    speech = "他说“绝不”，接着列举：" + "完整原话不能截断成新事实。" * 150 + "这是结尾。"
    current = state([receipt(1, payload={"speaker_name": "甲", "speech": speech})])
    result = extractive_compaction(current, "a", 0, 1, counter)
    entry = result["additions"][0]
    assert len(entry["text"]) > 300
    assert json.loads(entry["text"].split("原话：", 1)[1]) == speech
    assert entry["id"] in result["summary"]
    assert "完整原文见来源回执" in result["summary"]
    assert "完整原话不能截断" not in result["summary"]
    assert counter.count(result["summary"]) <= SUMMARY_LIMIT


def test_bounded_summary_keeps_recent_complete_evidence_but_all_additions_survive(counter):
    current = state([receipt(i) for i in range(1, 101)])
    current["receipts"]["a"][-1] = receipt(100, payload={"speech": "最新消息：钥匙已经送来。"})
    current["private"]["a"]["rolling_summary"] = "旧摘要内容很长。" * 100
    result = extractive_compaction(current, "a", 0, 100, counter)
    assert len(result["additions"]) == 100
    assert "最新消息：钥匙已经送来。" in result["summary"]
    assert "旧摘要内容很长" not in result["summary"]
    assert counter.count(result["summary"]) <= SUMMARY_LIMIT
    assert result["additions"][0]["source_receipt_ids"] == ["a:1"]


def test_noise_and_out_of_range_sources_are_not_indexed(counter):
    current = state(
        [
            receipt(1, payload={"speech": "覆盖范围以外。"}),
            receipt(2, channel="TurnStarted", payload={"turn": 2}),
            receipt(3, channel="MemoryUpdated", payload={"text": "私有操作内容"}),
            receipt(4, payload={"speech": "本批完整消息。"}),
            receipt(5, payload={"speech": "尚未覆盖。"}),
        ]
    )
    result = extractive_compaction(current, "a", 1, 4, counter)
    assert [entry["source_receipt_ids"] for entry in result["additions"]] == [["a:4"]]
    assert "覆盖范围以外" not in result["summary"]
    assert "私有操作内容" not in result["summary"]
    assert "尚未覆盖" not in result["summary"]


@pytest.mark.parametrize("start,end", [(-1, 1), (1, 0), (0, 3)])
def test_invalid_range_cannot_accidentally_read_a_negative_slice(counter, start, end):
    with pytest.raises(ValueError, match="owner's archive"):
        extractive_compaction(state([receipt(1)]), "a", start, end, counter)
