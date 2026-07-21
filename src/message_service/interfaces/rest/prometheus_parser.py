"""DOM-free parser for the Prometheus text exposition format (L3-DASH-036).

Turns the text ``GET /metrics`` serves into a structured, JSON-serializable
metric model the embedded dashboard (``GET /admin/metrics``) renders. Pure and
server-side (Python) so it is unit-testable without a browser; the client-side
SVG renderer consumes the JSON this produces and needs no parsing logic of its
own.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# A sample line: ``name{label="v",...} value [timestamp]`` or ``name value``.
# The value is a single non-whitespace token; an optional trailing timestamp
# (which client_python does not emit by default, but the exposition format
# permits) is captured separately and ignored — folding it into the value would
# make ``float()`` raise and 500 the metrics dashboard.
_SAMPLE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>.*)\})?"
    r"\s+(?P<value>\S+)(?:\s+(?P<timestamp>\S+))?\s*$"
)
# One ``key="value"`` label pair; the value may contain escaped quotes/backslashes.
_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')
# Single-pass unescape of a label value: ``\`` followed by ``\``, ``"``, or ``n``.
_LABEL_UNESCAPE_RE = re.compile(r"\\(.)")
_LABEL_ESCAPES = {"\\": "\\", '"': '"', "n": "\n"}

_HISTOGRAM_SUFFIXES = ("_bucket", "_sum", "_count")


@dataclass(frozen=True)
class Sample:
    """One metric sample: the full sample name, its label set, and its value."""

    name: str
    labels: dict[str, str]
    value: float


@dataclass(frozen=True)
class MetricFamily:
    """A metric family: its ``# TYPE`` name/type, ``# HELP`` text, and samples."""

    name: str
    type: str
    help: str
    samples: tuple[Sample, ...]


def _parse_value(raw: str) -> float:
    """Parse a Prometheus sample value, tolerating ``+Inf`` / ``-Inf`` / ``NaN``."""
    token = raw.strip()
    lowered = token.lower()
    if lowered in ("+inf", "inf"):
        return math.inf
    if lowered == "-inf":
        return -math.inf
    if lowered == "nan":
        return math.nan
    return float(token)


def _unescape_label_value(value: str) -> str:
    r"""Unescape a Prometheus label value in a single left-to-right pass.

    The exposition format escapes only ``\\`` (backslash), ``\"`` (quote), and
    ``\n`` (newline). A single-pass regex is required: chained ``str.replace``
    calls corrupt adjacent escapes — e.g. the escaped form ``\\n`` (an escaped
    backslash followed by a literal ``n``, decoding to ``\`` + ``n``) would be
    turned into a newline, because the ``\\`` → ``\`` replacement runs before the
    ``\n`` → newline replacement and leaves a spurious ``\n`` for the latter to
    eat. Consuming each backslash-escape exactly once avoids that. An
    unrecognized escape is left verbatim (backslash included).
    """
    return _LABEL_UNESCAPE_RE.sub(lambda m: _LABEL_ESCAPES.get(m.group(1), m.group(0)), value)


def _parse_labels(raw: str | None) -> dict[str, str]:
    """Parse the ``{...}`` label block into a dict, unescaping quotes/backslashes."""
    if not raw:
        return {}
    return {key: _unescape_label_value(value) for key, value in _LABEL_RE.findall(raw)}


def _family_for(sample_name: str, types: dict[str, str]) -> str | None:
    """Return the family name a sample belongs to, or ``None`` if unknown.

    A sample maps to a family with an exact ``# TYPE`` name, or — for histogram
    families — to the base name after stripping a ``_bucket``/``_sum``/``_count``
    suffix.
    """
    if sample_name in types:
        return sample_name
    for suffix in _HISTOGRAM_SUFFIXES:
        if sample_name.endswith(suffix):
            base = sample_name[: -len(suffix)]
            if types.get(base) == "histogram":
                return base
    return None


def parse_exposition(text: str) -> tuple[MetricFamily, ...]:
    """Parse a Prometheus text exposition into a tuple of metric families.

    Args:
        text: The exposition text (what ``GET /metrics`` returns).

    Returns:
        One :class:`MetricFamily` per declared ``# TYPE`` family, in declaration
        order, each carrying its ``# HELP`` text and every sample that belongs to
        it. Samples whose family cannot be determined are grouped under an
        implicit family named after the sample with type ``"untyped"``.
    """
    types: dict[str, str] = {}
    helps: dict[str, str] = {}
    order: list[str] = []
    samples: dict[str, list[Sample]] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            parts = line.split(maxsplit=3)
            if len(parts) >= 4 and parts[1] == "HELP":
                helps[parts[2]] = parts[3]
            elif len(parts) >= 4 and parts[1] == "TYPE":
                name, metric_type = parts[2], parts[3]
                if name not in types:
                    order.append(name)
                types[name] = metric_type
                samples.setdefault(name, [])
            continue

        match = _SAMPLE_RE.match(line)
        if match is None:
            continue
        sample = Sample(
            name=match.group("name"),
            labels=_parse_labels(match.group("labels")),
            value=_parse_value(match.group("value")),
        )
        family = _family_for(sample.name, types)
        if family is None:
            # No declared TYPE for this sample — surface it under an implicit
            # family so no data is silently dropped.
            family = sample.name
            if family not in samples:
                order.append(family)
                types.setdefault(family, "untyped")
        samples.setdefault(family, []).append(sample)

    return tuple(
        MetricFamily(
            name=name,
            type=types.get(name, "untyped"),
            help=helps.get(name, ""),
            samples=tuple(samples.get(name, ())),
        )
        for name in order
    )


__all__ = ["MetricFamily", "Sample", "parse_exposition"]
