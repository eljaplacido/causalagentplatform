# Testing, CI/CD & Engineering Best Practices

> **Scope:** End-to-end testing strategy, CI/CD pipeline design, quality gates, and engineering standards for the Unified Enterprise Causal Platform. These standards apply to the Python backend services and the React frontend.

---

## 1. Testing Philosophy

The platform makes causal claims. A bug in a recommendation engine is not just a software defect—it is a potentially wrong causal attribution that an operator acts on. Testing must therefore go beyond functional correctness to validate **causal correctness, governance integrity, and epistemic reliability**.

### Testing Priorities (in order)

1. **Guardian integrity** — If the Guardian can be bypassed, nothing else matters.
2. **Causal correctness** — DAG structural validity, identification assumptions, estimation accuracy.
3. **Audit trail immutability** — Append-only contracts must never be broken.
4. **Agent behavior contracts** — EpistemicState in/out types, skill output schemas.
5. **UX contract** — Critical UX invariants (thinking indicator latency, approval flow).
6. **Integration correctness** — Connector data flows, semantic layer writes, Kafka topics.
7. **Performance** — Benchmark targets from DATA_LAYER.md must be validated continuously.

---

## 2. Test Pyramid

```
                    ┌───────────────────┐
                    │     E2E Tests      │  ~5% of tests
                    │  (Playwright/k6)   │  Slow, high coverage
                    ├───────────────────┤
                    │  Integration Tests │  ~20% of tests
                    │  (pytest + Neo4j,  │  Real services, isolated
                    │   real Kafka)      │
                    ├───────────────────┤
                    │    Unit Tests      │  ~75% of tests
                    │  (pytest, Jest,    │  Fast, isolated, mocked deps
                    │   cargo test)      │
                    └───────────────────┘
```

**Current baseline (this repo, 2026-05-24):**
- Backend: **551 passing tests**, ~80% line coverage (see `pyproject.toml` `--cov-fail-under` gate)
- Frontend: Playwright e2e suite at `frontend/e2e/platform.spec.ts` (22 specs)

**Target:** ≥ 80% line coverage across Python backend, ≥ 85% branch coverage on Guardian/semantic layer paths, 100% coverage on `audit_write` and `guardian_check`.

---

## 3. Causal Correctness Tests

These are unique to this platform. They validate that the platform reasons correctly about causality, not just that the code runs.

### 3.1 DAG Structural Validity

```python
# Every approved graph fragment must pass structural tests before promotion
def test_dag_is_acyclic(graph_fragment):
    """GraphFragment must be a DAG: no cycles allowed."""
    assert not has_cycle(graph_fragment.edges)

def test_dag_backdoor_criterion(graph_fragment, treatment, outcome):
    """Backdoor criterion must be satisfiable given observed confounders."""
    adjustment_set = find_adjustment_set(graph_fragment, treatment, outcome)
    assert adjustment_set is not None, "No valid adjustment set found"

def test_dag_positivity(dataset, treatment, adjustment_set):
    """Every stratum of the adjustment set must have both treated and untreated units."""
    for stratum in enumerate_strata(dataset, adjustment_set):
        assert stratum.n_treated > 0 and stratum.n_control > 0
```

### 3.2 Counterfactual Consistency

```python
def test_counterfactual_consistency(scm, observation, intervention):
    """
    Counterfactual must be consistent: if the factual world matches
    the intervention, counterfactual outcome equals factual outcome.
    """
    factual_outcome = scm.predict(observation)
    counterfactual_outcome = scm.counterfactual(observation, intervention)
    if observation[intervention.variable] == intervention.value:
        assert abs(factual_outcome - counterfactual_outcome) < 1e-6
```

### 3.3 Sensitivity Analysis Coverage

```python
def test_sensitivity_analysis_run_before_approval(edge_id):
    """No edge transitions from DRAFT to APPROVED without a sensitivity result."""
    edge = semantic_layer.get_edge(edge_id)
    for assumption in edge.assumption_refs:
        assert assumption.sensitivity_result is not None, \
            f"Assumption {assumption.id} missing sensitivity result"
```

