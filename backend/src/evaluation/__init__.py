"""
Production evaluation pipeline for continuous agent behavioral observability.

Implements the missing evaluation infrastructure identified in the World Model
report (§2.8): decision-centric evaluation that measures what agents actually
DO in production, not just pre-deployment benchmark scores.

Modules:
  - consistency   — 8-run variance probe for agent output stability
  - probes        — Behavioral probes (calibration, Cynefin stability)
  - metrics       — Continuous metrics from audit_log + EpistemicState
"""
