"""
Four infrastructure claims were withdrawn, not built: EconML/PyMC estimation
backends (`econml-pymc`), the `cynepic-*` Rust crates (`rust-crates`), Neo4j as
the causal graph store (`neo4j-graph-store`) and Kafka event streaming
(`kafka-streaming`). They left conformance/declared_not_implemented.json by
retraction; this file pins the retraction, so a claim that comes back, or a
service that serves none of the code, fails here instead of reaching a reader.

Known truth asserted (each a fact about the tree, not a value the code returned):
  1. docker-compose.yml defines exactly the services the code talks to:
     PostgreSQL and Neo4j (optional: the experience buffer and drift alerts).
     No Kafka, Kafka UI or Redis, and no `future` profile of idle services.
  2. No dependency table or lock names EconML, PyMC, a Kafka client or Redis.
  3. The repository tracks no Rust source or Cargo manifest.
  4. The withdrawn phrasings do not reappear in the live docs, prompts or
     backend source (docs/history and docs/research are the record of what was
     said, and are not scanned).
  5. Neo4j stays what the docs now say it is: an optional side store reached
     only through Neo4jFactory by the experience buffer and drift-alert skills,
     never by the semantic layer (where the causal graph lives).

Negative controls: `find_withdrawn_claims` is run on a planted tree and must
report each planted phrase, and nothing in a clean one.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

# The claims as they were made (the register's claimed_in quotes and their
# siblings), by the register id they left under.
WITHDRAWN: dict[str, list[re.Pattern[str]]] = {
    "econml-pymc": [
        re.compile(r"DoWhy / EconML for statistical estimation"),
        re.compile(r"\(DoWhy, PyMC\)"),
        re.compile(r"DoWhy / PyMC / EconML"),
        re.compile(r"DoWhy, EconML, PyMC"),
        re.compile(r"compute ATE via DoWhy / PyMC"),
        re.compile(r"PyMC MCMC"),
    ],
    "rust-crates": [
        re.compile(r"cynepic-\w+`? (?:Rust )?crate"),
        re.compile(r"crate \(Rust\)"),
        re.compile(r"cynepic-bayes Bayesian belief tracking"),
    ],
    "neo4j-graph-store": [
        re.compile(r"Neo4j \(Graph Store\)"),
        re.compile(r"d-separation query p95"),
        re.compile(r"Neo4j for causal graph storage"),
        re.compile(r"PostgreSQL \+ Neo4j"),
        re.compile(r"\| \*\*Graph memory\*\* \| Neo4j \|"),
    ],
    "kafka-streaming": [
        re.compile(r"Ingestion Bus \(Kafka\)"),
        re.compile(r"Kafka \(Event Streaming\)"),
        re.compile(r"Alert emission via Kafka"),
        re.compile(r"(?i)emits? (?:drift )?alerts? (?:via|to) Kafka"),
        re.compile(r"requires PostgreSQL 16, Neo4j 5, Kafka"),
        re.compile(r"confluent_kafka"),
    ],
}

SCANNED_GLOBS = (
    "docs/*.md",
    "README.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "AGENTS.md",
    "prompts/*.yaml",
    "backend/src/**/*.py",
    "packages/semantic-types/src/*.ts",
)


def find_withdrawn_claims(root: Path) -> list[str]:
    """Every `path:line [register id] text` where a withdrawn phrasing appears."""
    hits: list[str] = []
    seen: set[Path] = set()
    for pattern in SCANNED_GLOBS:
        for path in sorted(root.glob(pattern)):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            text = path.read_text(encoding="utf-8")
            for n, line in enumerate(text.splitlines(), 1):
                for rid, regexes in WITHDRAWN.items():
                    if any(rx.search(line) for rx in regexes):
                        hits.append(f"{path.relative_to(root)}:{n} [{rid}] {line.strip()[:120]}")
    return hits


def _tracked(*patterns: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--", *patterns],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return [p for p in out.stdout.splitlines() if p]


# ── 1. compose ───────────────────────────────────────────────────────────────


def _top_level_block(text: str, key: str) -> set[str]:
    """The names directly under a top-level compose key (two-space indent).

    A line reader rather than a YAML parser: the backend declares no YAML
    library, and the compose file's layout is plain.
    """
    names: set[str] = set()
    inside = False
    for line in text.splitlines():
        if re.match(r"^\S", line):
            inside = line.rstrip() == f"{key}:"
            continue
        hit = re.match(r"^  ([A-Za-z0-9_.-]+):", line)
        if inside and hit:
            names.add(hit.group(1))
    return names


def test_compose_defines_only_services_the_code_uses() -> None:
    text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert _top_level_block(text, "services") == {"postgres", "neo4j"}
    assert _top_level_block(text, "volumes") == {"postgres_data", "neo4j_data", "neo4j_logs"}
    assert not re.search(r"(?m)^\s+profiles:", text)


# ── 2. dependencies ──────────────────────────────────────────────────────────

_ABSENT_DISTS = re.compile(
    r"(?im)^\s*\"?(econml|pymc|pymc3|confluent[-_]kafka|kafka[-_]python|aiokafka|redis)\b"
)


def test_no_dependency_on_the_withdrawn_backends() -> None:
    files = _tracked("*pyproject.toml", "requirements/*.txt", "requirements/*.in")
    assert any(f.endswith("pyproject.toml") for f in files)
    assert any(f.startswith("requirements/") and f.endswith(".txt") for f in files)
    offending = []
    for rel in files:
        for hit in _ABSENT_DISTS.finditer((REPO / rel).read_text(encoding="utf-8")):
            offending.append(f"{rel}: {hit.group(1)}")
    assert offending == []


# ── 3. Rust ──────────────────────────────────────────────────────────────────


def test_no_rust_in_the_repository() -> None:
    assert _tracked("*.rs", "*Cargo.toml", "*Cargo.lock") == []


# ── 4. docs, prompts, source ─────────────────────────────────────────────────


def test_withdrawn_claims_do_not_reappear() -> None:
    assert (REPO / "docs" / "AGENTS.md").is_file()  # the scan looks at something
    assert find_withdrawn_claims(REPO) == []


@pytest.mark.parametrize(
    ("rid", "planted"),
    [
        ("econml-pymc", "**Backed by:** DoWhy / EconML for statistical estimation"),
        ("rust-crates", "**Reference implementation:** `cynepic-causal` Rust crate"),
        ("neo4j-graph-store", "### 3.1 Neo4j (Graph Store)"),
        ("kafka-streaming", "External Sources ──► Connectors ──► Ingestion Bus (Kafka)"),
    ],
)
def test_the_scan_catches_a_planted_claim(tmp_path: Path, rid: str, planted: str) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "CLEAN.md").write_text("Neo4j is optional; the graph is in SQL.\n", encoding="utf-8")
    assert find_withdrawn_claims(tmp_path) == []
    (docs / "PLANTED.md").write_text(f"intro\n{planted}\n", encoding="utf-8")
    hits = find_withdrawn_claims(tmp_path)
    assert len(hits) == 1
    assert hits[0].startswith("docs/PLANTED.md:2 ")
    assert f"[{rid}]" in hits[0]


def test_the_scan_skips_the_historical_record(tmp_path: Path) -> None:
    hist = tmp_path / "docs" / "history"
    hist.mkdir(parents=True)
    (hist / "OLD.md").write_text("Ingestion Bus (Kafka)\n", encoding="utf-8")
    assert find_withdrawn_claims(tmp_path) == []


# ── 5. Neo4j is a side store, reached through the factory ────────────────────


def test_neo4j_is_reached_only_by_the_optional_side_stores() -> None:
    src = REPO / "backend" / "src"
    reaches = re.compile(r"^\s*(from neo4j|import neo4j)|Neo4jFactory", re.M)
    users = sorted(
        str(p.relative_to(src))
        for p in src.rglob("*.py")
        if reaches.search(p.read_text(encoding="utf-8"))
    )
    assert users == [
        "api/health.py",
        "db/neo4j_factory.py",
        "skills/drift_detect.py",
        "skills/experience_buffer.py",
        "skills/experience_buffer_neo4j.py",
    ]
    assert not any(u.startswith("semantic_layer/") for u in users)
