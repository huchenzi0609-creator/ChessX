import json

from chessx.contexts import bard_context, battle_context, build_context, observe, project_event


def world():
    return {
        "world_id": "w",
        "turn": 1,
        "board": {"rows": 8, "cols": 8},
        "pieces": {
            "a": {
                "id": "a",
                "name": "A",
                "type": "rook",
                "color": "red",
                "position": {"row": 0, "col": 0},
                "alive": True,
                "rest_remaining": 0,
            },
            "b": {
                "id": "b",
                "name": "B",
                "type": "rook",
                "color": "blue",
                "position": {"row": 1, "col": 1},
                "alive": True,
                "rest_remaining": 0,
            },
            "c": {
                "id": "c",
                "name": "BLIND_PERSON_c8a",
                "type": "knight",
                "position": {"row": 7, "col": 7},
                "alive": True,
            },
        },
        "private": {
            "a": {"persona": "SECRET_A_7f2", "memory": "MEM_A", "goals": "GOAL_A"},
            "b": {"persona": "SECRET_B_7f2", "memory": "MEM_B", "goals": "GOAL_B"},
            "c": {"memory": "MEM_C"},
        },
        "receipts": {
            "a": [{"id": "ra", "payload": {"speech": "HEARD_A_93d"}}],
            "b": [{"id": "rb", "payload": {"speech": "HEARD_B_93d"}}],
        },
        "objects": {
            "carried": {
                "id": "carried",
                "name": "B_INVENTORY_SECRET",
                "location": {"kind": "carried", "holder_id": "b"},
            },
            "visible": {
                "id": "visible",
                "name": "Visible",
                "location": {"kind": "ground", "position": {"row": 1, "col": 0}},
            },
            "blind": {
                "id": "blind",
                "name": "BLIND_OBJ_c8a",
                "location": {"kind": "ground", "position": {"row": 6, "col": 6}},
            },
        },
        "tombstones": {
            "t": {
                "id": "t",
                "piece_id": "dead",
                "position": {"row": 0, "col": 1},
                "killer_id": "SECRET_KILLER",
                "death_event_id": "w:12",
            }
        },
    }


def test_local_observation_and_self_only_context():
    state = world()
    context = build_context(
        state, "a", "action", {"unapproved_world": state, "instruction": "decide"}
    )
    serialized = json.dumps(context)
    assert all(
        text in serialized for text in ["SECRET_A_7f2", "MEM_A", "GOAL_A", "HEARD_A_93d", "Visible"]
    )
    assert all(
        text not in serialized
        for text in [
            "SECRET_B_7f2",
            "MEM_B",
            "GOAL_B",
            "HEARD_B_93d",
            "BLIND_OBJ_c8a",
            "BLIND_PERSON_c8a",
            "B_INVENTORY_SECRET",
            "SECRET_KILLER",
            "w:12",
        ]
    )
    assert len(context["observation"]["visible_cells"]) == 4
    assert "B_INVENTORY_SECRET" in json.dumps(observe(state, "b")["self"]["inventory"])
    context["observation"]["self"]["position"]["row"] = 5
    assert state["pieces"]["a"]["position"]["row"] == 0


def test_language_audience_and_no_later_backfill():
    state = world()
    event = {
        "id": "w:3",
        "type": "ConversationMessage",
        "audience": ["a", "b"],
        "payload": {
            "speech": "HEARD_ONLY_AB",
            "reasoning": "COT_SECRET",
            "memory": "PRIVATE_SECRET",
        },
    }
    assert project_event(state, event, "a")["payload"] == {"speech": "HEARD_ONLY_AB"}
    assert project_event(state, event, "c") is None
    state["pieces"]["c"]["position"] = {"row": 0, "col": 0}
    assert project_event(state, event, "c") is None
    event.pop("audience")
    assert project_event(state, event, "a") is None


def test_battle_narrator_memories_are_separately_labeled():
    context = battle_context(
        world(), {"id": "battle", "participant_ids": ["a", "b"], "anchor": {"row": 1, "col": 1}}
    )
    assert context["participant_memories"] == [
        {"owner_id": "a", "memory": "MEM_A"},
        {"owner_id": "b", "memory": "MEM_B"},
    ]
    assert all(text not in json.dumps(context) for text in ["SECRET_A_7f2", "GOAL_A", "MEM_C"])
    event = {
        "type": "BattleNarrated",
        "audience": ["a", "b"],
        "payload": {
            "battle_id": "battle",
            "round": 1,
            "narrative": "PRIVATE_NARRATIVE",
            "public_narrative": "Observable battle",
            "reasoning": "COT_SECRET",
        },
    }
    receipt = project_event(world(), event, "a")
    assert "public_narrative" in receipt["payload"]
    assert "PRIVATE_NARRATIVE" not in json.dumps(receipt)


