"""
Simulated data seeder — populates the platform with realistic test data.

Supports four use cases:
  1. DevOps: Service dependency graph + incident triage data
  2. Marketing: Customer churn causal model + intervention comparison
  3. Audit: Sample provenance traces from completed sessions
  4. Evaluation: Decision quality metrics from historical audit_log data

Usage:
    cd backend
    .venv/bin/python -m scripts.seed_data           # Seed all
    .venv/bin/python -m scripts.seed_data --devops   # DevOps only
    .venv/bin/python -m scripts.seed_data --marketing # Marketing only
    .venv/bin/python -m scripts.seed_data --clear     # Clear seeded data

It seeds SQLite and PostgreSQL alike. Every INSERT declares its parameters' types
with the bind types in src/db/types.py (TEXT_ARRAY, UUID_ARRAY, TIMESTAMP): a
raw JSON string bound into a TEXT[]/UUID[] column, or an ISO string into a
TIMESTAMPTZ, is what SQLite accepts and asyncpg refuses. On PostgreSQL it may
connect as causal_app: main() runs everything inside a service scope, which the
row-level-security policies let write every domain, and causal_app's INSERT and
UPDATE grants are all seeding needs.

--clear deletes the seeder's rows from the mutable tables only; the append-only
audit trail keeps the seeder's audit rows. On PostgreSQL --clear runs as the
tables' owner, never as causal_app (see "Clear seeded data" below).
"""

from __future__ import annotations

import argparse
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from src.db.factory import CloudSqlFactory
from src.db.types import TEXT_ARRAY, TIMESTAMP, UUID_ARRAY, typed_text
from src.semantic_layer.models import (
    ApprovalStatus,
    CausalEntity,
    CausalObjectRef,
    EntityType,
    FragmentApprovalStatus,
    GraphFragment,
    LineageRecord,
    UserRef,
)

# ── Test user ─────────────────────────────────────────────────────────────────

_SEEDER_USER = UserRef(id="seeder-001", email="seeder@causalplatform.local", name="Data Seeder")


def _verdict_id() -> UUID:
    """Generate a deterministic verdict ID for seeding."""
    return uuid4()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _make_lineage(action: str = "SEEDED") -> list[LineageRecord]:
    return [
        LineageRecord(
            step_type=action,
            actor_id=_SEEDER_USER.id,
            timestamp=datetime.now(UTC),
            description="Seeded test data for platform evaluation",
        )
    ]


async def _insert_seed_edge(
    session: Any,
    *,
    source: CausalObjectRef,
    target: CausalObjectRef,
    edge_type: str,
    confidence: float,
    approval_status: str,
    domain: str,
    verdict_id: UUID,
    created_at: str,
) -> UUID:
    """Insert a row into causal_edges and return its UUID.

    Used by every seed-fragment site so fragment.edge_ids resolve to real edges
    that the DAG Workbench (and any downstream skill / MCP tool) can fetch.
    Without this, fragments are referentially broken and the DAG canvas renders
    empty against seeded data.
    """
    edge_id = uuid4()
    approved_at = created_at if approval_status == "APPROVED" else None
    approved_by = _SEEDER_USER.model_dump_json() if approval_status == "APPROVED" else None
    await session.execute(
        typed_text(
            "INSERT INTO causal_edges ("
            "id, source_id, source_type, target_id, target_type, "
            "edge_type, direction, confidence, evidence_refs, assumption_refs, "
            "approval_status, approved_by, approved_at, guardian_verdict_ref, "
            "version, lineage, domain, created_at"
            ") VALUES ("
            ":id, :sid, :stype, :tid, :ttype, "
            ":etype, :dir, :conf, :evidence, :assumptions, "
            ":status, :approved_by, :approved_at, :verdict, "
            ":version, :lineage, :domain, :created_at"
            ")",
            evidence=UUID_ARRAY,
            assumptions=UUID_ARRAY,
            approved_at=TIMESTAMP,
            created_at=TIMESTAMP,
        ),
        {
            "id": str(edge_id),
            "sid": str(source.id),
            "stype": source.object_type,
            "tid": str(target.id),
            "ttype": target.object_type,
            "etype": edge_type,
            "dir": "FORWARD",
            "conf": confidence,
            "evidence": [],
            "assumptions": [],
            "status": approval_status,
            "approved_by": approved_by,
            "approved_at": approved_at,
            "verdict": str(verdict_id),
            "version": "1.0.0",
            "lineage": "[]",
            "domain": domain,
            "created_at": created_at,
        },
    )
    return edge_id


# ── DevOps Seed Data ──────────────────────────────────────────────────────────


SERVICES = {
    "api-gateway": "Handles external traffic routing and authentication",
    "payment-service": "Processes payments and manages transaction ledger",
    "inventory-service": "Tracks product inventory and stock levels",
    "notification-service": "Sends email, SMS, and push notifications",
    "auth-service": "Manages user authentication and session tokens",
    "order-service": "Orchestrates order placement across services",
}

DEPLOYMENT_EVENTS = [
    {"service": "payment-service", "version": "v3.2", "time_ago_h": 2, "cause": "Feature release"},
    {"service": "auth-service", "version": "v2.1", "time_ago_h": 5, "cause": "Security patch"},
    {"service": "api-gateway", "version": "v4.0", "time_ago_h": 24, "cause": "TLS upgrade"},
    {"service": "inventory-service", "version": "v1.7", "time_ago_h": 48, "cause": "Bug fix"},
    {"service": "notification-service", "version": "v3.0", "time_ago_h": 72, "cause": "API change"},
]

