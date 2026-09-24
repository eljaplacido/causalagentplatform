# Missing Standards & Protocols for Agentic Data Engineering, Architectures, Semantics & Metadata

## Executive Summary

The wave of agentic AI systems operating over enterprise data pipelines is exposing a deep and widening mismatch between what standards exist and what agentic workflows actually need. The core failure mode is well-established: agentic AI does not generate wrong outputs due to weak models — it generates confident outputs based on stale context, missing lineage, contradictory semantics, and ungoverned runtime behavior[^1][^2]. The protocols and standards gap is not a future risk; it is an active cause of production failures in 2025-2026. This report maps the most critical missing standards layer by layer, across protocol interoperability, semantic infrastructure, metadata lifecycle, governance, and industry-specific gaps.

***

## 1. Agent Interoperability & Communication Protocols

### 1.1 The Current Protocol Landscape

Four emerging protocols address different facets of agent interaction in agentic ecosystems, none of which covers the full stack[^3][^4]:

| Protocol | Scope | What It Covers | Key Gap |
|----------|-------|----------------|---------|
| **MCP** (Model Context Protocol) | Tool/data access | JSON-RPC interface for tools and data source discovery | No pricing, provenance, or granular permissions[^5] |
| **A2A** (Agent-to-Agent, Google) | Peer-to-peer task delegation | Capability-based Agent Cards, enterprise collaboration | No federated discovery, limited semantic negotiation[^3] |
| **ACP** (Agent Communication Protocol, IBM BeeAI → Linux Foundation) | Structured messaging | RESTful HTTP, MIME-typed messages, session management | Merging into A2A; transition path unclear[^6] |
| **ANP** (Agent Network Protocol) | Open-network discovery | W3C DIDs, JSON-LD graphs, decentralized marketplaces | Immature, not production-ready[^4] |

### 1.2 What Is Still Missing

**Federated orchestration and capability negotiation** is the primary gap across all four protocols[^7]. MCP provides a way for agents to call tools; A2A provides a way to delegate tasks between agents; but neither provides a robust framework for agents to autonomously form coalitions, negotiate service-level agreements, or verify that another agent is actually capable of performing a task before delegating[^7][^5]. A proposed Agent Communication Protocol (ACP) framed this as "the TCP/IP of the Agentic Web," requiring federated registries with verifiable credentials, semantic intent mapping, and automated SLAs — none of which exists as a ratified open standard[^7].

**Missing: A standard for agent capability negotiation and coalition formation** — a protocol layer that determines semantic compatibility, verifies capability claims cryptographically, and negotiates SLAs before task execution begins.

**Missing: Pricing, metering, and settlement metadata in agent protocols.** When an agent retrieves a confidential enterprise document through an MCP server, the protocol itself does not answer who pays, how resource consumption is metered, or what settlement mechanism triggers between agent owners[^5].

***

## 2. Agentic Provenance & Lineage Standards

### 2.1 W3C PROV Is Not Enough

The W3C PROV standard provides an established model for representing provenance — who produced what data, through which processes — and already defines Agent as one of its three core classes alongside Entity and Activity[^8]. However, W3C PROV predates agentic workflows by over a decade, and existing extensions for multi-agent systems fail to capture core agentic AI concepts: prompts, LLM invocations, tool calls, model responses, and the non-deterministic reasoning chains that connect inputs to outputs[^9][^8].

The academic proposal PROV-AGENT extends W3C PROV and incorporates MCP concepts to unify AI agent interactions with traditional workflow provenance into a single graph[^9][^8]. It models `AIAgent` as a subclass of W3C PROV Agent, enabling multi-agent workflows to be represented in the same provenance graph, capturing fine-grained provenance: tool invocations, prompt versions, model identifiers, and inter-agent delegation chains[^8]. This is research-grade, not a ratified standard.

### 2.2 OpenLineage — Necessary But Incomplete

OpenLineage provides an open specification for collecting lineage metadata across data pipelines, with integrations for orchestrators like Airflow[^10][^11]. It covers job execution lineage — which datasets a pipeline read from or wrote to — and is now used for auditing ML training data in financial services compliance contexts[^11]. However, OpenLineage has no model for:
- Capturing LLM prompts, responses, or model invocations as lineage nodes
- Representing agent decisions or tool calls as lineage events
- Linking an agent's output artifact back to the specific prompt version, model version, and tool state that produced it

**Missing: A ratified, production-grade agentic lineage standard** that extends both W3C PROV and OpenLineage to cover the full provenance of agentic outputs — from raw data through model invocation, tool use, and agent reasoning, to the final action or artifact. The EU AI Act's compliance requirements for reproducibility from model output back to source data create a regulatory forcing function, but no standard currently closes this loop[^1].

