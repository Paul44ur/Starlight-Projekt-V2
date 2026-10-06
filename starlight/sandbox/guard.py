"""Started first inside the process sandbox. Installs audit hooks, then runs the target:

python -I _starlight_guard.py main.py [args]
python -I _starlight_guard.py -m pytest -q
"""

import json
import os
import runpy
import sys

_settings = json.loads(os.environ.get("STARLIGHT_SANDBOX", "{}"))
_DENY = [os.path.normcase(p) for p in _settings.get("deny", [])]
_NETWORK = bool(_settings.get("network", False))
_WORKDIR = os.path.normcase(os.path.realpath(_settings.get("workdir") or os.getcwd()))
_BLOCKED_MODULES = {"keyring", "win32cred", "secretstorage", "jeepney", "starlight"}
_NO_NETWORK = {"socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.bind"}
_NO_PROGRAMS = {
    "subprocess.Popen",
    "os.system",
    "os.exec",
    "os.posix_spawn",
    "os.spawn",
    "os.startfile",
    "os.fork",
    "os.forkpty",
}
_FILE_EVENTS = {"open", "os.listdir", "os.scandir", "os.remove", "os.rename", "shutil.rmtree"}
_SSH = os.path.normcase(os.path.join(os.path.expanduser("~"), ".ssh"))


def _denied_path(path: object) -> bool:
    if not isinstance(path, (str, bytes, os.PathLike)):
        return False
    try:
        full = os.path.normcase(os.path.realpath(os.fsdecode(path)))
    except (TypeError, ValueError):
        return False
    if full == _WORKDIR or full.startswith(_WORKDIR + os.sep):
        return False  # the sandbox's own folder, even if it lives under a denied one
    roots = [*_DENY, _SSH]
    return any(full == root or full.startswith(root + os.sep) for root in roots)


def _hook(event: str, args: tuple[object, ...]) -> None:
    if event in _NO_PROGRAMS:
        raise PermissionError(f"sandbox: starting programs is not allowed ({event})")
    if not _NETWORK and event in _NO_NETWORK:
        raise PermissionError("sandbox: network access is not allowed")
    if event == "import" and args and str(args[0]).split(".")[0] in _BLOCKED_MODULES:
        raise PermissionError(f"sandbox: importing {args[0]} is not allowed")
    if event in _FILE_EVENTS and args and _denied_path(args[0]):
        raise PermissionError("sandbox: that path is outside the sandbox")


if _settings.get("memory_mb") and os.name == "posix":
    import resource

    _limit = int(_settings["memory_mb"]) * 2**20  # set here, before any user code runs
    resource.setrlimit(resource.RLIMIT_AS, (_limit, _limit))

sys.addaudithook(_hook)
sys.path.insert(0, os.getcwd())  # -I leaves the working folder off the path
sys.argv = sys.argv[1:]
if sys.argv and sys.argv[0] == "-m":
    sys.argv = sys.argv[1:]
    runpy.run_module(sys.argv[0], run_name="__main__", alter_sys=True)
elif sys.argv:
    runpy.run_path(sys.argv[0], run_name="__main__")
