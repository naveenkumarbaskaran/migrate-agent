"""Command-line interface for migrate-agent."""

from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from migrate_agent.agent import MigrateAgent
from migrate_agent.differ import SchemaDiffer

console = Console()


@click.group()
def cli() -> None:
    """migrate-agent -- AI-powered database migration generator."""


@cli.command()
@click.option(
    "--from",
    "from_schema",
    required=True,
    metavar="PATH",
    help="Path to the current (source) SQL schema file.",
)
@click.option(
    "--to",
    "to_schema",
    required=True,
    metavar="PATH",
    help="Path to the target SQL schema file.",
)
@click.option(
    "--dialect",
    default="postgres",
    show_default=True,
    type=click.Choice(["postgres", "mysql", "sqlite"], case_sensitive=False),
    help="SQL dialect to use when generating the migration.",
)
@click.option(
    "--output",
    "-o",
    default="migration.sql",
    show_default=True,
    metavar="PATH",
    help="Output path for the generated migration file.",
)
@click.option(
    "--api-key",
    envvar="ANTHROPIC_API_KEY",
    default=None,
    help="Anthropic API key (defaults to ANTHROPIC_API_KEY env var).",
)
@click.option(
    "--diff-only",
    is_flag=True,
    default=False,
    help="Print a schema diff and exit without calling the AI.",
)
def plan(
    from_schema: str,
    to_schema: str,
    dialect: str,
    output: str,
    api_key: str | None,
    diff_only: bool,
) -> None:
    """Generate an UP/DOWN migration SQL script between two schema versions.

    Examples:

    \b
        migrate-agent plan --from current.sql --to target.sql
        migrate-agent plan --from current.sql --to target.sql --dialect mysql -o v2.sql
        migrate-agent plan --from current.sql --to target.sql --diff-only
    """
    # ------------------------------------------------------------------ diff
    console.print(
        Panel(
            Text.from_markup(
                f"[bold]from:[/bold] {escape(from_schema)}  "
                f"[bold]to:[/bold] {escape(to_schema)}  "
                f"[bold]dialect:[/bold] {dialect.upper()}  "
                f"[bold]output:[/bold] {escape(output)}"
            ),
            title="[cyan]migrate-agent[/cyan]",
            border_style="cyan",
        )
    )

    # Show schema diff.
    try:
        differ = SchemaDiffer()
        from pathlib import Path
        current_sql = Path(from_schema).read_text(encoding="utf-8")
        target_sql = Path(to_schema).read_text(encoding="utf-8")
        diff = differ.diff(current_sql, target_sql)
        diff_text = str(diff)
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        if diff_only:
            sys.exit(1)
        diff_text = "(could not compute diff -- files will be read by the agent)"

    console.print(
        Panel(
            diff_text or "No structural differences detected.",
            title="[yellow]Schema Diff[/yellow]",
            border_style="yellow",
        )
    )

    if diff_only:
        return

    # ------------------------------------------------------------------ agent
    console.print("[cyan]Calling Claude to generate migration...[/cyan]")

    try:
        agent = MigrateAgent(api_key=api_key)
        summary = agent.plan(
            from_schema=from_schema,
            to_schema=to_schema,
            dialect=dialect,
            output=output,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Agent error:[/red] {exc}")
        sys.exit(1)

    console.print(
        Panel(
            escape(summary),
            title="[green]Agent Summary[/green]",
            border_style="green",
        )
    )

    # Show the generated file.
    try:
        generated = Path(output).read_text(encoding="utf-8")
        console.print(
            Panel(
                Syntax(generated, "sql", theme="monokai", line_numbers=True),
                title=f"[green]{escape(output)}[/green]",
                border_style="green",
            )
        )
    except FileNotFoundError:
        console.print(
            f"[yellow]Note:[/yellow] output file '{escape(output)}' was not created "
            "(agent may have returned SQL in the summary above)."
        )


@cli.command()
@click.option(
    "--from",
    "from_schema",
    required=True,
    metavar="PATH",
    help="Path to the current SQL schema file.",
)
@click.option(
    "--to",
    "to_schema",
    required=True,
    metavar="PATH",
    help="Path to the target SQL schema file.",
)
def diff(
    from_schema: str,
    to_schema: str,
) -> None:
    """Show a structural diff between two schema files without generating SQL.

    Example:

    \b
        migrate-agent diff --from current.sql --to target.sql
    """
    try:
        from pathlib import Path
        current_sql = Path(from_schema).read_text(encoding="utf-8")
        target_sql = Path(to_schema).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    differ = SchemaDiffer()
    schema_diff = differ.diff(current_sql, target_sql)
    console.print(
        Panel(
            str(schema_diff),
            title="[yellow]Schema Diff[/yellow]",
            border_style="yellow",
        )
    )


def main() -> None:
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
