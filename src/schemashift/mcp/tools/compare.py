"""Deterministic comparison of old-schema and migrated query results."""

from __future__ import annotations

import math
import re
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from time import monotonic
from typing import Any

from schemashift.guardrails.policies import (
    DEFAULT_ABSOLUTE_TOLERANCE,
    DEFAULT_RELATIVE_TOLERANCE,
)
from schemashift.mcp.context import ToolContext

from .sql_executor import (
    RawQueryResult,
    _json_value,
    execute_raw_readonly_sql,
    raw_result_to_dict,
)


@dataclass(frozen=True, slots=True)
class ComparisonPolicy:
    """Explicit comparison choices; ``ordered=None`` infers from ORDER BY."""

    ordered: bool | None = None
    absolute_tolerance: float = DEFAULT_ABSOLUTE_TOLERANCE
    relative_tolerance: float = DEFAULT_RELATIVE_TOLERANCE
    key_columns: tuple[str, ...] = ()
    sample_limit: int | None = None

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.absolute_tolerance)
            or not math.isfinite(self.relative_tolerance)
            or self.absolute_tolerance < 0
            or self.relative_tolerance < 0
        ):
            raise ValueError("Comparison tolerances must be finite and non-negative")
        if self.sample_limit is not None and self.sample_limit < 0:
            raise ValueError("sample_limit must be non-negative")

    @classmethod
    def from_value(cls, value: ComparisonPolicy | Mapping[str, Any] | None) -> ComparisonPolicy:
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("policy must be a ComparisonPolicy, mapping, or None")
        ordered_value = value.get("ordered")
        if ordered_value is not None and not isinstance(ordered_value, bool):
            raise ValueError("ordered must be a boolean or null")
        ordered = ordered_value
        keys = value.get("key_columns") or ()
        if isinstance(keys, str):
            keys = (keys,)
        return cls(
            ordered=ordered,
            absolute_tolerance=float(
                value.get(
                    "absolute_tolerance",
                    value.get("abs_tolerance", DEFAULT_ABSOLUTE_TOLERANCE),
                )
            ),
            relative_tolerance=float(
                value.get(
                    "relative_tolerance",
                    value.get("rel_tolerance", DEFAULT_RELATIVE_TOLERANCE),
                )
            ),
            key_columns=tuple(str(key) for key in keys),
            sample_limit=(
                int(value["sample_limit"]) if value.get("sample_limit") is not None else None
            ),
        )

    def to_dict(self, *, ordered: bool, sample_limit: int) -> dict[str, Any]:
        data = asdict(self)
        data["ordered"] = ordered
        data["key_columns"] = list(self.key_columns)
        data["sample_limit"] = sample_limit
        return data


_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("boolean", re.compile(r"^(?:BOOL|BOOLEAN)$", re.I)),
    (
        "integer",
        re.compile(r"^(?:U?TINYINT|U?SMALLINT|U?INTEGER|U?INT|U?BIGINT|HUGEINT)$", re.I),
    ),
    ("decimal", re.compile(r"^(?:DECIMAL|NUMERIC)(?:\(.*\))?$", re.I)),
    ("float", re.compile(r"^(?:FLOAT|REAL|DOUBLE)(?: PRECISION)?$", re.I)),
    ("date", re.compile(r"^DATE$", re.I)),
    (
        "timestamp",
        re.compile(
            r"^(?:TIMESTAMP|DATETIME)"
            r"(?:_S|_MS|_NS| WITH TIME ZONE| WITHOUT TIME ZONE)?$",
            re.I,
        ),
    ),
    ("time", re.compile(r"^TIME(?: WITH TIME ZONE| WITHOUT TIME ZONE)?$", re.I)),
    (
        "text",
        re.compile(r"^(?:VARCHAR|CHAR|BPCHAR|TEXT|STRING)(?:\(.*\))?$", re.I),
    ),
    ("binary", re.compile(r"^(?:BLOB|BYTEA|BINARY|VARBINARY)(?:\(.*\))?$", re.I)),
)


def canonical_type(type_name: str) -> str:
    normalized = " ".join(str(type_name).strip().upper().split())
    for family, pattern in _TYPE_PATTERNS:
        if pattern.match(normalized):
            return family
    # Preserve complex DuckDB types exactly after whitespace normalization.
    return normalized.lower()


