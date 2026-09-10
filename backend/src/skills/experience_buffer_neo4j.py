"""Neo4j-native experience buffer with TTL and relevance decay.

This module implements the production-ready experience buffer using Neo4j as
the backing store. It replaces the interim SQL session-snapshot approach with:

1. TTL (time-to-live): Incidents older than TTL_DAYS are automatically excluded
2. Relevance decay: Older incidents have reduced relevance scores
3. Vector similarity: Full-text and semantic similarity search
4. Graph-native storage: Incident patterns stored as connected nodes

Schema:
  (:Incident {id, description, domain, severity, created_at, ttl_at, resolution})
  (:Service {name}) -[:AFFECTED_BY]-> (:Incident)
  (:Metric {name, pattern}) -[:OBSERVED_IN]-> (:Incident)
  (:Resolution {action, outcome}) -[:APPLIED_TO]-> (:Incident)
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from src.db.neo4j_factory import Neo4jFactory

log = structlog.get_logger(__name__)

# Configuration
TTL_DAYS = int(os.getenv("EXPERIENCE_BUFFER_TTL_DAYS", "90"))
DECAY_HALF_LIFE_DAYS = int(os.getenv("EXPERIENCE_BUFFER_DECAY_HALF_LIFE", "30"))
DEFAULT_LIMIT = 5

_STOP_WORDS = {
    "a", "an", "and", "api", "affected", "by", "for", "from", "high",
    "id", "in", "incident", "latency", "of", "on", "or", "p0", "p1",
    "p2", "p3", "service", "severity", "the", "timeout", "to", "unknown",
}


def _tokenize(text: str) -> set[str]:
    """Tokenize free text into a lightweight bag of incident terms."""
    tokens = {
        token
        for token in re.findall(r"[a-z0-9\-]{3,}", text.lower())
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


def _calculate_decay_factor(created_at: datetime) -> float:
    """Calculate exponential decay factor based on incident age.
    
    Uses half-life decay: after DECAY_HALF_LIFE_DAYS, relevance is halved.
    """
    now = datetime.now(UTC)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)

    age_days = (now - created_at).days
    if age_days < 0:
        return 1.0

    # Exponential decay: relevance = 0.5^(age/half_life)
    import math
    decay = math.pow(0.5, age_days / DECAY_HALF_LIFE_DAYS)
    return max(0.1, decay)  # Minimum 10% relevance


async def _ensure_schema() -> None:
    """Create Neo4j constraints and indexes if they don't exist."""
    driver = Neo4jFactory.get_driver()
    if not driver:
        return

    async with driver.session() as session:
        # Constraints
        try:
            await session.run(
                "CREATE CONSTRAINT incident_id IF NOT EXISTS "
                "FOR (i:Incident) REQUIRE i.id IS UNIQUE"
            )
            await session.run(
                "CREATE CONSTRAINT service_name IF NOT EXISTS "
                "FOR (s:Service) REQUIRE s.name IS UNIQUE"
            )
            await session.run(
                "CREATE CONSTRAINT metric_id IF NOT EXISTS "
                "FOR (m:Metric) REQUIRE m.id IS UNIQUE"
            )
        except Exception as e:
            log.warning("neo4j.schema_constraint_exists", error=str(e))

        # Indexes for performance
        try:
            await session.run(
                "CREATE INDEX incident_domain IF NOT EXISTS "
                "FOR (i:Incident) ON (i.domain)"
            )
            await session.run(
                "CREATE INDEX incident_created IF NOT EXISTS "
                "FOR (i:Incident) ON (i.created_at)"
            )
            await session.run(
                "CREATE INDEX incident_ttl IF NOT EXISTS "
                "FOR (i:Incident) ON (i.ttl_at)"
            )
            await session.run(
                "CREATE FULLTEXT INDEX incident_description IF NOT EXISTS "
                "FOR (i:Incident) ON EACH [i.description, i.resolution]"
            )
        except Exception as e:
            log.warning("neo4j.schema_index_exists", error=str(e))


