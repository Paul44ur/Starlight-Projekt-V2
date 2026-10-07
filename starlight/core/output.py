"""Where Starlight saves the files it makes for you.

By default that is a "Starlight" folder in your Documents. On Windows the Documents,
Desktop and Downloads folders are looked up the way Windows itself does, so they are right
also when OneDrive or a folder move put them somewhere else. On Linux the localized folders
from xdg-user-dirs count (e.g. ~/Dokumente).

The folder is never Starlight's own folder: that holds its settings, data and memory.
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path

from starlight.core.config import ConfigError

DEFAULT_NAME = "Starlight"
SPARE_NAME = "Starlight files"  # if Starlight itself is installed at Documents/Starlight

_NAMES = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads"}

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _DONT_VERIFY = 0x4000  # KF_FLAG_DONT_VERIFY: the location, even if it is missing now
    _FOLDER_IDS = {
        "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
        "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
        "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    }

    class _GUID(ctypes.Structure):
        _fields_ = (
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        )

    def _system_folder(name: str) -> Path | None:
        """SHGetKnownFolderPath: the folder Windows (and Explorer) really uses."""
        value = uuid.UUID(_FOLDER_IDS[name])
        guid = _GUID(
            value.time_low,
            value.time_mid,
            value.time_hi_version,
            (ctypes.c_ubyte * 8)(*value.bytes[8:]),
        )
        get = ctypes.windll.shell32.SHGetKnownFolderPath
        get.argtypes = [
            ctypes.POINTER(_GUID),
            wintypes.DWORD,
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        get.restype = ctypes.c_long
        found = ctypes.c_wchar_p()
        if get(ctypes.byref(guid), _DONT_VERIFY, None, ctypes.byref(found)) != 0:
            return None
        try:
            return Path(found.value) if found.value else None
        finally:
            free = ctypes.windll.ole32.CoTaskMemFree
            free.argtypes = [ctypes.c_void_p]
            free(ctypes.cast(found, ctypes.c_void_p))

else:
    _XDG_KEYS = {
        "desktop": "XDG_DESKTOP_DIR",
        "documents": "XDG_DOCUMENTS_DIR",
        "downloads": "XDG_DOWNLOAD_DIR",
    }

    def _system_folder(name: str) -> Path | None:
        """The folder from ~/.config/user-dirs.dirs (xdg-user-dirs), if it names one."""
        home = Path.home()
        config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
        try:
            text = (config / "user-dirs.dirs").read_text(encoding="utf-8")
        except OSError:
            return None
        match = re.search(rf'^{_XDG_KEYS[name]}="([^"]*)"', text, re.MULTILINE)
        if match is None:
            return None
        folder = Path(match.group(1).replace("$HOME", str(home)))
        # xdg-user-dirs points a folder it does not use at $HOME itself
        return folder if folder.is_absolute() and folder != home else None


def system_folder(name: str) -> Path:
    """The user's Desktop, Documents or Downloads folder."""
    return _system_folder(name) or Path.home() / _NAMES[name]


def documents_folder() -> Path:
    return system_folder("documents")


def known_folders() -> dict[str, Path]:
    return {name: system_folder(name) for name in _NAMES}


def overlaps(a: Path, b: Path) -> bool:
    """True if one folder is, or is inside, the other."""
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def resolve_output_folder(setting: str, root: Path) -> Path:
    """The folder for files Starlight makes for you (``output.folder`` in the config;
    empty means Documents/Starlight). It never overlaps Starlight's own folder ``root``."""
    root = root.resolve()
    if setting.strip():
        folder = Path(os.path.expandvars(setting.strip())).expanduser()
        if not folder.is_absolute():
            folder = Path.home() / folder
        folder = folder.resolve()
        if overlaps(folder, root):
            raise ConfigError(
                f"output.folder ({folder}) overlaps Starlight's own folder ({root}), which "
                "holds its settings and data. Choose a folder outside it."
            )
        return folder
    documents = documents_folder()
    for name in (DEFAULT_NAME, SPARE_NAME):
        folder = (documents / name).resolve()
        if not overlaps(folder, root):
            return folder
    raise ConfigError(
        f"Starlight is installed in {root}, where it would save your files. Set "
        "output.folder in config/starlight.local.yaml to a folder outside it."
    )


def describe_output_folder(setting: str, root: Path) -> str:
    """The output folder for display, or what is wrong with the setting (never raises)."""
    try:
        return str(resolve_output_folder(setting, root))
    except ConfigError as exc:
        return f"not usable - {exc}"
