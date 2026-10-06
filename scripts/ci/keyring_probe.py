"""CI diagnostic (Windows): the test process crashes during garbage collection after the
first real keyring lookup, but keyring alone in a fresh process does not. Narrow down
what else must be loaded. Each case runs in its own process; a crash shows as a
non-zero exit code (0x80000003 = breakpoint exception)."""

import subprocess
import sys

KEYRING = (
    "from starlight.core.vault import KeyringBackend\n"
    "KeyringBackend().get('probe.nope')\n"
    "import gc; gc.collect(); print('collected')\n"
)
KERNEL = """
import asyncio, shutil, tempfile
from pathlib import Path
from starlight.core import Kernel, Paths
from starlight.core.vault import MemoryBackend
root = Path(tempfile.mkdtemp())
paths = Paths(root); paths.ensure()
if {modules}:
    for m in Path("modules").iterdir():
        if m.is_dir(): shutil.copytree(m, paths.modules / m.name)
paths.config_file.write_text("kill_switch:\\n  hotkey: null\\nstats:\\n  gpu_sample_interval: 0\\n")
async def main():
    async with Kernel(paths, vault_backend=MemoryBackend()) as k:
        await k.ask("hello")
asyncio.run(main())
"""
HEAVY = (
    "import uvicorn, fastapi, httpx, structlog, numpy, PIL, asyncssh, websockets\n"
    "import playwright.async_api, mss, pynput.keyboard\n"
)
CASES = {
    "keyring only": KEYRING,
    "heavy imports + keyring": HEAVY + KEYRING,
    "core, no modules + keyring": KERNEL.format(modules=False) + KEYRING,
    "core, all modules + keyring": KERNEL.format(modules=True) + KEYRING,
}
PYTEST = {
    "test 95 alone": ["tests/test_step10.py::test_logs_never_hold_secrets"],
    "test_step10.py": ["tests/test_step10.py"],
    "test_regressions + test 95": [
        "tests/test_regressions.py",
        "tests/test_step10.py::test_logs_never_hold_secrets",
    ],
}

for label, code in CASES.items():
    for faulthandler in (False, True):
        args = [sys.executable] + (["-X", "faulthandler"] if faulthandler else []) + ["-c", code]
        r = subprocess.run(args, capture_output=True, text=True, timeout=300)
        name = label + (" [faulthandler]" if faulthandler else "")
        print(f"{name:<45} exit 0x{r.returncode & 0xFFFFFFFF:08X}", flush=True)
        if r.returncode:
            print("   " + (r.stderr.strip() or r.stdout.strip())[-400:].replace("\n", "\n   "))
for label, targets in PYTEST.items():
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets],
        capture_output=True,
        text=True,
        timeout=600,
    )
    print(f"pytest {label:<38} exit 0x{r.returncode & 0xFFFFFFFF:08X}", flush=True)
    if r.returncode:
        print("   " + (r.stdout + r.stderr).strip()[-600:].replace("\n", "\n   "))