### 2.3 Data Lineage Freshness

Surface-level lineage — lineage tracking that lags behind actual platform state — is one of the most critical production failure modes[^1]. A lineage graph that shows what the system looked like last week rather than now creates mismatches that produce wrong agent actions. No existing standard defines **real-time lineage event emission requirements** or SLAs for lineage freshness in agentic contexts.

***

## 3. Metadata Standards for Agentic Systems

### 3.1 The Completeness Crisis

A 2025 analysis found that data professionals report metadata completeness rates of only 30–40% without automation[^12]. Manual metadata management cannot keep pace with modern data estates, and incomplete metadata in an agentic context does not just mean poor documentation — it means confident agent actions based on wrong state[^1][^13]. IT teams must also create rich metadata while simultaneously identifying PII, PHI, PCI, and other sensitive information to filter from AI system inputs, a requirement that existing metadata management frameworks were not designed to automate at agent speed[^13].

### 3.2 Open Agentic Schema Framework (OASF)

The Open Agentic Schema Framework (OASF), maintained by the Agntcy project, provides a standardized schema system for defining and managing AI agent capabilities, interactions, and metadata[^14][^15]. OASF records can be annotated with skills and domains to enable discovery across agentic systems. It is a promising foundation but remains an early-stage, community-maintained framework — not an ISO/W3C/IEEE-ratified standard — and its skills/domains taxonomy is explicitly acknowledged as a key area still under expansion[^14].

### 3.3 LLM Metadata Fragmentation

Different tools use varying metadata formats for LLM artifacts, making integration across providers difficult[^16]. Teams use `prompt_version`, `template_id`, and `prompt_revision` to mean the same thing; model identifiers are not globally persistent across providers; and there is no universal schema for expressing:
- Model version, quantization level, or inference configuration used in a production call
- Prompt template version and hash
- Sampling parameters (temperature, top-p) as metadata on outputs
- Context window boundaries and truncation events

**Missing: A universal LLM invocation metadata schema** — analogous to what OpenAPI provides for REST endpoints — that makes LLM calls first-class, interoperable, and auditable metadata objects.

### 3.4 FAIR-R: Extending FAIR for AI Readiness

The FAIR principles (Findable, Accessible, Interoperable, Reusable) have guided data management since 2016[^17]. However, FAIR does not inherently address AI-specific needs — it says nothing about training data quality, model input/output provenance, or the structured formats agents need to consume data programmatically[^18]. A 2025 paper proposes extending FAIR to **FAIR-R** (adding "Readiness for AI"), acknowledging that key challenges remain: how to structure data for AI without compromising ethics, what governance models ensure equitable access, and how to standardize provenance tracking for AI-ready datasets[^18]. FAIR-R is in early academic proposal stage.

***

## 4. Data Contract Standards

### 4.1 Current State

The Open Data Contract Standard (ODCS), now at version 3.1.0 and governed by the Bitol project, is the most prominent open standard for data contracts[^19]. It defines a structured YAML format describing datasets, their schema expectations, quality dimensions (accuracy, completeness, timeliness), access patterns, governance frameworks, and compliance metadata[^20]. Soda's enforcement integrates ODCS contracts directly into CI/CD pipelines as deployment gates, so schema violations fail a PR at the producer rather than an alert at midnight downstream[^21][^20].

dbt's native contracts cover schema enforcement within a dbt DAG but are explicitly not designed to function as full data contracts across the data lifecycle[^20]. They protect local transformation logic, not the producer-consumer boundary across organizational domains.

### 4.2 Missing Dimensions for Agentic Contexts

Existing data contract standards were designed for human-authored data pipelines. They lack:

- **Agent consumer declarations**: No standard way to declare that an agent is the consumer of a data product, with the specific context and timeliness requirements that agentic consumption entails
- **Semantic contract bindings**: ODCS covers schema and quality, but not semantic alignment — no standard mechanism to bind a data contract to an ontology or terminology system, so agents can verify they are interpreting field semantics correctly
- **Runtime enforcement for agents**: Data contracts as currently conceived are pre-pipeline deployment gates; they do not provide a runtime enforcement mechanism that an agent can query mid-execution to verify a data product's current contract compliance status
- **SLA freshness guarantees for agentic consumption**: An agent that relies on a data product needs a contractual SLA on data freshness, not just quality. No current data contract standard formalizes this as a machine-readable field that agents can programmatically consume[^22][^23]

***

## 5. Semantic Interoperability & Federated Ontology Standards

