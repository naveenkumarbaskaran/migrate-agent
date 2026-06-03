"""SchemaDiffer: parse CREATE TABLE statements and produce a structured diff."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ColumnDef:
    """Represents one column in a CREATE TABLE statement."""
    name: str
    definition: str  # everything after the column name (type, constraints)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ColumnDef):
            return NotImplemented
        return (
            self.name.lower() == other.name.lower()
            and self.definition.strip().upper() == other.definition.strip().upper()
        )


@dataclass
class TableDef:
    """Represents one CREATE TABLE block."""
    name: str
    columns: dict[str, ColumnDef] = field(default_factory=dict)
    raw: str = ""


@dataclass
class SchemaDiff:
    """The structural difference between two schemas."""
    added_tables: list[str] = field(default_factory=list)
    dropped_tables: list[str] = field(default_factory=list)
    added_columns: dict[str, list[str]] = field(default_factory=dict)
    dropped_columns: dict[str, list[str]] = field(default_factory=dict)
    altered_columns: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not any([
            self.added_tables,
            self.dropped_tables,
            self.added_columns,
            self.dropped_columns,
            self.altered_columns,
        ])

    def __str__(self) -> str:
        if self.is_empty():
            return "No structural differences detected."

        lines: list[str] = []

        if self.added_tables:
            lines.append("ADDED TABLES:")
            for t in self.added_tables:
                lines.append(f"  + {t}")

        if self.dropped_tables:
            lines.append("DROPPED TABLES:")
            for t in self.dropped_tables:
                lines.append(f"  - {t}")

        if self.added_columns:
            lines.append("ADDED COLUMNS:")
            for table, cols in self.added_columns.items():
                for col in cols:
                    lines.append(f"  + {table}.{col}")

        if self.dropped_columns:
            lines.append("DROPPED COLUMNS:")
            for table, cols in self.dropped_columns.items():
                for col in cols:
                    lines.append(f"  - {table}.{col}")

        if self.altered_columns:
            lines.append("ALTERED COLUMNS (old -> new):")
            for table, changes in self.altered_columns.items():
                for col_name, old_def, new_def in changes:
                    lines.append(f"  ~ {table}.{col_name}: [{old_def}] -> [{new_def}]")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# Matches: CREATE TABLE [IF NOT EXISTS] [schema.]table_name (...)
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:[\w`\"\[\]]+\.)?([\w`\"\[\]]+)\s*"
    r"(\([^;]+?\))\s*;",
    re.IGNORECASE | re.DOTALL,
)

# Split comma-separated column/constraint lines inside a CREATE TABLE body.
_COLUMN_SPLIT_RE = re.compile(r",\s*(?=[^()]*(?:\([^()]*\)[^()]*)*$)")

# Lines that are table-level constraints, not column defs.
_CONSTRAINT_RE = re.compile(
    r"^\s*(PRIMARY\s+KEY|UNIQUE|CHECK|FOREIGN\s+KEY|CONSTRAINT|INDEX|KEY)\b",
    re.IGNORECASE,
)


def _strip_quotes(name: str) -> str:
    """Remove surrounding backticks, double-quotes, or square brackets."""
    return name.strip("`\"[]")


def _parse_column_line(line: str) -> Optional[ColumnDef]:
    """Parse a single column definition line from a CREATE TABLE body.

    Returns None for table-level constraints.
    """
    stripped = line.strip()
    if not stripped or _CONSTRAINT_RE.match(stripped):
        return None

    # Column name is the first identifier (possibly quoted).
    m = re.match(r'^([`"\[]?[\w]+[`"\]]?)\s+(.*)', stripped, re.DOTALL)
    if not m:
        return None

    name = _strip_quotes(m.group(1))
    definition = m.group(2).strip().rstrip(",")
    return ColumnDef(name=name, definition=definition)


def _parse_schema(sql: str) -> dict[str, TableDef]:
    """Extract all CREATE TABLE definitions from a SQL string.

    Returns a dict mapping lower-case table name -> TableDef.
    """
    tables: dict[str, TableDef] = {}

    for match in _CREATE_TABLE_RE.finditer(sql):
        raw_name = _strip_quotes(match.group(1))
        body = match.group(2)  # everything inside the outer parens
        raw_sql = match.group(0)

        # Remove the outer parens and split into lines.
        inner = body.strip()
        if inner.startswith("("):
            inner = inner[1:]
        if inner.endswith(")"):
            inner = inner[:-1]

        # Use regex-based split that respects nested parens.
        column_lines = _COLUMN_SPLIT_RE.split(inner)

        columns: dict[str, ColumnDef] = {}
        for line in column_lines:
            col = _parse_column_line(line)
            if col:
                columns[col.name.lower()] = col

        tdef = TableDef(name=raw_name, columns=columns, raw=raw_sql)
        tables[raw_name.lower()] = tdef

    return tables


# ---------------------------------------------------------------------------
# Differ
# ---------------------------------------------------------------------------

class SchemaDiffer:
    """Compare two SQL schema strings and produce a SchemaDiff.

    Usage
    -----
    >>> differ = SchemaDiffer()
    >>> diff = differ.diff(current_sql, target_sql)
    >>> print(diff)
    """

    def diff(self, current_sql: str, target_sql: str) -> SchemaDiff:
        """Return a SchemaDiff describing changes from *current_sql* to *target_sql*."""
        current_tables = _parse_schema(current_sql)
        target_tables = _parse_schema(target_sql)

        result = SchemaDiff()

        current_names = set(current_tables.keys())
        target_names = set(target_tables.keys())

        # Tables added in target.
        for name in sorted(target_names - current_names):
            result.added_tables.append(target_tables[name].name)

        # Tables dropped from current.
        for name in sorted(current_names - target_names):
            result.dropped_tables.append(current_tables[name].name)

        # Tables present in both -- compare columns.
        for name in sorted(current_names & target_names):
            cur_cols = current_tables[name].columns
            tgt_cols = target_tables[name].columns
            table_display = target_tables[name].name

            cur_col_names = set(cur_cols.keys())
            tgt_col_names = set(tgt_cols.keys())

            # New columns.
            added = sorted(tgt_col_names - cur_col_names)
            if added:
                result.added_columns[table_display] = [
                    tgt_cols[c].name for c in added
                ]

            # Removed columns.
            dropped = sorted(cur_col_names - tgt_col_names)
            if dropped:
                result.dropped_columns[table_display] = [
                    cur_cols[c].name for c in dropped
                ]

            # Altered columns (same name, different definition).
            altered: list[tuple[str, str, str]] = []
            for col_name in sorted(cur_col_names & tgt_col_names):
                cur_col = cur_cols[col_name]
                tgt_col = tgt_cols[col_name]
                if cur_col != tgt_col:
                    altered.append(
                        (tgt_col.name, cur_col.definition, tgt_col.definition)
                    )
            if altered:
                result.altered_columns[table_display] = altered

        return result

    def diff_text(self, current_sql: str, target_sql: str) -> str:
        """Return a human-readable diff summary string."""
        return str(self.diff(current_sql, target_sql))