METRICS = [
    {"service": "api-gateway", "metric": "latency_p95_ms", "normal": 120, "spike": 450},
    {"service": "payment-service", "metric": "error_rate", "normal": 0.001, "spike": 0.05},
    {"service": "auth-service", "metric": "latency_p99_ms", "normal": 200, "spike": 800},
    {"service": "inventory-service", "metric": "throughput_rps", "normal": 500, "spike": 850},
    {"service": "notification-service", "metric": "queue_depth", "normal": 50, "spike": 5000},
]


def seed_devops(engine: Any) -> dict[str, Any]:
    """Seed DevOps domain: service entities, graph fragments, deployment events."""
    created: dict[str, Any] = {"entities": [], "fragments": [], "edges": []}
    now = _now_iso()
    entities_map: dict[str, UUID] = {}

    async def _seed():
        async with CloudSqlFactory.get_session_factory()() as session:
            # ── Create service entities ───────────────────────────────────────
            for svc_name, desc in SERVICES.items():
                verdict_id = _verdict_id()
                await session.execute(
                    typed_text(
                        "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                        "guardian_verdict, policy_rules_cited, created_at) "
                        "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)",
                        rules=TEXT_ARRAY,
                        ts=TIMESTAMP,
                    ),
                    {
                        "id": str(verdict_id),
                        "agent": "seeder",
                        "action": "SEED_ENTITY",
                        "hash": hashlib.sha256(svc_name.encode()).hexdigest(),
                        "verdict": "APPROVED",
                        "rules": [],
                        "ts": now,
                    },
                )

                entity = CausalEntity(
                    name=svc_name,
                    entity_type=EntityType.SERVICE,
                    domain="devops",
                    description=desc,
                    approval_status=ApprovalStatus.APPROVED,
                    created_by=_SEEDER_USER,
                    created_at=datetime.now(UTC),
                    approved_at=datetime.now(UTC),
                    approved_by=_SEEDER_USER,
                    lineage=_make_lineage("SEEDED"),
                    tags=["devops", "service", "seeded"],
                )
                await session.execute(
                    typed_text(
                        "INSERT INTO causal_entities (id, name, entity_type, domain, description, "
                        "attributes, ontology_version, approval_status, created_by, created_at, "
                        "approved_at, approved_by, lineage, tags) "
                        "VALUES (:id, :name, :type, :domain, :desc, :attrs, :ont, :status, "
                        ":created_by, :created_at, :approved_at, :approved_by, :lineage, :tags)",
                        created_at=TIMESTAMP,
                        approved_at=TIMESTAMP,
                        tags=TEXT_ARRAY,
                    ),
                    {
                        "id": str(entity.id),
                        "name": entity.name,
                        "type": str(entity.entity_type),
                        "domain": entity.domain,
                        "desc": entity.description or "",
                        "attrs": "{}",
                        "ont": entity.ontology_version,
                        "status": str(entity.approval_status),
                        "created_by": entity.created_by.model_dump_json(),
                        "created_at": entity.created_at,
                        "approved_at": entity.approved_at,
                        "approved_by": entity.approved_by.model_dump_json(),
                        "lineage": "[]",
                        "tags": entity.tags,
                    },
                )
                entities_map[svc_name] = entity.id
                created["entities"].append({"id": str(entity.id), "name": svc_name})

            await session.commit()

            # ── Create deployment → latency graph fragment ───────────────────
            frag_verdict = _verdict_id()
            frag_id = uuid4()
            await session.execute(
                typed_text(
                    "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                    "guardian_verdict, policy_rules_cited, created_at) "
                    "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)",
                    rules=TEXT_ARRAY,
                    ts=TIMESTAMP,
                ),
                {
                    "id": str(frag_verdict),
                    "agent": "seeder",
                    "action": "SEED_FRAGMENT",
                    "hash": hashlib.sha256(b"deployment-latency-fragment").hexdigest(),
                    "verdict": "APPROVED",
                    "rules": ["PR-001"],
                    "ts": now,
                },
            )

            entry_ref = CausalObjectRef(
                id=entities_map["payment-service"], object_type="CausalEntity"
            )
            exit_ref = CausalObjectRef(
                id=entities_map["api-gateway"], object_type="CausalEntity"
            )
            edge_id = await _insert_seed_edge(
                session,
                source=entry_ref,
                target=exit_ref,
                edge_type="CAUSES",
                confidence=0.82,
                approval_status="APPROVED",
                domain="devops",
                verdict_id=frag_verdict,
                created_at=now,
            )

            fragment = GraphFragment(
                id=frag_id,
                name="Deployment → Latency Causal Path",
                domain="devops",
                description=(
                    "Causal model linking service deployments to latency impacts. "
                    "Key edges: deployment → config-change → latency-spike. "
                    "Includes confounder: load-increase creates spurious correlation."
                ),
                edges=[edge_id],
                entry_nodes=[entry_ref],
                exit_nodes=[exit_ref],
                approval_status=FragmentApprovalStatus.APPROVED,
                guardian_verdict_ref=frag_verdict,
                created_by=_SEEDER_USER,
                tags=["devops", "deployment", "latency", "seeded"],
            )
            await session.execute(
                typed_text(
                    "INSERT INTO graph_fragments (id, name, domain, description, edge_ids, "
                    "entry_nodes, exit_nodes, approval_status, version, usage_count, tags, "
                    "guardian_verdict_ref, created_by, created_at) "
                    "VALUES (:id, :name, :domain, :desc, :edges, :entry, :exit, :status, "
                    ":version, :usage, :tags, :verdict, :created_by, :created_at)",
                    edges=UUID_ARRAY,
                    tags=TEXT_ARRAY,
                    created_at=TIMESTAMP,
                ),
                {
                    "id": str(fragment.id),
                    "name": fragment.name,
                    "domain": fragment.domain,
                    "desc": fragment.description,
                    "edges": [str(e) for e in fragment.edges],
                    "entry": json.dumps([r.model_dump(mode="json") for r in fragment.entry_nodes]),
                    "exit": json.dumps([r.model_dump(mode="json") for r in fragment.exit_nodes]),
                    "status": str(fragment.approval_status),
                    "version": fragment.version,
                    "usage": fragment.usage_count,
                    "tags": fragment.tags,
                    "verdict": str(frag_verdict),
                    "created_by": fragment.created_by.model_dump_json(),
                    "created_at": fragment.created_at,
                },
            )
            created["fragments"].append({"id": str(frag_id), "name": fragment.name})
            await session.commit()

    import asyncio
    asyncio.run(_seed())
    return created