### 5.1 The Semantic Gap in Data Mesh

As Data Mesh matures, semantic consistency across distributed data products has become the central unsolved problem[^24]. The initial success of domain-owned data products has created a new challenge: these products cannot semantically interoperate without a shared vocabulary layer. Different domains define "revenue," "active users," or "incident" differently, and agents reasoning across these definitions produce silent correctness bugs — reports that look right, pass automated checks, and mislead decision-makers[^1][^24].

A proposed solution is a **two-tiered federated semantic layer**: domain-level "Pragmatic Ontologies" that capture local business semantics, governed by a lean "Meta-Ontology" that acts as a protocol for cross-domain interaction[^24]. This pattern aligns with Gartner's 2026 D&A Summit positioning of semantic layers and knowledge graphs as foundational infrastructure for agentic AI[^25]. No standard body has ratified this two-tier model.

### 5.2 Ontology Alignment Is Not Standardized

For an agent to operate across multiple organizational data sources, it must perform ontology alignment — determining whether two concepts from different vocabularies mean the same thing. The IETF draft on Ontology-based Semantic Interaction for Internet of Agents (IoA) proposes a normative semantic layer using JSON-LD serialization, with required classes and properties, a negotiation procedure, and alignment rules for heterogeneous ontologies[^26]. This is a 2026 draft — not yet even a proposed standard — and it explicitly does not define transport, session, or security protocols, leaving critical integration layers unspecified[^26].

**Missing: A production-grade, ratified standard for cross-domain ontology alignment and negotiation** that agents can use at runtime to determine semantic compatibility (direct match, mapped match, or no match) before executing cross-domain tasks.

### 5.3 Semantic Annotation at Metadata Ingestion

EU initiatives through the SEMIC framework and Language DCAT-AP are pioneering metadata annotation using JSON-LD and DCAT-AP profiles, but these primarily target government and language data contexts[^27]. Enterprise data catalogs have no equivalent normative requirement. The European Commission's August 2025 standardization mandate (Mandate 614) assigns "data catalogues and semantic asset frameworks" to ETSI TC Data, establishing the first formal European path to harmonized standards in this area[^28] — but deliverables are years away.

***

## 6. Runtime Governance & Agent Identity Standards

### 6.1 The MCP Governance Gap

The Model Context Protocol has, within seventeen months of its introduction, produced an enterprise integration surface that is dynamic, ecosystem-scale, and trust-transitive — and that ISO 42001, the NIST AI RMF, and EU AI Act governance frameworks were not designed to address[^29]. A quantitative analysis through April 2026 found ~97,980 MCP-server repositories, with the active standalone population on the order of 10⁴ to 10⁵[^29]. These servers connect AI agents to enterprise systems — databases, code repositories, communication tools, financial systems — under governance frameworks built for deterministic systems with stable interfaces[^30].

The core problem: MCP's dynamic, runtime-adaptive execution model cannot be addressed by frameworks that assume stable, predictable system boundaries[^30]. A specific primitive identified as missing — **runtime capability-state attestation** — does not yet exist in any published MCP revision[^29]. No current published standard answers: what state is this specific agent instance currently authorized to act in, given its current context and the current state of the systems it can reach?

### 6.2 No Universal Agent Registry

Gartner's analysis explicitly states that no universal agent registry currently exists[^31]. This creates cascading governance failures: organizations cannot discover what agents are deployed in their ecosystem, cannot verify what capabilities and dependencies each agent possesses, and cannot authenticate agents attempting to access resources[^31]. A 1,445% surge in multi-agent system inquiries from Q1 2024 to Q2 2025 has made this gap operationally urgent[^31].

**Proposed: SBOM (Software Bill of Materials) for AI agents**, extending the SPDX foundation with agent-specific fields — core agent info, organizational context, capability documentation (tools, APIs, MCP implementations), dependency trees, and licensing/compliance markers[^31]. This is a community proposal, not a standard.

Gartner predicts that by 2028, standardized agent communication protocols will enable over 60% of multi-agent systems to incorporate agents from multiple vendors — but the identity and registry standards this requires do not yet exist[^31].

### 6.3 Static vs. Runtime Governance

Most organizations have policy documents but lack runtime controls[^1]. Data governance policies exist in PDFs and wikis. Nothing enforces them at the moment an agent executes a query. This distinction — between documented governance and **executable governance** — is one of the most consequential missing infrastructure elements:

- No standard for expressing data access policies in a machine-readable format that agents can query at runtime
- No standard for encoding "this column contains PII" as a signal an agent must respect before reading, not a label a human reads after the fact
- No standard runtime permission model that spans MCP servers, A2A agent delegations, and internal data product access layers simultaneously