### 3.4 Identification Test Suite

For each estimator used in production, maintain a test dataset with known ground-truth causal effects (synthetic data with known DGP):

```python
@pytest.mark.parametrize("estimator,expected_ate,tolerance", [
    (LinearATEEstimator, 2.5, 0.1),
    (DoWhyIPW, 2.5, 0.2),
    (CausalForestEstimator, 2.5, 0.3),
])
def test_estimator_recovers_true_ate(estimator, expected_ate, tolerance, synthetic_dataset):
    """Estimator must recover the known true ATE within tolerance on synthetic data."""
    ate = estimator.fit(synthetic_dataset).estimate()
    assert abs(ate - expected_ate) < tolerance
```

---

## 4. Guardian and Governance Tests

The Guardian cannot be bypassed. These tests are mandatory and block any merge.

### 4.1 Bypass Prevention Tests

```python
def test_semantic_layer_write_blocked_without_verdict():
    """Direct write to semantic layer without Guardian verdict must be rejected at DB level."""
    with pytest.raises(IntegrityError):
        db.execute(
            "INSERT INTO causal_edges (id, source, target) VALUES ($1, $2, $3)",
            [uuid4(), "A", "B"]  # Missing guardian_verdict_ref
        )

def test_guardian_rejected_blocks_semantic_write():
    """A REJECTED Guardian verdict must prevent the action from completing."""
    verdict = guardian.check(action=propose_edge("A", "B"), policy="strict")
    assert verdict.status == "REJECTED"
    with pytest.raises(GuardianBlockedError):
        semantic_layer.write_edge(edge, verdict=verdict)
```

### 4.2 Audit Trail Immutability

```python
def test_audit_log_no_update():
    """UPDATE on audit_log table must be rejected by database role."""
    with pytest.raises(PermissionError):
        db.execute("UPDATE audit_log SET action_type = 'tampered' WHERE id = $1", [entry_id])

def test_audit_log_no_delete():
    """DELETE on audit_log table must be rejected."""
    with pytest.raises(PermissionError):
        db.execute("DELETE FROM audit_log WHERE id = $1", [entry_id])

def test_every_semantic_write_has_audit_entry(approved_edge_id):
    """Every approved semantic layer write must have a corresponding audit entry."""
    audit_entries = db.query(
        "SELECT * FROM audit_log WHERE payload_hash = $1",
        [hash_of_edge(approved_edge_id)]
    )
    assert len(audit_entries) >= 1
    assert all(e.guardian_verdict == "APPROVED" for e in audit_entries)
```

### 4.2a Drift-Triggered Action Policy (PR-008)

Added in Sprint 1 (2026-04-27): Actions initiated by drift detection must not be
allowed to modify the semantic layer without re-validation workflow.

```python
def test_drift_action_blocked_from_semantic_write():
    """Drift-triggered actions with proposed_edges must be REJECTED under PR-008."""
    drift_payload = {
        "action_type": "DRIFT_REVALIDATION",
        "proposed_edges": [{"source": {"id": "a"}, "target": {"id": "b"}}],
    }
    result = governance.check(drift_payload, state)
    assert result.verdict == "REJECTED"
    assert "PR-008" in result.policy_rules_cited
```

### 4.2b Alert Emission Contract

Added in Sprint 1 (2026-04-27): Drift alerts must carry full statistical evidence
and emit through production AlertEmitter (no log-only terminal behavior).

```python
def test_alert_includes_evidence_payload():
    """Drift alerts must include KL divergence, feature scores, severity."""
    alert = captured_alert["context"]["evidence"]
    assert "kl_divergence" in alert
    assert "feature_scores" in alert
    assert "severity_score" in alert

def test_alert_emit_failure_not_hidden():
    """When alert emission fails, the failure must be surfaced in result metadata."""
    result = await agent.process(state)
    assert result.recommendations[0].risk_tier == "CRITICAL"
    # emit_error should be present in alert metadata (logged, not swallowed)
```