# ── Marketing Seed Data ───────────────────────────────────────────────────────


def seed_marketing(engine: Any) -> dict[str, Any]:
    """Seed Marketing domain: customer churn causal model."""
    created: dict[str, Any] = {"fragments": []}
    now = _now_iso()

    async def _seed():
        async with CloudSqlFactory.get_session_factory()() as session:
            verdict_id = _verdict_id()
            frag_id = uuid4()
            await session.execute(
                typed_text(
                    "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                    "guardian_verdict, policy_rules_cited, created_at) "
                    "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)",
                    rules=TEXT_ARRAY,
                    ts=TIMESTAMP,
                ),
                {
                    "id": str(verdict_id),
                    "agent": "seeder",
                    "action": "SEED_FRAGMENT",
                    "hash": hashlib.sha256(b"churn-causal-fragment").hexdigest(),
                    "verdict": "APPROVED",
                    "rules": ["PR-001", "PR-007"],
                    "ts": now,
                },
            )

            # Three-node chain: support_tickets → nps_score → churn.
            # NPS sits as a mediator, so we get two real edges between three nodes.
            support_ref = CausalObjectRef(id=uuid4(), object_type="Treatment")
            nps_ref = CausalObjectRef(id=uuid4(), object_type="Mediator")
            churn_ref = CausalObjectRef(id=uuid4(), object_type="Outcome")
            edge_a = await _insert_seed_edge(
                session,
                source=support_ref,
                target=nps_ref,
                edge_type="CAUSES",
                confidence=0.68,
                approval_status="APPROVED",
                domain="marketing",
                verdict_id=verdict_id,
                created_at=now,
            )
            edge_b = await _insert_seed_edge(
                session,
                source=nps_ref,
                target=churn_ref,
                edge_type="MEDIATES",
                confidence=0.74,
                approval_status="APPROVED",
                domain="marketing",
                verdict_id=verdict_id,
                created_at=now,
            )

            fragment = GraphFragment(
                id=frag_id,
                name="Customer Churn Causal Model",
                domain="marketing",
                description=(
                    "Causal drivers of customer churn: contract_length (low → churn), "
                    "support_tickets (high → churn), nps_score (low → churn). "
                    "Treatments: loyalty_program_launch (reduces churn), "
                    "ad_spend_increase (mixed effect). "
                    "Confounders: payment_method, product_usage_days."
                ),
                edges=[edge_a, edge_b],
                entry_nodes=[support_ref],
                exit_nodes=[churn_ref],
                approval_status=FragmentApprovalStatus.APPROVED,
                guardian_verdict_ref=verdict_id,
                created_by=_SEEDER_USER,
                tags=["marketing", "churn", "customer-retention", "seeded"],
            )
            await session.execute(
                typed_text(
                    "INSERT INTO graph_fragments (id, name, domain, description, edge_ids, "
                    "entry_nodes, exit_nodes, approval_status, version, usage_count, tags, "
                    "guardian_verdict_ref, created_by, created_at) "
                    "VALUES (:id, :name, :domain, :desc, :edges, :entry, :exit, :status, "
                    ":version, :usage, :tags, :verdict, :created_by, :created_at)",
                    edges=UUID_ARRAY,
                    tags=TEXT_ARRAY,
                    created_at=TIMESTAMP,
                ),
                {
                    "id": str(fragment.id),
                    "name": fragment.name,
                    "domain": fragment.domain,
                    "desc": fragment.description,
                    "edges": [str(e) for e in fragment.edges],
                    "entry": json.dumps([r.model_dump(mode="json") for r in fragment.entry_nodes]),
                    "exit": json.dumps([r.model_dump(mode="json") for r in fragment.exit_nodes]),
                    "status": str(fragment.approval_status),
                    "version": fragment.version,
                    "usage": fragment.usage_count,
                    "tags": fragment.tags,
                    "verdict": str(verdict_id),
                    "created_by": fragment.created_by.model_dump_json(),
                    "created_at": fragment.created_at,
                },
            )
            created["fragments"].append({"id": str(frag_id), "name": fragment.name})
            await session.commit()

    import asyncio
    asyncio.run(_seed())
    return created


# ── Evaluation Seed Data ──────────────────────────────────────────────────────


