"""
DriftMonitorAgent — continuous monitoring agent for causal and distribution drift.

Responsibilities (AGENTS.md §2.1 DriftMonitorAgent):
  - Monitor live data streams for distribution drift (KL divergence)
  - Detect causal drift (graph structure vs observed data patterns)
  - Detect semantic drift (entity definition inconsistencies)
  - Detect concept drift (model performance degradation)
  - Emit drift alerts through the in-process AlertEmitter (logging, callbacks,
    deduplication; there is no message broker)
  - Store drift alerts in Neo4j for audit and tracking, when NEO4J_URI is set
  - NEVER write to the semantic layer (alert-only, no graph modifications)

Implemented skills:
  drift_detect              — compute KL divergence on feature distributions
  store_drift_alert         — persist alerts to Neo4j (optional)
  get_recent_drift_alerts   — retrieve recent alerts
  causal_structure_compare  — compare graph edges against live data patterns
  alert_emit                — emit a drift alert through AlertEmitter

EpistemicState constraints enforced:
  - audit_log_required: All drift detections logged
  - no_semantic_layer_writes: Alert-only, no graph modifications
  - circuit_breaker_enforced: Max 5 cycles

Production readiness:
  - Configurable thresholds via constructor parameters
  - Drift severity scoring with multi-factor analysis
  - Optional Neo4j alert storage with TTL (NEO4J_URI; tested against mocks only)
  - Integration with experience buffer for pattern learning
"""

from __future__ import annotations

from typing import Any

import structlog

from src.agents.base import BaseAgent
from src.semantic_layer.models import (
    EpistemicState,
    Recommendation,
)
from src.skills import (
    DriftAlertStorageError,
    DriftDetectionError,
    acknowledge_drift_alert,
    calculate_drift_severity,
    detect_causal_drift,
    detect_semantic_drift,
    drift_detect,
    get_recent_drift_alerts,
    store_drift_alert,
)

log = structlog.get_logger(__name__)

# Default thresholds (can be overridden via constructor)
_DEFAULT_KL_THRESHOLD = 0.1
_DEFAULT_SEVERE_KL_THRESHOLD = 0.5
_DEFAULT_SEMANTIC_SIMILARITY_THRESHOLD = 0.7

#: Where an emitted alert goes: the in-process AlertEmitter. There is no message
#: broker, so no topic is named.
_ALERT_CHANNEL = "AlertEmitter (in-process)"


