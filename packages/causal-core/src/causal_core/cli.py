"""
Command line: ``causal-core analyze | schema | t0``.

    causal-core analyze data.csv --graph "Z -> X -> Y; Z -> Y" -t X -y Y
    causal-core analyze data.parquet --graph-file dag.txt -t X -y Y --json
    causal-core schema --out conformance/causal-claims/
    causal-core t0 --out benchmarks/t0/results/latest.json

``analyze`` exits 0 for a result, 2 for a refusal and 1 for an apparatus
failure, so a shell pipeline or CI job can branch on the outcome kind.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from causal_core.contract import CONTRACT_VERSION, AnalysisPolicy, json_schemas
from causal_core.pipeline import analyze
from causal_core.report import report

EXIT = {"result": 0, "refusal": 2, "apparatus_failure": 1}


def _read(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in (".json", ".jsonl"):
        return pd.read_json(path, lines=suffix == ".jsonl")
    return pd.read_csv(path)


def _analyze(args: argparse.Namespace) -> int:
    graph = Path(args.graph_file).read_text() if args.graph_file else args.graph
    if not graph:
        print("either --graph or --graph-file is required", file=sys.stderr)
        return 2
    query: dict[str, Any] = {}
    if args.design:
        query["design"] = args.design
    for key in (
        "adjust_for",
        "instruments",
        "negative_control_outcomes",
        "treatment_proxies",
        "outcome_proxies",
    ):
        val = getattr(args, key)
        if val:
            query[key] = tuple(v.strip() for v in val.split(",") if v.strip())
    policy = (
        AnalysisPolicy.model_validate_json(Path(args.policy).read_text())
        if args.policy
        else AnalysisPolicy()
    )
    out = analyze(
        _read(Path(args.data)),
        graph,
        treatment=args.treatment,
        outcome=args.outcome,
        policy=policy,
        **query,
    )
    if args.json:
        print(out.model_dump_json(indent=2))
    else:
        print(report(out))
    return EXIT[out.kind]


def _schema(args: argparse.Namespace) -> int:
    schemas = json_schemas()
    if not args.out:
        print(json.dumps(schemas, indent=2, sort_keys=True))
        return 0
    out = Path(args.out) / CONTRACT_VERSION
    out.mkdir(parents=True, exist_ok=True)
    for name, schema in schemas.items():
        (out / f"{name}.schema.json").write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n"
        )
    print(f"wrote {len(schemas)} schemas to {out}", file=sys.stderr)
    return 0


def _t0(args: argparse.Namespace) -> int:
    from causal_core.bench import t0

    argv = ["--out", args.out] if args.out else []
    return t0.main(argv)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="causal-core", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", help="estimate an effect, or say why not")
    a.add_argument("data", help="CSV, Parquet, JSON or JSONL")
    a.add_argument("-t", "--treatment", required=True)
    a.add_argument("-y", "--outcome", required=True)
    a.add_argument("--graph", help="graph text, e.g. 'Z -> X -> Y; Z -> Y'")
    a.add_argument("--graph-file")
    a.add_argument("--design", choices=["observational", "randomized"])
    a.add_argument("--adjust-for", dest="adjust_for")
    a.add_argument("--instruments")
    a.add_argument("--negative-control-outcomes", dest="negative_control_outcomes")
    a.add_argument("--treatment-proxies", dest="treatment_proxies")
    a.add_argument("--outcome-proxies", dest="outcome_proxies")
    a.add_argument("--policy", help="AnalysisPolicy as a JSON file")
    a.add_argument("--json", action="store_true", help="print the typed outcome as JSON")
    a.set_defaults(fn=_analyze)
    s = sub.add_parser("schema", help="print or write the contract's JSON Schemas")
    s.add_argument("--out")
    s.set_defaults(fn=_schema)
    t = sub.add_parser("t0", help="run the pre-registered T0 correctness gate")
    t.add_argument("--out")
    t.set_defaults(fn=_t0)
    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
