# UIX: Design System, Personas & User Stories

> **Scope:** User experience architecture, design principles, user personas, user stories, and component strategy for the Unified Enterprise Causal Platform. Every new feature must be anchored to an identified persona need before design begins.
>
> **Read against `frontend/src` 2026-09-26.** The personas and user stories (§3) are needs, not
> features, and many are unbuilt. The principles (§1) and the component inventory (§4.2) now
> say which parts exist. Onboarding walkthroughs (§5) are roadmap.

---

## 1. UIX Philosophy

The platform serves people who are trying to make better decisions under uncertainty in high-stakes operational contexts. The interface is not a data visualization tool or a dashboard. It is a **causal reasoning partner**—a surface where humans and AI co-construct understanding, validate assumptions, and approve actions.

### Core Design Principles

| Principle | Implementation |
|---|---|
| **Minimalist shell, progressive complexity** | The default view shows only what is needed for the current task. Complexity is revealed on demand or as the user's skill level increases—never imposed. |
| **AI-native interaction** | AI suggestions, confidence indicators, and causal evidence are first-class UI elements, not tooltips or footnotes. |
| **Causal transparency** | Every recommendation surface shows which assumptions it rests on and what the confidence bounds are. Users can drill into the reasoning chain. |
| **Human agency is always visible** | Approve/reject actions are always one click away. The AI proposes; the human decides. This is never obscured. |
| **Mobile-first responsive** | All surfaces are usable on mobile. Operational personas (SRE, Incident Commander) are on-call and need mobile-grade UX. *Partly checked: `/incidents` and `/workbench` stack their columns below the `md` breakpoint and the page headers wrap, which the page tests pin by class (jsdom does no layout); on 2026-09-26 every page measured a 390px scroll width at a 390px viewport in Chromium. No automated test measures layout.* |
| **Onboarding via walkthrough** | New users experience a structured guided walkthrough for their persona, not a blank canvas. *Roadmap: no walkthrough is built (§5).* |
| **Voice input for operational contexts** | Incident-response and operational surfaces support voice input (STT) for hands-free use. Not TTS by default. *Roadmap: no voice input is built.* |
| **Immediate thinking indicator** | Any AI processing shows a prominent, animated "thinking" indicator within 150ms of user submission. Never leave users staring at a static screen. *Built: `ThinkingIndicator`, with the 150ms contract pinned by its test.* |

---

## 2. Adaptive Persona Views

Each persona sees a **filtered, task-optimized lens** on the same underlying platform data. The platform does not build separate UIs per persona—it applies a view configuration over a shared component library.

Today there is a page per persona view, `/incidents`, `/analyst` (titled "Problem triage"),
`/executive`, `/governance` and `/engineer`, plus `/analyze`, `/workbench`, `/decisions` and
`/registry`. One navigation bar (`SiteNav`, in the root layout) sits above every page, the 404 page
(`frontend/src/app/not-found.tsx`, with a link home) included: the brand links home, then the
analysis pages (Analyze, Decision ledger, Assumption registry, DAG Workbench), then the operations
pages under "Operate", each labelled as the page titles itself, the current one marked. Home (`/`)
is a start page: start an analysis, open an example, the five most recent decisions, and a card
per page (it used to be a role picker that forwarded every later visit to the last role's page,
so home was unreachable). On a persona page `PersonaSelector` switches persona and goes to that
persona's page. How much of each view's surface below is built varies (see §4.2).

