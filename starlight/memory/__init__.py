"""Long-term memory (Step 5): one Markdown file per fact or topic in ``memory/``,
a local vector index rebuilt from those files, recall before each answer and writing
after each task."""

from starlight.memory.service import Memory
from starlight.memory.store import MemoryEntry, MemoryStore

__all__ = ["Memory", "MemoryEntry", "MemoryStore"]
