"""Experience buffer with Neo4j-native storage and SQL interim fallback.

This module provides a unified interface to the experience buffer that:
1. Uses Neo4j-native storage with TTL and relevance decay when available
2. Falls back to SQL session-snapshot interim approach when Neo4j is unavailable
3. Maintains backward compatibility with existing IncidentTriageAgent code

To use Neo4j, set the NEO4J_URI environment variable.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.db.neo4j_factory import Neo4jFactory
from src.skills.experience_buffer_neo4j import (
    query_similar_incidents as neo4j_query_similar,
)
from src.skills.experience_buffer_neo4j import (
    store_incident as neo4j_store_incident,
)

log = structlog.get_logger(__name__)

# Retain original SQL-backed interim implementation for fallback
from src.skills._experience_buffer_sql import (
    experience_buffer_query as sql_experience_buffer_query,
)


async def experience_buffer_query(
    *,
    description: str,
    domain: str | None = None,
    limit: int = 5,
    session_id: str | None = None,
    services: list[str] | None = None,
) -> dict[str, Any]:
    """Query the experience buffer for similar past incidents.
    
    Automatically uses Neo4j if available, otherwise falls back to SQL interim.
    
    Args:
        description: Incident description to match
        domain: Optional domain filter
        limit: Maximum number of results to return
        session_id: Current session ID (to exclude from results)
        services: Optional list of affected services
    
    Returns:
        Dict with similar_incidents list and metadata
    """
    # Check if Neo4j is available
    if Neo4jFactory.is_available():
        try:
            if await Neo4jFactory.verify_connectivity():
                log.debug("experience_buffer.using_neo4j", session_id=session_id)
                return await neo4j_query_similar(
                    description=description,
                    domain=domain,
                    services=services,
                    limit=limit,
                )
        except Exception as e:
            log.warning(
                "experience_buffer.neo4j_failed",
                error=str(e),
                fallback="sql_interim"
            )

    # Fall back to SQL interim approach
    log.debug("experience_buffer.using_sql_fallback", session_id=session_id)
    return await sql_experience_buffer_query(
        description=description,
        domain=domain,
        limit=limit,
        session_id=session_id,
    )


async def store_incident_experience(
    *,
    incident_id: str,
    description: str,
    domain: str | None = None,
    severity: str | None = None,
    services: list[str] | None = None,
    metrics: list[dict[str, Any]] | None = None,
    resolution: str | None = None,
    cynefin_domain: str | None = None,
    guardian_verdict: str | None = None,
) -> dict[str, Any]:
    """Store an incident in the experience buffer.
    
    Stores in Neo4j if available, otherwise returns status indicating
    that durable storage requires Neo4j.
    
    Args:
        incident_id: Unique identifier for the incident
        description: Incident description
        domain: Business domain
        severity: Incident severity
        services: Affected services
        metrics: Metric patterns observed
        resolution: Resolution action taken
        cynefin_domain: Cynefin classification
        guardian_verdict: Guardian policy verdict
    
    Returns:
        Dict with storage status
    """
    if Neo4jFactory.is_available():
        try:
            if await Neo4jFactory.verify_connectivity():
                return await neo4j_store_incident(
                    incident_id=incident_id,
                    description=description,
                    domain=domain,
                    severity=severity,
                    services=services,
                    metrics=metrics,
                    resolution=resolution,
                    cynefin_domain=cynefin_domain,
                    guardian_verdict=guardian_verdict,
                )
        except Exception as e:
            log.warning("experience_buffer.store_neo4j_failed", error=str(e))
            return {"stored": False, "reason": f"neo4j_error: {e}"}

    return {
        "stored": False,
        "reason": "neo4j_not_configured",
        "note": "Set NEO4J_URI to enable durable experience storage"
    }


async def get_experience_buffer_stats() -> dict[str, Any]:
    """Get statistics about the experience buffer.
    
    Returns:
        Dict with buffer statistics
    """
    from src.skills.experience_buffer_neo4j import get_buffer_stats

    if Neo4jFactory.is_available():
        try:
            return await get_buffer_stats()
        except Exception as e:
            log.warning("experience_buffer.stats_failed", error=str(e))
            return {"available": False, "error": str(e)}

    return {
        "available": False,
        "reason": "neo4j_not_configured",
        "note": "Set NEO4J_URI to enable experience buffer"
    }
