"""Prior public prose is frozen context backed by original events, never new truth."""

from copy import deepcopy

import pytest
from chessx.domain.errors import RuleError
from test_story import Harness, complete, event, state


def current_world(end=12, paragraphs=None):
    current = state(end)
    current["segments"][0].update(id="current", started_seq=10)
    current["stories"] = {
        "prior": {
            "segment_id": "prior",
            "source_end_seq": 5,
            "title": "Earlier public story",
            "paragraphs": paragraphs
            or [
                {
                    "text": "EARLIER_PUBLIC_STORY",
                    "event_refs": ["world:1"],
                    "claim_kind": "event_based",
                }
            ],
        }
    }
    return current


async def test_earlier_story_includes_verified_original_public_sources_and_no_private_future_or_foreign():
    paragraphs = [
        {"text": "EARLIER_PUBLIC_STORY", "event_refs": ["world:1"]},
        {"text": "PRIVATE_PAST_PARAGRAPH", "event_refs": ["world:2"]},
        {"text": "FUTURE_STORY_FACT", "event_refs": ["world:99"]},
        {"text": "FOREIGN_STORY_FACT", "event_refs": ["elsewhere:1"]},
    ]
    current = current_world(paragraphs=paragraphs)
    runner = Harness(
        [
            event(1, text="EARLIER_PUBLIC_SOURCE"),
            event(2, text="PRIVATE_PAST_SOURCE", kind="MemoryUpdated"),
            event(10, text="CURRENT_PUBLIC_SOURCE"),
            event(99, text="FUTURE_SOURCE"),
        ]
    )
    await complete(runner, current)
    context = runner.calls[0][1]
    continuity = context["continuity"]
    assert [record["id"] for record in continuity["source_events"]] == ["world:1"]
    assert continuity["prior_passages"][0]["text"] == "EARLIER_PUBLIC_STORY"
    assert continuity["omitted_passages"] == 3
    shown = repr(context)
    for private in (
        "PRIVATE_PAST_PARAGRAPH",
        "PRIVATE_PAST_SOURCE",
        "PRIVATE_MEMORY_SHOULD_NOT_APPEAR",
        "PRIVATE_TRACE_SHOULD_NOT_APPEAR",
        "FUTURE_STORY_FACT",
        "FUTURE_SOURCE",
        "FOREIGN_STORY_FACT",
    ):
        assert private not in shown
    assert "CURRENT_PUBLIC_SOURCE" in shown


async def test_pending_job_freezes_previous_prose_and_original_public_sources_against_rewrite():
    current = current_world()
    runner = Harness([event(1, text="EARLIER_PUBLIC_SOURCE"), event(10)])
    assert await runner.bard_step(current) == []
    segment = current["segments"][0]
    frozen = deepcopy(segment["story_continuity"])
    current["stories"]["prior"]["paragraphs"][0]["text"] = "EDITED_PRIOR_PROSE"
    runner.store.records[0]["payload"]["speech"] = "EDITED_SOURCE_IN_TEST"
    await runner.bard_step(current)
    assert runner.calls[0][1]["continuity"] == frozen
    assert "EDITED_" not in repr(runner.calls[0][1])


async def test_current_block_may_attribute_previous_claim_using_only_allowed_union():
    current = current_world()
    result = {
        "title": "Continuation",
        "paragraphs": [
            {
                "text": "Earlier words now received a reply.",
                "event_refs": ["world:1", "world:10"],
                "claim_kind": "event_based",
            }
        ],
    }
    runner = Harness([event(1), event(10)], override=result)
    await complete(runner, current)
    assert current["stories"]["current"]["paragraphs"] == result["paragraphs"]
    with pytest.raises(RuleError):
        runner._story_validator(runner._story_refs(runner.calls[0][1]))(
            {"paragraphs": [{"event_refs": ["world:999"]}]}
        )


async def test_composition_can_newly_cite_supplied_prior_source_not_used_by_chapter_summaries():
    class CompositionHarness(Harness):
        async def ask(
            self, current, pid, stage, slot, schema, protocol, validator=None, context=None
        ):
            if "summaries" not in context:
                return await super().ask(
                    current, pid, stage, slot, schema, protocol, validator, context
                )
            self.calls.append((slot, deepcopy(context)))
            result = {
                "title": "Continuation",
                "paragraphs": [
                    {
                        "text": "An earlier public claim became relevant again.",
                        "event_refs": ["world:1", context["events"][0]["id"]],
                        "claim_kind": "event_based",
                    }
                ],
            }
            validator(result)
            return result

    records = [event(1)] + [event(i, text="current detail " * 180) for i in range(10, 21)]
    current, runner = current_world(end=20), CompositionHarness(records, capacity=8192)
    await runner.bard_step(current)
    assert current["segments"][0]["story_job"]["chunked"]
    await complete(runner, current)
    assert "world:1" in current["stories"]["current"]["paragraphs"][0]["event_refs"]


def test_oversized_continuity_omits_whole_passage_without_truncating_its_text():
    long_text = "完整旧段落。" * 600
    current = current_world(
        paragraphs=[
            {"text": long_text, "event_refs": ["world:1"]},
            {"text": "SHORT_COMPLETE_PRIOR_PASSAGE", "event_refs": ["world:3"]},
        ]
    )
    runner = Harness([event(1), event(3), event(10)])
    continuity = runner._story_continuity(current, current["segments"][0])
    assert continuity["prior_passages"] == [
        {"segment_id": "prior", "text": "SHORT_COMPLETE_PRIOR_PASSAGE", "event_refs": ["world:3"]}
    ]
    assert continuity["omitted_passages"] == 1
    assert long_text not in repr(continuity)


def test_input_pressure_removes_prior_passages_before_current_event_facts():
    current = current_world()
    runner = Harness([event(1), event(10)], capacity=4096)
    context = runner._story_input(current, {"started_seq": 10, "source_end_seq": 12}, [event(10)])
    context["continuity"] = {
        "prior_passages": [{"text": "prior " * 5000, "event_refs": ["world:1"]}],
        "source_events": [{"id": "world:1", "payload": {"speech": "prior source"}}],
    }
    before = deepcopy(context["events"])
    fitted = runner._fit_story(current, context)
    assert fitted["events"] == before
    assert fitted["continuity"]["prior_passages"] == []
    assert fitted["continuity"]["source_events"] == [] and fitted["continuity"]["windowed"]
    assert context["continuity"]["prior_passages"]


async def test_legacy_no_previous_story_adds_no_facts_and_requires_only_one_existing_request():
    current = state(2)
    runner = Harness([event(1), event(2)])
    await complete(runner, current)
    assert len(runner.calls) == 1
    assert runner.calls[0][1]["continuity"]["prior_passages"] == []
    assert runner.calls[0][1]["continuity"]["source_events"] == []
    assert {record["id"] for record in runner.calls[0][1]["events"]} == {"world:1", "world:2"}


async def test_legacy_pending_job_without_continuity_does_not_fetch_or_rewrite_past_stories():
    current, runner = current_world(), Harness([event(1), event(10)])
    segment = current["segments"][0]
    segment["source_end_seq"] = 12
    projected = runner._story_input(current, segment, [event(10)])["events"]
    segment["story_job"] = {"blocks": [projected], "index": 0, "summaries": [], "chunked": False}
    await complete(runner, current)
    assert len(runner.calls) == 1 and "continuity" not in runner.calls[0][1]
    assert "EARLIER_PUBLIC_STORY" not in repr(runner.calls)
