"""Canonical JSON, digests, and the published contract schemas."""

from __future__ import annotations

import json
import math
from pathlib import Path

import jsonschema
import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

import causal_core as cc
from causal_core.canonical import _es_number, canonical_json, data_digest, digest
from causal_core.contract import CONTRACT_VERSION, OUTCOME_ADAPTER, Interval, json_schemas

REPO = Path(__file__).resolve().parents[3]
SCHEMA_DIR = REPO / "conformance" / "causal-claims" / CONTRACT_VERSION

# RFC 8785 appendix and ECMAScript Number::toString edge cases.
NUMBER_VECTORS = [
    (0.0, "0"),
    (-0.0, "0"),
    (1e21, "1e+21"),
    (1e-7, "1e-7"),
    (0.000001, "0.000001"),
    (123456789012345680000.0, "123456789012345680000"),
    (5e-324, "5e-324"),
    (1.7976931348623157e308, "1.7976931348623157e+308"),
    (9007199254740992.0, "9007199254740992"),
    (295147905179352830000.0, "295147905179352830000"),
    (1e23, "1e+23"),
    (0.1, "0.1"),
    (1.5e-5, "0.000015"),
    (-1.5, "-1.5"),
    (333333333.3333333, "333333333.3333333"),
]


@pytest.mark.parametrize(("value", "expected"), NUMBER_VECTORS)
def test_jcs_numbers(value: float, expected: str) -> None:
    assert _es_number(value) == expected


def test_jcs_objects_sort_by_utf16_and_escape_minimally() -> None:
    assert canonical_json({"b": [1, 2.5, None, True], "a": "xé", "€": 1, "\r": 2}) == (
        '{"\\r":2,"a":"xé","b":[1,2.5,null,true],"€":1}'
    )


def test_jcs_rejects_non_finite() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": math.nan})


def test_data_digest_ignores_row_index_and_column_order() -> None:
    a = pd.DataFrame({"x": [1.0, 2.0], "y": [3, 4]})
    b = pd.DataFrame({"y": [3.0, 4.0], "x": [1, 2]}, index=[10, 11])
    assert data_digest(a) == data_digest(b)
    c = a.assign(x=[1.0, 2.0000001])
    assert data_digest(a) != data_digest(c)


def test_digest_is_stable_across_key_order() -> None:
    assert digest({"a": 1, "b": [1, 2]}) == digest({"b": [1, 2], "a": 1})


def test_contract_rejects_nan_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Interval(level=0.95, lo=math.nan, hi=1.0, method="x")
    with pytest.raises(ValidationError):
        cc.CausalQuery(treatment="X", outcome="Y", surprise=1)  # type: ignore[call-arg]


def test_committed_schemas_match_the_code() -> None:
    """Contract/fixture gate: change a model without regenerating the schemas -> fail.

    Regenerate with: causal-core schema --out conformance/causal-claims
    """
    generated = json_schemas()
    committed = {
        p.name.removesuffix(".schema.json"): json.loads(p.read_text())
        for p in SCHEMA_DIR.glob("*.schema.json")
    }
    assert set(committed) == set(generated), "schema files and contract models differ"
    for name, schema in generated.items():
        assert json.loads(json.dumps(schema, sort_keys=True)) == committed[name], (
            f"{name}: committed schema is stale; run `causal-core schema --out "
            "conformance/causal-claims`"
        )


def test_every_outcome_kind_validates_against_the_published_schema(
    confounded_binary: pd.DataFrame,
) -> None:
    schema = json.loads((SCHEMA_DIR / "outcome.schema.json").read_text())
    outcomes = [
        cc.analyze(confounded_binary, "Z -> X; Z -> Y; X -> Y; W -> Y", treatment="X", outcome="Y"),
        cc.analyze(confounded_binary, "X -> Y; X <-> Y", treatment="X", outcome="Y"),
        cc.analyze(object(), "X -> Y", treatment="X", outcome="Y"),
    ]
    kinds = set()
    for out in outcomes:
        doc = json.loads(out.model_dump_json())
        jsonschema.validate(doc, schema)
        assert OUTCOME_ADAPTER.validate_python(doc).kind == out.kind
        kinds.add(out.kind)
    assert kinds == {"result", "refusal"}


def test_result_digest_reproduces(confounded_binary: pd.DataFrame) -> None:
    a = cc.analyze(confounded_binary, "Z -> X; Z -> Y; X -> Y; W -> Y", treatment="X", outcome="Y")
    b = cc.analyze(
        confounded_binary.sample(frac=1.0, random_state=3).reset_index(drop=True),
        "Z -> X; Z -> Y; X -> Y; W -> Y",
        treatment="X",
        outcome="Y",
    )
    assert isinstance(a, cc.CausalResult)
    again = cc.analyze(
        confounded_binary, "Z -> X; Z -> Y; X -> Y; W -> Y", treatment="X", outcome="Y"
    )
    assert a.result_digest == again.result_digest
    # Row order changes the data digest (it is a content hash of the table as given).
    assert a.provenance.data_digest != b.provenance.data_digest
    body = a.model_dump(mode="json", exclude={"result_digest"})
    assert digest(body) == a.result_digest
    assert np.isfinite(a.primary.point)
