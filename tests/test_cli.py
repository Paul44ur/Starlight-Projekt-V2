from __future__ import annotations

import pytest

from starlight import __version__
from starlight.cli import main
from starlight.core import Paths


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_new_module_from_template_and_call_it(
    paths: Paths, capsys: pytest.CaptureFixture[str]
) -> None:
    home = str(paths.root)
    assert main(["--home", home, "modules", "new", "hello"]) == 0
    assert (paths.modules / "hello" / "module.yaml").read_text().count("name: hello") == 1
    assert "HelloModule" in (paths.modules / "hello" / "module.py").read_text()

    assert main(["--home", home, "tools"]) == 0
    assert "hello.greet" in capsys.readouterr().out

    assert main(["--home", home, "ask", '!hello.greet {"name": "Paul"}']) == 0
    assert capsys.readouterr().out.strip() == "Hello, Paul!"


def test_new_module_rejects_bad_names(paths: Paths) -> None:
    assert main(["--home", str(paths.root), "modules", "new", "Bad-Name"]) == 1
    assert main(["--home", str(paths.root), "modules", "new", "_template"]) == 1


def test_kill_and_resume(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    home = str(paths.root)
    assert main(["--home", home, "kill", "because"]) == 0
    assert (paths.data / "STOP").exists()
    assert main(["--home", home, "ask", "hi"]) == 2
    assert main(["--home", home, "resume"]) == 0
    assert not (paths.data / "STOP").exists()
    assert main(["--home", home, "ask", "hi"]) == 0
    assert "You said: hi" in capsys.readouterr().out


def test_enable_disable(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    home = str(paths.root)
    main(["--home", home, "modules", "new", "hello"])
    assert main(["--home", home, "modules", "disable", "hello"]) == 0
    main(["--home", home, "modules", "list"])
    assert "disabled" in capsys.readouterr().out
    assert main(["--home", home, "modules", "enable", "nope"]) == 1


def test_bad_config_is_reported(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    paths.config_file.write_text("agent:\n  max_steps: 0\n")
    assert main(["--home", str(paths.root), "ask", "hi"]) == 1
    assert "Configuration error" in capsys.readouterr().err