def seed_evaluation_data(engine: Any) -> dict[str, Any]:
    """Seed audit_log with 30 days of evaluation data for decision quality metrics."""
    now = datetime.now(UTC)
    created = {"audit_entries": 0, "approved": 0, "rejected": 0, "escalated": 0}

    async def _seed():
        async with CloudSqlFactory.get_session_factory()() as session:
            for days_ago in range(30):
                # 10-20 entries per day
                for hour in range(0, 24, 3):
                    ts = (now - timedelta(days=days_ago, hours=hour)).isoformat()
                    verdict = "APPROVED"
                    if hour % 7 == 0:
                        verdict = "REJECTED"
                        created["rejected"] += 1
                    elif hour % 11 == 0:
                        verdict = "REQUIRES_ESCALATION"
                        created["escalated"] += 1
                    else:
                        created["approved"] += 1

                    entry_id = str(uuid4())
                    policy_rules = (
                        ["PR-002"] if verdict == "REJECTED"
                        else ["PR-003"] if verdict == "REQUIRES_ESCALATION"
                        else ["PR-001"]
                    )
                    await session.execute(
                        typed_text(
                            "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                            "guardian_verdict, policy_rules_cited, session_id, created_at) "
                            "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :session, :ts)",
                            rules=TEXT_ARRAY,
                            ts=TIMESTAMP,
                        ),
                        {
                            "id": entry_id,
                            "agent": f"agent-{(hour % 8) + 1}",
                            "action": "EVALUATION_SEED",
                            "hash": hashlib.sha256(entry_id.encode()).hexdigest(),
                            "verdict": verdict,
                            "rules": policy_rules,
                            "session": f"eval-session-{days_ago}-{hour}",
                            "ts": ts,
                        },
                    )
                    created["audit_entries"] += 1

            await session.commit()

    import asyncio
    asyncio.run(_seed())
    return created


# ── Review Queue Seed Data ────────────────────────────────────────────────────


REVIEW_FRAGMENTS = [
    {
        "name": "Marketing → Conversion Causal Path (Q2)",
        "domain": "marketing",
        "description": (
            "Proposed model: email_campaign → site_visit → conversion. "
            "Pending review: confounder candidate (seasonality) not yet adjusted."
        ),
        "status": FragmentApprovalStatus.DRAFT,
        "tags": ["marketing", "conversion", "under-review", "seeded"],
        "rules": '["PR-001", "PR-007"]',
    },
    {
        "name": "Inventory Stockout → Lost Revenue (DRAFT)",
        "domain": "devops",
        "description": (
            "Draft model linking inventory stockouts to revenue impact. "
            "Awaiting sensitivity analysis on demand-elasticity assumption."
        ),
        "status": FragmentApprovalStatus.DRAFT,
        "tags": ["devops", "inventory", "draft", "seeded"],
        "rules": '["PR-007"]',
    },
    {
        "name": "Auth Service Outage → User Churn",
        "domain": "marketing",
        "description": (
            "Cross-domain causal hypothesis: prolonged auth-service outages drive "
            "user churn within 14 days. Awaiting H-Neuron hallucination score check."
        ),
        "status": FragmentApprovalStatus.DRAFT,
        "tags": ["cross-domain", "auth", "churn", "under-review", "seeded"],
        "rules": '["PR-002"]',
    },
    {
        "name": "Notification Queue Depth → Latency Bleed",
        "domain": "devops",
        "description": (
            "Draft: notification-service backpressure propagates to api-gateway "
            "via their shared message queue. Cycle-check pending."
        ),
        "status": FragmentApprovalStatus.DRAFT,
        "tags": ["devops", "backpressure", "draft", "seeded"],
        "rules": '["PR-001"]',
    },
]

REVIEW_ENTITIES = [
    {
        "name": "feature-flag-canary",
        "type": EntityType.SERVICE,
        "domain": "devops",
        "description": "Canary rollout controller — pending ontology review",
        "status": ApprovalStatus.UNDER_REVIEW,
    },
    {
        "name": "loyalty-program-tier",
        "type": EntityType.SERVICE,
        "domain": "marketing",
        "description": "Tiered loyalty assignment service — draft entity",
        "status": ApprovalStatus.DRAFT,
    },
]


