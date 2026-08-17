from pathlib import Path
import os
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def make_dry_run(*arguments: str) -> list[str]:
    environment = os.environ.copy()
    environment.pop("UV", None)
    result = subprocess.run(
        ["make", "-n", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return [line for line in result.stdout.splitlines() if line]


def test_test_and_integration_targets_have_distinct_scopes():
    assert make_dry_run("test") == [
        'uv run --python 3.11 --extra test pytest -m "not integration"',
    ]
    assert make_dry_run("test-integration") == [
        "uv run --python 3.11 --extra test pytest -m integration",
    ]


def test_lint_and_check_targets_use_locked_lint_extra():
    assert make_dry_run("lint") == [
        "uv run --python 3.11 --extra lint ruff check src tests scripts",
    ]
    assert make_dry_run("check") == [
        "uv run --python 3.11 --extra lint ruff check src tests scripts",
        'uv run --python 3.11 --extra test pytest -m "not integration"',
    ]


def test_run_forwards_arguments_and_variables_are_overridable():
    assert make_dry_run("run", "ARGS=--debug --once", "UV=/opt/uv", "PYTHON_VERSION=3.12") == [
        "/opt/uv run --python 3.12 cluesb --debug --once",
    ]


def test_standalone_target_and_alias_run_the_same_builder():
    expected = [
        "uv run --python 3.11 --extra standalone python scripts/build_standalone.py",
    ]
    assert make_dry_run("standalone") == expected
    assert make_dry_run("build-standalone") == expected


def test_install_builds_standalone_and_uses_overridable_destination():
    assert make_dry_run("install", "INSTALL_DIR=/tmp/cluesb-bin") == [
        "uv run --python 3.11 --extra standalone python scripts/build_standalone.py",
        'install -d "/tmp/cluesb-bin"',
        'install -m 755 dist/standalone/cluesb "/tmp/cluesb-bin/cluesb"',
    ]
