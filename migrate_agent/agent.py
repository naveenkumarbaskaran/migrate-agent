"""MigrateAgent: Uses Claude to generate safe database migration scripts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import anthropic

from migrate_agent.differ import SchemaDiffer

# Dialects we know how to validate destructive operations for.
_DESTRUCTIVE_PATTERNS: dict[str, list[str]] = {
    "postgres": [
        r"\bDROP\s+TABLE\b",
        r"\bDROP\s+COLUMN\b",
        r"\bTRUNCATE\b",
        r"\bDROP\s+INDEX\b",
        r"\bDROP\s+CONSTRAINT\b",
        r"\bALTER\s+COLUMN\b.*\bTYPE\b",
    ],
    "mysql": [
        r"\bDROP\s+TABLE\b",
        r"\bDROP\s+COLUMN\b",
        r"\bTRUNCATE\b",
        r"\bDROP\s+INDEX\b",
        r"\bMODIFY\s+COLUMN\b",
        r"\bCHANGE\s+COLUMN\b",
    ],
    "sqlite": [
        r"\bDROP\s+TABLE\b",
        r"\bDROP\s+COLUMN\b",
        r"\bDROP\s+INDEX\b",
    ],
}


def _read_file(path: str) -> str:
    """Read a file from disk and return its text content."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return p.read_text(encoding="utf-8")