### 4.3 Circuit Breaker Tests (Rust / Python)

```rust
// In cynepic-guardian tests
#[test]
fn circuit_breaker_opens_after_threshold() {
    let mut breaker = CircuitBreaker::new(threshold: 3);
    for _ in 0..3 {
        breaker.record_failure();
    }
    assert_eq!(breaker.state(), CircuitState::Open);
    assert!(breaker.allow_request().is_err());
}

#[test]
fn loop_detector_stops_runaway_cycle() {
    let mut detector = LoopDetector::new(max_iterations: 5);
    for _ in 0..5 {
        assert!(detector.check_and_increment().is_ok());
    }
    assert!(detector.check_and_increment().is_err());
}
```

---

## 5. Agent Behavior Tests

### 5.1 EpistemicState Contract Tests

```python
def test_agent_returns_valid_epistemic_state(agent, sample_input):
    """Every agent must return a well-formed EpistemicState."""
    result = agent.process(sample_input)
    assert isinstance(result, EpistemicState)
    assert result.cynefin_domain in VALID_CYNEFIN_DOMAINS
    assert 0.0 <= result.overall_confidence <= 1.0
    assert result.guardian_verdict is not None
    assert len(result.reasoning_steps) > 0

def test_agent_does_not_write_without_approved_verdict(agent, sample_input, mock_semantic_layer):
    """Agent must not call any write method before receiving APPROVED verdict."""
    mock_semantic_layer.write = MagicMock()
    result = agent.process(sample_input)
    if result.guardian_verdict != "APPROVED":
        mock_semantic_layer.write.assert_not_called()

def test_session_state_recovers_after_cache_loss(client):
    """Session API must recover the latest EpistemicState from durable session storage after hot-cache loss."""
    session_id = client.post("/api/v1/session/run", json={"description": "latency spike"}).json()["session_id"]
    clear_process_cache()
    recovered = client.get(f"/api/v1/session/{session_id}/state")
    assert recovered.status_code == 200

def test_approved_fragment_edges_are_resolved_for_simulation(simulation_agent, approved_fragment):
    """SimulationAgent must resolve fragment edge IDs into full edge payloads before identification/estimation."""
    result = simulation_agent.process(approved_fragment)
    assert len(result.simulation_results) >= 1
```

### 5.2 H-Neuron Hallucination Risk Tests

```python
def test_high_hallucination_risk_triggers_review_flag(agent):
    """When H-Neuron score > 0.7, output must be flagged for human review."""
    with patch("h_neuron.score", return_value=0.85):
        result = agent.process(ambiguous_input)
    assert result.requires_human_review is True
    assert "hallucination_risk" in result.flags
```

---

## 6. Performance Tests

Performance contracts from DATA_LAYER.md are validated continuously in CI.

```python
# Performance regression tests (run in CI, fail if > threshold)
@pytest.mark.performance
def test_dag_dsepar_query_latency(neo4j_client, approved_graph):
    """d-separation query must complete in < 50ms p95 on 500-node graph."""
    latencies = []
    for _ in range(100):
        start = time.perf_counter()
        neo4j_client.find_adjustment_set(approved_graph, treatment, outcome)
        latencies.append(time.perf_counter() - start)
    p95 = sorted(latencies)[94]
    assert p95 < 0.050, f"d-separation p95={p95:.3f}s exceeds 50ms threshold"

@pytest.mark.performance
def test_audit_log_insert_latency(db):
    """Audit log insert must be < 5ms p99."""
    latencies = [measure(lambda: db.insert_audit_entry(sample_entry)) for _ in range(200)]
    assert sorted(latencies)[198] < 0.005
```

**Benchmark scripts:** Maintain a benchmark suite under `backend/scripts/` for regression-testing causal estimators and the Guardian audit path. Run on every PR that touches agent, semantic layer, or data layer code.

---

## 7. Frontend Testing

**Framework:** Vitest + React Testing Library + Playwright

### 7.1 Component Tests (Vitest + RTL)

