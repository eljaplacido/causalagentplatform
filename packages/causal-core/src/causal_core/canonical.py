"""
Canonical JSON (RFC 8785, JCS) and the digests built on it.

Two implementations that agree on these bytes agree on every digest, which is
what lets a result produced in Python be verified from TypeScript, or replayed
next year. Numbers follow the ECMAScript ``Number.prototype.toString`` rules
that JCS mandates; NaN and infinities are rejected rather than encoded.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel


def _es_number(x: float) -> str:
    """Format a float the way ECMAScript's Number::toString does."""
    if math.isnan(x) or math.isinf(x):
        raise ValueError("JCS cannot encode NaN or infinity")
    if x == 0:
        return "0"  # also covers -0
    if x.is_integer() and abs(x) < 2**53:
        return str(int(x))
    sign = "-" if x < 0 else ""
    # repr gives the shortest round-tripping digits; re-lay them out per ES rules.
    r = repr(abs(x))
    if "e" in r:
        mant, exp_s = r.split("e")
        exp = int(exp_s)
    else:
        mant, exp = r, 0
    if "." in mant:
        int_part, frac_part = mant.split(".")
    else:
        int_part, frac_part = mant, ""
    digits = (int_part + frac_part).lstrip("0")
    # n: position of the decimal point relative to the start of `digits`.
    lead_zeros = len(int_part + frac_part) - len((int_part + frac_part).lstrip("0"))
    n = len(int_part) + exp - lead_zeros
    digits = digits.rstrip("0") or "0"
    k = len(digits)
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + digits
    e = n - 1
    e_str = f"e+{e}" if e >= 0 else f"e-{-e}"
    if k == 1:
        return sign + digits + e_str
    return sign + digits[0] + "." + digits[1:] + e_str


def _encode(value: Any, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, str):
        out.append(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, (int, np.integer)):
        # JSON numbers are IEEE doubles under JCS; beyond 2**53 go through that form.
        iv = int(value)
        out.append(str(iv) if abs(iv) <= 2**53 else _es_number(float(iv)))
    elif isinstance(value, (float, np.floating)):
        out.append(_es_number(float(value)))
    elif isinstance(value, BaseModel):
        _encode(value.model_dump(mode="json"), out)
    elif isinstance(value, Mapping):
        keys = sorted(value, key=lambda k: str(k).encode("utf-16-be"))
        out.append("{")
        for i, k in enumerate(keys):
            if i:
                out.append(",")
            out.append(json.dumps(str(k), ensure_ascii=False))
            out.append(":")
            _encode(value[k], out)
        out.append("}")
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        out.append("[")
        for i, v in enumerate(value):
            if i:
                out.append(",")
            _encode(v, out)
        out.append("]")
    else:
        raise TypeError(f"cannot canonicalize {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize ``value`` as RFC 8785 canonical JSON."""
    out: list[str] = []
    _encode(value, out)
    return "".join(out)


def digest(value: Any) -> str:
    """``sha256:<hex>`` of the canonical JSON form of ``value``."""
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def data_digest(frame: pd.DataFrame) -> str:
    """Digest of a data frame's content, independent of row index and column order.

    Columns are taken in sorted name order. Numeric columns are hashed as
    little-endian float64 bytes (so int and float encodings of the same values
    collide on purpose); anything else through its canonical JSON form.
    """
    h = hashlib.sha256()
    h.update(f"rows={len(frame)};".encode())
    for col in sorted(frame.columns, key=str):
        h.update(canonical_json(str(col)).encode("utf-8"))
        series = frame[col]
        if pd.api.types.is_bool_dtype(series) or pd.api.types.is_numeric_dtype(series):
            arr = np.ascontiguousarray(series.to_numpy(dtype="float64", na_value=np.nan))
            h.update(b"f8:")
            h.update(arr.astype("<f8").tobytes())
        else:
            h.update(b"json:")
            h.update(
                canonical_json([None if pd.isna(v) else str(v) for v in series]).encode("utf-8")
            )
    return "sha256:" + h.hexdigest()