- **`/analyze`** asks a causal question of the user's data in four steps: data (a CSV file or
  pasted text, parsed in the browser; or an example from `GET /api/v1/analysis/examples`, each
  simulated from a structural model with a stated true effect), the question (treatment, outcome,
  randomized or observational, ATE or ATT), the graph (picked from the columns as confounders and
  other causes of the outcome, or written in the kernel's syntax), and the answer. The graph is
  checked as it is edited (`POST /api/v1/analysis/check`): identified or not, the adjustment set,
  what would make it identified, nodes with no column (treated as unmeasured, as the analysis
  treats them) and unreviewed edges. The answer (`POST /api/v1/analysis/run`, which records
  nothing) shows the estimate and interval against zero, the worthwhile threshold and, for an
  example, the true effect; the identification, estimator cross-check, falsification and design
  checks, E-value and balance; or, for a refusal, the reason and what would make the question
  answerable. "Decide and record" sends the same inputs to `POST /api/v1/decisions`, which re-runs
  the analysis and seals the verdict. Jargon carries its definition on hover or focus (`Term`).
- **`/workbench` (DAG Workbench)** shows the fragments and their edges, and proposes a new fragment
  from existing edges through `POST /api/semantic/drafts`. The drafter must classify the situation
  (a declared Cynefin domain, DISORDER not offered, or a reasoning session's id) and name the
  source data: a reference and its SHA-256 digest, typed or computed in the browser from a chosen
  file (the file is not uploaded). Approval requires source data (septa-15), so submit waits for
  it. The Guardian's
  verdict is shown as the API returns it: on success the audit entry and whether
  `GET /api/governance/review-queue` lists the draft; on the 403 the verdict, reason and rules
  cited. The route judges the draft in the declared or session domain
  (`backend/tests/integration/test_draft_submission_api.py`), so a classified draft reaches the
  review queue. Authoring new edges or entities is not built, and the register entry
  `dag-workbench` stays open (`partial`).
- **`/decisions` (decision ledger)** lists `GET /api/v1/decisions` (filter by action, limit),
  shows a record's verdict, checks, evidence and chain digests, verifies the chain
  (`/verify`), takes a follow-up (`POST .../{entry_digest}/followup`, once per decision) and shows
  the calibration report (`/calibration`). Numbers are the API's, shown in the kernel's own
  format (four significant digits, as `causal_core.claims.format_number` writes them) with the
  exact value as the tooltip; an absent value is shown as "—". Each calibration rate is the
  kernel's `RateEstimate`: its counts ("9 of 12") always, the rate and its Clopper-Pearson
  interval only when the report states one (from `min_followups`, ten, up), and otherwise "—"
  with the report's reason. The rationale's
  claim markers (`⟦claim:effect⟧`, the interval's ends, its level, `n`) are filled from the
  record's own evidence, the effect read back from the stored benefit by the requested direction;
  when the record lacks a figure a marker names, the rationale is labelled as a template.
- **`/registry` (assumption registry)** lists `GET /api/v1/models` and shows a model's versions,
  the diff between two, the impact of a version on recorded decisions, and the history check.
  It is read-only: registering, revising and reviewing a model are API-only.

| Persona View | Primary Surface | Secondary Surfaces |
|---|---|---|
| `OPERATOR` | Incident feed + intervention proposals | Cynefin classification, root-cause drill-down |
| `ANALYST` | Analyze (estimate an effect, record a decision) | Decision ledger, assumption registry, DAG Workbench |
| `EXECUTIVE` | Decision summary + outcome tracking | High-level intervention comparison |
| `GOVERNANCE` | Assumption review queue + audit trail | Sensitivity results, policy verdicts |
| `ENGINEER` | Graph fragment library + connector status | Drift alerts, model health |

---

## 3. User Personas

---

### Persona 1: Platform Engineer (Mihail)

**Role:** Senior platform/infrastructure engineer responsible for deploying and maintaining internal services and causal models on the platform.

**Goals:**
- Keep the causal platform healthy and performant
- Integrate new data sources into the semantic layer
- Diagnose issues with connector pipelines or agent behavior
- Ensure causal graph assets stay current with system changes

**Context:** Primarily desktop, on-call mobile. Deep technical background. Does not want AI to explain basics—wants direct, actionable signals. Comfortable with raw data and logs.

**Frustrations:**
- Too many abstraction layers between him and the underlying data
- Alerts without diagnostic context
- Graph changes that break downstream simulations without warning

**User Stories:**

> **US-01** As a Platform Engineer, I want to see connector health status and last successful sync time on a single dashboard, so I can identify ingestion failures immediately without querying logs manually.

> **US-02** As a Platform Engineer, I want to receive a structured alert when a new graph fragment deprecation would affect a running simulation, so I can coordinate the change without breaking downstream users.

> **US-03** As a Platform Engineer, I want to view the full audit trail for a specific causal edge—from proposal to Guardian verdict to approval—so I can investigate anomalous changes to the graph.

> **US-04** As a Platform Engineer, I want to run a connector health check and view its semantic object mapping output in a raw JSON view, so I can debug mapping issues without waiting for a deployment cycle.

> **US-05** As a Platform Engineer, I want to configure drift alert thresholds per domain and per entity type, so I can tune sensitivity without modifying platform code.

---

### Persona 2: Site Reliability Engineer (Aino)

**Role:** SRE responsible for service reliability, SLO management, and incident response for a portfolio of microservices.

**Goals:**
- Identify root causes of incidents faster than current mean-time-to-identify
- Understand which changes caused which performance degradations
- Avoid unnecessary rollbacks by understanding actual causal impact
- Learn from past incidents to improve future response

**Context:** Heavy mobile use during on-call. Works under time pressure. Needs clear, ranked options with confidence levels—not probabilistic jargon. Wants to understand the "why" behind a recommendation.

**Frustrations:**
- Correlation-based alerts that fire without causal context
- Post-mortems that identify symptoms, not causes
- Having to manually cross-reference deployment logs with metrics

**User Stories:**

> **US-06** As an SRE, I want to see a ranked list of likely causal drivers within 60 seconds of acknowledging an incident, so I can start investigation with a hypothesis rather than blank data.

> **US-07** As an SRE, I want to view the causal chain from a recent deployment to an observed SLO breach with confidence scores, so I can explain the incident to stakeholders with evidence rather than guesses.

> **US-08** As an SRE, I want to simulate the likely impact of rolling back a deployment vs. applying a config change, so I can choose the least-disruptive mitigation with evidence.

> **US-09** As an SRE, I want to see similar past incidents from the experience buffer alongside current incident data, so I can apply proven resolution patterns without starting from scratch.

> **US-10** As an SRE, I want to submit voice input during an incident to query the platform, so I can interact with the system while performing simultaneous operational actions.

---

### Persona 3: Incident Commander (Laila)

**Role:** Senior engineer or team lead who takes command during high-severity incidents and is responsible for coordinating response and approving remediation actions.

**Goals:**
- Quickly establish a shared mental model of the incident across the response team
- Approve the most effective remediation action with confidence
- Maintain an audit trail of decisions made during the incident
- Close the feedback loop after the incident resolves

**Context:** Desktop during active incidents, mobile for monitoring. Needs a clear, non-technical narrative they can share with stakeholders alongside a technical causal explanation they can trust.

**Frustrations:**
- Too many conflicting opinions during incidents without shared evidence
- No way to formally approve an action with rationale attached
- Retrospective post-mortems that are disconnected from real-time causal decisions

**User Stories:**

> **US-11** As an Incident Commander, I want to see a single "incident brief" view showing Cynefin domain classification, top causal hypotheses, and proposed interventions in one screen, so I can orient the team in under 2 minutes.

> **US-12** As an Incident Commander, I want to formally approve a proposed intervention with a reason attached, so the decision is recorded in the audit trail and can be referenced in the post-mortem.

> **US-13** As an Incident Commander, I want to generate a stakeholder-appropriate narrative explanation of the incident cause and response in one click, so I can communicate status without translating technical details manually.

> **US-14** As an Incident Commander, I want to be immediately routed to a human escalation path when the Cynefin domain classification is CHAOTIC, so I can take command directly without waiting for AI recommendations that may not be reliable.

> **US-15** As an Incident Commander, I want to close the feedback loop by marking the actual resolution cause after the incident ends, so the causal model improves from real outcome data.

---

### Persona 4: Engineering Manager (Tomás)

**Role:** Engineering manager responsible for team performance, incident reduction, and the quality of engineering decisions across their organization.

**Goals:**
- Track whether causal interventions are actually improving outcomes over time
- Identify systemic causal patterns causing repeated incidents
- Make data-driven investment decisions (which services to harden, which tech debt to prioritize)
- Report on incident resolution quality to leadership

**Context:** Desktop, weekly cadence review rather than real-time response. Needs trend views, not individual alert details. Low tolerance for technical jargon.

**User Stories:**

> **US-16** As an Engineering Manager, I want to see a weekly trend view of mean-time-to-root-cause and intervention accuracy across my team's services, so I can track whether the platform is delivering measurable value.

> **US-17** As an Engineering Manager, I want to identify the top 5 recurring causal patterns causing incidents in my service domain over the past 90 days, so I can prioritize structural fixes over reactive patches.

> **US-18** As an Engineering Manager, I want to view a readable summary of the causal assumptions underlying our most-used graph fragment, so I can assess model quality without reading raw technical specs.

---

### Persona 5: Causal Analyst / Data Scientist (Ryo)

**Role:** Data scientist or causal inference specialist responsible for building, validating, and maintaining causal graph assets on the platform.

**Goals:**
- Build high-quality causal graphs from domain expert knowledge and data evidence
- Run and interpret sensitivity analyses on approved assumptions
- Detect and respond to causal drift over time
- Collaborate with domain experts to elicit and validate causal knowledge

**Context:** Desktop, deep focus work. Comfortable with statistical output. Wants raw values alongside narrative, not just summaries. Needs version control and reproducibility guarantees.

**Frustrations:**
- No structured way to record the rationale behind each causal edge
- Assumptions buried in notebooks with no governance lifecycle
- Cannot run sensitivity analyses without writing code

**User Stories:**

> **US-19** As a Causal Analyst, I want to submit a candidate causal edge with supporting evidence links and receive an automated statistical validity check before it enters review, so I can catch obvious errors before human review cycles.

> **US-20** As a Causal Analyst, I want to see a diff view of proposed changes to a graph fragment compared to the current approved version, so I can review changes precisely without reading raw JSON.

> **US-21** As a Causal Analyst, I want to run a one-click Rosenbaum sensitivity analysis on any approved edge and view the E-value result alongside the edge detail, so I can assess robustness without scripting.

> **US-22** As a Causal Analyst, I want to view the complete version history of a graph fragment including who approved each version and what evidence was cited, so I can trace why the causal structure evolved over time.

> **US-23** As a Causal Analyst, I want to conduct a structured assumption elicitation session with a domain expert via a guided interview UI, so I can capture domain knowledge in a structured format without free-text notes.

---

### Persona 6: Business Analyst (Priya)

**Role:** Business analyst using the Decision Simulation Cockpit to evaluate proposed business actions before execution, primarily in marketing or customer success contexts.

**Goals:**
- Compare the causal impact of different business interventions before committing budget
- Understand the uncertainty and confidence bounds on recommendations
- Get explanations she can share with business stakeholders

**Context:** Desktop, moderate technical background. Does not write code. Needs clarity over precision—she needs to understand the direction and confidence of an effect, not the exact statistical machinery behind it.

**User Stories:**

> **US-24** As a Business Analyst, I want to select two proposed interventions and see a side-by-side comparison of their estimated effects, costs, and confidence intervals, so I can recommend the better option to my manager with evidence.

> **US-25** As a Business Analyst, I want every simulation output to show me which assumptions it rests on in plain language, so I can understand what would have to be true for the recommendation to hold.

> **US-26** As a Business Analyst, I want to export a simulation result as a PDF summary with narrative explanation and supporting assumptions, so I can present the analysis in a business review without technical preparation.

---

### Persona 7: Compliance / Governance Officer (Olu)

**Role:** Responsible for ensuring the platform's AI-assisted decisions meet regulatory requirements (EU AI Act, GDPR) and internal governance policies.

**Goals:**
- Review and approve/reject proposed causal graph changes
- Audit all approved interventions and their outcomes
- Ensure assumptions are documented and reviewed on schedule
- Generate compliance reports for regulatory purposes

**Context:** Desktop, workflow-driven. Needs a structured review queue, not a dashboard of metrics. Values completeness and traceability above speed.

**User Stories:**

> **US-27** As a Governance Officer, I want a structured review queue of all draft graph changes awaiting approval, ordered by risk tier, so I can prioritize my review time on high-stakes changes.

> **US-28** As a Governance Officer, I want to view the full evidence chain for a proposed causal edge—statistical tests, document citations, agent reasoning steps—before approving or rejecting it.

> **US-29** As a Governance Officer, I want to receive an automated alert when a graph asset that supports a high-stakes intervention has not had its assumptions reviewed within 90 days, so I can maintain governance schedules.

> **US-30** As a Governance Officer, I want to generate an audit report for a specific time period showing all approved interventions, their causal basis, and their actual outcomes, formatted for regulatory submission.

---

## 4. Component Architecture

### 4.1 Design System Foundation

**Benchmark standard:** Radix UI primitives + shadcn/ui-style components + Tailwind CSS
- Radix UI: accessibility-first, unstyled primitives (Dialog, Dropdown, Toast, Tooltip). These are dependencies of `frontend/`
- shadcn/ui pattern: composable components built on Radix with `class-variance-authority`, `clsx` and `tailwind-merge`. The utilities are dependencies; the components are written in this repository
- Tailwind: utility-first styling with consistent design tokens

### 4.2 Core Component Groups

Components under `frontend/src/components/` are marked **built**. The rest are **not built**.

| Group | Built | Not built | Used by |
|---|---|---|---|
| **Causal Graph** | `DAGCanvas`, `EdgeProposal`, `NodeDetail`, `GraphDiff`, `FragmentCard`, `FragmentDraftComposer` | -- | Workbench (`DAGCanvas`, `FragmentDraftComposer`), Analyst and Platform Engineer (`FragmentCard`) |
| **Decisions** | `VerdictBadge`, `DecisionDetail` (with the follow-up form), `CalibrationReportPanel` | -- | Executive, Governance Officer, Analyst |
| **Assumption registry** | `ModelVersionView` (with `EdgeTable`), `ModelDiffView`, `ImpactReportView` | -- | Analyst, Governance Officer |
| **Incident Response** | `IncidentBrief`, `CynefinBadge` (in `shared/`), `CausalHypothesisList`, `InterventionCard`, `EscalationBanner` | -- | SRE, Incident Commander |
| **Simulation** | `InterventionComparison` (with `EffectEstimateBar` inside it), `AssumptionDisclosure` | `ScenarioRange` | Business Analyst, SRE |
| **Governance** | `ReviewQueue`, `ApprovalPanel` | `AuditTrailEntry`, `SensitivityResult`, `AssumptionCard` | Governance Officer, Analyst |
| **Evidence** | -- | `EvidenceCitation`, `DocumentSnippet`, `StatisticalTestResult`, `ConfidenceMeter` | Analyst, Governance Officer |
| **Shared** | `EpistemicConfidenceBadge`, `ThinkingIndicator`, `PersonaSelector`, `AuditLogPanel`, `GuardianVerdictBadge`, `SiteNav` | -- | All |

The review queue shows each item's risk tier and submitter from the backend, most at risk
first (US-27). The tier is derived from stored fields by `backend/src/semantic_layer/review_risk.py`:
an edge is Critical below stated confidence 0.5, High below 0.8 or without a sensitivity result
the platform recorded for it that says it is robust (a drafter's own result does not count), else
Medium; a fragment is High while it names an unapproved edge,
else Medium; an entity is Low. The tier badge's tooltip gives the reasons, and an item whose
submitter was never recorded (edges drafted before migration 012) says "not recorded"; a row that
arrives without a tier shows a dash and sorts last. The
Evidence group has no backend to read from yet (register `agent-skills-are-stubs`).
A review item's domain is its business domain (`devops`, `marketing`), shown as a plain label in
the queue and the panel header; a `CynefinBadge` appears only when an item carries a Cynefin
classification, which the review-queue endpoint does not return. Until 2026-09-26 the page fed the
business domain to `CynefinBadge`, which crashed on it; the badge now renders any value that is
not a Cynefin domain as a neutral "Unclassified", never as a real domain.
The type is the backend's `object_type`, labelled; the submitted time is `created_at`, or a dash
when there is none. The queue sends an edge's relation type as its name and not its endpoints, so
an edge row reads "CAUSES edge" and says its endpoints are not in the review queue. The audit
panel's badge shows the log's `total` beside the rows fetched ("latest 50 of 267"), not the page
size. The page enters mock mode (with `NEXT_PUBLIC_ENABLE_MOCK_FALLBACK=true`)
only when the review queue itself cannot be reached; an audit-log failure alone is reported in
the audit panel and leaves decisions enabled. Across the governance, incidents and analyst pages
a 401 or 403 (other than a Guardian block) reads "not authorised", not "backend unavailable" or
an empty list.

The incidents page speaks the backend's severity vocabulary, P0 to P3, end to end (rows the seed
script wrote as SEV1 to SEV4 are shown as P0 to P3). No endpoint returns an effect estimate for an
incident's interventions, so `InterventionCard` says "No effect estimate" rather than drawing an
interval. The Guardian's verdict and a person's recorded decision are shown side by side as two
labelled facts ("Guardian verdict", "Human decision"); the incidents API returns the decision as
`humanDecision` and leaves `guardianVerdict` as the Guardian issued it. Once a decision is
recorded the card closes approve and reject and says what was decided. The number on a card is
the recommendation's `confidence`, which the triage agent sets to its heuristic triage score, so
it is labelled "Triage score", not drawn as a confidence meter. The triage agent's `ESCALATE: ...`
directive is shown as an escalation notice, with no approve or reject: it advises handing the
incident to a person and is not an intervention. A new incident's id is `INC-` and eight random
hex digits.

A `FragmentCard`'s footer badge follows the fragment's `approvalStatus`: only an APPROVED fragment
shows the Guardian's verdict; a draft reads "Draft · awaiting review" and a deprecated one
"Deprecated", whatever verdict reference it carries (pinned by
`frontend/src/components/graph/__tests__/FragmentCard.test.tsx`).

The analyst page draws no causal graph: the session API returns how many edges a run proposed
(`proposed_edge_count`) but not the edges, so the page shows that count and the run's
recommendations as sent, numbered by their place in the list (the backend's ranks can repeat); a
proposed edge's endpoints arrive as ids, and are shown as short labelled ids. The header's
"Classification" is the response's `cynefin_domain`, the one classification on the page: when the
backend's explanation or recommendations name another Cynefin domain, the page says so (the tour
of 2026-09-26 met a COMPLICATED run explained as CHAOTIC, from before the backend's escalation text
named its actual reason), and a new run clears the previous run's result. `EdgeProposal`, `NodeDetail` and `GraphDiff` are built, and no page
renders them since the analyst page stopped drawing a graph it had made up.
Approving or rejecting in `ApprovalPanel` asks the reviewer to classify the situation
(Cynefin domain; required, nothing pre-selected, DISORDER not offered): Guardian reads it,
and without one rule PR-006 refuses the request. A rejection needs an APPROVED verdict for
`GOVERNANCE_REJECT` (`septa-53`), so it asks for the domain exactly as an approval does, and
the page does not call `/reject` unless the verdict is APPROVED.
The tests of the three analysis views are `frontend/src/app/{workbench,decisions,registry}/__tests__/page.test.tsx`;
the ledger and registry tests use responses the kernel produced (`frontend/src/test-fixtures/kernel.ts`).
`AssumptionDisclosure` is built but no page renders it; the analyst page imported it
without using it, and the import was removed.

### 4.3 Thinking Indicator (Critical UX Contract)

Any agent action longer than 150ms must show the `ThinkingIndicator` component immediately after user submission. This is a non-negotiable UX contract, pinned by `frontend/src/components/shared/__tests__/ThinkingIndicator.test.tsx`.

```tsx
// ThinkingIndicator: appears within 150ms of any agent action
<ThinkingIndicator 
  stage={currentStage}          // "Classifying domain..." | "Analyzing causal paths..." | "Checking policy..."
  elapsed={elapsedMs}
  cynefinDomain={domain}        // Shows domain badge as soon as classified
/>
```

### 4.4 Progressive Disclosure Pattern

> **Status:** design pattern. It is not applied consistently across pages, and no test checks it.

```
Level 0 (default):    Recommendation + confidence level + approve/reject actions
Level 1 (expand):     Causal chain summary + top 3 assumptions
Level 2 (drill-down): Full DAG fragment + evidence citations + sensitivity result
Level 3 (expert):     Raw EpistemicState, LLM reasoning steps, Guardian verdict detail
```

Users progress through levels by interaction, not by role. Role determines which levels are accessible, not which level is shown by default.

---

## 5. Onboarding Walkthrough

> **Status:** roadmap. No walkthrough is built.

Each persona has a dedicated first-session walkthrough. Walkthroughs are:
- Contextual (triggered by first login, not by a banner)
- Interactive (user performs real actions with guided prompts, not videos)
- Skippable at any step
- Resumable if abandoned

**Analyst walkthrough flow:**
1. View an existing approved graph fragment (explore)
2. See how an edge was proposed and approved (observe governance)
3. Propose a candidate edge on sample data (guided practice)
4. Run sensitivity analysis on the proposed edge (practice with real tool)
5. Submit for review (complete the loop)

**SRE walkthrough flow:**
1. View a sample incident brief (orient)
2. Read the causal hypothesis list and expand one causal chain (understand)
3. Compare two intervention proposals on sample data (practice)
4. Approve one intervention in a sandbox (complete the loop)

---

## 6. Accessibility Requirements

> **Status:** requirements. No audit has been done, and no automated accessibility test runs.
> The Radix primitives provide ARIA patterns where they are used. The only automated check is
> lint: the `jsx-a11y` rules in Next's `core-web-vitals` set (valid ARIA attributes for each
> role, `alt` text), run over `frontend/src` by the gate's `frontend-eslint` step and held at
> zero findings. Selection in lists (connector cards, incidents, the review queue, DAG nodes,
> fragment cards, decisions, registered models and versions) is marked with `aria-current`;
> `aria-selected` is kept for tabs. A backend refusal or error is announced with `role="alert"`,
> a backend-confirmed result with `role="status"`.

- WCAG 2.1 AA compliance on all screens
- Keyboard navigation for all interactive elements
- Screen reader compatibility via Radix ARIA patterns
- Color contrast ratio ≥ 4.5:1 for all text
- No information conveyed by color alone (confidence levels use icon + color)
- Focus management on modal and panel open/close

---

## 7. Feature Gating Policy

New UI features are only added when anchored to:
1. A named user story in this document (or a new story added here first)
2. Validation via user testing with the relevant persona (minimum 3 sessions before shipping)
3. Complexity justified: does adding this feature make the default view more complex? If yes, it goes behind progressive disclosure.

No feature is added because it is technically possible. Features exist to serve identified persona needs.