McKinsey's 2026 AI Trust Maturity Survey found that only about one-third of organizations report maturity levels of three or higher in governance and agentic AI governance dimensions, and that the gap between technical readiness and governance readiness is growing[^32].

***

## 7. Synthetic Data Standards

The urgency of standards for synthetic data in the agentic era is rapidly escalating. LLMs are approaching the limits of available human text data on the internet, and predictions suggest that between 2026 and 2032 all available human internet text will be exhausted for training purposes — making synthetic data a primary training fuel[^33]. Yet no standards currently govern synthetic data's quality, reliability, fairness, or documentation requirements in AI training pipelines[^33].

The EU AI Act Article 10 references synthetic data for high-risk AI systems but encourages its use without fully addressing the risks of bias amplification or documentation gaps in rapidly iterated synthetic data pipelines[^33]. Identified missing standards include:

- **Synthetic data "nutritional labels"**: Structured documentation of how datasets were generated, training provenance, quality/fidelity assessments, bias test results, and privacy-preserving techniques applied — comparable to food ingredient labeling[^33]
- **Version control standards for synthetic datasets**: No standard mechanism exists for versioning synthetic datasets in a way that is machine-readable, semantically stable, and linkable to the models that consumed them
- **Bias and fairness evaluation benchmarks** for synthetic data specifically, distinct from natural data bias frameworks

***

## 8. Industry-Specific Gaps

### 8.1 Healthcare: Beyond FHIR APIs

FHIR (HL7 FHIR) provides standardized resource structures (Patient, Observation, Condition, MedicationRequest), explicit clinical semantics through SNOMED CT, LOINC, and RxNorm, and built-in auditability — making it the strongest domain-specific foundation for agentic AI in any industry[^34]. TEFCA (Trusted Exchange Framework and Common Agreement) moved from policy to functional reality in 2025, enabling network-level data exchange[^35].

However, by 2025 the industry recognized that having FHIR APIs is insufficient if the data behind them is "dirty" — syntactic interoperability without semantic normalization[^35][^36]. Specific gaps in the agentic healthcare context:

- **FHIR 5.0 lacks standard semantics for emerging data types**: Genomics data, behavioral health data, and IoT health device streams have no fully standardized FHIR representation, creating semantic gaps agents cannot safely bridge[^37]
- **No standard for agentic clinical decision boundaries**: Which agent actions require human-in-the-loop approval, under what FHIR-encoded conditions, with what audit trail format — these are design principles discussed in literature but not encoded in any standard[^34]
- **Missing: FHIR-native provenance profiles for AI agents** that extend FHIR's built-in provenance resource to capture LLM model version, prompt context, and reasoning chain alongside clinical actions

### 8.2 Financial Services: Audit Trails and Semantic Linking

Financial services AI in 2026 has made meaningful progress on auditable logic — systems can now show exactly where an output came from — and semantic linking between clients, collateral, and regulatory filings[^38]. The EU AI Act and SEC expectations are coming into force, requiring auditability of AI-generated decisions in credit risk, fraud detection, and portfolio management[^39].

Missing standards:
- **No standard format for AI-generated financial decision provenance** that satisfies both internal audit requirements and SEC/ESMA regulatory reporting simultaneously
- **No cross-institution agent identity standard**: When one institution's agent delegates to another (e.g., in interbank settlement or KYC processes), no standard governs how identity, capability, and authorization are verified across institutional boundaries
- **No standard for financial data contract semantic binding**: A data contract in a bank can specify schema and quality, but no standard binds it to a regulatory taxonomy (e.g., XBRL, BCBS 239) so agents can verify semantic compliance automatically

### 8.3 Manufacturing & IoT: EU Data Act and the Edge Gap

The EU Data Act, fully applicable from September 2025, establishes that IoT device manufacturers can no longer monopolize the data generated by their devices — users have the right to access and share it in machine-readable formats[^40][^41]. This is a landmark policy change, but the technical standards to fulfill it are lagging:

- **No standard for IoT edge-to-agent metadata propagation**: Sensor readings flowing from industrial edge devices into cloud data pipelines carry no standardized metadata about calibration state, firmware version, or data quality at the point of capture — making it impossible for agents to assess the reliability of the data they receive
- **No cross-domain industrial ontology for agentic systems**: OPC UA covers industrial automation communication, but its semantic layer is not interoperable with data mesh ontologies, FHIR, or financial taxonomies — creating isolated semantic islands that multi-domain agents cannot traverse
- **Missing: A standard for IoT data product interfaces** that specifies how edge-generated time-series data is described, contracted, and made available to agents via MCP or equivalent protocols

