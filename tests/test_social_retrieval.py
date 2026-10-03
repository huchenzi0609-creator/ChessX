"""Retrieval expands names and words while retaining owner scope and claim sources."""

import json
from copy import deepcopy

from chessx.contexts.memory import recall, terms
from chessx.contexts.retrieval import alias_groups, expanded_terms
from chessx.domain.models import Scenario
from chessx.domain.scenarios import create_world


def world():
    scenario = Scenario.model_validate(
        {
            "scenario_id": "social",
            "title": "Social retrieval fixture",
            "board": {"rows": 8, "cols": 8},
            "pieces": [
                {
                    "id": pid,
                    "name": name,
                    "type": "rook",
                    "color": "blue",
                    "position": {"row": row, "col": col},
                }
                for pid, name, row, col in [
                    ("a", "Alice", 1, 1),
                    ("b", "王明", 7, 7),
                    ("c", "Other", 6, 7),
                ]
            ],
        }
    )
    state = create_world(
        scenario, {"a": "我认识王明（Wang Ming）。", "b": "SECRET_B_PERSONA"}, "social"
    )
    state["turn"] = 50
    return state


def received(seq, text, speaker="b", name="王明", turn=1):
    return {
        "receipt_id": f"a:{seq}",
        "source_event_id": f"social:{seq}",
        "channel": "ConversationMessage",
        "payload": {
            "type": "ConversationMessage",
            "turn": turn,
            "actor_id": speaker,
            "payload": {"speaker_id": speaker, "speaker_name": name, "speech": text},
        },
    }


def test_owner_markdown_english_alias_finds_original_chinese_utterance():
    state = world()
    source = received(1, "王明说过，那封信仍留在旧屋。")
    state["receipts"]["a"] = [source] + [
        received(i, f"无关琐事第{i}件。", turn=i) for i in range(2, 22)
    ]
    result = recall(state, "a", {"latest_message": {"speech": "What did Wang Ming say?"}})
    assert any(record["receipt_id"] == "a:1" for record in result["archive_evidence"])
    assert (
        next(r for r in result["archive_evidence"] if r["receipt_id"] == "a:1")["payload"]["speech"]
        == source["payload"]["payload"]["speech"]
    )
    groups = alias_groups(state["private"]["a"], [], {"pieces": []})
    assert terms("王明") <= expanded_terms("Wang Ming", terms, groups)


def test_synonym_death_query_recovers_old_obituary_word_without_new_truth():
    state = world()
    state["receipts"]["a"] = [received(1, "那个人早已亡故。", turn=1)] + [
        received(i, f"天气晴朗，今日记事{i}。", turn=i) for i in range(2, 32)
    ]
    result = recall(state, "a", {"latest_message": {"speech": "Tell me about the death."}})
    record = next(r for r in result["archive_evidence"] if r["receipt_id"] == "a:1")
    assert record["turn"] == 1 and record["payload"]["speech"] == "那个人早已亡故。"
    assert "not that a speaker's claim is true" in result["scope"]
    assert "world_fact" not in record and "truth" not in record


def test_relationship_claim_and_later_denial_remain_separate_attributed_sources():
    state = world()
    state["receipts"]["a"] = [
        received(1, "他是我的父亲。", turn=1),
        received(2, "先前说错了，他不是我的父亲。", turn=30),
    ]
    result = recall(state, "a", {"latest_message": {"speech": "Who is your father?"}})
    relations = result["social_evidence"]["relations"]
    assert {record["receipt_id"] for record in relations} == {"a:1", "a:2"}
    assert all(record["actor_id"] == "b" for record in relations)
    assert {record["payload"]["speech"] for record in relations} == {
        "他是我的父亲。",
        "先前说错了，他不是我的父亲。",
    }
    assert "not certified states" in result["social_evidence"]["scope"]


def test_promise_and_later_denial_remain_claims_not_a_certified_commitment_state():
    state = world()
    state["receipts"]["a"] = [
        received(1, "我承诺将信交给你。", turn=1),
        received(2, "我没有作过这样的承诺。", turn=20),
    ]
    result = recall(state, "a", {"latest_message": {"speech": "Did you promise to deliver it?"}})
    commitments = result["social_evidence"]["commitments"]
    assert {record["receipt_id"] for record in commitments} == {"a:1", "a:2"}
    assert all("fulfilled" not in record and "binding" not in record for record in commitments)
    assert "denial or fulfillment" in result["social_evidence"]["scope"]


def test_unseen_other_private_names_aliases_and_receipts_cannot_enter_owner_recall():
    state = world()
    state["private"]["b"].update(
        memory="SECRET_B_MEMORY", retrieval_aliases={"Other": ["SECRET_ALIAS"]}
    )
    state["receipts"]["b"] = [{**received(1, "SECRET_OTHER_DIALOGUE"), "receipt_id": "b:1"}]
    state["private"]["b"]["long_term_memory"] = {
        "other": {
            "text": "SECRET_OTHER_LONG_TERM",
            "kind": "heard_claim",
            "source_receipt_ids": ["b:1"],
        }
    }
    state["receipts"]["a"] = [received(1, "我答应稍后谈谈。")]
    result = recall(state, "a", {"latest_message": {"speech": "Other promises"}})
    encoded = json.dumps(result, ensure_ascii=False)
    assert "SECRET_" not in encoded and "b:1" not in encoded
    assert "SECRET_ALIAS" not in str(
        alias_groups(state["private"]["a"], state["receipts"]["a"], {"pieces": []})
    )


def test_archive_text_and_original_owner_metadata_remain_unchanged_after_retrieval():
    state = world()
    state["receipts"]["a"] = [received(1, "我保证来找你，但现在还在西边。")]
    state["private"]["a"]["retrieval_aliases"] = {"王明": ["王先生", "Wang Ming"]}
    before = deepcopy(state)
    result = recall(state, "a", {"latest_message": {"speech": "王先生许下了什么承诺？"}})
    assert state == before
    result["archive_evidence"][0]["payload"]["speech"] = "changed in caller"
    assert state == before


def test_synonym_identity_expansion_never_links_two_different_people_as_one_identity():
    state = world()
    state["receipts"]["a"] = [received(1, "他像父亲一样待我。")]
    result = recall(state, "a", {"latest_message": {"speech": "father identity"}})
    assert all("same_person" not in record for record in result["archive_evidence"])
    assert "not that a speaker's claim is true" in result["scope"]


def test_english_synonyms_match_whole_words_without_deadline_becoming_death():
    assert not (terms("去世") & expanded_terms("The delivery deadline is tomorrow.", terms))
    assert terms("死亡") & expanded_terms("They said he was dead.", terms)