def test_death_witness_and_tombstone_discovery():
    state = world()
    event = {
        "type": "DeathExecuted",
        "payload": {"piece_id": "dead", "position": {"row": 0, "col": 1}, "killer_id": "KILLER"},
    }
    assert project_event(state, event, "a")["payload"]["killer_id"] == "KILLER"
    assert project_event(state, event, "c") is None
    assert "KILLER" not in json.dumps(observe(state, "a"))


def test_reasoning_and_private_updates_never_enter_receipts_or_bard():
    state = world()
    events = [
        {
            "id": "w:1",
            "seq": 1,
            "type": "MemoryUpdated",
            "payload": {"memory": "SECRET_A_7f2"},
            "audience": ["a"],
        },
        {
            "id": "w:2",
            "seq": 2,
            "type": "ReasoningTrace",
            "payload": {"reasoning": "COT_SECRET"},
            "audience": ["a"],
        },
        {
            "id": "w:3",
            "seq": 3,
            "type": "ConversationMessage",
            "payload": {
                "speech": "Public claim",
                "private": "PRIVATE_SENTINEL",
                "reasoning": "COT_SECRET",
            },
            "audience": ["a", "b"],
        },
    ]
    assert project_event(state, events[0], "a") is None
    assert project_event(state, events[1], "a") is None
    bard = bard_context(state, 1, 3, events)
    assert [event["id"] for event in bard["events"]] == ["w:3"]
    assert all(
        text not in json.dumps(bard) for text in ["SECRET_A_7f2", "COT_SECRET", "PRIVATE_SENTINEL"]
    )


def test_empty_explicit_audience_blocks_spatial_fallback():
    event = {
        "type": "MoveCommitted",
        "audience": [],
        "payload": {"piece_id": "b", "position": {"row": 0, "col": 1}},
    }
    assert project_event(world(), event, "a") is None


def test_actual_combat_outcome_and_settlement_payloads_reach_only_participants():
    state = world()
    events = [
        {
            "type": "BattleOutcomeCommitted",
            "payload": {
                "battle_id": "b1",
                "winner_ids": ["a"],
                "loser_ids": ["b"],
                "reasoning": "PRIVATE_COT",
            },
        },
        {
            "type": "ReleaseVoteCast",
            "payload": {"battle_id": "b1", "loser_id": "b", "approve": True, "terminal": True},
        },
        {
            "type": "SettlementStarted",
            "payload": {"battle_id": "b1", "conversation_id": "c1", "loser_id": "b"},
        },
        {"type": "NegotiationResolved", "payload": {"conversation_id": "c1", "result": "stop"}},
        {
            "type": "NegotiationPermissionUsed",
            "payload": {"conversation_id": "c1", "kind": "pass", "proposal": "undecided"},
        },
    ]
    for event in events:
        event["audience"] = ["a", "b"]
        event["actor_id"] = "a"
        expected = {key: value for key, value in event["payload"].items() if key != "reasoning"}
        assert project_event(state, event, "a")["payload"] == expected
        assert project_event(state, event, "b")["actor_id"] == "a"
        assert project_event(state, event, "b")["payload"] == expected
        assert project_event(state, event, "c") is None


def test_bard_omits_control_metadata_and_retains_world_language_facts():
    state = world()
    kinds = [
        "PhaseChanged",
        "TurnCompleted",
        "CursorAdvanced",
        "SegmentStarted",
        "RestartRecovered",
        "MoveCommitted",
        "ConversationMessage",
        "BattleOutcomeCommitted",
    ]
    events = [
        {
            "id": f"w:{i}",
            "seq": i,
            "type": kind,
            "payload": {
                "piece_id": "a",
                "speech": "Public fact",
                "winner_ids": ["a"],
                "loser_ids": ["b"],
            },
        }
        for i, kind in enumerate(kinds, 1)
    ]
    context = bard_context(state, 1, len(events), events)
    assert [e["type"] for e in context["events"]] == [
        "MoveCommitted",
        "ConversationMessage",
        "BattleOutcomeCommitted",
    ]
    assert context["events"][-1]["payload"]["winner_ids"] == ["a"]