```tsx
// Critical UX contract: ThinkingIndicator appears within 150ms
test("ThinkingIndicator appears within 150ms of agent submission", async () => {
  const { getByTestId } = render(<IncidentBriefPanel />);
  const submitButton = getByTestId("submit-incident");
  
  const startTime = performance.now();
  fireEvent.click(submitButton);
  
  await waitFor(() => {
    expect(getByTestId("thinking-indicator")).toBeInTheDocument();
  }, { timeout: 200 });
  
  const elapsed = performance.now() - startTime;
  expect(elapsed).toBeLessThan(150);
});

// Guardian approval flow: approve/reject always visible
test("Approve and reject actions are visible on every intervention card", () => {
  const { getByRole } = render(<InterventionCard intervention={mockIntervention} />);
  expect(getByRole("button", { name: /approve/i })).toBeInTheDocument();
  expect(getByRole("button", { name: /reject/i })).toBeInTheDocument();
});
```

### 7.1a Frontend Fail-Closed Contract (Sprint 2, 2026-04-27)

When the backend is unavailable, the frontend must show an explicit degraded-state
message. Mock data must NEVER be silently substituted in production mode.

```tsx
// Governance page: backend unreachable → explicit degraded state
test("Governance page shows degraded state when backend is unreachable", async () => {
  // Mock backend failure
  server.use(
    rest.get("/api/governance/review-queue", (req, res, ctx) =>
      res(ctx.status(503))
    )
  );
  const { getByText } = render(<GovernancePage />);
  await waitFor(() => {
    expect(getByText(/backend unavailable/i)).toBeInTheDocument();
  });
  // Mock data must NOT appear unless explicitly opted-in
  expect(queryByText(/payment_latency → checkout_conversion/i)).not.toBeInTheDocument();
});

// Incidents page: backend error on submit → explicit error, no fake incident
test("Incident submission failure shows explicit error, no fake record", async () => {
  server.use(
    rest.post("/api/v1/session/incident", (req, res, ctx) =>
      res(ctx.status(503))
    )
  );
  const { getByText, queryByText } = render(<IncidentsPage />);
  fireEvent.click(getByText("Submit Incident"));
  await waitFor(() => {
    expect(getByText(/incident submission failed/i)).toBeInTheDocument();
  });
  // No fake incident should be created in the list
  expect(queryByText(/INC-/i)).not.toBeInTheDocument();
});
```
```

### 7.2 E2E Tests (Playwright)

```typescript
// E2E: SRE incident triage flow
test("SRE can receive causal hypothesis within 60 seconds of incident", async ({ page }) => {
  await page.goto("/incidents/new");
  await page.fill("[data-testid=incident-title]", "Service latency spike");
  await page.click("[data-testid=submit-incident]");
  
  // ThinkingIndicator must appear immediately
  await expect(page.getByTestId("thinking-indicator")).toBeVisible({ timeout: 200 });
  
  // Causal hypothesis list must appear within 60 seconds
  await expect(page.getByTestId("causal-hypothesis-list")).toBeVisible({ timeout: 60000 });
  
  // At least one hypothesis with confidence score
  const hypotheses = page.getByTestId("hypothesis-item");
  await expect(hypotheses).toHaveCountGreaterThan(0);
  
  const firstHypothesis = hypotheses.first();
  await expect(firstHypothesis.getByTestId("confidence-score")).toBeVisible();
});

// E2E: Governance Officer approval flow
test("Governance Officer can approve a graph edge with full evidence chain", async ({ page }) => {
  await page.goto("/governance/review-queue");
  await page.click("[data-testid=first-review-item]");
  
  // Evidence chain visible
  await expect(page.getByTestId("evidence-citations")).toBeVisible();
  await expect(page.getByTestId("sensitivity-result")).toBeVisible();
  
  // Approve action
  await page.click("[data-testid=approve-button]");
  await page.fill("[data-testid=approval-reason]", "Evidence supports causal claim");
  await page.click("[data-testid=confirm-approval]");
  
  // Audit entry created
  await expect(page.getByTestId("approval-success")).toBeVisible();
});
```

---

## 8. CI/CD Pipeline

### 8.1 Pipeline Architecture

```yaml
# .github/workflows/ci.yml (outline)

