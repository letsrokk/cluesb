from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import platform
import subprocess
import sys


def build_command(root: Path, *, executable: str = sys.executable) -> list[str]:
    root = root.resolve()
    return [
        executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--distpath",
        str(root / "dist" / "standalone"),
        "--workpath",
        str(root / "build" / "pyinstaller"),
        str(root / "cluesb.spec"),
    ]


def _run(command: list[str]) -> int:
    return subprocess.run(command, check=False).returncode


def main(
    *,
    root: Path | None = None,
    system: Callable[[], str] = platform.system,
    machine: Callable[[], str] = platform.machine,
    runner: Callable[[list[str]], int] = _run,
) -> int:
    project_root = (root or Path(__file__).resolve().parents[1]).resolve()
    if system() != "Darwin":
        print("cluesb standalone builds are supported only on macOS.", file=sys.stderr)
        return 2

    architecture = machine()
    print(f"Building standalone cluesb executable for {architecture}...")
    result = runner(build_command(project_root))
    if result:
        print(f"PyInstaller failed with exit status {result}.", file=sys.stderr)
        return result

    artifact = project_root / "dist" / "standalone" / "cluesb"
    if not artifact.is_file():
        print(f"PyInstaller completed but did not create {artifact}.", file=sys.stderr)
        return 1
    print(f"Built {artifact} ({architecture})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
