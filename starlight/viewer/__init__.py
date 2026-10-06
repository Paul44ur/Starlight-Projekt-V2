"""Statistics viewer (Step 10): a separate app that only reads data/stats.db, so it keeps
working when the AI is stopped. Run it with ``starlight viewer``."""

from starlight.viewer.app import StatsReader, create_app

__all__ = ["StatsReader", "create_app"]
