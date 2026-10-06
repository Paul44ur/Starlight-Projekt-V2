"""CI diagnostic (Windows): which keyring code path crashes during garbage collection?
Each case runs in its own process; a crash shows as a non-zero exit code."""

import subprocess
import sys

FORCE_CTYPES = "import sys; sys.modules['cffi'] = None\n"  # win32ctypes then uses ctypes
CASES = {
    "lookup (cffi)": (
        "import gc, keyring\nkeyring.get_password('starlight-ci', 'nope')\ngc.collect()\n"
    ),
    "round trip (cffi)": (
        "import gc, keyring\nkeyring.set_password('starlight-ci', 'k', 'v')\n"
        "assert keyring.get_password('starlight-ci', 'k') == 'v'\n"
        "keyring.delete_password('starlight-ci', 'k')\ngc.collect()\n"
    ),
    "plain cffi": "import gc, cffi\nf = cffi.FFI()\nf.cdef('int x;')\ndel f\ngc.collect()\n",
}

failed = False
for name, code in CASES.items():
    variants = {name: code}
    if "(cffi)" in name:
        variants[name.replace("cffi", "ctypes")] = FORCE_CTYPES + code
    for label, source in variants.items():
        codes = []
        for _ in range(3):
            result = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True)
            codes.append(f"0x{result.returncode & 0xFFFFFFFF:08X}")
            if result.returncode:
                failed = True
                print(f"  {label}: stderr tail: {result.stderr.strip()[-300:]}")
        print(f"{label:<22} exit codes: {', '.join(codes)}")
print("some case crashed" if failed else "no case crashed")
