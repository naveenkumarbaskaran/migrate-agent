"""migrate-agent: AI-powered database migration script generator."""

from migrate_agent.agent import MigrateAgent
from migrate_agent.differ import SchemaDiffer

__all__ = ["MigrateAgent", "SchemaDiffer"]
__version__ = "0.1.0"