on: [push, pull_request]

jobs:
  # ── Fast lane (< 5 min, runs on every commit) ──────────────────────────
  lint-and-type-check:
    runs-on: ubuntu-latest
    steps:
      - ruff check --select ALL         # Python: linting + isort
      - mypy --strict                   # Python: strict type checking
      - cargo clippy -- -D warnings     # Rust: lint as errors
      - npx tsc --noEmit                # TypeScript: type check

  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - pytest tests/unit -x --tb=short  # Fast fail on first error
      - cargo test                        # Rust unit tests
      - vitest run --reporter=verbose     # Frontend unit tests

  security-scan:
    runs-on: ubuntu-latest
    steps:
      - pip-audit                         # Python dependency audit
      - cargo audit                       # Rust dependency audit
      - bandit -r src/                    # Python SAST

  # ── Integration lane (< 15 min, runs on PR) ────────────────────────────
  integration-tests:
    runs-on: ubuntu-latest
    services:
      neo4j: neo4j:5-community
      postgres: postgres:16
      kafka: confluentinc/cp-kafka:7
    steps:
      - pytest tests/integration --timeout=120
      - pytest tests/causal_correctness  # DAG validity, sensitivity, identification

  guardian-integrity-tests:
    runs-on: ubuntu-latest
    steps:
      - pytest tests/guardian -v         # All Guardian bypass tests: must be 100% pass

  performance-regression:
    runs-on: ubuntu-latest
    steps:
      - pytest tests/performance -m performance --benchmark-json=results.json
      - python scripts/compare_benchmarks.py results.json baseline.json  # Fail if regression

  # ── Container lane (< 20 min, runs on PR to main) ──────────────────────
  container-build:
    runs-on: ubuntu-latest
    steps:
      - docker build --target production
      - trivy image --exit-code 1 --severity HIGH,CRITICAL <image>  # Block on HIGH/CRITICAL

  # ── Full E2E (< 30 min, runs on merge to main) ─────────────────────────
  e2e-tests:
    runs-on: ubuntu-latest
    steps:
      - docker-compose up -d            # Full stack
      - playwright test                 # E2E test suite
      - k6 run tests/performance/load.js  # Load test

  # ── Deploy (runs on tag push) ──────────────────────────────────────────
  deploy-staging:
    needs: [e2e-tests, container-build]
    steps:
      - gcloud run deploy --platform managed --region europe-west1
      - firebase deploy --only hosting
      - python scripts/smoke_test.py staging
```

### 8.2 Quality Gates (Block Merge)

| Gate | Condition | Rationale |
|---|---|---|
| All unit tests pass | 100% | Non-negotiable |
| All Guardian tests pass | 100% | Safety-critical |
| All causal correctness tests pass | 100% | Model integrity |
| Lint: zero errors | 0 errors | Code quality |
| Type check: zero errors | 0 errors | Type safety |
| Security scan: no HIGH/CRITICAL | 0 | Security |
| Coverage: backend | ≥ 80% lines | Regression prevention |
| Coverage: Guardian paths | ≥ 100% branch | Safety-critical |
| Performance: no regression | < 10% degradation | Reliability |

### 8.3 Deployment Strategy

**Environments:**

| Environment | Trigger | Purpose |
|---|---|---|
| `local` | Developer machine | Development, SQLite |
| `preview` | PR opened | Feature review, real integrations |
| `staging` | Merge to `main` | Pre-production validation |
| `production` | Git tag `v*.*.*` | Live |

**Deployment method:** Blue-green on Cloud Run. New revision receives 0% traffic initially. Automated smoke test runs. If smoke test passes, traffic shifts to 100% new revision. Previous revision retained for 24h instant rollback.

**Rollback:** `gcloud run services update-traffic --to-revisions=PREVIOUS=100` — one command, under 30 seconds.

---

## 9. Code Quality Standards

### 9.1 Python

| Tool | Configuration | Enforced |
|---|---|---|
| Ruff | `select = ["ALL"]`, targeted ignores | CI lint gate |
| MyPy | `--strict` | CI type gate |
| pytest | `asyncio_mode = "auto"`, `--cov` | CI test gate |
| DeepEval | LLM output quality metrics | Per-agent quality gate |

```toml
# pyproject.toml
[tool.ruff]
select = ["E", "F", "I", "N", "W", "UP", "B", "C4", "SIM", "TCH", "ANN"]
line-length = 100

