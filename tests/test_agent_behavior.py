"""Owner-scoped mobility, natural-speech boundaries and unconstrained bard output."""

import json
from copy import deepcopy

import httpx
import pytest
from chessx.config import Settings
from chessx.contexts import build_context
from chessx.contexts.budgets import fit_context
from chessx.domain.errors import RuleError
from chessx.domain.models import Scenario
from chessx.domain.scenarios import create_world
from chessx.llm import FixtureAdapter, ModelError, ModelResult, ProviderAdapter
from chessx.llm.provider import CHARACTER_BEHAVIOR_VERSION, compose_messages
from chessx.llm.speech import validate_character_speech, validate_speech_text
from chessx.llm.validation import validate_output
from chessx.rules.geometry import legal_move
from chessx.runtime import schemas
from chessx.runtime.runner import WorldRunner
from chessx.storage import Store


def world(kind="rook"):
    scenario = Scenario.model_validate(
        {
            "scenario_id": "behavior",
            "title": "Behavior fixture",
            "board": {"rows": 8, "cols": 8},
            "pieces": [
                {
                    "id": "a",
                    "name": "A",
                    "type": kind,
                    "color": "青",
                    "position": {"row": 3, "col": 3},
                },
                {
                    "id": "b",
                    "name": "B",
                    "type": "rook",
                    "color": "青",
                    "position": {"row": 4, "col": 4},
                },
                {
                    "id": "hidden",
                    "name": "HIDDEN_NAME",
                    "type": "rook",
                    "color": "青",
                    "position": {"row": 7, "col": 7},
                },
            ],
        }
    )
    state = create_world(
        scenario, {"a": "A_PRIVATE", "b": "B_PRIVATE", "hidden": "HIDDEN_PRIVATE"}, "behavior"
    )
    state.update(mode="fixture", error=None)
    return state


def receipt(speaker, speech, cid="c", seq=1):
    return {
        "receipt_id": f"a:{seq}",
        "source_event_id": f"world_fixture:{seq}",
        "channel": "ConversationMessage",
        "payload": {
            "actor_id": speaker,
            "turn": 1,
            "payload": {"conversation_id": cid, "speaker_id": speaker, "speech": speech},
        },
    }


@pytest.mark.parametrize("kind", ["pawn", "rook", "bishop", "queen", "king", "knight"])
def test_move_options_match_geometry_without_revealing_unseen_occupants(kind):
    state = world(kind)
    context = build_context(state, "a", "action")
    actual = {tuple(target) for target in context["navigation"]["geometric_move_targets"]}
    expected = set()
    for row in range(8):
        for col in range(8):
            if (row, col) == (3, 3):
                continue
            try:
                legal_move(state, "a", {"row": row, "col": col})
            except RuleError:
                continue
            expected.add((row, col))
    assert actual == expected
    encoded = json.dumps(context)
    assert (
        "HIDDEN_NAME" not in encoded
        and "HIDDEN_PRIVATE" not in encoded
        and "B_PRIVATE" not in encoded
    )
    moved = deepcopy(state)
    moved["pieces"]["hidden"]["position"] = {"row": 7, "col": 6}
    assert build_context(moved, "a", "action")["navigation"] == context["navigation"]


def test_rest_snapshot_and_battle_lock_remove_options_but_conversation_does_not():
    state = world()
    state["pieces"]["a"]["conversation_id"] = "c"
    action = build_context(state, "a", "action")["navigation"]
    assert action["can_choose_move_in_this_stage"] and action["geometric_move_targets"]
    assert action["movement_leaves_conversation"] and not action["conversation_blocks_movement"]
    assert not build_context(state, "a", "ordinary")["navigation"]["can_choose_move_in_this_stage"]
    state["rest_at_start"] = {"a": 1}
    state["pieces"]["a"]["rest_remaining"] = 0
    assert build_context(state, "a", "action")["navigation"]["geometric_move_targets"] == []
    state["rest_at_start"]["a"] = 0
    state["locked_this_turn"] = ["a"]
    assert build_context(state, "a", "action")["navigation"]["geometric_move_targets"] == []


