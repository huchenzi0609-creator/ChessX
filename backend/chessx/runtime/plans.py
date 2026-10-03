"""Owner-private intentions survive turns; only committed actions report progress.

Plans are descriptive memory, never a scheduler, route command or public goal.
Omitting this optional channel preserves a previous plan and every physical
intent continues through the existing rules independently.
"""

import re
from copy import deepcopy
from typing import Literal

from pydantic import Field, ValidationError

from chessx.domain.errors import RuleError
from chessx.domain.models import Position, StrictModel
from chessx.rules.geometry import inside_board

PLAN_HISTORY_LIMIT = 12


class PrivatePlan(StrictModel):
    decision: Literal["keep", "revise", "complete", "abandon"]
    purpose: str | None = Field(default=None, min_length=1, max_length=160)
    motive: str | None = Field(default=None, min_length=1, max_length=120)
    next_step: str | None = Field(default=None, min_length=1, max_length=120)
    target_person_id: str | None = Field(default=None, min_length=1, max_length=128)
    desired_position: Position | None = None
    evidence_receipt_ids: list[str] = Field(default_factory=list, max_length=6)


def action_schema() -> dict:
    from chessx.runtime.schemas import ACTION

    schema = deepcopy(ACTION)
    plan_schema = PrivatePlan.model_json_schema()
    # The Position definition is identical to the physical intent definition.
    schema.setdefault("$defs", {}).update(plan_schema.pop("$defs", {}))
    schema["$defs"]["PrivatePlan"] = plan_schema
    intent_names = [item["$ref"].rsplit("/", 1)[-1] for item in schema["oneOf"]]
    for name in intent_names:
        schema["$defs"][name]["properties"]["private_plan"] = {
            "anyOf": [{"$ref": "#/$defs/PrivatePlan"}, {"type": "null"}]
        }

    def strip_annotations(value):
        if isinstance(value, dict):
            value.pop("title", None)
            value.pop("default", None)
            for field, child in value.items():
                if field in {
                    "properties",
                    "$defs",
                    "definitions",
                    "patternProperties",
                    "dependentSchemas",
                }:
                    for schema_node in child.values():
                        strip_annotations(schema_node)
                else:
                    strip_annotations(child)
        elif isinstance(value, list):
            for child in value:
                strip_annotations(child)

    strip_annotations(schema)
    return schema


def split_action_result(result: dict) -> tuple[dict, dict | None]:
    intent = deepcopy(result)
    plan = intent.pop("private_plan", None)
    return intent, plan


def _explicit_receipts(context: dict) -> set[str]:
    allowed = set()

    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get("receipt_id"), str):
                allowed.add(value["receipt_id"])
            for key in ("source_receipt_ids", "evidence_receipt_ids"):
                allowed.update(item for item in value.get(key, []) if isinstance(item, str))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(context)
    return allowed