def _schema(columns: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    return [
        {
            "name": str(column["name"]),
            "type": str(column["type"]),
            "canonical_type": canonical_type(str(column["type"])),
        }
        for column in columns
    ]


def _stable_value(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_stable_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_stable_value(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((str(key), _stable_value(item)) for key, item in value.items()))
    if isinstance(value, bytearray):
        return bytes(value)
    try:
        hash(value)
        return value
    except TypeError:
        return repr(value)


def _values_equal(
    old: Any,
    new: Any,
    family: str,
    policy: ComparisonPolicy,
) -> bool:
    if old is None or new is None:
        return old is None and new is None
    if family == "float":
        try:
            old_float, new_float = float(old), float(new)
        except (TypeError, ValueError, OverflowError):
            return False
        if math.isnan(old_float) or math.isnan(new_float):
            return math.isnan(old_float) and math.isnan(new_float)
        return math.isclose(
            old_float,
            new_float,
            rel_tol=policy.relative_tolerance,
            abs_tol=policy.absolute_tolerance,
        )
    # Integers and Decimal values deliberately use exact equality.
    return old == new


def _rows_equal(
    old: Sequence[Any],
    new: Sequence[Any],
    families: Sequence[str],
    policy: ComparisonPolicy,
) -> bool:
    return len(old) == len(new) and all(
        _values_equal(old_value, new_value, family, policy)
        for old_value, new_value, family in zip(old, new, families, strict=True)
    )


def _non_float_key(row: Sequence[Any], families: Sequence[str]) -> tuple[Any, ...]:
    return tuple(
        ("__float__",) if family == "float" else _stable_value(value)
        for value, family in zip(row, families, strict=True)
    )


class _ComparisonTimedOut(RuntimeError):
    """Raised internally when deterministic result matching exceeds its budget."""


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and monotonic() >= deadline:
        raise _ComparisonTimedOut


def _float_token(value: Any) -> tuple[str, float | str | None]:
    """Return a stable token for grouping values from a FLOAT result column."""

    if value is None:
        return ("null", None)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        # DuckDB should never produce this for a FLOAT result, but keeping it
        # unmatchable preserves _values_equal's behavior if an adapter does.
        return ("invalid", repr(value))
    if math.isnan(number):
        return ("nan", None)
    if math.isinf(number):
        return ("infinity", "+" if number > 0 else "-")
    # Normalizing signed zero is safe because 0.0 == -0.0.
    return ("finite", 0.0 if number == 0 else number)


@dataclass(slots=True)
class _RowGroup:
    rows: list[tuple[Any, ...]]
    float_tokens: tuple[tuple[str, float | str | None], ...]

    @property
    def representative(self) -> tuple[Any, ...]:
        return self.rows[0]


@dataclass(slots=True)
class _FloatColumnIndex:
    finite_values: list[float]
    finite_group_ids: list[int]
    special_group_ids: dict[tuple[str, float | str | None], list[int]]


@dataclass(slots=True)
class _FlowEdge:
    target: int
    reverse: int
    capacity: int
    original_capacity: int


def _group_float_rows(
    rows: Sequence[tuple[Any, ...]],
    float_indexes: Sequence[int],
) -> list[_RowGroup]:
    grouped: dict[tuple[tuple[str, float | str | None], ...], list[tuple[Any, ...]]] = {}
    for row in rows:
        signature = tuple(_float_token(row[index]) for index in float_indexes)
        grouped.setdefault(signature, []).append(row)
    return [_RowGroup(items, signature) for signature, items in grouped.items()]


def _float_column_index(groups: Sequence[_RowGroup], token_index: int) -> _FloatColumnIndex:
    finite: list[tuple[float, int]] = []
    special: dict[tuple[str, float | str | None], list[int]] = defaultdict(list)
    for group_id, group in enumerate(groups):
        token = group.float_tokens[token_index]
        if token[0] == "finite":
            finite.append((float(token[1]), group_id))
        else:
            special[token].append(group_id)
    finite.sort()
    return _FloatColumnIndex(
        finite_values=[value for value, _ in finite],
        finite_group_ids=[group_id for _, group_id in finite],
        special_group_ids=dict(special),
    )


def _candidate_group_ids(
    token: tuple[str, float | str | None],
    index: _FloatColumnIndex,
    policy: ComparisonPolicy,
) -> Sequence[int]:
    if token[0] != "finite":
        # Nulls, NaNs, and infinities only match their identical category.
        # Invalid converted values deliberately have no candidates.
        if token[0] == "invalid":
            return ()
        return index.special_group_ids.get(token, ())

    value = float(token[1])
    relative = policy.relative_tolerance
    if relative >= 1:
        # With a relative tolerance >= 1 there is no useful finite range bound.
        # The deadline still bounds this intentionally pathological policy.
        return index.finite_group_ids

    # This is a conservative bound for math.isclose. If |candidate| is the
    # larger magnitude, then |candidate| <= |value| / (1 - relative).
    relative_radius = relative * abs(value) / (1 - relative) if relative else 0.0
    radius = max(policy.absolute_tolerance, relative_radius)
    lower = bisect_left(index.finite_values, value - radius)
    upper = bisect_right(index.finite_values, value + radius)
    return index.finite_group_ids[lower:upper]


def _add_flow_edge(
    graph: list[list[_FlowEdge]], source: int, target: int, capacity: int
) -> _FlowEdge:
    forward = _FlowEdge(target, len(graph[target]), capacity, capacity)
    backward = _FlowEdge(source, len(graph[source]), 0, 0)
    graph[source].append(forward)
    graph[target].append(backward)
    return forward


def _maximum_group_matching(
    old_groups: Sequence[_RowGroup],
    new_groups: Sequence[_RowGroup],
    families: Sequence[str],
    policy: ComparisonPolicy,
    *,
    deadline: float | None,
) -> tuple[list[int], list[int]]:
    """Compute an exact capacitated bipartite matching between row groups.

    Candidate discovery uses a sorted index on the most selective floating
    column. Dinic's algorithm then finds a maximum matching, avoiding the
    order-dependent false mismatches of greedy candidate removal.
    """

    float_indexes = [index for index, family in enumerate(families) if family == "float"]
    indexes = [
        _float_column_index(new_groups, token_index) for token_index in range(len(float_indexes))
    ]

    # Select the column expected to generate the fewest candidate edges. This
    # keeps the normal 10,000-row case close to O(n log n), while the deadline
    # bounds deliberately dense tolerance relations.
    best_token_index = 0
    best_candidate_count: int | None = None
    for token_index, index in enumerate(indexes):
        _check_deadline(deadline)
        candidate_count = 0
        for group in old_groups:
            _check_deadline(deadline)
            candidate_count += len(
                _candidate_group_ids(group.float_tokens[token_index], index, policy)
            )
        if best_candidate_count is None or candidate_count < best_candidate_count:
            best_token_index = token_index
            best_candidate_count = candidate_count

    old_offset = 1
    new_offset = old_offset + len(old_groups)
    sink = new_offset + len(new_groups)
    graph: list[list[_FlowEdge]] = [[] for _ in range(sink + 1)]
    source = 0
    for old_group_id, group in enumerate(old_groups):
        _add_flow_edge(graph, source, old_offset + old_group_id, len(group.rows))
    for new_group_id, group in enumerate(new_groups):
        _add_flow_edge(graph, new_offset + new_group_id, sink, len(group.rows))

    edge_references: list[list[_FlowEdge]] = [[] for _ in old_groups]
    candidate_index = indexes[best_token_index]
    for old_group_id, old_group in enumerate(old_groups):
        candidates = _candidate_group_ids(
            old_group.float_tokens[best_token_index], candidate_index, policy
        )
        for new_group_id in candidates:
            _check_deadline(deadline)
            new_group = new_groups[new_group_id]
            if not _rows_equal(
                old_group.representative,
                new_group.representative,
                families,
                policy,
            ):
                continue
            edge_references[old_group_id].append(
                _add_flow_edge(
                    graph,
                    old_offset + old_group_id,
                    new_offset + new_group_id,
                    min(len(old_group.rows), len(new_group.rows)),
                )
            )

    levels = [-1] * len(graph)

    def build_levels() -> bool:
        levels[:] = [-1] * len(graph)
        levels[source] = 0
        queue = deque([source])
        while queue:
            node = queue.popleft()
            for edge in graph[node]:
                _check_deadline(deadline)
                if edge.capacity > 0 and levels[edge.target] < 0:
                    levels[edge.target] = levels[node] + 1
                    queue.append(edge.target)
        return levels[sink] >= 0

    cursors = [0] * len(graph)

    def send_flow(node: int, available: int) -> int:
        _check_deadline(deadline)
        if node == sink:
            return available
        while cursors[node] < len(graph[node]):
            edge = graph[node][cursors[node]]
            if edge.capacity > 0 and levels[edge.target] == levels[node] + 1:
                sent = send_flow(edge.target, min(available, edge.capacity))
                if sent:
                    edge.capacity -= sent
                    graph[edge.target][edge.reverse].capacity += sent
                    return sent
            cursors[node] += 1
        return 0

    maximum_possible = min(
        sum(len(group.rows) for group in old_groups),
        sum(len(group.rows) for group in new_groups),
    )
    matched = 0
    while matched < maximum_possible and build_levels():
        cursors[:] = [0] * len(graph)
        while matched < maximum_possible:
            sent = send_flow(source, maximum_possible - matched)
            if not sent:
                break
            matched += sent

    matched_old = [0] * len(old_groups)
    matched_new = [0] * len(new_groups)
    for old_group_id, edges in enumerate(edge_references):
        for edge in edges:
            used = edge.original_capacity - edge.capacity
            matched_old[old_group_id] += used
            matched_new[edge.target - new_offset] += used
    return matched_old, matched_new


def _unordered_diff(
    old_rows: Sequence[tuple[Any, ...]],
    new_rows: Sequence[tuple[Any, ...]],
    families: Sequence[str],
    policy: ComparisonPolicy,
    *,
    deadline: float | None = None,
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Return an exact multiset diff, using maximum matching for float rows."""

    old_buckets: dict[tuple[Any, ...], list[tuple[Any, ...]]] = defaultdict(list)
    new_buckets: dict[tuple[Any, ...], list[tuple[Any, ...]]] = defaultdict(list)
    for row in old_rows:
        old_buckets[_non_float_key(row, families)].append(row)
    for row in new_rows:
        new_buckets[_non_float_key(row, families)].append(row)

    has_float = "float" in families
    missing: list[tuple[Any, ...]] = []
    extra: list[tuple[Any, ...]] = []
    for key, old_bucket in old_buckets.items():
        _check_deadline(deadline)
        new_bucket = new_buckets.pop(key, [])
        if not new_bucket:
            missing.extend(old_bucket)
            continue
        if not has_float:
            common = min(len(old_bucket), len(new_bucket))
            missing.extend(old_bucket[common:])
            extra.extend(new_bucket[common:])
            continue

        float_indexes = [index for index, family in enumerate(families) if family == "float"]
        old_groups = _group_float_rows(old_bucket, float_indexes)
        new_groups = _group_float_rows(new_bucket, float_indexes)
        matched_old, matched_new = _maximum_group_matching(
            old_groups,
            new_groups,
            families,
            policy,
            deadline=deadline,
        )
        for group, count in zip(old_groups, matched_old, strict=True):
            missing.extend(group.rows[count:])
        for group, count in zip(new_groups, matched_new, strict=True):
            extra.extend(group.rows[count:])

    for new_bucket in new_buckets.values():
        _check_deadline(deadline)
        extra.extend(new_bucket)
    return missing, extra


def _ordered_diff(
    old_rows: Sequence[tuple[Any, ...]],
    new_rows: Sequence[tuple[Any, ...]],
    families: Sequence[str],
    policy: ComparisonPolicy,
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    common = min(len(old_rows), len(new_rows))
    for index in range(common):
        if not _rows_equal(old_rows[index], new_rows[index], families, policy):
            mismatches.append(
                {
                    "position": index,
                    "old": [_json_value(value) for value in old_rows[index]],
                    "new": [_json_value(value) for value in new_rows[index]],
                }
            )
    return mismatches


def _duplicate_report(
    rows: Sequence[tuple[Any, ...]],
    column_names: Sequence[str],
    key_columns: Sequence[str],
    sample_limit: int,
) -> dict[str, Any]:
    if key_columns:
        indexes = [column_names.index(key) for key in key_columns]
        label = list(key_columns)
    else:
        indexes = list(range(len(column_names)))
        label = list(column_names)
    counts: Counter[tuple[Any, ...]] = Counter(
        tuple(_stable_value(row[index]) for index in indexes) for row in rows
    )
    duplicates = [(key, count) for key, count in counts.items() if count > 1]
    return {
        "key_columns": label,
        "duplicate_key_count": len(duplicates),
        "duplicate_row_excess": sum(count - 1 for _, count in duplicates),
        "samples": [
            {
                "key": [_json_value(value) for value in key],
                "count": count,
            }
            for key, count in duplicates[:sample_limit]
        ],
    }


def _null_counts(rows: Sequence[tuple[Any, ...]], column_names: Sequence[str]) -> dict[str, int]:
    return {
        name: sum(row[index] is None for row in rows) for index, name in enumerate(column_names)
    }


def _numeric_aggregates(
    rows: Sequence[tuple[Any, ...]],
    schema: Sequence[Mapping[str, str]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, column in enumerate(schema):
        if column["canonical_type"] not in {"integer", "decimal", "float"}:
            continue
        values = [row[index] for row in rows if row[index] is not None]
        if not values:
            result[column["name"]] = {"count": 0, "min": None, "max": None, "sum": None}
            continue
        total: Any
        if column["canonical_type"] == "float":
            total = math.fsum(float(value) for value in values)
        else:
            total = sum(values)
        result[column["name"]] = {
            "count": len(values),
            "min": _json_value(min(values)),
            "max": _json_value(max(values)),
            "sum": _json_value(total),
        }
    return result


def _execution_summary(result: RawQueryResult) -> dict[str, Any]:
    payload = raw_result_to_dict(result)
    # Full rows are already represented in bounded mismatch samples below.
    payload.pop("rows", None)
    return payload


def compare_results(
    old_database_id: str,
    old_sql: str,
    new_database_id: str,
    new_sql: str,
    policy: ComparisonPolicy | Mapping[str, Any] | None = None,
    *,
    context: ToolContext,
) -> dict[str, Any]:
    """Execute and compare two registered query results.

    Duplicate multiplicity is preserved.  Rows are treated as an unordered
    multiset unless policy requires ordering or either query has a top-level
    ``ORDER BY``.  Tolerance is applied only to columns whose DuckDB result type
    canonicalizes to floating point.
    """

    try:
        comparison_policy = ComparisonPolicy.from_value(policy)
    except (TypeError, ValueError) as exc:
        error = {"code": "invalid_policy", "message": str(exc)}
        return {
            "ok": False,
            "equivalent": False,
            "verdict": "error",
            "truncated": False,
            "execution_ok": False,
            "old_execution_ok": False,
            "new_execution_ok": False,
            "issues": [error],
            "errors": [error],
            "error": error,
        }

    requested_sample_limit = (
        context.mismatch_sample_limit
        if comparison_policy.sample_limit is None
        else comparison_policy.sample_limit
    )
    sample_limit = min(requested_sample_limit, context.mismatch_sample_limit)
    old = execute_raw_readonly_sql(old_database_id, old_sql, context=context)
    new = execute_raw_readonly_sql(new_database_id, new_sql, context=context)
    inferred_ordered = old.safety.has_order_by or new.safety.has_order_by
    ordered = (
        comparison_policy.ordered if comparison_policy.ordered is not None else inferred_ordered
    )
    base: dict[str, Any] = {
        "old_database_id": str(old_database_id),
        "new_database_id": str(new_database_id),
        "ordered": ordered,
        "policy": comparison_policy.to_dict(ordered=ordered, sample_limit=sample_limit),
        "old_execution": _execution_summary(old),
        "new_execution": _execution_summary(new),
        "old_execution_ok": old.ok,
        "new_execution_ok": new.ok,
        "execution_ok": old.ok and new.ok,
        "truncated": old.truncated or new.truncated,
    }

    if not old.ok or not new.ok:
        issues = []
        if not old.ok:
            issues.append(
                {
                    "code": "old_execution_failed",
                    "message": (old.error or {}).get("message", "Old query failed"),
                }
            )
        if not new.ok:
            issues.append(
                {
                    "code": "new_execution_failed",
                    "message": (new.error or {}).get("message", "New query failed"),
                }
            )
        return {
            **base,
            "ok": False,
            "equivalent": False,
            "verdict": "error",
            "issues": issues,
            "errors": issues,
            "error": issues[0] if issues else None,
        }

    old_schema, new_schema = _schema(old.columns), _schema(new.columns)
    old_signature = [(column["name"], column["canonical_type"]) for column in old_schema]
    new_signature = [(column["name"], column["canonical_type"]) for column in new_schema]
    schema_matches = old_signature == new_signature
    schema_report = {
        "matches": schema_matches,
        "old_columns": old_schema,
        "new_columns": new_schema,
    }

    issues: list[dict[str, str]] = []
    if not schema_matches:
        issues.append(
            {
                "code": "schema_mismatch",
                "message": "Result column names, order, or canonical types differ",
            }
        )

    if comparison_policy.key_columns:
        old_names = [column["name"] for column in old_schema]
        missing_keys = [key for key in comparison_policy.key_columns if key not in old_names]
        if missing_keys:
            issues.append(
                {
                    "code": "unknown_key_column",
                    "message": "Unknown comparison key columns: " + ", ".join(missing_keys),
                }
            )

    if old.truncated or new.truncated:
        issues.append(
            {
                "code": "truncated_results",
                "message": "At least one result exceeded the row cap; equivalence is inconclusive",
            }
        )

    can_compare_rows = schema_matches and not any(
        issue["code"] == "unknown_key_column" for issue in issues
    )
    families = [column["canonical_type"] for column in old_schema]
    column_names = [column["name"] for column in old_schema]
    missing_rows: list[tuple[Any, ...]] = []
    extra_rows: list[tuple[Any, ...]] = []
    position_mismatches: list[dict[str, Any]] = []
    comparison_deadline = monotonic() + context.query_timeout_seconds
    try:
        if can_compare_rows:
            if ordered:
                position_mismatches = _ordered_diff(old.rows, new.rows, families, comparison_policy)
                if len(old.rows) > len(new.rows):
                    missing_rows = list(old.rows[len(new.rows) :])
                elif len(new.rows) > len(old.rows):
                    extra_rows = list(new.rows[len(old.rows) :])
            else:
                missing_rows, extra_rows = _unordered_diff(
                    old.rows,
                    new.rows,
                    families,
                    comparison_policy,
                    deadline=comparison_deadline,
                )
    except _ComparisonTimedOut:
        timeout_issue = {
            "code": "comparison_timeout",
            "message": (
                "Result comparison exceeded the configured timeout of "
                f"{context.query_timeout_seconds:g} seconds"
            ),
        }
        timeout_issues = [*issues, timeout_issue]
        return {
            **base,
            "ok": False,
            "equivalent": False,
            "verdict": "error",
            "schema": schema_report,
            "row_counts": {
                "old": old.row_count,
                "new": new.row_count,
                "delta": new.row_count - old.row_count,
            },
            "issues": timeout_issues,
            "errors": timeout_issues,
            "error": timeout_issue,
        }

    row_mismatch = bool(missing_rows or extra_rows or position_mismatches)
    if can_compare_rows and row_mismatch:
        issues.append(
            {
                "code": "row_mismatch",
                "message": "Query results differ after applying the comparison policy",
            }
        )

    duplicates = {"old": None, "new": None}
    nulls = {"old": {}, "new": {}, "delta": {}}
    aggregates = {"old": {}, "new": {}}
    if can_compare_rows:
        duplicates = {
            "old": _duplicate_report(
                old.rows,
                column_names,
                comparison_policy.key_columns,
                sample_limit,
            ),
            "new": _duplicate_report(
                new.rows,
                column_names,
                comparison_policy.key_columns,
                sample_limit,
            ),
        }
        old_nulls, new_nulls = (
            _null_counts(old.rows, column_names),
            _null_counts(new.rows, column_names),
        )
        nulls = {
            "old": old_nulls,
            "new": new_nulls,
            "delta": {name: new_nulls[name] - old_nulls[name] for name in column_names},
        }
        aggregates = {
            "old": _numeric_aggregates(old.rows, old_schema),
            "new": _numeric_aggregates(new.rows, new_schema),
        }

    equivalent = can_compare_rows and not row_mismatch and not old.truncated and not new.truncated
    verdict = (
        "pass" if equivalent else ("inconclusive" if old.truncated or new.truncated else "mismatch")
    )
    return {
        **base,
        "ok": True,
        "equivalent": equivalent,
        "verdict": verdict,
        "schema": schema_report,
        "row_counts": {
            "old": old.row_count,
            "new": new.row_count,
            "delta": new.row_count - old.row_count,
        },
        "comparison": {
            "missing_count": len(missing_rows),
            "extra_count": len(extra_rows),
            "position_mismatch_count": len(position_mismatches),
            "missing_rows": [
                [_json_value(value) for value in row] for row in missing_rows[:sample_limit]
            ],
            "extra_rows": [
                [_json_value(value) for value in row] for row in extra_rows[:sample_limit]
            ],
            "position_mismatches": position_mismatches[:sample_limit],
        },
        "duplicates": duplicates,
        "nulls": nulls,
        "numeric_aggregates": aggregates,
        "issues": issues,
        "errors": issues,
        "error": issues[0] if issues else None,
    }


__all__ = ["ComparisonPolicy", "canonical_type", "compare_results"]
