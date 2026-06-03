# migrate-agent

> Database migration agent — generate, validate, and rollback schema changes safely.

## What it does

Analyses your current schema and desired state, generates migration scripts (Alembic/Flyway/raw SQL), validates against production constraints, and builds a rollback plan.

## Quickstart

```bash
pip install migrate-agent-ai
```

## Usage

```bash
migrate-agent plan --from db.current.sql --to db.target.sql --dialect postgres
```

## Part of

This repo is listed in [awesome-agents](https://github.com/naveenkumarbaskaran/awesome-agents) — a curated collection of 60+ AI agent apps you can actually run.

## License

MIT