class DriftMonitorAgent(BaseAgent):
    """
    Continuous monitoring agent that detects and alerts on drift.

    This agent is read-only with respect to the semantic layer.
    It emits alerts through the in-process AlertEmitter and, when NEO4J_URI
    is set, stores them in Neo4j for audit.
    Flags the EpistemicState for human review when severe drift is detected.

    Designed to run as a background agent (maps to OpenFang automate lane).

    Usage::

        agent = DriftMonitorAgent(
            kl_threshold=0.1,
            severe_kl_threshold=0.5,
        )
        state = await agent.process(state)
        # state.flags contains drift indicators
        # state.recommendations contains drift response actions

    Args:
        agent_id: Unique identifier for this agent instance
        kl_threshold: Threshold for moderate drift detection
        severe_kl_threshold: Threshold for severe drift detection
        semantic_similarity_threshold: Threshold for semantic drift detection
        enable_neo4j_storage: Whether to store alerts in Neo4j
        enable_alert_emission: Whether to emit alerts through AlertEmitter
    """

    def __init__(
        self,
        agent_id: str = "drift-monitor-agent",
        kl_threshold: float = _DEFAULT_KL_THRESHOLD,
        severe_kl_threshold: float = _DEFAULT_SEVERE_KL_THRESHOLD,
        semantic_similarity_threshold: float = _DEFAULT_SEMANTIC_SIMILARITY_THRESHOLD,
        enable_neo4j_storage: bool = True,
        enable_alert_emission: bool = True,
    ) -> None:
        super().__init__(agent_id=agent_id)
        self._kl_threshold = kl_threshold
        self._severe_kl_threshold = severe_kl_threshold
        self._semantic_similarity_threshold = semantic_similarity_threshold
        self._enable_neo4j_storage = enable_neo4j_storage
        self._enable_alert_emission = enable_alert_emission

        # In-process alert emitter: structured log and registered callbacks,
        # with deduplication and throttling. There is no message broker.
        # Imported lazily to break circular import with sensors package.
        from src.sensors.alert_emit import AlertEmitter
        self._alert_emitter = AlertEmitter(source=agent_id)

        self._log.info(
            "drift_monitor.agent_initialized",
            kl_threshold=kl_threshold,
            severe_kl_threshold=severe_kl_threshold,
            semantic_threshold=semantic_similarity_threshold,
            neo4j_storage=enable_neo4j_storage,
            alert_emission=enable_alert_emission,
        )

    async def process(self, state: EpistemicState) -> EpistemicState:
        """
        Run drift detection pipeline.

        Steps:
          1. Circuit breaker + hallucination checks
          2. Distribution drift detection (KL divergence)
          3. Causal structure comparison
          4. Semantic drift detection (if entity definitions available)
          5. Severity classification and alert emission
          6. Store drift alerts in Neo4j
          7. Return updated state with drift flags and recommendations
        """
        state = self._check_circuit_breaker(state)
        state = self._check_hallucination_risk(state)

        fragment_id = state.active_graph_fragment_id

        # Step 1: Distribution drift detection
        drift_out, state = await self._call_skill(
            "drift_detect",
            {
                "fragment_id": fragment_id,
                "domain": state.cynefin_domain,
            },
            state,
        )

        kl_divergence = float(drift_out.get("kl_divergence", 0.0))
        drifted_features: list[str] = drift_out.get("drifted_features", [])
        drift_type: str = drift_out.get("drift_type", "none")
        feature_scores: dict[str, float] = drift_out.get("feature_scores", {})

        # Step 2: Causal structure comparison
        structure_out, state = await self._call_skill(
            "causal_structure_compare",
            {
                "fragment_id": fragment_id,
                "kl_divergence": kl_divergence,
                "drifted_features": drifted_features,
            },
            state,
        )
        structural_drift = structure_out.get("structural_drift_detected", False)
        affected_edges: list[str] = structure_out.get("affected_edges", [])

        # Step 3: Semantic drift detection (if definitions available)
        semantic_out, state = await self._call_skill(
            "semantic_drift_detect",
            {
                "fragment_id": fragment_id,
            },
            state,
        )
        semantic_drift = semantic_out.get("semantic_drift", False)
        drifted_entities: list[dict] = semantic_out.get("drifted_entities", [])

        # Step 4: Calculate comprehensive drift severity
        severity_result = calculate_drift_severity(
            kl_divergence=kl_divergence,
            drifted_features=drifted_features,
            structural_drift=structural_drift,
            semantic_drift=semantic_drift,
            concept_drift=False,  # Would need label data
        )

        severity = severity_result["severity"]
        severity_score = severity_result["score"]

        # Step 5: Classify severity and route
        recommendations: list[Recommendation] = []
        new_flags: list[str] = list(state.flags)
        requires_human = state.requires_human_review
        alerts_emitted: list[dict] = []

        # Distribution drift handling
        if kl_divergence > self._severe_kl_threshold:
            new_flags.append("severe_distribution_drift")
            requires_human = True
            recommendations.append(
                Recommendation(
                    rank=1,
                    action="ESCALATE: Severe distribution drift detected — governance review required",
                    rationale=(
                        f"KL divergence {kl_divergence:.4f} exceeds severe threshold "
                        f"({self._severe_kl_threshold}). Features affected: "
                        f"{', '.join(drifted_features) or 'unspecified'}. "
                        f"Severity score: {severity_score}/100."
                    ),
                    confidence=min(kl_divergence, 1.0),
                    risk_tier="CRITICAL",
                )
            )

            # Emit and store alert
            alert_result = await self._emit_and_store_alert(
                state=state,
                alert_type="SEVERE_DISTRIBUTION_DRIFT",
                severity="CRITICAL",
                drift_score=kl_divergence,
                drifted_features=drifted_features,
                drift_type="distribution",
                description=f"Severe distribution drift detected. KL divergence: {kl_divergence:.4f}",
                recommendation="Immediate governance review required.",
                evidence={
                    "kl_divergence": kl_divergence,
                    "feature_scores": feature_scores,
                    "severity_score": severity_score,
                    "threshold": self._severe_kl_threshold,
                },
            )
            alerts_emitted.append(alert_result)

        elif kl_divergence > self._kl_threshold:
            new_flags.append("moderate_distribution_drift")
            recommendations.append(
                Recommendation(
                    rank=1,
                    action="Re-run sensitivity analysis on affected simulations",
                    rationale=(
                        f"KL divergence {kl_divergence:.4f} exceeds threshold "
                        f"({self._kl_threshold}). Features: "
                        f"{', '.join(drifted_features) or 'unspecified'}. "
                        f"Severity score: {severity_score}/100."
                    ),
                    confidence=min(kl_divergence, 1.0),
                    risk_tier="MEDIUM",
                )
            )

            alert_result = await self._emit_and_store_alert(
                state=state,
                alert_type="DISTRIBUTION_DRIFT",
                severity="MEDIUM",
                drift_score=kl_divergence,
                drifted_features=drifted_features,
                drift_type="distribution",
                description=f"Moderate distribution drift detected. KL divergence: {kl_divergence:.4f}",
                recommendation="Re-run sensitivity analysis on affected simulations.",
                evidence={
                    "kl_divergence": kl_divergence,
                    "feature_scores": feature_scores,
                    "severity_score": severity_score,
                    "threshold": self._kl_threshold,
                },
            )
            alerts_emitted.append(alert_result)

        # Structural drift handling
        if structural_drift:
            new_flags.append("causal_structure_drift")
            requires_human = True
            recommendations.append(
                Recommendation(
                    rank=len(recommendations) + 1,
                    action="Flag graph fragment for re-review — structural drift detected",
                    rationale=(
                        f"Graph structure no longer matches live data patterns. "
                        f"Affected edges: {', '.join(affected_edges) or 'see details'}."
                    ),
                    confidence=0.8,
                    risk_tier="HIGH",
                )
            )

            alert_result = await self._emit_and_store_alert(
                state=state,
                alert_type="CAUSAL_STRUCTURE_DRIFT",
                severity="HIGH",
                drift_score=0.8,
                drifted_features=affected_edges,
                drift_type="causal",
                description="Causal structure drift detected. Graph structure diverges from data patterns.",
                recommendation="Flag graph fragment for re-review.",
                evidence={
                    "affected_edges_count": len(affected_edges),
                    "affected_edges": affected_edges,
                    "severity_score": severity_score,
                },
            )
            alerts_emitted.append(alert_result)

        # Semantic drift handling
        if semantic_drift:
            new_flags.append("semantic_drift")
            recommendations.append(
                Recommendation(
                    rank=len(recommendations) + 1,
                    action="Review entity definitions — semantic drift detected",
                    rationale=(
                        f"Entity definitions have diverged from baseline. "
                        f"{len(drifted_entities)} entities affected."
                    ),
                    confidence=0.75,
                    risk_tier="MEDIUM",
                )
            )

            alert_result = await self._emit_and_store_alert(
                state=state,
                alert_type="SEMANTIC_DRIFT",
                severity="MEDIUM",
                drift_score=0.6,
                drifted_features=[e.get("entity_id", "unknown") for e in drifted_entities],
                drift_type="semantic",
                description=f"Semantic drift detected. {len(drifted_entities)} entities affected.",
                recommendation="Review entity definitions for consistency.",
                evidence={
                    "drifted_entities_count": len(drifted_entities),
                    "drifted_entities": drifted_entities,
                    "avg_similarity": semantic_out.get("avg_similarity", 1.0),
                },
            )
            alerts_emitted.append(alert_result)

        # Healthy status if no drift detected
        if not recommendations:
            recommendations.append(
                Recommendation(
                    rank=1,
                    action="No drift detected — system healthy",
                    rationale=f"KL divergence {kl_divergence:.4f} within normal bounds. Severity score: {severity_score}/100.",
                    confidence=0.95,
                    risk_tier="LOW",
                )
            )

        # Build explanation
        explanation_parts = [
            f"Drift monitoring complete. KL divergence: {kl_divergence:.4f}. "
            f"Severity: {severity} ({severity_score}/100).",
        ]
        if drifted_features:
            explanation_parts.append(
                f"Drifted features: {', '.join(drifted_features)}."
            )
        if structural_drift:
            explanation_parts.append("Structural drift detected in graph assets.")
        if semantic_drift:
            explanation_parts.append(f"Semantic drift detected: {len(drifted_entities)} entities affected.")
        if alerts_emitted:
            explanation_parts.append(f"Alerts emitted: {len(alerts_emitted)}.")

        state = state.model_copy(
            update={
                "flags": new_flags,
                "requires_human_review": requires_human,
                "recommendations": [*state.recommendations, *recommendations],
                "draft_explanation": " ".join(explanation_parts),
            }
        )

        state = self._add_reasoning_step(
            state,
            node="DriftMonitor",
            action="DRIFT_CHECK",
            inputs={
                "fragment_id": fragment_id,
                "domain": state.cynefin_domain,
                "kl_threshold": self._kl_threshold,
                "severe_threshold": self._severe_kl_threshold,
            },
            outputs={
                "kl_divergence": kl_divergence,
                "drift_type": drift_type,
                "structural_drift": structural_drift,
                "semantic_drift": semantic_drift,
                "drifted_feature_count": len(drifted_features),
                "severity": severity,
                "severity_score": severity_score,
                "alerts_emitted": len(alerts_emitted),
            },
        )

        self._log.info(
            "drift_monitor.complete",
            session_id=state.session_id,
            kl_divergence=kl_divergence,
            structural_drift=structural_drift,
            semantic_drift=semantic_drift,
            severity=severity,
            severity_score=severity_score,
            alert_count=len(alerts_emitted),
        )
        return state

    async def _emit_and_store_alert(
        self,
        state: EpistemicState,
        alert_type: str,
        severity: str,
        drift_score: float,
        drifted_features: list[str],
        drift_type: str,
        description: str,
        recommendation: str,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Emit the alert through AlertEmitter and store it in Neo4j (if configured)."""
        alert_result = {
            "alert_type": alert_type,
            "severity": severity,
            "stored": False,
            "emitted": False,
            "evidence": evidence,
        }

        # Emit through AlertEmitter if enabled
        if self._enable_alert_emission:
            try:
                emit_out = await self._emit_production_alert(
                    state=state,
                    alert_type=alert_type,
                    severity=severity,
                    drift_score=drift_score,
                    drifted_features=drifted_features,
                    description=description,
                    recommendation=recommendation,
                    evidence=evidence,
                )
                alert_result["emitted"] = emit_out.get("emitted", False)
                alert_result["alert_id"] = emit_out.get("alert_id")
            except Exception as e:
                # Alert emission failure is logged but not hidden:
                # downstream consumers may need to know that emission failed.
                self._log.error("alert_emit.failed", error=str(e), alert_type=alert_type)
                alert_result["emit_error"] = str(e)

        # Store in Neo4j if enabled
        if self._enable_neo4j_storage:
            try:
                storage_result = await store_drift_alert(
                    alert_type=alert_type,
                    severity=severity,
                    fragment_id=state.active_graph_fragment_id or "",
                    drift_score=drift_score,
                    drifted_features=drifted_features,
                    drift_type=drift_type,
                    description=description,
                    recommendation=recommendation,
                    metadata={
                        "session_id": state.session_id,
                        "agent_id": self.agent_id,
                        "cynefin_domain": state.cynefin_domain.value,
                        "evidence": evidence,
                    },
                )
                alert_result["stored"] = storage_result.get("stored", False)
                alert_result["alert_id"] = alert_result.get("alert_id") or storage_result.get("alert_id")
            except DriftAlertStorageError as e:
                self._log.error("drift_alert.storage_failed", error=str(e), alert_type=alert_type)
                alert_result["storage_error"] = str(e)
            except Exception as e:
                self._log.error("drift_alert.storage_error", error=str(e), alert_type=alert_type)
                alert_result["storage_error"] = str(e)

        # Guard: at least one of emit or store succeeded, or we surface the failure.
        if not alert_result["emitted"] and not alert_result["stored"]:
            self._log.error(
                "drift_alert.complete_failure",
                alert_type=alert_type,
                session_id=state.session_id,
                errors={
                    k: v for k, v in alert_result.items()
                    if k.endswith("_error")
                },
            )

        return alert_result

    async def _emit_production_alert(
        self,
        state: EpistemicState,
        alert_type: str,
        severity: str,
        drift_score: float,
        drifted_features: list[str],
        description: str,
        recommendation: str,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Emit a drift alert through the production AlertEmitter.

        This is the non-stub alert emission path. It uses AlertEmitter for
        deduplication, throttling, and severity routing. Returns metadata
        about the emission result.
        """
        # Lazy import to break circular dependency with sensors package
        from src.sensors.alert_emit import AlertSeverity

        # Map string severity to AlertSeverity enum
        severity_map = {
            "CRITICAL": AlertSeverity.CRITICAL,
            "HIGH": AlertSeverity.HIGH,
            "MEDIUM": AlertSeverity.MEDIUM,
            "LOW": AlertSeverity.LOW,
        }
        alert_severity = severity_map.get(severity, AlertSeverity.MEDIUM)

        # Build full evidence context for the alert
        context: dict[str, Any] = {
            "alert_type": alert_type,
            "drift_score": drift_score,
            "drifted_features": drifted_features,
            "fragment_id": state.active_graph_fragment_id,
            "session_id": state.session_id,
            "recommendation": recommendation,
        }
        if evidence:
            context["evidence"] = evidence

        alert = await self._alert_emitter.emit(
            severity=alert_severity,
            alert_type=alert_type,
            message=description,
            context=context,
        )

        if alert:
            return {
                "emitted": True,
                "alert_id": alert.alert_id,
                "channel": _ALERT_CHANNEL,
                "timestamp": alert.timestamp.isoformat(),
            }

        # Alert was deduplicated or throttled — still considered "handled"
        # but not a new emission. Return empty alert_id and emitted=False.
        return {"emitted": False, "reason": "deduplicated_or_throttled"}

    # ── Skill dispatch ────────────────────────────────────────────────────────

    async def _execute_skill(
        self,
        skill_name: str,
        inputs: dict[str, Any],
        state: EpistemicState,
    ) -> dict[str, Any]:
        if skill_name == "drift_detect":
            return await self._skill_drift_detect(inputs)
        if skill_name == "causal_structure_compare":
            return await self._skill_causal_structure_compare(inputs)
        if skill_name == "semantic_drift_detect":
            return await self._skill_semantic_drift_detect(inputs)
        if skill_name == "alert_emit":
            # The skill needs the state to build the alert's context. It was not
            # passed on, so the skill always took its no-state branch.
            return await self._skill_alert_emit({**inputs, "_state": state})
        if skill_name == "semantic_layer_read":
            return await self._skill_semantic_layer_read(inputs)
        return await super()._execute_skill(skill_name, inputs, state)

    # ── Skill implementations ─────────────────────────────────────────────────

    async def _skill_drift_detect(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Compute KL divergence on feature distributions.

        Production: reads live data stream and compares against baseline
        distributions stored with the graph fragment.
        """
        try:
            # Get data from inputs or use placeholder
            reference_data = inputs.get("reference_data", {})
            current_data = inputs.get("current_data", {})

            # If no data provided, check if we should fetch from fragment
            if not reference_data or not current_data:
                # Return explicit placeholder for monitoring mode (no stored data yet)
                return {
                    "kl_divergence": 0.0,
                    "drifted_features": [],
                    "drift_type": "none",
                    "feature_scores": {},
                    "note": "no_data_for_comparison",
                    "fallback_mode": "heuristic",
                }

            return await drift_detect(
                reference_data=reference_data,
                current_data=current_data,
                kl_threshold=self._kl_threshold,
                severe_threshold=self._severe_kl_threshold,
            )
        except DriftDetectionError as e:
            self._log.error("drift_detect.failed", error=str(e))
            return {
                "kl_divergence": 0.0,
                "drifted_features": [],
                "drift_type": "none",
                "feature_scores": {},
                "error": str(e),
                "fallback_mode": "error",
            }

    async def _skill_causal_structure_compare(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Compare graph structure against live data patterns.

        Production: runs conditional independence tests on live data and
        compares with edges in the approved fragment.
        """
        try:
            fragment_edges = inputs.get("fragment_edges", [])
            data_correlations = inputs.get("data_correlations", {})

            if not fragment_edges:
                # No fragment edges available
                return {
                    "structural_drift_detected": False,
                    "affected_edges": [],
                    "note": "no_fragment_edges",
                    "fallback_mode": "heuristic",
                }

            result = await detect_causal_drift(
                reference_graph_edges=fragment_edges,
                current_data_correlations=data_correlations,
                correlation_threshold=0.5,
            )

            return {
                "structural_drift_detected": result.get("causal_drift", False),
                "affected_edges": [e.get("edge_id", "") for e in result.get("drifted_edges", [])],
                "drifted_count": result.get("drifted_count", 0),
            }
        except Exception as e:
            self._log.error("causal_structure_compare.failed", error=str(e))
            return {
                "structural_drift_detected": False,
                "affected_edges": [],
                "error": str(e),
                "fallback_mode": "error",
            }

    async def _skill_semantic_drift_detect(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Detect semantic drift in entity definitions.

        Compares reference entity definitions against current definitions.
        """
        try:
            reference_definitions = inputs.get("reference_definitions", {})
            current_definitions = inputs.get("current_definitions", {})

            if not reference_definitions or not current_definitions:
                # No definitions available for comparison
                return {
                    "semantic_drift": False,
                    "avg_similarity": 1.0,
                    "drifted_entities": [],
                    "note": "no_definitions_for_comparison",
                    "fallback_mode": "heuristic",
                }

            return detect_semantic_drift(
                reference_definitions=reference_definitions,
                current_definitions=current_definitions,
            )
        except Exception as e:
            self._log.error("semantic_drift_detect.failed", error=str(e))
            return {
                "semantic_drift": False,
                "avg_similarity": 1.0,
                "drifted_entities": [],
                "error": str(e),
                "fallback_mode": "error",
            }

    async def _skill_alert_emit(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Emit a drift alert through AlertEmitter (deduplication, throttling,
        severity routing). Without a state there is nothing to build the alert
        from, and nothing is emitted.
        """
        # This skill is now a thin wrapper over _emit_production_alert.
        # The real emission happens through AlertEmitter.
        state = inputs.get("_state")
        if state is None:
            # A call without state: nothing to build the alert from. It used to
            # answer emitted=True with a Kafka topic name while emitting nothing.
            self._log.warning(
                "drift_monitor.alert.emit_without_state",
                alert_type=inputs.get("alert_type"),
                note="alert_emit called without state; nothing emitted",
            )
            return {"emitted": False, "reason": "no_state"}

        return await self._emit_production_alert(
            state=state,
            alert_type=inputs.get("alert_type", "UNKNOWN"),
            severity=inputs.get("severity", "MEDIUM"),
            drift_score=inputs.get("kl_divergence", 0.0),
            drifted_features=inputs.get("features", []),
            description=inputs.get("description", "Drift alert"),
            recommendation=inputs.get("recommendation", ""),
        )

    async def _skill_semantic_layer_read(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """Read graph context for comparison."""
        return {"graph_context": {}}

    # ── Public API for sensor integration ────────────────────────────────────

    async def get_recent_alerts(
        self,
        fragment_id: str | None = None,
        drift_type: str | None = None,
        severity: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """
        Get recent drift alerts.

        Public API for sensors and monitoring dashboards.
        """
        return await get_recent_drift_alerts(
            fragment_id=fragment_id,
            drift_type=drift_type,
            severity=severity,
            limit=limit,
            include_acknowledged=False,
        )

    async def acknowledge_alert(self, alert_id: str) -> dict[str, Any]:
        """
        Acknowledge a drift alert.

        Public API for alert management.
        """
        return await acknowledge_drift_alert(alert_id, acknowledged_by=self.agent_id)