def test_dialogue_recall_uses_only_delivered_current_conversation_and_survives_event_noise():
    state = world()
    state["receipts"]["a"] = [
        receipt("a", "OWN_WORDS", seq=1),
        receipt("b", "HEARD_WORDS", seq=2),
        receipt("b", "OTHER_CONVERSATION", cid="other", seq=3),
    ] + [{"channel": "TurnCompleted", "payload": {}} for _ in range(40)]
    state["receipts"]["b"] = [receipt("hidden", "NOT_HEARD_BY_A", seq=99)]
    context = build_context(state, "a", "ordinary", {"conversation_id": "c"})
    recall = context["dialogue_recall"]
    assert recall["last_own_utterances"] == ["OWN_WORDS"]
    assert [x["speech"] for x in recall["recent_heard"]] == ["OWN_WORDS", "HEARD_WORDS"]
    assert "NOT_HEARD_BY_A" not in json.dumps(context)


def test_known_location_keeps_only_own_old_sighting_and_does_not_update_from_global_state():
    state = world()
    state["receipts"]["a"] = [
        {
            "channel": "MoveCommitted",
            "payload": {
                "turn": 1,
                "actor_id": "hidden",
                "payload": {"piece_id": "hidden", "position": {"row": 1, "col": 1}},
            },
        }
    ]
    known = build_context(state, "a", "action")["navigation"]["known_locations"]
    last_seen = next(item for item in known if item["piece_id"] == "hidden")
    assert last_seen["position"] == {"row": 1, "col": 1}
    assert last_seen["current_visible"] is False
    assert "HIDDEN_NAME" not in json.dumps(known)


@pytest.mark.parametrize(
    "speech",
    [
        "你刚才说过。（参考233）",
        "我还记得。[233]",
        "我知道【引用：233】",
        "来源是receipt_id=a:233",
        "参考233。",
        "world_abc123:233",
        "message_123456789abc",
    ],
)
def test_internal_citations_cannot_enter_character_speech(speech):
    with pytest.raises(ModelError) as error:
        validate_speech_text(speech)
    assert error.value.code == "SPEECH_INTERNAL_REFERENCE"
    assert speech not in str(error.value)


@pytest.mark.parametrize(
    "speech",
    [
        "好。",
        "你刚才说他在桥边。",
        "233号屋子没人。",
        "这件事我还不知道。",
        "我参考233年的历法来算。",
    ],
)
def test_natural_reminders_numbers_and_short_acknowledgments_are_not_citations(speech):
    validate_speech_text(speech)


def test_long_exact_repetition_is_rejected_but_short_repetition_and_new_response_are_allowed():
    long = "我们已经讨论过这个问题，现在应该先去寻找牧人确认当时发生了什么事情。"
    context = {"dialogue_recall": {"last_own_utterances": [long, "好。"]}}
    with pytest.raises(ModelError) as error:
        validate_character_speech({"speech": long}, context, "ordinary")
    assert error.value.code == "SPEECH_REPETITIVE"
    validate_character_speech({"speech": "好。"}, context, "ordinary")
    validate_character_speech({"speech": "我去桥边看看。"}, context, "ordinary")
    validate_character_speech({"speech": long}, context, "negotiation")


def test_old_frozen_discipline_gets_new_behavior_protocol_without_replacing_snapshot():
    state = world()
    state["discipline"] = "OLD_FROZEN_DISCIPLINE"
    context = build_context(state, "a", "ordinary")
    messages = compose_messages("ordinary", context, {"type": "object"})
    assert CHARACTER_BEHAVIOR_VERSION in messages[0]["content"]
    assert "OLD_FROZEN_DISCIPLINE" in messages[1]["content"]
    assert state["discipline"] == "OLD_FROZEN_DISCIPLINE"


def test_long_dialogue_recall_windows_old_turns_without_changing_source_archive():
    original = {
        "persona": "OWNER_ANCHOR",
        "dialogue_recall": {
            "recent_heard": [
                {"speaker_id": "a", "speech": f"utterance {i}: " + "word " * 500} for i in range(12)
            ],
            "last_own_utterances": [],
        },
    }
    before = deepcopy(original)
    fitted = fit_context(
        "ordinary",
        original,
        {"type": "object"},
        FixtureAdapter().counter,
        max_output_tokens=512,
        input_limit=2000,
    )
    assert 1 <= len(fitted["dialogue_recall"]["recent_heard"]) < 12
    assert (
        fitted["dialogue_recall"]["recent_heard"][-1]
        == original["dialogue_recall"]["recent_heard"][-1]
    )
    assert fitted["persona"] == "OWNER_ANCHOR" and original == before