### 8.4 AEC & BIM: The Governance Vacuum

The draft revision of the BIM standard DIS/ISO 19650-1:2026, published for consultation in March 2026, contains no mention of agents, autonomous workflows, or agentic AI — despite being revised at a time when vendors are actively shipping agent-capable platforms with MCP hooks[^42]. This is a critical sector-specific gap: when an agent makes a design decision that contributes to a construction outcome, the standard that governs information management in BIM has no mechanism to record how that decision was made, who or what was accountable, or how much autonomy was granted[^42].

Missing:
- **Agent accountability frameworks in ISO 19650**: A governance layer specifying what agents are permitted to do in BIM workflows, how autonomy is calibrated against risk, and how agent decisions are recorded for auditability
- **IFC schema extensions for agentic provenance**: IFC (Industry Foundation Classes) coordinates geometry and metadata but does not define agent action records or conflict resolution provenance[^42]

***

## 9. Cross-Cutting Missing Infrastructure

### 9.1 Real-Time Context Freshness Standards

Gartner's 2025 analysis found that organizations using autonomous agentic pipelines achieve a 48% reduction in decision latency and a 35% improvement in policy compliance compared to batch analytics setups[^43]. The shift from batch to real-time context is operationally validated. Yet no standard governs:

- **Context freshness SLAs**: What is the maximum acceptable staleness for different classes of agent context (operational vs. analytical vs. compliance)? No standard defines this, leaving every organization to reinvent it
- **Streaming metadata standards for agentic pipelines**: The MCP roadmap acknowledges that "running it at scale has revealed gaps around horizontal scaling and stateless operation"[^44], but no standard governs how streaming platforms emit metadata that agents can consume in a standardized, semantically typed format
- **Change Data Capture (CDC) semantics for agent context**: CDC streams are used to propagate updates across systems in real-time, but they carry no standard semantic annotation — agents cannot determine what a change means without domain-specific, non-portable logic[^45]

### 9.2 Data Product Specifications for AI Consumers

The Open Data Product Specification (ODPS) supports quality dimensions including accuracy, completeness, and timeliness, and supports defined access patterns including Model Context Protocol for AI agents[^23]. However, enterprise data products in 2025 largely failed enterprise AI agents because they lacked structured context — the semantics, lineage, and governance metadata that make a data asset safe for autonomous consumption[^22]. The architecture requires all three components: Data Products as the economic unit, a Data Developer Platform as industrialization, and MCP as the AI interface — with any missing component causing failure[^22].

**Missing: A universal data product specification standard** that mandates AI-consumability metadata — semantic bindings, agent access patterns, freshness SLAs, lineage pointers, and compliance flags — as first-class fields, not optional annotations.

### 9.3 Agentic Identity, Delegation, and Trust Chains

When a human delegates a task to an agent, and that agent sub-delegates to specialized agents, and those agents access external data and tools — the resulting trust chain has no standard representation. Specifically:
- No standard for **human-to-agent delegation tokens** that carry the original human's permissions, scope limitations, and time bounds through multi-hop agent chains
- No standard for **inter-agent trust attestation** — how does Agent B know that Agent A has been authorized by a legitimate human principal, and that the authorization has not been revoked?
- No standard for **agent action receipts** — a cryptographically signed record of what an agent did, what data it accessed, and what reasoning it applied — that downstream systems and auditors can verify

***

## 10. Emerging Standard Bodies and Initiatives to Watch

| Initiative | Scope | Status (April 2026) |
|-----------|-------|----------------------|
| **IETF IoA Semantic Interaction Draft** | Ontology-based agent interoperability | Draft, not yet proposed standard[^26] |
| **EU Mandate 614 (ETSI TC Data)** | Data catalogues, semantic asset frameworks | Formal mandate accepted, deliverables pending[^28] |
| **PROV-AGENT (arXiv)** | W3C PROV + MCP for agentic provenance | Academic proposal, open-source implementation[^8] |
| **OASF (Agntcy/Cisco)** | Agent capability schema and metadata | Community standard, pre-ratification[^14] |
| **ODCS v3.1.0 (Bitol)** | Data contracts | Open standard, in active use, lacks agentic extensions[^19] |
| **A2A + ACP merger (Linux Foundation)** | Agent-to-agent communication | Under active consolidation[^6] |
| **FAIR-R Framework** | AI-ready data principles | Academic proposal stage[^18] |
| **Agent SBOM (SPDX extension)** | Agent identity and capability registry | Community proposal, no formal body[^31] |