def seed_review_queue(engine: Any) -> dict[str, Any]:
    """Seed the governance review queue with DRAFT and UNDER_REVIEW objects."""
    created: dict[str, Any] = {"fragments": [], "entities": []}

    async def _seed():
        async with CloudSqlFactory.get_session_factory()() as session:
            # Stagger created_at across the last 5 days so the queue isn't flat
            for offset, frag_spec in enumerate(REVIEW_FRAGMENTS):
                verdict_id = _verdict_id()
                frag_id = uuid4()
                ts = (datetime.now(UTC) - timedelta(days=offset, hours=offset * 3)).isoformat()

                await session.execute(
                    typed_text(
                        "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                        "guardian_verdict, policy_rules_cited, created_at) "
                        "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)",
                        rules=TEXT_ARRAY,
                        ts=TIMESTAMP,
                    ),
                    {
                        "id": str(verdict_id),
                        "agent": "seeder",
                        "action": "SEED_REVIEW_FRAGMENT",
                        "hash": hashlib.sha256(frag_spec["name"].encode()).hexdigest(),
                        "verdict": "REQUIRES_ESCALATION",
                        "rules": json.loads(frag_spec["rules"]),
                        "ts": ts,
                    },
                )

                entry_ref = CausalObjectRef(id=uuid4(), object_type="Treatment")
                exit_ref = CausalObjectRef(id=uuid4(), object_type="Outcome")
                # Review-queue fragments are DRAFT/UNDER_REVIEW, so the edge
                # mirrors the fragment's status — its Guardian verdict is
                # REQUIRES_ESCALATION pending human review.
                edge_status = (
                    "APPROVED"
                    if frag_spec["status"] == FragmentApprovalStatus.APPROVED
                    else "DRAFT"
                )
                edge_id = await _insert_seed_edge(
                    session,
                    source=entry_ref,
                    target=exit_ref,
                    edge_type="CAUSES",
                    confidence=0.55,
                    approval_status=edge_status,
                    domain=frag_spec["domain"],
                    verdict_id=verdict_id,
                    created_at=ts,
                )

                fragment = GraphFragment(
                    id=frag_id,
                    name=frag_spec["name"],
                    domain=frag_spec["domain"],
                    description=frag_spec["description"],
                    edges=[edge_id],
                    entry_nodes=[entry_ref],
                    exit_nodes=[exit_ref],
                    approval_status=frag_spec["status"],
                    guardian_verdict_ref=verdict_id,
                    created_by=_SEEDER_USER,
                    tags=frag_spec["tags"],
                )
                await session.execute(
                    typed_text(
                        "INSERT INTO graph_fragments (id, name, domain, description, edge_ids, "
                        "entry_nodes, exit_nodes, approval_status, version, usage_count, tags, "
                        "guardian_verdict_ref, created_by, created_at) "
                        "VALUES (:id, :name, :domain, :desc, :edges, :entry, :exit, :status, "
                        ":version, :usage, :tags, :verdict, :created_by, :created_at)",
                        edges=UUID_ARRAY,
                        tags=TEXT_ARRAY,
                        created_at=TIMESTAMP,
                    ),
                    {
                        "id": str(fragment.id),
                        "name": fragment.name,
                        "domain": fragment.domain,
                        "desc": fragment.description,
                        "edges": [str(e) for e in fragment.edges],
                        "entry": json.dumps([r.model_dump(mode="json") for r in fragment.entry_nodes]),
                        "exit": json.dumps([r.model_dump(mode="json") for r in fragment.exit_nodes]),
                        "status": str(fragment.approval_status),
                        "version": fragment.version,
                        "usage": fragment.usage_count,
                        "tags": fragment.tags,
                        "verdict": str(verdict_id),
                        "created_by": fragment.created_by.model_dump_json(),
                        "created_at": ts,
                    },
                )
                created["fragments"].append({"id": str(frag_id), "name": fragment.name, "status": str(frag_spec["status"])})

            for offset, ent_spec in enumerate(REVIEW_ENTITIES):
                verdict_id = _verdict_id()
                ts = (datetime.now(UTC) - timedelta(days=offset + 1, hours=2)).isoformat()

                await session.execute(
                    typed_text(
                        "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                        "guardian_verdict, policy_rules_cited, created_at) "
                        "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :ts)",
                        rules=TEXT_ARRAY,
                        ts=TIMESTAMP,
                    ),
                    {
                        "id": str(verdict_id),
                        "agent": "seeder",
                        "action": "SEED_REVIEW_ENTITY",
                        "hash": hashlib.sha256(ent_spec["name"].encode()).hexdigest(),
                        "verdict": "REQUIRES_ESCALATION",
                        "rules": ["PR-001"],
                        "ts": ts,
                    },
                )

                entity = CausalEntity(
                    name=ent_spec["name"],
                    entity_type=ent_spec["type"],
                    domain=ent_spec["domain"],
                    description=ent_spec["description"],
                    approval_status=ent_spec["status"],
                    created_by=_SEEDER_USER,
                    created_at=datetime.now(UTC) - timedelta(days=offset + 1),
                    lineage=_make_lineage("SEEDED_FOR_REVIEW"),
                    tags=[ent_spec["domain"], "pending-review", "seeded"],
                )
                await session.execute(
                    typed_text(
                        "INSERT INTO causal_entities (id, name, entity_type, domain, description, "
                        "attributes, ontology_version, approval_status, created_by, created_at, "
                        "approved_at, approved_by, lineage, tags) "
                        "VALUES (:id, :name, :type, :domain, :desc, :attrs, :ont, :status, "
                        ":created_by, :created_at, :approved_at, :approved_by, :lineage, :tags)",
                        created_at=TIMESTAMP,
                        approved_at=TIMESTAMP,
                        tags=TEXT_ARRAY,
                    ),
                    {
                        "id": str(entity.id),
                        "name": entity.name,
                        "type": str(entity.entity_type),
                        "domain": entity.domain,
                        "desc": entity.description or "",
                        "attrs": "{}",
                        "ont": entity.ontology_version,
                        "status": str(entity.approval_status),
                        "created_by": entity.created_by.model_dump_json(),
                        "created_at": ts,
                        "approved_at": None,
                        "approved_by": None,
                        "lineage": "[]",
                        "tags": entity.tags,
                    },
                )
                created["entities"].append({"id": str(entity.id), "name": ent_spec["name"], "status": str(ent_spec["status"])})

            await session.commit()

    import asyncio
    asyncio.run(_seed())
    return created


# ── Incidents Seed Data ───────────────────────────────────────────────────────