def _known_person(state: dict, pid: str, target: str, context: dict) -> bool:
    piece = state["pieces"].get(target)
    if not piece or target == pid:
        return False
    observation = context.get("observation", {})
    if any(person.get("id") == target for person in observation.get("pieces", [])):
        return True
    owner = state.get("private", {}).get(pid, {})
    if target in owner.get("spatial_memory", {}).get("seen_people", {}):
        return True
    identity_fields = {"actor_id", "speaker_id", "piece_id", "sender_id", "recipient_id"}

    def mentioned(value):
        if isinstance(value, dict):
            return any(value.get(field) == target for field in identity_fields) or any(
                mentioned(child) for child in value.values()
            )
        if isinstance(value, list):
            return any(mentioned(child) for child in value)
        return False

    if mentioned(state.get("receipts", {}).get(pid, [])):
        return True
    persona = owner.get("initial_memory", owner.get("persona", ""))
    # Consult only this named candidate. No global identities are added to context.
    literal_id = bool(re.search(rf"(?<!\w){re.escape(target)}(?!\w)", persona))
    name = piece.get("name", "")
    literal_name = (
        bool(name and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", persona))
        if name.isascii()
        else bool(name and name in persona)
    )
    return literal_id or literal_name


def validate_plan(state: dict, pid: str, raw_plan: dict | None, context: dict) -> dict | None:
    if raw_plan is None:
        return None
    try:
        plan = PrivatePlan.model_validate(raw_plan).model_dump(exclude_none=True)
    except ValidationError as error:
        raise RuleError("PRIVATE_PLAN_INVALID", "The private action plan is malformed") from error
    sources = plan["evidence_receipt_ids"]
    own_sources = {r.get("receipt_id") for r in state.get("receipts", {}).get(pid, [])}
    if len(set(sources)) != len(sources) or not set(sources) <= own_sources & _explicit_receipts(
        context
    ):
        raise RuleError("PLAN_SOURCE_INVALID", "Plan evidence must use supplied owner receipt IDs")
    if plan.get("target_person_id") and not _known_person(
        state, pid, plan["target_person_id"], context
    ):
        raise RuleError("PLAN_TARGET_UNKNOWN", "A private plan cannot identify an unknown person")
    if plan.get("desired_position") and not inside_board(state, plan["desired_position"]):
        raise RuleError("PLAN_POSITION_INVALID", "The desired plan position is outside the board")
    previous = state.get("private", {}).get(pid, {}).get("travel_plan")
    if plan["decision"] in {"complete", "abandon"} and (
        not previous or previous.get("status") != "active"
    ):
        raise RuleError("PLAN_NOT_ACTIVE", "There is no private plan to complete or abandon")
    if plan["decision"] == "revise" and not plan.get("purpose"):
        raise RuleError("PLAN_PURPOSE_REQUIRED", "A revised plan requires a purpose")
    return plan


def apply_plan(state: dict, pid: str, plan: dict | None, context: dict) -> None:
    plan = validate_plan(state, pid, plan, context)
    if plan is None:
        return
    owner = state["private"][pid]
    previous = owner.get("travel_plan")
    if plan["decision"] == "keep":
        if previous:
            previous["last_review_turn"] = state["turn"]
        return
    history = deepcopy(previous.get("history", [])) if previous else []
    if previous:
        history.append(
            {
                "kind": "plan_review",
                "turn": state["turn"],
                "previous_version": previous["version"],
                "previous_purpose": previous.get("purpose", ""),
                "decision": plan["decision"],
            }
        )
    version = (previous or {}).get("version", 0) + 1
    if plan["decision"] == "revise":
        current = {
            "id": f"plan:{pid}:{version}",
            "version": version,
            "status": "active",
            "purpose": plan["purpose"],
            "motive": plan.get("motive", ""),
            "next_step": plan.get("next_step", ""),
            "evidence_receipt_ids": plan["evidence_receipt_ids"],
            "created_turn": state["turn"],
            "origin_position": deepcopy(state["pieces"][pid]["position"]),
            "progress": {},
        }
        for field in ("target_person_id", "desired_position"):
            if field in plan:
                current[field] = deepcopy(plan[field])
    else:
        current = deepcopy(previous)
        current.update(
            version=version, status="complete" if plan["decision"] == "complete" else "abandoned"
        )
        if plan.get("next_step"):
            current["next_step"] = plan["next_step"]
    current.update(
        updated_turn=state["turn"],
        last_review_turn=state["turn"],
        history=history[-PLAN_HISTORY_LIMIT:],
    )
    owner["travel_plan"] = current


def _distance(origin: dict, destination: dict) -> int:
    return max(abs(destination["row"] - origin["row"]), abs(destination["col"] - origin["col"]))


def record_plan_progress(state: dict, events: list[dict]) -> None:
    for index, event in enumerate(events, 1):
        kind, payload = event.get("type"), event.get("payload", {})
        if kind == "ConversationCreated":
            members = payload.get("members", payload.get("member_ids", []))
            for member in members:
                contact = deepcopy(event)
                contact.update(
                    type="PlanContactObserved",
                    actor_id=member,
                    event_id=event.get("event_id", f"{state['world_id']}:{state['seq'] + index}"),
                )
                record_plan_progress(state, [contact])
            continue
        pid = event.get("actor_id", payload.get("piece_id"))
        owner = state.get("private", {}).get(pid, {})
        plan = owner.get("travel_plan")
        if not plan or plan.get("status") != "active":
            continue
        if kind not in {
            "MoveProposed",
            "MoveCommitted",
            "NegotiationResolved",
            "PieceWaited",
            "PlanContactObserved",
        }:
            continue
        event_id = event.get("event_id", f"{state['world_id']}:{state['seq'] + index}")
        if any(item.get("source_event_id") == event_id for item in plan.get("history", [])):
            continue
        progress = plan.setdefault("progress", {})
        record = {"kind": kind, "turn": state["turn"], "source_event_id": event_id}
        if kind == "MoveProposed":
            progress["attempted_target"] = deepcopy(payload["target"])
            record["attempted_target"] = deepcopy(payload["target"])
        elif kind == "MoveCommitted":
            origin, position = payload["origin"], payload["position"]
            record.update(
                origin=deepcopy(origin),
                position=deepcopy(position),
                actual_distance=_distance(origin, position),
            )
            target = plan.get("desired_position")
            if target:
                record.update(
                    distance_before=_distance(origin, target),
                    distance_after=_distance(position, target),
                    reached_desired_position=position == target,
                )
            if progress.get("attempted_target"):
                record["reached_attempted_target"] = position == progress["attempted_target"]
            progress["last_actual_move"] = deepcopy(record)
            progress["current_position"] = deepcopy(position)
        elif kind == "NegotiationResolved":
            record.update(
                result=payload["result"],
                contact_position=deepcopy(payload.get("position")),
                position_scope="negotiation_anchor_not_confirmed_arrival",
            )
            progress["last_contact"] = deepcopy(record)
        elif kind == "PieceWaited":
            record["position"] = deepcopy(state["pieces"][pid]["position"])
            progress["last_wait_turn"] = state["turn"]
        elif kind == "PlanContactObserved":
            record.update(
                member_ids=deepcopy(payload.get("members", payload.get("member_ids", []))),
                conversation_id=payload["conversation_id"],
                position_scope="shared_conversation_not_shared_physical_cell",
            )
            progress["last_conversation_contact"] = deepcopy(record)
        plan.setdefault("history", []).append(record)
        plan["history"] = plan["history"][-PLAN_HISTORY_LIMIT:]
        plan["updated_turn"] = state["turn"]