***

## 11. Priority Ranking of Missing Standards by Impact

The following assessment rates each gap by urgency (how immediately it blocks production agentic deployments) and breadth (how many industries and use cases are affected):

| Missing Standard | Urgency | Breadth | Primary Blocker |
|-----------------|---------|---------|-----------------|
| Runtime governance / executable policies | Critical | Universal | EU AI Act compliance, enterprise security |
| Universal agent identity & registry | Critical | Universal | Multi-agent orchestration at scale |
| Agentic provenance / PROV-AGENT ratification | High | Universal | Auditability, reproducibility |
| Federated semantic layer / ontology alignment | High | Universal | Data mesh, cross-domain agents |
| Data contracts with agentic extensions | High | Universal | Pipeline reliability, semantic correctness |
| MCP runtime capability-state attestation | High | Universal | MCP governance gap[^29] |
| Agent SBOM / capability declaration | High | Enterprise | Vendor interoperability, security |
| FAIR-R / AI-ready data specification | Medium | Research, public sector | Training data governance |
| Synthetic data standards | Medium | Universal | LLM training sustainability |
| FHIR agentic provenance profiles | High | Healthcare | Clinical AI compliance |
| ISO 19650 agentic governance | High | AEC/BIM | Construction liability |
| IoT edge-to-agent metadata | Medium | Manufacturing, IoT | Industrial agentic deployments |
| Cross-institution agent identity (finance) | Medium | Financial services | Interbank, KYC, settlement |
| Real-time context freshness SLAs | Medium | Universal | Operational agentic systems |
| Streaming metadata for agentic pipelines | Medium | Universal | Live context architecture |

***

## Conclusion

The agentic data engineering landscape in 2026 is characterized by a protocol stack that addresses the easy problems (tool connectivity via MCP, task delegation via A2A) while leaving the hard problems unresolved: runtime governance, semantic alignment across domains, agentic provenance that satisfies regulatory requirements, and universal agent identity that enables trust across organizational boundaries. The recurring theme across all industries is that governance, semantics, and metadata standards are not merely operational nice-to-haves — they are the conditions under which autonomous systems can be safely authorized to act. The technology has outpaced the standards ecosystem by at least one full standards revision cycle[^42], and the liability consequences of that gap are beginning to materialize in litigation, regulatory enforcement, and production failures[^46][^29]. The most strategically important investments for practitioners today are in the areas where standards are closest to ratification: agentic provenance (PROV-AGENT/OpenLineage integration), federated semantic layers for data mesh, executable data contracts with agent-consumable metadata, and agent identity registries built on SPDX or OASF foundations.

---

## References