SEED_INCIDENTS = [
    {
        "id": "INC-SEED-001",
        "title": "Payment-svc p99 latency spike after v3.2 rollout",
        "severity": "SEV1",
        "affected_services": ["payment-service", "api-gateway"],
        "status": "ACTIVE",
        "session_id": "seed-session-001",
        "cynefin_domain": "COMPLICATED",
        "domain_confidence": 0.82,
        "overall_confidence": 0.74,
        "guardian_verdict": "REQUIRES_ESCALATION",
        "draft_explanation": (
            "Latency p99 rose from 120ms to 450ms within 8 minutes of the payment-service "
            "v3.2 canary release. Strong temporal correlation with the deployment event; "
            "no concurrent traffic shift. Likely root cause: connection-pool exhaustion."
        ),
        "hours_ago": 2,
        "interventions": [
            {"title": "Rollback payment-service to v3.1", "risk_tier": "MEDIUM", "verdict": "APPROVED", "confidence": 0.83},
            {"title": "Scale connection pool from 50 to 200", "risk_tier": "LOW", "verdict": "REQUIRES_ESCALATION", "confidence": 0.71},
        ],
    },
    {
        "id": "INC-SEED-002",
        "title": "Notification queue depth runaway",
        "severity": "SEV2",
        "affected_services": ["notification-service"],
        "status": "INVESTIGATING",
        "session_id": "seed-session-002",
        "cynefin_domain": "COMPLEX",
        "domain_confidence": 0.68,
        "overall_confidence": 0.62,
        "guardian_verdict": "APPROVED",
        "draft_explanation": (
            "Queue depth climbed from baseline 50 to 5000 messages over 30 minutes. "
            "Email delivery downstream rate-limited by SES. Cascade risk to API gateway."
        ),
        "hours_ago": 8,
        "interventions": [
            {"title": "Throttle inbound publish rate by 50%", "risk_tier": "LOW", "verdict": "APPROVED", "confidence": 0.79},
            {"title": "Switch SES region to us-west-2", "risk_tier": "HIGH", "verdict": "REJECTED", "confidence": 0.45},
        ],
    },
    {
        "id": "INC-SEED-003",
        "title": "Auth-service session token regression",
        "severity": "SEV2",
        "affected_services": ["auth-service", "api-gateway"],
        "status": "INVESTIGATING",
        "session_id": "seed-session-003",
        "cynefin_domain": "COMPLICATED",
        "domain_confidence": 0.91,
        "overall_confidence": 0.85,
        "guardian_verdict": "APPROVED",
        "draft_explanation": (
            "2.1% of OAuth refresh attempts returning 401 since auth-service v2.1 "
            "security patch deployed 5h ago. Token version field mismatch suspected."
        ),
        "hours_ago": 5,
        "interventions": [
            {"title": "Patch token-version backwards-compat shim", "risk_tier": "LOW", "verdict": "APPROVED", "confidence": 0.88},
        ],
    },
    {
        "id": "INC-SEED-004",
        "title": "Inventory-svc stockout on three SKUs",
        "severity": "SEV3",
        "affected_services": ["inventory-service", "order-service"],
        "status": "INVESTIGATING",
        "session_id": "seed-session-004",
        "cynefin_domain": "OBVIOUS",
        "domain_confidence": 0.95,
        "overall_confidence": 0.93,
        "guardian_verdict": "APPROVED",
        "draft_explanation": (
            "Three high-velocity SKUs hit zero inventory. Reorder ETAs available; "
            "no upstream causal mystery — straightforward operational response."
        ),
        "hours_ago": 18,
        "interventions": [
            {"title": "Flip SKUs to backorder display", "risk_tier": "LOW", "verdict": "APPROVED", "confidence": 0.96},
        ],
    },
    {
        "id": "INC-SEED-005",
        "title": "API-gateway TLS handshake failures (region eu-west-1)",
        "severity": "SEV1",
        "affected_services": ["api-gateway"],
        "status": "ACTIVE",
        "session_id": "seed-session-005",
        "cynefin_domain": "CHAOTIC",
        "domain_confidence": 0.88,
        "overall_confidence": 0.55,
        "guardian_verdict": "REQUIRES_ESCALATION",
        "draft_explanation": (
            "Sudden 60% TLS handshake failure rate in eu-west-1 only. "
            "Pattern not matching any prior incident. Immediate human escalation."
        ),
        "hours_ago": 1,
        "interventions": [
            {"title": "Failover eu-west-1 traffic to eu-central-1", "risk_tier": "HIGH", "verdict": "REQUIRES_ESCALATION", "confidence": 0.51},
            {"title": "Roll back api-gateway v4.0 TLS upgrade", "risk_tier": "MEDIUM", "verdict": "REQUIRES_ESCALATION", "confidence": 0.64},
        ],
    },
]