[tool.mypy]
strict = true
ignore_missing_imports = false

[tool.pytest.ini_options]
asyncio_mode = "auto"
addopts = "--cov=src --cov-report=term-missing --cov-fail-under=80"
```

### 9.2 Rust

| Tool | Configuration | Enforced |
|---|---|---|
| `cargo clippy` | `-- -D warnings` (warnings are errors) | CI lint gate |
| `cargo fmt` | Default rustfmt config | CI format gate |
| `cargo test` | All tests including doc tests | CI test gate |
| `cargo audit` | All advisories, deny severity ≥ medium | CI security gate |
| `cargo deny` | License policy, ban list | CI security gate |

```toml
# .cargo/config.toml
[build]
rustflags = ["-D", "warnings"]
```

**Standard for `assert!` macros:** No `assert!` in library code (only in tests). Use `Result<T, E>` and propagate errors. (The Rust standards section is retained as a reference for any future native components — this repo is currently all-Python.)

### 9.3 TypeScript / React

| Tool | Configuration | Enforced |
|---|---|---|
| TypeScript | `strict: true`, `noUncheckedIndexedAccess: true` | CI type gate |
| ESLint | `@typescript-eslint/recommended-type-checked` | CI lint gate |
| Vitest | Coverage threshold 80% | CI test gate |
| Playwright | E2E suite | CI e2e gate |

---

## 10. Formal Methods and Specification

Following the formal methods tradition in all three repos:

- **TLA+** specifications for critical state machine behaviors: Guardian policy enforcement, circuit breaker state transitions, audit trail append operations
- **Z3 Solver** for verifying constraint satisfaction in GovernanceAgent policies
- TLA+ specs live in `specs/` directory, verified in CI via TLC model checker

```tla
(* EXAMPLE: Guardian cannot transition from APPROVED to anything else *)
TypeInvariant ==
  /\ \A verdict \in verdicts:
       verdict.status \in {"PENDING", "APPROVED", "REJECTED", "REQUIRES_ESCALATION"}

GuardianSafety ==
  (* Once APPROVED, a verdict's status is immutable *)
  \A verdict \in verdicts:
    verdict.status = "APPROVED" => 
      verdict.status' = "APPROVED"
```

---

## 11. Observability and Monitoring

All services emit structured logs and traces. Monitoring is not an afterthought.

### 11.1 Instrumentation Standards

```python
# Structured logging: every agent action is a structured log event
import structlog

log = structlog.get_logger()

log.info(
    "agent.skill_called",
    agent_id=agent_id,
    skill="guardian_check",
    cynefin_domain=state.cynefin_domain,
    confidence=state.overall_confidence,
    guardian_verdict=verdict.status,
    duration_ms=elapsed_ms,
    session_id=state.session_id,
)
```

### 11.2 Key Metrics to Monitor

| Metric | Alert Threshold | What it indicates |
|---|---|---|
| `guardian.rejection_rate` | > 20% over 5m | Policy misconfiguration or adversarial input |
| `agent.hallucination_risk_avg` | > 0.5 over 10m | LLM reliability degradation |
| `semantic_layer.write_latency_p99` | > 100ms | Database performance issue |
| `causal_drift.detected_count` | > 3 per hour | Data distribution shift |
| `circuit_breaker.open_count` | > 0 | Agent loop or escalation spike |
| `audit_log.insert_failure_rate` | > 0% | Critical: audit trail at risk |
