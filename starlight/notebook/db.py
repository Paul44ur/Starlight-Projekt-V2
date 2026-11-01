"""Opening a SQLite file of the notebook and bringing its tables up to date.

A database keeps its schema version in ``PRAGMA user_version``. ``migrations[n]`` is the
SQL that takes it from version n to n + 1, so a newer Starlight adds a step at the end and
an older database catches up step by step. A new database is created with incremental
auto-vacuum, so space freed by deleting rows can be given back without a full VACUUM.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from pathlib import Path

BUSY_SECONDS = 10.0  # another Starlight process (a terminal command) may be writing


def connect(path: Path, migrations: Sequence[str]) -> sqlite3.Connection:
    """The database at ``path`` (made if missing), its schema brought up to date."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=BUSY_SECONDS)
    try:
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version > len(migrations):
            raise sqlite3.DatabaseError(
                f"{path} was made by a newer Starlight (schema {version}); update Starlight"
            )
        if version == 0:  # (only takes effect before the first table exists)
            db.execute("PRAGMA auto_vacuum = INCREMENTAL")
        for number in range(version, len(migrations)):
            with db:  # each step, and its new version number, as one transaction
                db.executescript(f"BEGIN; {migrations[number]}; PRAGMA user_version = {number + 1}")
    except BaseException:
        db.close()
        raise
    return db


@contextmanager
def opened(path: Path, migrations: Sequence[str]) -> Iterator[sqlite3.Connection]:
    """``connect`` for a ``with`` block: committed at the end (rolled back on an error),
    then closed."""
    with closing(connect(path, migrations)) as db, db:
        yield db