async def test_bard_accepts_complete_manuscript_above_old_limit_and_keeps_citations():
    text = "A witnessed event was remembered in the story. " * 400
    content = {
        "title": "Story",
        "paragraphs": [{"text": text, "event_refs": ["world:233"], "claim_kind": "event_based"}],
    }
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(content),
                            "reasoning_content": "brief trace",
                        },
                    }
                ]
            },
        )

    adapter = ProviderAdapter(
        "https://provider.invalid/v1",
        "SYNTHETIC_KEY",
        "model",
        tokenizer_agreement=True,
        transport=httpx.MockTransport(handler),
    )
    adapter._probed = True
    result = await adapter.generate(
        "bard", {"events": [{"id": "world:233"}]}, schemas.BARD, max_output_tokens=8192
    )
    assert result.output_tokens > 2048 and result.content == content
    assert requests[0]["max_tokens"] == 9192
    assert "natural-dialogue-v1" not in requests[0]["messages"][0]["content"]
    # The same long content remains illegal as a piece speech.
    with pytest.raises(ModelError) as error:
        validate_output(
            json.dumps({"speech": text}), "trace", {"type": "object"}, adapter.counter, None
        )
    assert error.value.code == "SPEECH_BUDGET_EXCEEDED"


async def test_bad_reference_is_retried_before_any_speech_commit(tmp_path):
    state = world()
    store = Store(tmp_path / "speech.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    calls = []

    class CorrectingAdapter(FixtureAdapter):
        async def generate(self, stage, context, schema, *, max_output_tokens=512):
            calls.append(deepcopy(context))
            content = {
                "kind": "say",
                "speech": "你说过。（参考233）" if len(calls) == 1 else "那我去看看。",
            }
            return ModelResult(content, "trace", 10, 10, 1, 0)

    runner.adapters[state["world_id"]] = CorrectingAdapter()
    schema = {
        "type": "object",
        "properties": {"kind": {"const": "say"}, "speech": {"type": "string"}},
        "required": ["kind", "speech"],
    }
    result = await runner.ask(state, "a", "ordinary", "test:speech", schema, {})
    assert result["speech"] == "那我去看看。"
    assert calls[1]["protocol"]["validation_feedback"]["code"] == "SPEECH_INTERNAL_REFERENCE"
    assert "natural spoken language" in calls[1]["protocol"]["validation_feedback"]["instruction"]
    assert store.events(state["world_id"])["events"] == []
    failed = store.db.execute(
        "SELECT status,result FROM operations ORDER BY rowid LIMIT 1"
    ).fetchone()
    assert failed[0] == "failed" and "参考233" not in failed[1]
    await runner.close()
    store.close()


@pytest.mark.parametrize("total_budget", [4096, 50000])
async def test_bard_runner_allocates_remaining_capacity_instead_of_2048(tmp_path, total_budget):
    state = world()
    state["budget"] = {"max_total_tokens": total_budget}
    store = Store(tmp_path / "bard.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path, context_capacity=16384))
    limits = []

    class BardAdapter(FixtureAdapter):
        async def generate(self, stage, context, schema, *, max_output_tokens=512):
            limits.append(max_output_tokens)
            return ModelResult(
                {
                    "title": "Story",
                    "paragraphs": [
                        {
                            "text": "Something happened.",
                            "event_refs": ["world:1"],
                            "claim_kind": "event_based",
                        }
                    ],
                },
                "trace",
                100,
                40,
                1,
                0,
            )

    runner.adapters[state["world_id"]] = BardAdapter()
    await runner.ask(
        state, None, "bard", "bard:1", schemas.BARD, {}, context={"events": [{"id": "world:1"}]}
    )
    assert 2048 < limits[0] < 16384
    assert limits[0] + 1000 < total_budget
    await runner.close()
    store.close()
