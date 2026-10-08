"""The user's profile: a few facts every answer should know (name, language, home place,
time zone, preferences). It is one ordinary memory file, ``memory/profile.md``, so it can
be read and fixed by hand or on the Memory page:

    ---
    topic: Profile
    ...
    ---
    Name: Paul
    Language: German
    Home: Vienna, Austria

Unlike other memories it is not recalled by similarity: it goes into every request (see
``starlight/core/situation.py``), and facts are never merged into it by accident.
"""

from __future__ import annotations

import re

from starlight.memory.store import MemoryEntry, MemoryStore

PROFILE_ID = "profile"
FIELDS = {
    "name": "Name",
    "language": "Language",
    "home": "Home",
    "timezone": "Time zone",
    "preferences": "Preferences",
}
ALIASES = {
    "city": "home",
    "place": "home",
    "location": "home",
    "lives in": "home",
    "time zone": "timezone",
    "tz": "timezone",
    "lang": "language",
    "preference": "preferences",
    "likes": "preferences",
}
LONGEST = {"preferences": 1000}  # characters a field may hold; others 300
LINE = re.compile(r"^\s*([A-Za-z][A-Za-z ]{0,20}?)\s*:\s*(.*\S)\s*$")


def field_key(name: str) -> str | None:
    key = name.strip().lower().replace("_", " ")
    key = ALIASES.get(key, key)
    key = key.replace(" ", "")
    for known, label in FIELDS.items():
        if key in (known, label.lower().replace(" ", "")):
            return known
    return None


class Profile:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def read(self) -> dict[str, str]:
        """Field -> value. Lines that are not "Field: value" count as preferences."""
        entry = self.store.get(PROFILE_ID)
        if entry is None:
            return {}
        values: dict[str, str] = {}
        extra: list[str] = []
        for line in entry.content.splitlines():
            match = LINE.match(line)
            key = field_key(match.group(1)) if match else None
            if match and key:
                values[key] = match.group(2)
            elif line.strip():
                extra.append(line.strip().lstrip("-* "))
        if extra:
            values["preferences"] = "; ".join([*filter(None, [values.get("preferences")]), *extra])
        return values

    def set(self, field: str, value: str) -> dict[str, str]:
        """Set (or with an empty value, clear) one field. Returns the whole profile."""
        key = field_key(field)
        if key is None:
            raise ValueError(f"unknown profile field '{field}' (known: {', '.join(FIELDS)})")
        values = self.read()
        value = " ".join(value.split())[: LONGEST.get(key, 300)]
        if value:
            values[key] = value
        else:
            values.pop(key, None)
        self.write(values)
        return values

    def write(self, values: dict[str, str]) -> None:
        lines = [f"{label}: {values[key]}" for key, label in FIELDS.items() if values.get(key)]
        self.store.write(
            MemoryEntry(
                id=PROFILE_ID,
                topic="Profile",
                content="\n".join(lines),
                source="user",
                tags=["profile"],
            )
        )

    def describe(self) -> str:
        values = self.read()
        return "; ".join(f"{FIELDS[k].lower()}: {v}" for k, v in values.items() if k in FIELDS)
