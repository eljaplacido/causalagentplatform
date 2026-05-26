"""Interim SQL-backed experience buffer retrieval (fallback implementation).

This is the original session_state snapshot-based implementation,
retained as a fallback when Neo4j is not available.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text

from src.db.factory import CloudSqlFactory
from src.semantic_layer.models import EpistemicState

_STOP_WORDS = {
    "a",
    "an",
    "and",
    "api",
    "affected",
    "by",
    "for",
    "from",
    "high",
    "id",
    "in",
    "incident",
    "latency",
    "of",
    "on",
    "or",
    "p0",
    "p1",
    "p2",
    "p3",
    "service",
    "severity",
    "the",
    "timeout",
    "to",
    "unknown",
}


async def experience_buffer_query(
    *,
    description: str,
    domain: str | None,
    limit: int = 5,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Return similar incident sessions from the durable session store (SQL fallback)."""
    candidates = await _load_recent_incident_sessions(exclude_session_id=session_id)
    query_tokens = _tokenize(description)

    ranked: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_tokens = _tokenize(candidate.draft_explanation)
        similarity = _jaccard_similarity(query_tokens, candidate_tokens)
        if domain and str(candidate.cynefin_domain) == str(domain):
            similarity = min(1.0, similarity + 0.05)
        if similarity < 0.12:
            continue

        ranked.append(
            {
                "session_id": candidate.session_id,
                "title": _title_from_state(candidate),
                "summary": candidate.draft_explanation,
                "resolution": _resolution_from_state(candidate),
                "similarity": round(similarity, 4),
                "cynefin_domain": str(candidate.cynefin_domain),
                "guardian_verdict": str(candidate.guardian_verdict),
                "timestamp": candidate.timestamp.isoformat(),
                "source": "session_state_interim",
            }
        )

    ranked = sorted(
        ranked,
        key=lambda item: (
            -float(item["similarity"]),
            str(item["timestamp"]),
        ),
    )

    return {
        "similar_incidents": ranked[:limit],
        "retrieval_source": "session_state_interim",
        "candidate_count": len(candidates),
    }


async def _load_recent_incident_sessions(
    *,
    exclude_session_id: str | None,
    max_rows: int = 50,
) -> list[EpistemicState]:
    """Load recent incident-triage session snapshots from the SQL session store."""
    query = (
        "SELECT session_id, state_json "
        "FROM session_state "
        "WHERE agent_id = :agent_id "
        "ORDER BY updated_at DESC "
        "LIMIT :limit"
    )

    async with CloudSqlFactory.get_session_factory()() as session:
        rows = await session.execute(
            text(query),
            {
                "agent_id": "incident-triage-agent",
                "limit": max_rows,
            },
        )
        records = rows.mappings().all()

    states: list[EpistemicState] = []
    for record in records:
        if exclude_session_id and record["session_id"] == exclude_session_id:
            continue
        payload = record["state_json"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        try:
            state = EpistemicState.model_validate(payload)
        except Exception:
            continue
        if not state.draft_explanation:
            continue
        states.append(state)
    return states


def _tokenize(text_value: str) -> set[str]:
    """Tokenize free text into a lightweight bag of incident terms."""
    tokens = {
        token
        for token in re.findall(r"[a-z0-9\-]{3,}", text_value.lower())
        if token not in _STOP_WORDS
    }
    return tokens


def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    """Return Jaccard similarity over two token sets."""
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    union = len(left | right)
    if union == 0:
        return 0.0
    return intersection / union


def _title_from_state(state: EpistemicState) -> str:
    """Derive a short incident title from the stored explanation text."""
    sentence = state.draft_explanation.split(".", maxsplit=1)[0].strip()
    if sentence:
        return sentence[:120]
    return f"Incident session {state.session_id}"


def _resolution_from_state(state: EpistemicState) -> str:
    """Return the leading prior recommendation, if any, as a resolution hint."""
    if state.recommendations:
        return state.recommendations[0].action
    if state.requires_human_review:
        return "Escalated to human review"
    return "No prior resolution recorded"