def seed_incidents(engine: Any) -> dict[str, Any]:
    """Seed the incidents table with realistic records spanning severities and verdicts.

    Also writes corresponding intervention APPROVED/REJECTED audit_log entries so the
    UI rehydrates the post-decision state on reload.
    """
    created: dict[str, Any] = {"incidents": [], "decisions": 0}

    async def _seed():
        async with CloudSqlFactory.get_session_factory()() as session:
            for spec in SEED_INCIDENTS:
                created_at = (datetime.now(UTC) - timedelta(hours=spec["hours_ago"])).isoformat()
                proposed_interventions = []
                for rank, intervention in enumerate(spec["interventions"], start=1):
                    int_id = f"int-{rank}-{spec['session_id']}"
                    proposed_interventions.append(
                        {
                            "id": int_id,
                            "title": intervention["title"],
                            "description": f"Recommended action for {spec['title']}.",
                            "confidence": intervention["confidence"],
                            "riskTier": intervention["risk_tier"],
                            "guardianVerdict": intervention["verdict"],
                            "rank": rank,
                        }
                    )

                    # If APPROVED/REJECTED, also record an audit_log decision so
                    # /interventions/decisions returns it and the merge layer
                    # rehydrates the verdict on reload.
                    if intervention["verdict"] in ("APPROVED", "REJECTED"):
                        decision_ts = (datetime.now(UTC) - timedelta(hours=spec["hours_ago"]) + timedelta(minutes=15)).isoformat()
                        payload = json.dumps(
                            {
                                "intervention_id": int_id,
                                "incident_id": spec["id"],
                                "intervention_title": intervention["title"],
                                "reason": "Seeded decision for demo data",
                                "decided_by": "seeder-001",
                            },
                            sort_keys=True,
                        )
                        await session.execute(
                            typed_text(
                                "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                                "guardian_verdict, policy_rules_cited, session_id, created_at) "
                                "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :session, :ts)",
                                rules=TEXT_ARRAY,
                                ts=TIMESTAMP,
                            ),
                            {
                                "id": str(uuid4()),
                                "agent": "seeder",
                                "action": f"INTERVENTION_{intervention['verdict']}",
                                "hash": payload,
                                "verdict": intervention["verdict"],
                                "rules": ["PR-006"],
                                "session": spec["id"],
                                "ts": decision_ts,
                            },
                        )
                        created["decisions"] += 1

                hypotheses = [
                    {
                        "id": f"hyp-{spec['session_id']}",
                        "rank": 1,
                        "description": spec["draft_explanation"],
                        "confidence": spec["overall_confidence"],
                    }
                ]

                await session.execute(
                    # An upsert on the fixed seed ids, as the incidents API does:
                    # INSERT OR REPLACE is SQLite-only; ON CONFLICT is both dialects'.
                    typed_text(
                        "INSERT INTO incidents "
                        "(id, title, severity, affected_services, status, session_id, "
                        "cynefin_domain, domain_confidence, overall_confidence, guardian_verdict, "
                        "draft_explanation, causal_hypotheses, proposed_interventions, "
                        "submitted_by, created_at, updated_at) "
                        "VALUES (:id, :title, :sev, :svcs, :status, :session_id, "
                        ":cynefin, :dc, :oc, :verdict, :explain, :hyp, :ints, "
                        ":by, :ts, :ts) "
                        "ON CONFLICT (id) DO UPDATE SET "
                        "title = excluded.title, severity = excluded.severity, "
                        "affected_services = excluded.affected_services, "
                        "status = excluded.status, session_id = excluded.session_id, "
                        "cynefin_domain = excluded.cynefin_domain, "
                        "domain_confidence = excluded.domain_confidence, "
                        "overall_confidence = excluded.overall_confidence, "
                        "guardian_verdict = excluded.guardian_verdict, "
                        "draft_explanation = excluded.draft_explanation, "
                        "causal_hypotheses = excluded.causal_hypotheses, "
                        "proposed_interventions = excluded.proposed_interventions, "
                        "submitted_by = excluded.submitted_by, "
                        "created_at = excluded.created_at, updated_at = excluded.updated_at",
                        ts=TIMESTAMP,
                    ),
                    {
                        "id": spec["id"],
                        "title": spec["title"],
                        "sev": spec["severity"],
                        "svcs": json.dumps(spec["affected_services"]),
                        "status": spec["status"],
                        "session_id": spec["session_id"],
                        "cynefin": spec["cynefin_domain"],
                        "dc": spec["domain_confidence"],
                        "oc": spec["overall_confidence"],
                        "verdict": spec["guardian_verdict"],
                        "explain": spec["draft_explanation"],
                        "hyp": json.dumps(hypotheses),
                        "ints": json.dumps(proposed_interventions),
                        "by": "seeder-001",
                        "ts": created_at,
                    },
                )
                created["incidents"].append({"id": spec["id"], "title": spec["title"], "severity": spec["severity"]})

            await session.commit()

    import asyncio
    asyncio.run(_seed())
    return created


# ── Clear seeded data ─────────────────────────────────────────────────────────
#
# --clear deletes the seeder's rows from the tables that allow a DELETE at all,
# and nothing else:
#
#   graph_fragments, causal_entities  created_by is the seeder's UserRef
#   causal_edges                      guardian_verdict_ref is a seeder audit row
#                                     (edges carry no created_by)
#   incidents                         submitted_by is the seeder
#
# It never touches an append-only table -- audit_log, decision_records,
# decision_followups, causal_model_versions, decision_model_refs -- whose
# triggers refuse a DELETE on both backends (and on PostgreSQL no role is
# granted one). The seeder's audit rows therefore stay, by design, and --clear
# reports how many it kept. Seeding again adds new ones.
#
# On PostgreSQL it must run as a role that may DELETE from those tables: the
# tables' owner, as migrations and seeding do (docs/QUICKSTART.md). causal_app,
# the role the backend connects as, has no DELETE grant anywhere; as causal_app
# --clear refuses up front instead of failing halfway.

#: The tables --clear deletes from, children before parents.
CLEARABLE_TABLES: tuple[str, ...] = (
    "graph_fragments",
    "causal_edges",
    "causal_entities",
    "incidents",
)

#: Tables --clear never touches: append-only by trigger (and by grant on PostgreSQL).
APPEND_ONLY_TABLES: tuple[str, ...] = (
    "audit_log",
    "decision_records",
    "decision_followups",
    "causal_model_versions",
    "decision_model_refs",
)

_SEEDER_AGENT_ID = "seeder"


