"""Resumable event-based story jobs with bounded, original-source citations."""

from copy import deepcopy
from uuid import uuid4

from chessx.contexts import bard_context
from chessx.contexts.budgets import fit_context
from chessx.domain.errors import RuleError
from chessx.llm import ModelError
from chessx.runtime import schemas

STORY_RETRY_RESERVE = 256


class StoryMixin:
    def _story_input(self, state: dict, segment: dict, events: list[dict]) -> dict:
        context = bard_context(state, segment["started_seq"], segment["source_end_seq"], events)
        if segment.get("story_continuity"):
            context["continuity"] = deepcopy(segment["story_continuity"])
        return context

    def _story_continuity(self, state: dict, segment: dict) -> dict:
        """Freeze earlier public passages with verified original public sources."""
        previous = sorted(
            (
                story
                for sid, story in state.get("stories", {}).items()
                if sid != segment["id"] and story.get("source_end_seq", 0) < segment["started_seq"]
            ),
            key=lambda story: story.get("source_end_seq", 0),
            reverse=True,
        )[:2]
        passages, sources, chars, skipped = [], {}, 0, 0
        for story in previous:
            for paragraph in reversed(story.get("paragraphs", [])):
                text, refs = paragraph.get("text", ""), paragraph.get("event_refs", [])
                if not refs or len(refs) > 8 or len(passages) >= 6 or chars + len(text) > 2400:
                    skipped += 1
                    continue
                verified = {}
                for ref in refs:
                    try:
                        wid, seq = ref.rsplit(":", 1)
                        seq = int(seq)
                    except (ValueError, AttributeError):
                        continue
                    if wid != state["world_id"] or not 0 < seq < segment["started_seq"]:
                        continue
                    records = bard_context(
                        state, seq, seq, self.store.events(state["world_id"], seq - 1, 1)["events"]
                    )["events"]
                    if records and records[0]["id"] == ref:
                        verified[ref] = records[0]
                if set(refs) != set(verified):
                    skipped += 1
                    continue
                passages.append(
                    {"segment_id": story.get("segment_id"), "text": text, "event_refs": list(refs)}
                )
                sources.update(verified)
                chars += len(text)
        passages.reverse()
        return {
            "prior_passages": passages,
            "source_events": list(sources.values()),
            "omitted_passages": skipped,
            "scope": "Earlier public story only. Use for continuity, attribution and avoiding retelling; original source events control factual claims. No canonical plot, private motives or unseen actions. Focus on changes in this segment.",
        }

    @staticmethod
    def _story_refs(context: dict) -> set[str]:
        return {event["id"] for event in context["events"]} | {
            event["id"] for event in context.get("continuity", {}).get("source_events", [])
        }

    def _fit_story(self, state: dict, context: dict) -> dict:
        context = deepcopy(context)
        # WorldRunner.ask adds this field. Plan with the exact same request DTO;
        # even a five-token discrepancy can make a maximally packed block fail.
        context["stage"] = "bard"
        if hasattr(self, "execution_binding"):
            context["execution_contract"] = self.execution_binding(state)
        return fit_context(
            "bard",
            context,
            schemas.BARD,
            self.adapter(state).counter,
            # Reserve room for an unconstrained manuscript; actual output gets
            # the remaining model capacity rather than a fixed 2048-token cap.
            max_output_tokens=self.settings.context_capacity // 3 + STORY_RETRY_RESERVE,
            model_capacity=self.settings.context_capacity,
        )

    def _story_events(self, state: dict, segment: dict) -> list[dict]:
        events, cursor = [], segment["started_seq"] - 1
        while cursor < segment["source_end_seq"]:
            page = self.store.events(state["world_id"], cursor, 1000)
            events.extend(
                event for event in page["events"] if event["seq"] <= segment["source_end_seq"]
            )
            if not page["has_more"] or page["next_cursor"] >= segment["source_end_seq"]:
                break
            cursor = page["next_cursor"]
        return events

    def _story_blocks(self, state: dict, segment: dict, events: list[dict]) -> list[list[dict]]:
        blocks, current = [], []
        for record in events:
            candidate = current + [record]
            try:
                self._fit_story(state, self._story_input(state, segment, candidate))
            except ModelError as error:
                if error.code != "CONTEXT_BUDGET_EXCEEDED":
                    raise
                if not current:
                    raise ModelError(
                        "CONTEXT_BUDGET_EXCEEDED",
                        "A single story source event exceeds the configured context capacity.",
                    ) from None
                blocks.append(current)
                current = [record]
                self._fit_story(state, self._story_input(state, segment, current))
            else:
                current = candidate
        if current:
            blocks.append(current)
        return blocks

    @staticmethod
    def _story_validator(allowed: set[str]):
        def validate(result: dict) -> None:
            for paragraph in result["paragraphs"]:
                references = paragraph["event_refs"]
                if not references or not set(references).issubset(allowed):
                    raise RuleError(
                        "STORY_REFERENCE_INVALID",
                        "Story paragraphs must cite original event IDs supplied in this request.",
                    )

        return validate

    def _finish_story(self, state: dict, segment: dict, story: dict) -> list[dict]:
        story = deepcopy(story)
        story.update(
            segment_id=segment["id"],
            mode=state["mode"],
            source_end_seq=segment["source_end_seq"],
            revision=segment.get("story_revision", 0),
        )
        state.setdefault("story_versions", {}).setdefault(segment["id"], []).append(deepcopy(story))
        state["stories"][segment["id"]] = story
        segment.update(status="paused", ended_seq=segment["source_end_seq"])
        segment.pop("story_job", None)
        if state["completed_turn"] < state["target_turn"]:
            state["segments"].append(
                {
                    "id": f"segment_{uuid4().hex}",
                    "from_turn": state["completed_turn"] + 1,
                    "target_turn": state["target_turn"],
                    "started_seq": state["seq"] + 1,
                    "status": "running",
                }
            )
            state.update(phase="START", cursor={}, lifecycle="RUNNING")
        else:
            state.update(lifecycle="PAUSED_SEGMENT", cursor={"story_done": True})
        return [
            {
                "type": "BardCreated",
                "payload": {"segment_id": segment["id"], "title": story["title"]},
                "audience": [],
            }
        ]

    def _chaptered_story(self, state: dict, segment: dict, summaries: list[dict]) -> list[dict]:
        # These are complete, validated model-written narratives, not fallback
        # fixture prose. Assembly changes neither their text nor source IDs and
        # needs no model context containing every chapter at once.
        chapters, paragraph_start = [], 0
        for summary in summaries:
            chapters.append(
                {
                    "title": summary["title"],
                    "paragraph_start": paragraph_start,
                    "paragraph_count": len(summary["paragraphs"]),
                }
            )
            paragraph_start += len(summary["paragraphs"])
        story = {
            "title": summaries[0]["title"],
            "paragraphs": [
                deepcopy(paragraph) for summary in summaries for paragraph in summary["paragraphs"]
            ],
            "composition": "ordered_chapters",
            "chapter_count": len(summaries),
            "chapters": chapters,
        }
        allowed = {
            event["id"]
            for block in segment["story_job"]["blocks"]
            for event in self._story_input(state, segment, block)["events"]
        }
        allowed.update(
            event["id"] for event in segment.get("story_continuity", {}).get("source_events", [])
        )
        self._story_validator(allowed)(story)
        return self._finish_story(state, segment, story)

    async def bard_step(self, state: dict) -> list[dict]:
        segment = state["segments"][-1]
        if "story_job" not in segment:
            segment.setdefault("source_end_seq", state["seq"])
            if "story_continuity" not in segment:
                segment["story_continuity"] = self._story_continuity(state, segment)
            events = self._story_input(state, segment, self._story_events(state, segment))["events"]
            if not state["pieces"] or not events:
                return self._finish_story(
                    state,
                    segment,
                    {
                        "title": "空棋盘记录" if not state["pieces"] else "本段暂无公开事件",
                        "paragraphs": [],
                    },
                )
            try:
                self._fit_story(state, self._story_input(state, segment, events))
                blocks = [events]
            except ModelError as error:
                if error.code != "CONTEXT_BUDGET_EXCEEDED":
                    raise
                blocks = self._story_blocks(state, segment, events)
            segment["story_job"] = {
                "blocks": blocks,
                "index": 0,
                "summaries": [],
                "chunked": len(blocks) > 1,
            }
            # Empty drafts become a private CursorAdvanced commit in Store.
            return []
        job = segment["story_job"]
        index = job["index"]
        if index < len(job["blocks"]):
            try:
                context = self._fit_story(
                    state, self._story_input(state, segment, job["blocks"][index])
                )
            except ModelError as error:
                if error.code != "CONTEXT_BUDGET_EXCEEDED":
                    raise
                # Existing jobs may have been packed with an older prompt or
                # reservation. Repartition only this pending block; completed
                # chapters, original source range and indices stay durable.
                blocks = self._story_blocks(state, segment, job["blocks"][index])
                if len(blocks) < 2:
                    raise
                job["blocks"][index : index + 1] = blocks
                job["chunked"] = True
                return []
            allowed = self._story_refs(context)
            result = await self.ask(
                state,
                None,
                "bard",
                f"segment:{segment['id']}:bard:version:{segment.get('story_revision', 0)}:block:{index}",
                schemas.BARD,
                {},
                context=context,
                validator=self._story_validator(allowed),
            )
            # Direct one-block stories use exactly one model operation.
            if not job["chunked"]:
                return self._finish_story(state, segment, result)
            job["summaries"].append(deepcopy(result))
            job["index"] += 1
            return []
        # Composition receives controlled summaries with original citations. No
        # private memory or new world information can enter this separate input.
        originals = list(
            dict.fromkeys(
                [
                    ref
                    for summary in job["summaries"]
                    for paragraph in summary["paragraphs"]
                    for ref in paragraph["event_refs"]
                ]
            )
        )
        context = {
            "world_id": state["world_id"],
            "start_seq": segment["started_seq"],
            "end_seq": segment["source_end_seq"],
            "events": [{"id": ref} for ref in originals],
            "summaries": deepcopy(job["summaries"]),
            "instruction": "Compose these ordered source-based summaries. Keep subjective claims attributed. Cite only the supplied ORIGINAL event IDs, never summary IDs. Do not invent or alter world facts.",
            "continuity": deepcopy(segment.get("story_continuity", {})),
        }
        try:
            context = self._fit_story(state, context)
        except ModelError as error:
            if error.code != "CONTEXT_BUDGET_EXCEEDED":
                raise
            # Manuscripts can be longer than one model window. Do not discard,
            # truncate or recursively compress completed chapters to fit it.
            return self._chaptered_story(state, segment, job["summaries"])
        result = await self.ask(
            state,
            None,
            "bard",
            f"segment:{segment['id']}:bard:version:{segment.get('story_revision', 0)}:compose",
            schemas.BARD,
            {},
            context=context,
            validator=self._story_validator(self._story_refs(context)),
        )
        return self._finish_story(state, segment, result)
