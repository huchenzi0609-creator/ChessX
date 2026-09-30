from copy import deepcopy
from types import SimpleNamespace

import pytest
from chessx.domain.errors import RuleError
from chessx.llm import ModelError
from chessx.llm.validation import TokenCounter
from chessx.runtime.story import StoryMixin


def event(index, *, text="A witnessed world event", kind="ConversationMessage"):
    return {
        "event_id": f"world:{index}",
        "seq": index,
        "type": kind,
        "payload": {
            "speech": text,
            "speaker_id": "a",
            "reasoning": "PRIVATE_TRACE_SHOULD_NOT_APPEAR",
        },
    }


def state(end=3):
    return {
        "world_id": "world",
        "mode": "fixture",
        "seq": end,
        "pieces": {"a": {"id": "a"}},
        "stories": {},
        "private": {"a": {"memory": "PRIVATE_MEMORY_SHOULD_NOT_APPEAR"}},
        "segments": [
            {"id": "s1", "from_turn": 1, "target_turn": 1, "started_seq": 1, "status": "running"}
        ],
        "completed_turn": 1,
        "target_turn": 1,
        "phase": "BARD",
        "lifecycle": "RUNNING",
        "cursor": {},
    }


class FakeStore:
    def __init__(self, events):
        self.records = events

    def events(self, wid, after=0, limit=1000):
        records = [record for record in self.records if record["seq"] > after]
        selected = records[:limit]
        return {
            "events": deepcopy(selected),
            "next_cursor": selected[-1]["seq"] if selected else after,
            "has_more": len(records) > limit,
        }


class Harness(StoryMixin):
    def __init__(self, events, capacity=16384, override=None, large_summaries=False):
        self.store = FakeStore(events)
        self.settings = SimpleNamespace(context_capacity=capacity)
        self.model = SimpleNamespace(counter=TokenCounter("cl100k_base"))
        self.calls = []
        self.override = override
        self.large_summaries = large_summaries

    def adapter(self, state):
        return self.model

    async def ask(self, state, pid, stage, slot, schema, protocol, validator=None, context=None):
        self.calls.append((slot, deepcopy(context)))
        result = self.override or {
            "title": "Event-based fixture",
            "paragraphs": [
                {
                    "text": "summary " * 500 if self.large_summaries else "Events were recorded.",
                    "event_refs": [source["id"] for source in context["events"]],
                    "claim_kind": "event_based",
                }
            ],
        }
        if validator:
            validator(result)
        return deepcopy(result)


async def complete(runner, current, limit=100):
    for _ in range(limit):
        before = len(runner.calls)
        drafts = await runner.bard_step(current)
        assert len(runner.calls) - before <= 1
        if drafts:
            return drafts
        current["seq"] += 1  # The real Store persists an implicit CursorAdvanced.
    raise AssertionError("story job did not terminate")


async def test_small_story_prepares_durably_then_calls_once_with_frozen_sources():
    runner, current = Harness([event(1), event(2), event(3), event(4)]), state(3)
    assert await runner.bard_step(current) == []
    assert not runner.calls and current["segments"][0]["source_end_seq"] == 3
    current["seq"] = 4
    drafts = await runner.bard_step(current)
    assert len(runner.calls) == 1
    assert [source["id"] for source in runner.calls[0][1]["events"]] == [
        "world:1",
        "world:2",
        "world:3",
    ]
    assert current["stories"]["s1"]["source_end_seq"] == 3
    assert current["segments"][0]["ended_seq"] == 3
    assert current["lifecycle"] == "PAUSED_SEGMENT"
    assert drafts[0]["type"] == "BardCreated"
    assert "story_job" not in current["segments"][0]