def _clear_statements(dialect: str) -> dict[str, str]:
    """The DELETE for each clearable table, in the given SQL dialect."""
    if dialect == "postgresql":
        created_by_seeder = "created_by->>'id' = :seeder_id"
    elif dialect == "sqlite":
        created_by_seeder = "json_extract(created_by, '$.id') = :seeder_id"
    else:
        raise RuntimeError(f"seed_data --clear: unsupported database dialect {dialect!r}")
    return {
        "graph_fragments": f"DELETE FROM graph_fragments WHERE {created_by_seeder}",
        "causal_edges": (
            "DELETE FROM causal_edges WHERE guardian_verdict_ref IN "
            "(SELECT id FROM audit_log WHERE agent_id = :seeder_agent)"
        ),
        "causal_entities": f"DELETE FROM causal_entities WHERE {created_by_seeder}",
        "incidents": "DELETE FROM incidents WHERE submitted_by = :seeder_id",
    }


async def clear_seeded(session: Any) -> dict[str, Any]:
    """Delete the seeder's rows (see above) in one transaction on ``session``."""
    dialect = session.bind.dialect.name
    statements = _clear_statements(dialect)

    if dialect == "postgresql":
        role = (await session.execute(text("SELECT current_user"))).scalar()
        missing = [
            table
            for table in CLEARABLE_TABLES
            if not (
                await session.execute(
                    text("SELECT has_table_privilege(:t, 'DELETE')"), {"t": table}
                )
            ).scalar()
        ]
        if missing:
            raise RuntimeError(
                f"seed_data --clear deletes rows and must run as the tables' owner: "
                f"role {role!r} has no DELETE on {', '.join(missing)}. Run it with "
                "DATABASE_URL naming the owner (the role that ran the migrations), "
                "not causal_app. Nothing was deleted."
            )

    params = {"seeder_id": _SEEDER_USER.id, "seeder_agent": _SEEDER_AGENT_ID}
    cleared: dict[str, int] = {}
    for table in CLEARABLE_TABLES:
        result = await session.execute(text(statements[table]), params)
        cleared[table] = result.rowcount or 0
    kept = (
        await session.execute(
            text("SELECT COUNT(*) FROM audit_log WHERE agent_id = :seeder_agent"), params
        )
    ).scalar()
    await session.commit()
    return {"cleared": cleared, "retained_append_only": {"audit_log": int(kept or 0)}}


def clear_seeded_data(engine: Any) -> dict[str, Any]:
    """Remove the seeder's rows from the clearable tables (see above)."""

    async def _clear() -> dict[str, Any]:
        async with CloudSqlFactory.get_session_factory()() as session:
            return await clear_seeded(session)

    import asyncio
    return asyncio.run(_clear())


# ── Main CLI ──────────────────────────────────────────────────────────────────

import json


def main() -> None:
    # The seeder acts for no user and writes into several domains: it declares a
    # service scope, which the RLS policies let see every domain. Without it,
    # on PostgreSQL, every write to the semantic tables would be refused.
    from src.db.scope import service_scope

    with service_scope("scripts.seed_data"):
        _main()


def _main() -> None:
    parser = argparse.ArgumentParser(description="Seed the Causal Platform with test data")
    parser.add_argument("--devops", action="store_true", help="Seed DevOps domain data")
    parser.add_argument("--marketing", action="store_true", help="Seed Marketing domain data")
    parser.add_argument("--evaluation", action="store_true", help="Seed evaluation metrics data")
    parser.add_argument("--reviews", action="store_true", help="Seed DRAFT/UNDER_REVIEW objects for the governance queue")
    parser.add_argument("--incidents", action="store_true", help="Seed realistic incidents with intervention verdicts")
    parser.add_argument("--all", action="store_true", help="Seed all domains")
    parser.add_argument("--clear", action="store_true", help="Clear seeded data")
    args = parser.parse_args()

    if args.clear:
        print(json.dumps(clear_seeded_data(None), indent=2))
        return

    if not any([args.devops, args.marketing, args.evaluation, args.reviews, args.incidents, args.all]):
        parser.print_help()
        return

    results: dict[str, Any] = {}

    if args.all or args.devops:
        print("Seeding DevOps domain...")
        results["devops"] = seed_devops(None)
        print(f"  Entities: {len(results['devops']['entities'])}, "
              f"Fragments: {len(results['devops']['fragments'])}")

    if args.all or args.marketing:
        print("Seeding Marketing domain...")
        results["marketing"] = seed_marketing(None)
        print(f"  Fragments: {len(results['marketing']['fragments'])}")

    if args.all or args.evaluation:
        print("Seeding evaluation data (30 days)...")
        results["evaluation"] = seed_evaluation_data(None)
        print(f"  Audit entries: {results['evaluation']['audit_entries']}, "
              f"Approved: {results['evaluation']['approved']}, "
              f"Rejected: {results['evaluation']['rejected']}, "
              f"Escalated: {results['evaluation']['escalated']}")

    if args.all or args.reviews:
        print("Seeding governance review queue (DRAFT + UNDER_REVIEW)...")
        results["reviews"] = seed_review_queue(None)
        print(f"  Fragments: {len(results['reviews']['fragments'])}, "
              f"Entities: {len(results['reviews']['entities'])}")

    if args.all or args.incidents:
        print("Seeding incidents...")
        results["incidents"] = seed_incidents(None)
        print(f"  Incidents: {len(results['incidents']['incidents'])}, "
              f"Decisions: {results['incidents']['decisions']}")

    print("\nAll seeded. Run with --clear to remove seeded data.")


if __name__ == "__main__":
    main()
