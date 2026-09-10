"""
Abstract base class for all platform connectors.

Mirrors the TypeScript CausalConnector interface from
packages/semantic-types/src/connectors.ts.

All connectors:
  - Implement fetchEvents() + mapToSemanticObjects() ETL pipeline
  - Output only DRAFT objects — never APPROVED
  - Strip PII before emitting
  - Report health via validate()

DATA_LAYER.md §4: All connectors implement CausalConnector interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


@dataclass
class DomainEvent:
    """
    Platform domain event.

    Mirrors TypeScript DomainEvent interface in connectors.ts.
    """

    event_id: str
    domain: str
    entity_type: str
    event_type: str
    payload: dict[str, Any]
    source: str
    timestamp: str  # ISO8601
    version: str = "1.0"

    @classmethod
    def create(
        cls,
        domain: str,
        entity_type: str,
        event_type: str,
        payload: dict[str, Any],
        source: str,
        version: str = "1.0",
    ) -> DomainEvent:
        """Factory: generates a new event_id and current timestamp."""
        return cls(
            event_id=str(uuid4()),
            domain=domain,
            entity_type=entity_type,
            event_type=event_type,
            payload=payload,
            source=source,
            timestamp=datetime.now(UTC).isoformat(),
            version=version,
        )


@dataclass
class CausalSemanticDraft:
    """
    A draft causal object ready for ingestion into the semantic layer.

    Outputs from mapToSemanticObjects are always DRAFT.
    They flow through CausalDiscoveryAgent → GuardianCheck before promotion.

    Mirrors TypeScript CausalSemanticDraft in entities.ts.
    """

    object_type: str  # "CausalEntity" | "CausalEdge" | "Assumption"
    draft_payload: dict[str, Any]
    source_event_id: str
    inferred_at: str  # ISO8601
    domain: str
    confidence: float  # 0.0–1.0
    pii_stripped: bool = True  # MUST be True before emission


@dataclass
class ConnectorHealthResult:
    """
    Health check result for a connector.

    Mirrors TypeScript ConnectorHealthResult in connectors.ts.
    """

    connector_id: str
    healthy: bool
    last_successful_sync: str | None  # ISO8601
    error_message: str | None
    fields_missing: list[str] = field(default_factory=list)
    checked_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


class BaseCausalConnector(ABC):
    """
    Abstract base class implementing the CausalConnector interface.

    All connectors inherit from this class and implement:
      - fetchEvents()       — pull events from the source system
      - mapToSemanticObjects() — map raw events to DRAFT semantic objects
      - validate()          — health check

    The subscribe() method publishes events to a Kafka topic.
    """

    def __init__(self, connector_id: str, domain: str, version: str = "1.0") -> None:
        self._id = connector_id
        self._domain = domain
        self._version = version
        self._last_successful_sync: str | None = None

    @property
    def id(self) -> str:
        return self._id

    @property
    def domain(self) -> str:
        return self._domain

    @property
    def version(self) -> str:
        return self._version

    @abstractmethod
    async def fetch_events(self, cursor: str | None = None) -> list[DomainEvent]:
        """
        Fetch domain events from the source system since cursor.

        cursor=None means full sync from the beginning of available history.
        cursor is an opaque string (ISO8601 timestamp or offset ID).

        Returns a list of DomainEvents ordered oldest-first.
        Never returns events that contain un-stripped PII.
        """

    @abstractmethod
    def map_to_semantic_objects(
        self, events: list[DomainEvent]
    ) -> list[CausalSemanticDraft]:
        """
        Map raw domain events to DRAFT causal semantic objects.

        All outputs are DRAFT status — never APPROVED.
        Must strip PII and pseudonymize before returning.

        Called by the ingestion pipeline after fetchEvents().
        """

    @abstractmethod
    def validate(self) -> ConnectorHealthResult:
        """
        Check connector health and mapping coverage.

        Returns ConnectorHealthResult with healthy=True only when:
          - Source system is reachable
          - All required fields are present and mappable
          - Last sync completed without errors
        """

    def subscribe(self, topic: str) -> None:
        """
        Publish events to a Kafka topic for real-time streaming.

        Default implementation is a no-op stub.
        Production: replace with confluent_kafka.Producer.
        """