1. [Agentic Ai Readiness...](https://www.codebridge.tech/articles/agentic-ai-for-data-engineering-why-trusted-context-governance-and-pipeline-reliability-matter-more-than-autonomy) - Your data layer determines whether agentic AI works in production. Learn the five foundations CTOs n...

2. [Agentic AI Fails in Production for Simple Reasons — What MLDS ...](https://dev.to/theprodsde/agentic-ai-fails-in-production-for-simple-reasons-what-mlds-2026-taught-me-2ec) - MLDS 2026 reinforced that enterprise‑grade agentic AI is a system design problem, requiring validati...

3. [A Survey of Agent Interoperability Protocols | PDF - Scribd](https://www.scribd.com/document/895001961/A-Survey-of-Agent-Interoperability-Protocols) - This document surveys four emerging agent interoperability protocols: Model Context Protocol (MCP), ...

4. [A Survey of Agent Interoperability Protocols: Model Context ...](https://arxiv.org/html/2505.02279v2)

5. [Agent Architectures: MCP, A2A, and Beyond - ipto.ai](https://www.ipto.ai/articles/agent-architectures-mcp-a2a/) - How the Model Context Protocol, Agent-to-Agent protocols, and emerging standards shape the modern AI...

6. [Key features of ACP](https://www.ibm.com/think/topics/agent-communication-protocol) - Agent Communication Protocol (ACP) is an open standard for agent-to-agent communication. With this p...

7. [Beyond Context Sharing: A Unified Agent Communication Protocol ...](https://arxiv.org/html/2602.15055v1)

8. [PROV-AGENT: Unified Provenance for Tracking AI Agent ... - arXiv](https://arxiv.org/html/2508.02866v1) - The W3C PROV standard [7] is a widely adopted representation model for provenance, providing a struc...

9. [PROV-AGENT: Unified Provenance for Tracking AI Agent Interactions in Agentic Workflows](https://arxiv.org/pdf/2508.02866v1.pdf)

10. [Why an Open Standard for Lineage Metadata? - OpenLineage](https://openlineage.io/blog/why-open-standard/) - An open spec for lineage metadata will be more likely to succeed because it will foster collaboratio...

11. [Uncover data blindspots with OpenLineage - Collibra](https://www.collibra.com/blog/uncover-data-blindspots-with-openlineage) - “Where did this data actually come from?” If that question causes a collective sigh in your meetings...

12. [What Is Metadata Automation? A Strategic Guide for 2026 - Alation](https://www.alation.com/blog/what-is-metadata-automation/) - Metadata automation is the process of using rules, AI, and automated systems to capture, classify, e...

13. [Your AI Is Only as Smart as Your Metadata - Dataversity](https://www.dataversity.net/articles/your-ai-is-only-as-smart-as-your-metadata/) - The CDO Insights 2025 survey reports that data quality and readiness is a top obstacle to AI success...

14. [agntcy/oasf: Open Agentic Schema Framework - GitHub](https://github.com/agntcy/oasf) - The Open Agentic Schema Framework (OASF) is a standardized schema system for defining and managing A...

15. [Open Agentic Schema Framework - Agntcy](https://docs.agntcy.org/oasf/open-agentic-schema-framework/) - OASF defines a set of standards for AI agent content representation that aims to: Define common data...

16. [LLM Metadata Standards: Problems vs. Solutions - Latitude.so](https://latitude.so/blog/llm-metadata-standards-problems-vs-solutions) - Explore the challenges of LLM metadata management and discover structured solutions for improved eff...

17. [FAIR Principles: Interpretations and Implementation Considerations](https://direct.mit.edu/dint/article/2/1-2/10/10017/FAIR-Principles-Interpretations-and-Implementation) - Abstract. The FAIR principles have been widely cited, endorsed and adopted by a broad range of stake...

18. [Moving Toward the FAIR-R principles: Advancing AI-Ready Data](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5164337) - In today's rapidly evolving AI ecosystem, making data ready for AI-optimized for training, fine-tuni...

19. [Data Contracts: The Missing Handshake Between Data Producers ...](https://www.carlosacchi.cloud/data-contracts-the-missing-handshake-between-data-producers-and-consumers-2468cf220d42) - Open Data Contract Standard (ODCS), now at version 3.1.0, is the most prominent open standard. It is...

20. [The Definitive Guide to Data Contracts - Soda.io](https://soda.io/blog/guide-to-data-contracts) - A data contract is not a product by itself, but one component of a data product. A data product is a...

21. [Data Contracts Shift Governance Left with Open Data Contract ...](https://www.linkedin.com/posts/imtinanfarooq_for-the-past-two-years-data-contracts-activity-7435987624676253696-03AR) - Soda's contract enforcement integrates with CI/CD pipelines directly — schema validation, freshness ...

22. [Data Products: The Essential Context for Enterprise AI](https://moderndata101.substack.com/p/data-products-the-essential-context) - The Moment

23. [How Standardized Data Product Specifications Drive Business Value](https://www.alation.com/blog/business-value-standardized-data-products-jarkko-moilanen/) - The future: AI-first, product-oriented. One of the most exciting developments is the growing interse...

24. [How to overcome semantic challenges in Data Mesh with ... - LinkedIn](https://www.linkedin.com/posts/cognito-one_datamesh-datastrategy-enterprisearchitecture-activity-7379189350976266240-Jm5u) - As Data Mesh matures from a niche paradigm to a standard in enterprise architecture, a second wave o...

25. [What Is Ontology? Definition, Components & AI Use Cases in 2026](https://atlan.com/know/ontology-101-explainer/) - Ontology gives AI systems shared meaning. Learn what it is, how it differs from knowledge graphs and...

26. [Ontology-based Semantic Interaction for Internet of Agents](https://datatracker.ietf.org/doc/html/draft-zhang-dmsc-ioa-semantic-interaction-02) - This document specifies a normative semantic layer for agent-to-agent interaction in the Internet of...

27. [1](https://interoperable-europe.ec.europa.eu/sites/default/files/news/2024-08/Semantic%20Interoperability%20Solutions%20for%20Data%20Spaces.pdf)

28. [European standardization request for data spaces is now official](https://internationaldataspaces.org/european-standardization-request-for-data-spaces-is-now-official/) - It's official. The European Commission published its long-anticipated standardization request for da...

29. [[PDF] The MCP Governance Gap: Empirical Evidence ... - Linus Teklenburg](https://linus-teklenburg.de/paper)

30. [Securing the Model Context Protocol (MCP): Risks, Controls, and ...](https://arxiv.org/html/2511.20920v1) - The gap between existing standards and MCP operational requirements necessitates new security contro...

31. [The Hidden Crisis in Enterprise AI: Why Software Bills of Materials ...](https://xmpro.com/the-hidden-crisis-in-enterprise-ai-why-software-bills-of-materials-are-your-best-defense/) - Gartner predicts that through 2028, over 50% of AI initiatives will halt because organizations canno...

32. [State of AI trust in 2026: Shifting to the agentic era - McKinsey](https://www.mckinsey.com/capabilities/tech-and-ai/our-insights/tech-forward/state-of-ai-trust-in-2026-shifting-to-the-agentic-era) - Findings from McKinsey's 2026 AI Trust Maturity Survey reveal progress in trust maturity, alongside ...

33. [The Urgency of Standards for Synthetic Data in the Era of Agentic AI](https://www.techpolicy.press/the-urgency-of-standards-for-synthetic-data-in-the-era-of-agentic-ai/) - Another study predicts that between 2026 and 2032, LLMs will exhaust all available human text data o...

34. [How FHIR Enables Agentic AI in Healthcare | Blog - Itirra](https://itirra.com/blog/how-fhir-enables-agentic-ai-in-healthcare/) - Agentic AI can’t run on messy integrations. Learn how FHIR enables agentic AI in healthcare with por...

35. [From Interoperability to Intelligence: How 2025 Shaped Healthcare ...](https://www.linkedin.com/pulse/from-interoperability-intelligence-how-2025-shaped-data-mahesh-dedhia-kkunf) - The "Semantic Gap" & Data Normalization - By 2025, the industry realized that having FHIR APIs wasn'...

36. [Semantic Interoperability In Healthcare In 2025: A Guide](https://murphi.ai/semantic-interoperability-in-healthcare/) - Make data work together with standard formats and smart mapping with our practical guide on semantic...

37. [Agentic AI and Healthcare Data Interoperability: A Game-Changer ...](https://visvero.com/agentic-ai-and-healthcare-data-interoperability-a-game-changer-for-patient-care/) - AI used in healthcare can drive better outcomes by synthesizing disparate data into one cohesive vie...

38. [Financial Services AI Trends 2026: Closing the Production Value Gap](https://www.dataiku.com/stories/blog/financial-services-ai-trends-2026) - Learn why agentic AI is finally reaching production in financial services and how institutions addre...

39. [What 2025–2026 Data Reveal about the Agentic AI Market](https://mev.com/blog/what-2025-2026-data-reveal-about-the-agentic-ai-market) - 2025–2026 data shows rapid growth in the agentic AI market. Explore key statistics on adoption, ROI,...

40. [The EU Data Act – key obligations for service providers - Smartius](https://smartius.fi/news/the-eu-data-act-key-obligations-for-service-providers/) - The EU Data Act (Fi. Datasäädös) entered into application on 12 September 2025. The regulation appli...

41. [Data Act 2025: new rules for sharing IoT and cloud data in Europe](https://auris.legal/en/data-act-2025-nuevas-reglas-para-compartir-datos-iot-y-cloud-en-europa/) - The European Union has approved Regulation (EU) 2023/2854, known as the Data Act, a central pillar o...

42. [Agentic BIM's missing infrastructure - AEC Magazine](https://aecmag.com/ai/agentic-bims-missing-infrastructure/) - Agentic BIM’s missing infrastructure. A Google research paper provides the framework for making agen...

43. [Real-Time Streaming as the Context Layer for AI Agents - LinkedIn](https://www.linkedin.com/pulse/real-time-streaming-context-layer-ai-agents-timeplusinc-upqse) - Empower LLMs with Up-to-the-Millisecond Data Awareness By Gang Tao, Co-Founder and CTO of Timeplus M...

44. [Roadmap - What is the Model Context Protocol (MCP)?](https://modelcontextprotocol.io/development/roadmap) - Streamable HTTP gave MCP a production-ready transport, but running it at scale has revealed gaps aro...

45. [Agentic AI Pipelines: Streaming Data for Autonomous Agents](https://conduktor.io/glossary/agentic-ai-pipelines) - Build streaming data pipelines that power autonomous AI agents with real-time context, fresh vector ...

46. [Agentic AI: The liability gap your contracts may not cover](https://www.cliffordchance.com/insights/resources/blogs/talking-tech/en/articles/2026/02/agentic-ai-and-the-liability-gap-your-contracts-may-not-cover.html) - Agentic AI is reshaping the nature of technology risk. Businesses relying on unmodified agreements m...

