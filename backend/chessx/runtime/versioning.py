"""Explicit execution compatibility; journal format migration never changes rules."""

import json
from copy import deepcopy
from hashlib import sha256

from chessx.domain.profile import LATEST_RULE_PROFILE, profile_manifest_hash

SAVE_SCHEMA_VERSION = 2
LEGACY_EXECUTION_POLICY = "legacy-unversioned"
EXECUTION_POLICY_ID = "chessx-2026-10-03-v2"
_POLICY = {
    "id": EXECUTION_POLICY_ID,
    "version": 2,
    "rule_changes": {
        "ordinary_utterances_per_actor_per_turn": 10,
        "conversation_messages_per_max_participant": 50,
        "movement_rest": "chebyshev-origin-destination",
        "vision_radius": 1,
    },
    "memory": "owner-scoped-extractive-with-sourced-reflection",
    "operation_result": "generation-and-policy-bound",
    "battle_read": "subjective_memory_only",
    "travel_plan_private": "goal_linked_v1",
    "retrieval": "owner_alias_relation_commitment_v2",
    "bard_continuity": "prior_public_story_v1",
    "checkpoint_format": "delta_v2",
    "save_schema_version": SAVE_SCHEMA_VERSION,
}


def frozen_execution_policy() -> dict:
    return deepcopy(_POLICY)


def execution_policy_hash(policy: dict) -> str:
    return sha256(
        json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def policy_id(state: dict) -> str:
    policy = state.get("execution_policy")
    return (
        policy.get("id", LEGACY_EXECUTION_POLICY)
        if isinstance(policy, dict)
        else LEGACY_EXECUTION_POLICY
    )


def policy_is_current(state: dict) -> bool:
    return (
        policy_id(state) == EXECUTION_POLICY_ID
        and state.get("execution_policy_hash") == execution_policy_hash(frozen_execution_policy())
        and execution_policy_hash(state["execution_policy"]) == state.get("execution_policy_hash")
        and state.get("save_schema_version") == SAVE_SCHEMA_VERSION
        and state.get("rule_profile") == LATEST_RULE_PROFILE
        and state.get("rule_manifest_hash") == profile_manifest_hash(LATEST_RULE_PROFILE)
        and execution_policy_hash(state.get("rule_manifest", {})) == state.get("rule_manifest_hash")
    )


def upgrade_preview(
    state: dict,
    *,
    task_running: bool = False,
    pending_operations: int = 0,
    prompt_changed: bool = False,
    config_changed: bool = False,
) -> dict:
    blockers = []
    if state.get("rule_profile") not in {"demo-v2.1", "demo-v2.2"}:
        blockers.append(
            {
                "code": "RULE_PROFILE_UNSUPPORTED",
                "message": "This executable cannot migrate an unknown rule profile.",
            }
        )
    if state.get("save_schema_version", 1) > SAVE_SCHEMA_VERSION:
        blockers.append(
            {
                "code": "SAVE_SCHEMA_UNSUPPORTED",
                "message": "A newer save schema cannot be downgraded by this executable.",
            }
        )
    if task_running or state.get("lifecycle") in {"RUNNING", "STEPPING", "PAUSING"}:
        blockers.append(
            {"code": "WORLD_BUSY", "message": "Pause execution before changing policy."}
        )
    if pending_operations:
        blockers.append(
            {
                "code": "OPERATIONS_PENDING",
                "message": "Pending model requests must finish or be invalidated by pause/restart.",
            }
        )
    if state.get("cursor", {}).get("work"):
        blockers.append(
            {
                "code": "PROTOCOL_PENDING",
                "message": "This server cannot continue an unfinished legacy protocol under a new policy. Preserve/read/export this save, or reset/create a new world; no cursor or votes are silently discarded. A compatible original build is required to finish it before upgrading.",
            }
        )
    if any(
        segment.get("story_job") and segment.get("status") == "running"
        for segment in state.get("segments", [])
    ):
        blockers.append(
            {
                "code": "STORY_PENDING",
                "message": "The unfinished story retains its original policy and prompt. Preserve/read/export or reset this save; finish it only with a compatible original build before upgrading.",
            }
        )
    current_policy = policy_is_current(state)
    current = current_policy and not prompt_changed and not config_changed
    return {
        "current_policy": policy_id(state),
        "target_policy": EXECUTION_POLICY_ID,
        "already_current": current,
        "policy_current": current_policy,
        "prompt_changed": prompt_changed,
        "config_changed": config_changed,
        "can_upgrade": not blockers and not current,
        "blockers": blockers,
        "changes": (
            []
            if current_policy
            else [
                "Future dialogue permits ten utterances per actor per turn and a participant-scaled conversation limit.",
                "Future movement rest uses Chebyshev distance; existing rest counters remain historical state.",
                "All future observations use a one-cell vision radius.",
                "Historical events and initial states remain unchanged; only future execution adopts this policy.",
            ]
        )
        + (
            [
                "Future execution adopts the current prompt manifest; historical prompts and operations remain unchanged."
            ]
            if prompt_changed
            else []
        )
        + (
            [
                "Future execution adopts the current nonsecret server configuration; credentials remain server-only."
            ]
            if config_changed
            else []
        ),
        "backup_required": True,
        "expected_seq": state["seq"],
    }
