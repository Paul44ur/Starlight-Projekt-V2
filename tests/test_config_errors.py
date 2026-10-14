"""A mistake in a settings file is explained in plain words: which file and line, which
setting, what is wrong, and the name that was probably meant."""

from __future__ import annotations

import pytest

from starlight.core import ConfigError, Paths, load_config
from starlight.core.config import load_module_config
from starlight.doctor import check_config


def write(paths: Paths, name: str, text: str) -> None:
    (paths.config / name).write_text(text, encoding="utf-8")


def message(paths: Paths) -> str:
    with pytest.raises(ConfigError) as caught:
        load_config(paths)
    return str(caught.value)


def test_a_wrong_value_names_the_file_the_line_and_what_was_written(paths: Paths) -> None:
    write(paths, "starlight.yaml", "model:\n  provider: echo\n  context_window: lots\n")
    text = message(paths)
    assert "config" in text and "starlight.yaml, line 3: model.context_window" in text
    assert "valid integer" in text and "'lots'" in text


def test_a_mistyped_setting_name_suggests_the_right_one(paths: Paths) -> None:
    write(paths, "starlight.yaml", "web:\n  prot: 8700\n")
    text = message(paths)
    assert "web.prot" in text and "did you mean 'port'?" in text


def test_the_file_that_wins_is_the_one_named(paths: Paths) -> None:
    write(paths, "starlight.yaml", "stats:\n  retention_days: 10\n")
    write(paths, "starlight.local.yaml", "# my own settings\n\nstats:\n  retention_days: -3\n")
    text = message(paths)
    assert "starlight.local.yaml, line 4: stats.retention_days" in text
    assert "starlight.yaml, line" not in text.replace("starlight.local.yaml", "")


def test_several_problems_are_listed_together(paths: Paths) -> None:
    write(
        paths, "starlight.yaml", "web:\n  prot: 1\nstats:\n  retention_days: 0\nlogging:\n  x: 1\n"
    )
    text = message(paths)
    assert text.startswith("3 settings are wrong")
    assert "web.prot" in text and "stats.retention_days" in text and "logging.x" in text


def test_a_yaml_slip_gives_the_line_and_a_hint(paths: Paths) -> None:
    write(paths, "starlight.yaml", "model:\n\tprovider: echo\n")
    text = message(paths)
    assert "line 2: invalid YAML" in text and "tab" in text
    write(paths, "starlight.yaml", "model:\n  provider: echo\n  options: {base_url: x\n")
    assert "starlight.yaml, line" in message(paths)


def test_a_file_that_is_not_a_list_of_settings_is_explained(paths: Paths) -> None:
    write(paths, "starlight.yaml", "- just\n- a list\n")
    assert "must be a list of 'setting: value' lines, not a list" in message(paths)


def test_a_module_setting_is_explained_too(paths: Paths) -> None:
    from pydantic import BaseModel, ConfigDict

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        greeting: str = "Hello"
        retries: int = 3

    (paths.module_configs).mkdir(parents=True, exist_ok=True)
    (paths.module_configs / "hello.yaml").write_text("greting: Hi\nretries: many\n")
    with pytest.raises(ConfigError) as caught:
        load_module_config(paths, "hello", Settings)
    text = str(caught.value)
    assert "hello.yaml, line 1: greting" in text and "did you mean 'greeting'?" in text
    assert "hello.yaml, line 2: retries" in text


def test_the_doctor_shows_the_same_explanation(paths: Paths) -> None:
    write(paths, "starlight.yaml", "web:\n  prot: 1\n")
    check, config = check_config(paths)
    assert config is None and check.status == "fail"
    assert "did you mean 'port'?" in check.detail


def test_a_good_file_still_loads(paths: Paths) -> None:
    write(paths, "starlight.yaml", "web:\n  port: 8701\n")
    assert load_config(paths).web.port == 8701
