"""The groups that keep tests apart in a parallel run (`pytest -n 4`, see conftest.py)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import ONE_WORKER, ONE_WORKER_FILES, REPO, xdist_group_for


def group_of(item: pytest.Item) -> str | None:
    marker = item.get_closest_marker("xdist_group")
    return None if marker is None else str(marker.kwargs["name"])


def test_every_collected_test_has_a_group(request: pytest.FixtureRequest) -> None:
    items = request.session.items
    assert items
    assert [i.nodeid for i in items if group_of(i) is None] == []


def test_the_one_worker_files_share_one_name(request: pytest.FixtureRequest) -> None:
    items = [i for i in request.session.items if i.path.name in ONE_WORKER_FILES]
    if not items:  # (this file was run alone)
        pytest.skip("none of the one-worker files was collected")
    assert {group_of(i) for i in items} == {ONE_WORKER}


def test_other_files_stay_together_but_apart_from_each_other() -> None:
    a = xdist_group_for("tests/test_notes_editing.py")
    assert a == xdist_group_for("tests/test_notes_editing.py")
    assert a != xdist_group_for("tests/test_kanban.py")
    assert a != ONE_WORKER


def test_the_files_that_cannot_overlap_exist() -> None:
    for name in ONE_WORKER_FILES:
        assert (REPO / "tests" / name).is_file(), name


def collect(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--co", "-q", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_a_plain_run_collects_without_a_warning_about_the_marker() -> None:
    result = collect("-W", "error", "tests/test_parallel_run.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "tests/test_parallel_run.py::" in result.stdout


def test_a_run_without_xdist_still_collects(tmp_path: Path) -> None:
    # `-p no:xdist` is what a machine without pytest-xdist looks like to the hook.
    result = collect("-p", "no:xdist", "-W", "error", "tests/test_parallel_run.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "tests/test_parallel_run.py::" in result.stdout


GROUPED = """
import os
import pytest

pytestmark = pytest.mark.xdist_group(name="x")


@pytest.mark.parametrize("n", range(4))
def test_it(n, tmp_path_factory):
    with open(os.environ["WHO_RAN"], "a") as f:
        f.write(os.environ["PYTEST_XDIST_WORKER"] + "\\n")
"""


def test_a_plain_n_keeps_a_group_in_one_worker(tmp_path: Path) -> None:
    """`-n 2` alone must use the group scheduler: both files' tests land in one worker."""
    pytest.importorskip("xdist")
    for name in ("test_one.py", "test_two.py"):
        (tmp_path / name).write_text(GROUPED)
    who = tmp_path / "who.txt"
    result = subprocess.run(
        [
            *(sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "tests.conftest"),
            *("-n", "2", "-q", "--rootdir", str(tmp_path), str(tmp_path)),
        ],
        cwd=tmp_path,
        env={**os.environ, "WHO_RAN": str(who), "PYTHONPATH": str(REPO)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "8 passed" in result.stdout, result.stdout
    assert len(set(who.read_text().split())) == 1