def _write_file(path: str, content: str) -> str:
    """Write text content to a file on disk."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Successfully wrote {len(content)} bytes to {path}"


def _validate_sql(sql: str, dialect: str) -> dict[str, Any]:
    """Validate SQL for destructive operations and basic syntax issues.

    Returns a dict with keys:
      - valid (bool)
      - warnings (list[str]): destructive operations detected
      - errors (list[str]): hard errors (e.g. unbalanced parens)
    """
    dialect_lower = dialect.lower()
    patterns = _DESTRUCTIVE_PATTERNS.get(
        dialect_lower, _DESTRUCTIVE_PATTERNS["postgres"]
    )

    warnings: list[str] = []
    errors: list[str] = []

    # Check for destructive patterns (case-insensitive).
    for pattern in patterns:
        matches = re.findall(pattern, sql, flags=re.IGNORECASE)
        if matches:
            warnings.append(
                f"Destructive operation detected: '{matches[0].strip()}' "
                f"-- verify this is intentional before applying."
            )

    # Rudimentary syntax checks.
    open_parens = sql.count("(")
    close_parens = sql.count(")")
    if open_parens != close_parens:
        errors.append(
            f"Unbalanced parentheses: {open_parens} '(' vs {close_parens} ')'"
        )

    # Each statement should end with a semicolon.
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    if not statements:
        errors.append("No SQL statements found.")

    return {
        "valid": len(errors) == 0,
        "warnings": warnings,
        "errors": errors,
        "statement_count": len(statements),
        "dialect": dialect,
    }


# Tool definitions sent to the Claude API.
_TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": (
            "Read a file from disk. Use this to load SQL schema files "
            "before generating migrations."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file to read.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write text content to a file. Use this to save the finished "
            "migration SQL script."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to write to.",
                },
                "content": {
                    "type": "string",
                    "description": "Text content to write.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "validate_sql",
        "description": (
            "Validate a SQL string for destructive operations (DROP TABLE, "
            "DROP COLUMN, TRUNCATE, type changes, etc.) and basic syntax. "
            "Always call this before writing the final migration file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The SQL to validate.",
                },
                "dialect": {
                    "type": "string",
                    "description": "SQL dialect: 'postgres', 'mysql', or 'sqlite'.",
                    "enum": ["postgres", "mysql", "sqlite"],
                },
            },
            "required": ["sql", "dialect"],
        },
    },
]


def _dispatch_tool(name: str, tool_input: dict[str, Any]) -> str:
    """Execute a tool call and return its result as a string."""
    if name == "read_file":
        try:
            return _read_file(tool_input["path"])
        except FileNotFoundError as exc:
            return f"ERROR: {exc}"
        except OSError as exc:
            return f"ERROR reading file: {exc}"

    if name == "write_file":
        try:
            return _write_file(tool_input["path"], tool_input["content"])
        except OSError as exc:
            return f"ERROR writing file: {exc}"

    if name == "validate_sql":
        result = _validate_sql(tool_input["sql"], tool_input["dialect"])
        return json.dumps(result, indent=2)

    return f"ERROR: Unknown tool '{name}'"


class MigrateAgent:
    """AI agent that generates UP/DOWN migration SQL between two schema versions.

    The agent uses Claude claude-sonnet-4-6 with three tools:
      - read_file   -- load schema files from disk
      - write_file  -- save the finished migration
      - validate_sql -- detect destructive operations before writing

    Example
    -------
    >>> agent = MigrateAgent()
    >>> result = agent.plan(
    ...     from_schema="current.sql",
    ...     to_schema="target.sql",
    ...     dialect="postgres",
    ...     output="migration.sql",
    ... )
    >>> print(result)
    """

    MODEL = "claude-sonnet-4-6"
    MAX_TOKENS = 4096
    MAX_ITERATIONS = 20  # guard against infinite tool loops

    def __init__(self, api_key: str | None = None) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._differ = SchemaDiffer()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(
        self,
        from_schema: str,
        to_schema: str,
        dialect: str = "postgres",
        output: str = "migration.sql",
    ) -> str:
        """Generate an UP/DOWN migration from *from_schema* to *to_schema*.

        Parameters
        ----------
        from_schema:
            Path to the current (source) SQL schema file.
        to_schema:
            Path to the target SQL schema file.
        dialect:
            SQL dialect -- 'postgres', 'mysql', or 'sqlite'.
        output:
            Path where the migration file will be written.

        Returns
        -------
        str
            A human-readable summary of what was generated.
        """
        # Pre-compute a structural diff to give the agent helpful context.
        try:
            current_sql = _read_file(from_schema)
            target_sql = _read_file(to_schema)
            diff_summary = self._differ.diff(current_sql, target_sql)
        except FileNotFoundError:
            # The agent will handle missing files via read_file tool calls.
            diff_summary = "(diff unavailable -- agent will read files directly)"

        system_prompt = (
            f"You are a database migration expert specialising in {dialect.upper()} SQL. "
            "Your task is to produce a complete, safe migration script that transforms "
            "the CURRENT schema into the TARGET schema.\n\n"
            "Rules:\n"
            "1. The output file MUST contain clearly separated sections:\n"
            "   -- === UP MIGRATION ===  (forward: current -> target)\n"
            "   -- === DOWN MIGRATION === (rollback: target -> current)\n"
            "2. Every statement must end with a semicolon.\n"
            "3. Use IF EXISTS / IF NOT EXISTS guards where appropriate.\n"
            "4. ALWAYS call validate_sql on the complete SQL before calling write_file.\n"
            "5. If validate_sql reports destructive operations, add a prominent SQL "
            "   comment warning immediately above each destructive statement.\n"
            "6. Do NOT truncate or summarise -- write the full, runnable SQL.\n"
        )

        user_message = (
            f"Generate a migration script from `{from_schema}` to `{to_schema}` "
            f"for the **{dialect}** dialect.\n\n"
            f"Schema diff summary (pre-computed):\n{diff_summary}\n\n"
            f"Steps:\n"
            f"1. Use read_file to load both schema files.\n"
            f"2. Analyse the differences carefully.\n"
            f"3. Write the UP and DOWN migration SQL.\n"
            f"4. Call validate_sql on the combined SQL.\n"
            f"5. Call write_file to save the result to `{output}`.\n"
            f"6. Summarise what you did."
        )

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_message}
        ]

        # Agentic loop -- keep going until Claude finishes (end_turn) or we
        # hit the safety iteration cap.
        for _iteration in range(self.MAX_ITERATIONS):
            response = self._client.messages.create(
                model=self.MODEL,
                max_tokens=self.MAX_TOKENS,
                system=system_prompt,
                tools=_TOOLS,
                messages=messages,
            )

            # Append Claude's response to history.
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                # Extract the final text block.
                for block in response.content:
                    if hasattr(block, "text"):
                        return block.text
                return "Migration completed (no summary text returned)."

            if response.stop_reason != "tool_use":
                return (
                    f"Agent stopped unexpectedly: stop_reason={response.stop_reason!r}"
                )

            # Execute every tool call Claude requested.
            tool_results: list[dict[str, Any]] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                tool_output = _dispatch_tool(block.name, block.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": tool_output,
                    }
                )

            messages.append({"role": "user", "content": tool_results})

        return f"ERROR: Agent exceeded maximum iterations ({self.MAX_ITERATIONS})."
