"""The contract-parity gate: does it hold today, and can it see a divergence?

A gate nobody tested is a gate nobody has seen fail. These tests pin both
halves: that the three vocabulary sources agree today, and that the checker
fails when they do not -- including the failure mode that makes this class of
bug invisible in the first place, a source that parses to nothing and therefore
compares nothing while reporting success.

The checker lives in scripts/, which is not an importable package, so it is
loaded by path the way .gnomon/ci.sh invokes it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "contract_parity.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("contract_parity_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def parity() -> ModuleType:
    return _load()


# ── the repository as it stands ───────────────────────────────────────────────


def test_the_three_sources_agree_today(parity: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit 0: no divergence that is not already named in contract-parity.json."""
    monkeypatch.setattr(sys, "argv", ["contract_parity.py"])
    assert parity.main() == 0


def test_every_listed_divergence_is_still_real(parity: ModuleType) -> None:
    """A stale entry means the list stopped describing the code -- the defect the list exists to prevent."""
    found = parity.divergences(parity.collect())
    listed = parity.load_json(parity.ROOT / parity.PARITY_FILE)["known_divergences"]
    assert set(listed) <= set(found), (
        f"no longer diverge, strike them off {parity.PARITY_FILE}: {sorted(set(listed) - set(found))}"
    )


def test_the_alias_pairs_the_two_spellings(parity: ModuleType) -> None:
    """TypeScript's CausalEntityType and Python's EntityType are one vocabulary."""
    names = parity.collect()
    assert "EntityType" in names
    assert {"py", "pkg"} <= set(names["EntityType"]), (
        "the alias stopped resolving, so the two spellings are being compared as separate names"
    )


# ── seeded defects: each must make the checker fail ───────────────────────────


def test_detects_a_value_dropped_from_one_source(parity: ModuleType) -> None:
    """The plain case: one runtime loses a value the others still send."""
    names = {"EdgeType": {"py": {"CAUSES", "BLOCKS"}, "pkg": {"CAUSES"}}}
    found = parity.divergences(names)
    assert "EdgeType" in found
    assert "BLOCKS" in found["EdgeType"]


def test_detects_a_different_vocabulary_under_the_same_name(parity: ModuleType) -> None:
    """The dangerous case: same name, disjoint values, and tsc sees nothing wrong."""
    names = {"ReviewStatus": {"py": {"ACCEPTED"}, "fe": {"UNDER_REVIEW"}}}
    assert "ReviewStatus" in parity.divergences(names)


def test_a_name_in_one_source_only_is_not_a_divergence(parity: ModuleType) -> None:
    """A frontend-only presentation enum is legitimate; only shared names can disagree."""
    names = {"ThinkingStage": {"fe": {"PLANNING", "ACTING"}}}
    assert parity.divergences(names) == {}


def test_refuses_when_a_source_parses_to_nothing(
    parity: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A gate that inspected nothing may not report success.

    A moved file or a changed declaration style yields an empty parse, which
    would otherwise make every shared name vanish and the comparison pass while
    comparing nothing.
    """
    empty = tmp_path / "models.py"
    empty.write_text("# no enums here\n")
    monkeypatch.setattr(parity, "PY_MODELS", empty)
    with pytest.raises(parity.ParityError, match="parsed 0 declarations"):
        parity.collect()


def test_refuses_when_a_source_file_is_missing(
    parity: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(parity, "FE_TYPES", tmp_path / "gone.ts")
    with pytest.raises(parity.ParityError, match="is missing"):
        parity.collect()


# ── the parsers ───────────────────────────────────────────────────────────────


def test_parses_python_stringenums(parity: ModuleType, tmp_path: Path) -> None:
    src = tmp_path / "m.py"
    src.write_text(
        'from enum import StrEnum\n\n\n'
        'class Colour(StrEnum):\n'
        '    """A docstring must not break the parse."""\n\n'
        '    RED = "RED"\n'
        '    BLUE = "BLUE"\n\n\n'
        'class NotAnEnum:\n'
        '    x = "IGNORED"\n'
    )
    parsed = parity.py_enums(src)
    assert parsed == {"Colour": {"RED", "BLUE"}}


def test_parses_typescript_unions_on_one_line_and_many(parity: ModuleType, tmp_path: Path) -> None:
    src = tmp_path / "t.ts"
    src.write_text(
        'export type Flat = "A" | "B";\n\n'
        "export type Tall =\n"
        '  | "X"\n'
        '  | "Y"\n'
        '  | "Z";\n\n'
        "export interface NotAUnion { a: string }\n"
    )
    parsed = parity.ts_unions([src])
    assert parsed == {"Flat": {"A", "B"}, "Tall": {"X", "Y", "Z"}}
