"""A small always-on-top banner shown while Starlight controls mouse and keyboard."""

from __future__ import annotations

from starlight.core.banner import banner

TEXT = "Starlight is controlling mouse and keyboard  ·  {hotkey} or a screen corner stops it"


class Indicator:
    """The banner as one module instance sees it."""

    def __init__(self, enabled: bool = True, hotkey: str = "Ctrl+Alt+J", linger: float = 2.0):
        self.enabled = enabled
        self.text = TEXT.format(hotkey=hotkey)
        self.linger = linger

    def show(self) -> None:
        if self.enabled:
            banner().show(self.text)

    def hide(self, immediately: bool = False) -> None:
        if self.enabled:
            banner().hide(after=0.0 if immediately else self.linger)

    def close(self) -> None:
        self.hide(immediately=True)