async def test_story_inputs_never_include_private_ids_text_or_future_events():
    runner = Harness(
        [
            event(1, kind="MemoryUpdated", text="PRIVATE_MEMORY_EVENT"),
            event(2),
            event(3, kind="ReasoningTrace"),
            event(4, text="FUTURE_FACT"),
        ]
    )
    current = state(3)
    await complete(runner, current)
    serialized = repr(runner.calls)
    assert "world:2" in serialized
    assert all(
        value not in serialized
        for value in [
            "world:1",
            "world:3",
            "world:4",
            "PRIVATE_MEMORY_EVENT",
            "PRIVATE_MEMORY_SHOULD_NOT_APPEAR",
            "PRIVATE_TRACE_SHOULD_NOT_APPEAR",
            "FUTURE_FACT",
        ]
    )


@pytest.mark.parametrize("invalid_ref", ["world:1", "world:999", "summary:0"])
async def test_private_future_and_summary_citations_rejected(invalid_ref):
    result = {
        "title": "Invalid",
        "paragraphs": [{"text": "Claim", "event_refs": [invalid_ref], "claim_kind": "event_based"}],
    }
    runner = Harness([event(1, kind="MemoryUpdated"), event(2)], override=result)
    current = state(2)
    await runner.bard_step(current)
    with pytest.raises(RuleError) as error:
        await runner.bard_step(current)
    assert error.value.code == "STORY_REFERENCE_INVALID"
    assert current["segments"][0]["story_job"]["index"] == 0
    assert current["stories"] == {}


async def test_long_story_chunks_all_facts_in_order_then_composes_original_references():
    records = [event(index, text=f"Fact {index}: " + "detail " * 250) for index in range(1, 21)]
    runner, current = Harness(records, capacity=8192), state(20)
    await runner.bard_step(current)
    job = current["segments"][0]["story_job"]
    assert len(job["blocks"]) > 1
    flattened = [source["id"] for block in job["blocks"] for source in block]
    assert flattened == [record["event_id"] for record in records]
    block_count = len(job["blocks"])
    await complete(runner, current)
    assert len(runner.calls) == block_count + 1
    composition = runner.calls[-1][1]
    assert "summaries" in composition
    assert {source["id"] for source in composition["events"]} == set(flattened)
    assert set(current["stories"]["s1"]["paragraphs"][0]["event_refs"]) == set(flattened)


async def test_oversized_single_event_pauses_without_losing_fact():
    runner, current = Harness([event(1, text="essential " * 20000)]), state(1)
    original = deepcopy(current)
    with pytest.raises(ModelError) as error:
        await runner.bard_step(current)
    assert error.value.code == "CONTEXT_BUDGET_EXCEEDED"
    assert not runner.calls and current["stories"] == {}
    assert current["private"] == original["private"]


async def test_oversized_composition_preserves_completed_blocks_and_pauses():
    records = [event(index, text="detail " * 300) for index in range(1, 12)]
    runner, current = Harness(records, capacity=4096, large_summaries=True), state(11)
    await runner.bard_step(current)
    job = current["segments"][0]["story_job"]
    for _ in job["blocks"]:
        await runner.bard_step(current)
    before = deepcopy(job)
    with pytest.raises(ModelError) as error:
        await runner.bard_step(current)
    assert error.value.code == "CONTEXT_BUDGET_EXCEEDED"
    assert job == before and len(runner.calls) == len(job["blocks"])


async def test_finishing_pending_old_story_allows_resumed_additional_turns():
    runner, current = Harness([event(1), event(2)]), state(2)
    await runner.bard_step(current)
    current["target_turn"] = 4
    current["seq"] += 1
    await runner.bard_step(current)
    assert "s1" in current["stories"]
    assert current["segments"][0]["target_turn"] == 1
    assert current["segments"][-1]["from_turn"] == 2
    assert current["segments"][-1]["target_turn"] == 4
    assert current["phase"] == "START" and current["lifecycle"] == "RUNNING"
    assert current["cursor"] == {}


async def test_empty_world_generates_no_model_request():
    runner, current = Harness([event(1, kind="TurnCompleted")]), state(1)
    current["pieces"] = {}
    await runner.bard_step(current)
    assert not runner.calls
    assert current["stories"]["s1"]["paragraphs"] == []
    assert current["lifecycle"] == "PAUSED_SEGMENT"
