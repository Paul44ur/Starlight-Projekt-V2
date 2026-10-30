"""Game mode: while a game runs, Starlight's model pauses so the game gets the whole graphics
card (a 14B model holds about 10 GB of video memory); when the game closes, it comes back.

A game is a program from one of the game folders (Steam's steamapps/common, Epic Games, GOG
...) or one you name. The pause itself is a file the launcher watches (see
starlight/core/modelpause.py): it stops the model server, or unloads the model in LM
Studio, and starts it again when the pause ends.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, tool
from starlight.core.jsonfile import load_json, save_json
from starlight.core.modelpause import pause_model, read_pause, resume_model

BY_GAME_MODE = "game mode"


class Config(BaseModel):
    auto: bool = True
    check_every: float = Field(default=10, ge=2)
    folders: list[str] = Field(default_factory=lambda: ["steamapps/common", "Epic Games"])
    games: list[str] = Field(default_factory=list)
    ignore: list[str] = Field(default_factory=list)


def _norm(path: str) -> str:
    return path.replace("\\", "/").lower()


def find_game(
    processes: Iterable[tuple[str, str]], folders: list[str], games: list[str], ignore: list[str]
) -> str:
    """The first running game among (name, exe path) pairs, by its program name; "" when
    none runs."""
    named = {g.lower() for g in games}
    skipped = {i.lower() for i in ignore}
    places = [f"/{_norm(f).strip('/')}/" for f in folders if f.strip("/\\ ")]
    for name, exe in processes:
        program = (name or exe.replace("\\", "/").rsplit("/", 1)[-1]).lower()
        if not program or program in skipped:
            continue
        if program in named or any(place in _norm(exe) for place in places):
            return name or program
    return ""


def running_processes() -> list[tuple[str, str]]:
    """(name, exe path) of the programs running now; empty without psutil."""
    try:
        import psutil
    except ImportError:
        return []
    found = []
    for process in psutil.process_iter(["name", "exe"]):
        info = process.info
        found.append((str(info.get("name") or ""), str(info.get("exe") or "")))
    return found


class GameModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.file = ctx.data_dir / "games.json"
        self.playing = ""  # the game that paused the model, while it runs
        if self.config.auto:
            ctx.every(self.config.check_every, self.check, name="game-check", first_delay=5)

    def _games(self) -> list[str]:
        return sorted({*self.config.games, *load_json(self.file, [])}, key=str.lower)

    def _find(self) -> str:
        return find_game(
            running_processes(), self.config.folders, self._games(), self.config.ignore
        )

    async def check(self) -> None:
        """One look: a game started (pause the model) or the one that did ended (resume).
        Resumed by you while a game runs, the model stays until that game has closed."""
        game = await asyncio.to_thread(self._find)
        pause = read_pause(self.ctx.paths)
        if not game:
            ended, self.playing = self.playing, ""
            if pause is not None and pause.get("by") == BY_GAME_MODE:
                resume_model(self.ctx.paths)
                await self.ctx.deliver(
                    f"{ended or 'The game'} closed: Starlight's model is loading again "
                    "(about a minute).",
                    "Game mode",
                )
            return
        if pause is not None or game == self.playing:
            return  # paused already, or you resumed it while this game runs
        self.playing = game
        pause_model(self.ctx.paths, f"{game} is running", BY_GAME_MODE)
        await self.ctx.deliver(
            f"{game} started: Starlight's model is paused so the game gets the whole "
            "graphics card. It comes back when you close the game.",
            "Game mode",
        )

    @tool
    def status(self) -> dict[str, Any]:
        """Whether the model is paused for a game (or by you), and which game runs now."""
        pause = read_pause(self.ctx.paths)
        return {
            "paused": pause is not None,
            "reason": (pause or {}).get("reason", ""),
            "game_running": self._find(),
            "automatic": self.config.auto,
        }

    @tool
    def pause_model(self, reason: str = "you asked") -> str:
        """Pause the model now to free the graphics card (until game.resume_model)."""
        pause_model(self.ctx.paths, reason, "you")
        return "Paused: the model leaves the graphics card within a few seconds."

    @tool
    def resume_model(self) -> str:
        """End a pause: the model loads again (about a minute)."""
        if not resume_model(self.ctx.paths):
            return "The model was not paused."
        return "The model is loading again (about a minute)."

    @tool
    def add_game(self, program: str) -> str:
        """Count a program as a game (its file name, e.g. "minecraft.exe")."""
        name = program.strip().replace("\\", "/").rsplit("/", 1)[-1]
        if not name:
            return "Give the program's file name, e.g. minecraft.exe."
        games = load_json(self.file, [])
        if name.lower() not in {g.lower() for g in games}:
            games.append(name)
            save_json(self.file, games)
        return f"{name} counts as a game now."

    @tool
    def remove_game(self, program: str) -> str:
        """Stop counting a program you added as a game."""
        games = [g for g in load_json(self.file, []) if g.lower() != program.strip().lower()]
        save_json(self.file, games)
        return f"{program} no longer counts as a game."

    @tool
    def games(self) -> dict[str, Any]:
        """The programs and folders that count as games."""
        return {"programs": self._games(), "folders": self.config.folders}
