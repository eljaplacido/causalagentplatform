"""Neo4jFactory — async Neo4j driver factory for graph operations.

Selection logic:
  - NEO4J_URI env var set     → Connect to Neo4j (Bolt protocol)
  - NEO4J_URI unset           → Return None (fallback to SQL interim)

Code outside this module NEVER instantiates Neo4j driver directly. Always use:

    driver = Neo4jFactory.get_driver()
    if driver:
        async with driver.session() as session:
            ...

This ensures transparent Neo4j availability checking and proper connection pooling.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from neo4j import AsyncDriver

log = structlog.get_logger(__name__)

# ── Sentinel: track whether the driver has been initialised ──────────────────
_driver: AsyncDriver | None = None
_driver_available: bool | None = None


class Neo4jFactory:
    """Factory for Neo4j async driver with singleton caching."""

    # Explicit singleton cache
    _driver: AsyncDriver | None = None
    _available: bool | None = None

    @classmethod
    def get_driver(cls) -> AsyncDriver | None:
        """Return (or create) the singleton Neo4j async driver, or None if not configured."""
        if cls._available is False:
            return None
        if cls._driver is not None:
            return cls._driver

        uri = os.getenv("NEO4J_URI")
        if not uri:
            cls._available = False
            log.info("neo4j.not_configured", reason="NEO4J_URI not set")
            return None

        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "")

        try:
            from neo4j import AsyncGraphDatabase

            cls._driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
            cls._available = True
            log.info("neo4j.driver.created", uri=uri, user=user)
            return cls._driver
        except Exception as e:
            cls._available = False
            log.warning("neo4j.driver.failed", error=str(e))
            return None

    @classmethod
    async def verify_connectivity(cls) -> bool:
        """Verify that Neo4j is reachable and return True if available."""
        driver = cls.get_driver()
        if driver is None:
            return False
        try:
            await driver.verify_connectivity()
            return True
        except Exception as e:
            log.warning("neo4j.connectivity_failed", error=str(e))
            return False

    @classmethod
    def is_available(cls) -> bool:
        """Return True if Neo4j driver is configured (does not verify connectivity)."""
        if cls._available is not None:
            return cls._available
        return os.getenv("NEO4J_URI") is not None

    @classmethod
    def reset(cls) -> None:
        """Clear the cached driver. Only for use in tests."""
        cls._driver = None
        cls._available = None
        log.debug("neo4j.factory.reset")

    @classmethod
    async def close(cls) -> None:
        """Close the Neo4j driver if open."""
        if cls._driver:
            await cls._driver.close()
            cls._driver = None
            log.info("neo4j.driver.closed")
