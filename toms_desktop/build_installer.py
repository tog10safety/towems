"""Build the application and the self-contained per-user Windows installer."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PYINSTALLER = ROOT / ".venv" / "Scripts" / "pyinstaller.exe"


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    if not PYTHON.exists() or not PYINSTALLER.exists():
        print("Create the virtual environment and install requirements-dev.txt first.")
        return 1
    run([str(PYINSTALLER), "--noconfirm", "--clean", "towems_armory.spec"])
    app = ROOT / "dist" / "TOWEMS-Armory.exe"
    if not app.exists():
        print("Application executable was not produced.")
        return 1
    run([str(PYINSTALLER), "--noconfirm", "--clean", "installer.spec"])
    setup = ROOT / "dist" / "TOWEMS-Armory-Setup.exe"
    if not setup.exists():
        print("Installer executable was not produced.")
        return 1
    print(f"Application: {app} ({app.stat().st_size / 1024 / 1024:.1f} MiB)")
    print(f"Installer:   {setup} ({setup.stat().st_size / 1024 / 1024:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