async def store_incident(
    *,
    incident_id: str,
    description: str,
    domain: str | None,
    severity: str | None,
    services: list[str] | None,
    metrics: list[dict[str, Any]] | None,
    resolution: str | None,
    cynefin_domain: str | None,
    guardian_verdict: str | None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Store an incident in the Neo4j experience buffer with TTL.
    
    Args:
        incident_id: Unique identifier for the incident
        description: Incident description text
        domain: Business domain (e.g., 'devops', 'marketing')
        severity: Incident severity level
        services: List of affected service names
        metrics: List of metric patterns observed (each with 'name' and 'pattern')
        resolution: Resolution action taken
        cynefin_domain: Cynefin domain classification
        guardian_verdict: Guardian policy verdict
        created_at: Incident timestamp (defaults to now)
    
    Returns:
        Dict with stored incident details
    """
    driver = Neo4jFactory.get_driver()
    if not driver:
        return {"stored": False, "reason": "neo4j_not_available"}

    await _ensure_schema()

    now = created_at or datetime.now(UTC)
    ttl_at = now + timedelta(days=TTL_DAYS)

    async with driver.session() as session:
        # Create incident node
        result = await session.run(
            """
            CREATE (i:Incident {
                id: $id,
                description: $description,
                domain: $domain,
                severity: $severity,
                created_at: $created_at,
                ttl_at: $ttl_at,
                resolution: $resolution,
                cynefin_domain: $cynefin_domain,
                guardian_verdict: $guardian_verdict
            })
            RETURN i
            """,
            {
                "id": incident_id,
                "description": description,
                "domain": domain or "unknown",
                "severity": severity or "unknown",
                "created_at": now.isoformat(),
                "ttl_at": ttl_at.isoformat(),
                "resolution": resolution or "",
                "cynefin_domain": cynefin_domain or "",
                "guardian_verdict": guardian_verdict or "",
            }
        )
        record = await result.single()

        # Create service nodes and relationships
        for service in (services or []):
            await session.run(
                """
                MERGE (s:Service {name: $service})
                WITH s
                MATCH (i:Incident {id: $incident_id})
                CREATE (s)-[:AFFECTED_BY]->(i)
                """,
                {"service": service, "incident_id": incident_id}
            )

        # Create metric nodes and relationships
        for metric in (metrics or []):
            metric_id = f"{incident_id}_{metric.get('name', 'unknown')}"
            await session.run(
                """
                CREATE (m:Metric {
                    id: $metric_id,
                    name: $name,
                    pattern: $pattern
                })
                WITH m
                MATCH (i:Incident {id: $incident_id})
                CREATE (m)-[:OBSERVED_IN]->(i)
                """,
                {
                    "metric_id": metric_id,
                    "name": metric.get("name", ""),
                    "pattern": metric.get("pattern", ""),
                    "incident_id": incident_id,
                }
            )

    log.info(
        "experience_buffer.stored",
        incident_id=incident_id,
        domain=domain,
        ttl_days=TTL_DAYS,
    )

    return {
        "stored": True,
        "incident_id": incident_id,
        "ttl_at": ttl_at.isoformat(),
    }


async def query_similar_incidents(
    *,
    description: str,
    domain: str | None = None,
    services: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    min_similarity: float = 0.1,
) -> dict[str, Any]:
    """Query Neo4j experience buffer for similar incidents with relevance decay.
    
    Args:
        description: Incident description to match against
        domain: Optional domain filter
        services: Optional list of affected services to match
        limit: Maximum number of results
        min_similarity: Minimum similarity threshold (0.0-1.0)
    
    Returns:
        Dict with similar_incidents list and metadata
    """
    driver = Neo4jFactory.get_driver()
    if not driver:
        return {
            "similar_incidents": [],
            "retrieval_source": "neo4j_not_available",
            "candidate_count": 0,
        }

    await _ensure_schema()

    now = datetime.now(UTC).isoformat()
    query_tokens = _tokenize(description)

    async with driver.session() as session:
        # Cypher query with TTL filter and decay calculation
        cypher = """
        MATCH (i:Incident)
        WHERE i.ttl_at > $now
        """

        params: dict[str, Any] = {"now": now, "limit": limit * 3}  # Fetch extra for ranking

        # Add domain filter if specified
        if domain:
            cypher += " AND (i.domain = $domain OR i.cynefin_domain = $domain)\n"
            params["domain"] = domain

        # Add service filter if specified
        if services:
            cypher += """
            OPTIONAL MATCH (s:Service)-[:AFFECTED_BY]->(i)
            WHERE s.name IN $services
            """
            params["services"] = services

        cypher += """
        RETURN i.id as id,
               i.description as description,
               i.domain as domain,
               i.cynefin_domain as cynefin_domain,
               i.resolution as resolution,
               i.created_at as created_at,
               i.guardian_verdict as guardian_verdict,
               i.severity as severity
        ORDER BY i.created_at DESC
        LIMIT $limit
        """

        result = await session.run(cypher, params)
        records = await result.data()

    # Post-process with Jaccard similarity and decay
    ranked: list[dict[str, Any]] = []
    for record in records:
        candidate_tokens = _tokenize(record.get("description", ""))
        similarity = _jaccard_similarity(query_tokens, candidate_tokens)

        # Apply relevance decay based on age
        created_at_str = record.get("created_at", "")
        try:
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            decay_factor = _calculate_decay_factor(created_at)
        except (ValueError, AttributeError):
            decay_factor = 1.0

        # Adjust similarity with decay
        adjusted_similarity = similarity * decay_factor

        # Boost if domain matches exactly
        if domain and (record.get("domain") == domain or record.get("cynefin_domain") == domain):
            adjusted_similarity = min(1.0, adjusted_similarity + 0.05)

        if adjusted_similarity < min_similarity:
            continue

        ranked.append({
            "session_id": record.get("id"),
            "title": _title_from_description(record.get("description", "")),
            "summary": record.get("description", ""),
            "resolution": record.get("resolution") or "No resolution recorded",
            "similarity": round(adjusted_similarity, 4),
            "cynefin_domain": record.get("cynefin_domain") or "UNKNOWN",
            "guardian_verdict": record.get("guardian_verdict") or "PENDING",
            "timestamp": record.get("created_at", ""),
            "source": "neo4j_experience_buffer",
            "decay_factor": round(decay_factor, 4),
        })

    # Sort by adjusted similarity (descending), then by timestamp (descending)
    ranked = sorted(
        ranked,
        key=lambda item: (-float(item["similarity"]), str(item["timestamp"])),
    )

    log.info(
        "experience_buffer.queried",
        query_length=len(description),
        candidates=len(records),
        results=len(ranked),
        domain=domain,
    )

    return {
        "similar_incidents": ranked[:limit],
        "retrieval_source": "neo4j_experience_buffer",
        "candidate_count": len(records),
        "ttl_days": TTL_DAYS,
        "decay_half_life_days": DECAY_HALF_LIFE_DAYS,
    }


def _title_from_description(description: str) -> str:
    """Derive a short incident title from the description text."""
    if not description:
        return "Untitled incident"
    sentence = description.split(".", maxsplit=1)[0].strip()
    return sentence[:120] if sentence else "Untitled incident"


async def cleanup_expired_incidents() -> dict[str, Any]:
    """Remove incidents that have exceeded their TTL.
    
    Returns:
        Dict with cleanup statistics
    """
    driver = Neo4jFactory.get_driver()
    if not driver:
        return {"cleaned": False, "reason": "neo4j_not_available"}

    now = datetime.now(UTC).isoformat()

    async with driver.session() as session:
        # Count expired incidents
        count_result = await session.run(
            "MATCH (i:Incident) WHERE i.ttl_at < $now RETURN count(i) as count",
            {"now": now}
        )
        count_record = await count_result.single()
        expired_count = count_record["count"] if count_record else 0

        if expired_count == 0:
            return {"cleaned": True, "removed_count": 0}

        # Delete expired incidents and their relationships
        await session.run(
            """
            MATCH (i:Incident)
            WHERE i.ttl_at < $now
            OPTIONAL MATCH (m:Metric)-[:OBSERVED_IN]->(i)
            DETACH DELETE i, m
            """,
            {"now": now}
        )

    log.info("experience_buffer.cleanup", removed_count=expired_count)
    return {"cleaned": True, "removed_count": expired_count}


async def get_buffer_stats() -> dict[str, Any]:
    """Get statistics about the experience buffer.
    
    Returns:
        Dict with buffer statistics
    """
    driver = Neo4jFactory.get_driver()
    if not driver:
        return {"available": False, "reason": "neo4j_not_available"}

    await _ensure_schema()

    async with driver.session() as session:
        # Total incidents
        total_result = await session.run(
            "MATCH (i:Incident) RETURN count(i) as total"
        )
        total_record = await total_result.single()
        total = total_record["total"] if total_record else 0

        # Active (non-expired) incidents
        now = datetime.now(UTC).isoformat()
        active_result = await session.run(
            "MATCH (i:Incident) WHERE i.ttl_at > $now RETURN count(i) as active",
            {"now": now}
        )
        active_record = await active_result.single()
        active = active_record["active"] if active_record else 0

        # Domain distribution
        domain_result = await session.run(
            "MATCH (i:Incident) RETURN i.domain as domain, count(i) as count"
        )
        domain_records = await domain_result.data()
        domain_distribution = {
            r["domain"]: r["count"] for r in domain_records if r["domain"]
        }

        # Cynefin domain distribution
        cynefin_result = await session.run(
            "MATCH (i:Incident) RETURN i.cynefin_domain as domain, count(i) as count"
        )
        cynefin_records = await cynefin_result.data()
        cynefin_distribution = {
            r["domain"]: r["count"] for r in cynefin_records if r["domain"]
        }

    return {
        "available": True,
        "total_incidents": total,
        "active_incidents": active,
        "expired_incidents": total - active,
        "ttl_days": TTL_DAYS,
        "decay_half_life_days": DECAY_HALF_LIFE_DAYS,
        "domain_distribution": domain_distribution,
        "cynefin_distribution": cynefin_distribution,
    }
