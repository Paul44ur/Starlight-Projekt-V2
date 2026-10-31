"""Write and run code safely (Step 9).

* the core routes code work to the ``code`` model (``roles: code:`` in starlight.yaml,
  e.g. Qwen2.5-Coder); without one, the main model writes the code
* everything runs in the sandbox (Docker when available): time limit, no vault, network
  only when allowed
* write, test, fix: the model writes code and tests, the tests run, errors go back to the
  model, a limited number of times, then it reports
* running code needs your confirmation until you allow it
* a program whose tests pass is saved in your Starlight folder, under code/<task>
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from starlight.core import Message, Module, ModuleContext, Permission, tool
from starlight.memory.store import slugify
from starlight.sandbox import RunResult, Sandbox, create_sandbox

SYSTEM = """\
You write small, correct Python 3.12 programs with tests.
Reply with JSON only, no prose:
{"files": {"main.py": "...", "test_main.py": "..."}, "test_command": "python test_main.py"}
Rules: standard library only; tests use plain assert statements and finish with
print("ALL TESTS PASSED"); a failing test must exit with an error; no network access
unless the task says it is allowed; no files outside the working folder."""
FIX = """\
The tests failed. Output:

{output}

Fix the code (or a wrong test) and reply with the complete JSON again."""


class Config(BaseModel):
    sandbox: Literal["auto", "docker", "process"] = "auto"
    docker_image: str = "python:3.12-slim"
    memory_mb: int = 1024
    timeout: float = 60.0
    max_attempts: int = 4
    allow_network: bool = False  # tasks may still ask; this sets the default


def parse_solution(text: str) -> dict[str, Any] | None:
    """The model's JSON, also when wrapped in a code fence or followed by prose."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        files = data.get("files") if isinstance(data, dict) else None
        if isinstance(files, dict) and files:
            return {
                "files": {str(k): str(v) for k, v in files.items()},
                "test_command": str(data.get("test_command") or ""),
            }
    return None


class CodeModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        paths = ctx.paths
        self.sandbox: Sandbox = create_sandbox(
            self.config.sandbox,
            ctx.data_dir / "sandbox",
            deny=[paths.config, paths.data, paths.memory, paths.skills, paths.logs],
            image=self.config.docker_image,
            memory_mb=self.config.memory_mb,
        )
        ctx.kill_switch.add_cleanup(self.sandbox.kill_all, owner=ctx.name)
        ctx.logger.info("sandbox ready", backend=self.sandbox.name)

    async def shutdown(self) -> None:
        self.sandbox.kill_all()

    def _result(self, run: RunResult) -> dict[str, Any]:
        return {
            "ok": run.ok,
            "exit_code": run.exit_code,
            "timed_out": run.timed_out,
            "stdout": run.stdout[-6000:],
            "stderr": run.stderr[-4000:],
            "seconds": run.duration_s,
            "sandbox": run.backend,
        }

    @tool(permission=Permission.CONFIRM)
    async def run_python(
        self, code: str, timeout: float = 30, network: bool = False
    ) -> dict[str, Any]:
        """Run a Python snippet in the sandbox and return its output. Asks first."""
        self.ctx.kill_switch.check()
        run = await self.sandbox.run(
            {"main.py": code}, ["python", "main.py"], min(timeout, 600), network
        )
        return self._result(run)

    @tool(permission=Permission.CONFIRM)
    async def write_code(
        self, task: str, network: bool = False, max_attempts: int = 0
    ) -> dict[str, Any]:
        """Write a Python program with tests for a task, run the tests in the sandbox and
        fix it until they pass, at most `max_attempts` times (0: max_attempts from the
        config, 4 as shipped). Asks first."""
        model = self.ctx.model_for("code")
        if model is None:
            raise RuntimeError("no model available for code")
        allowed = network or self.config.allow_network
        attempts = max_attempts or self.config.max_attempts
        task_text = task + ("\n(Network access is allowed.)" if allowed else "")
        messages = [Message("system", SYSTEM), Message("user", task_text)]
        history: list[dict[str, Any]] = []
        solution: dict[str, Any] | None = None
        run: RunResult | None = None

        for attempt in range(1, attempts + 1):
            self.ctx.kill_switch.check()
            response = await model.chat(messages, [])
            messages.append(Message("assistant", response.content))
            solution = parse_solution(response.content)
            if solution is None:
                history.append({"attempt": attempt, "error": "no usable JSON in the reply"})
                messages.append(Message("user", "Reply with the JSON format only."))
                continue
            command = solution["test_command"] or (
                "python test_main.py" if "test_main.py" in solution["files"] else "python main.py"
            )
            try:
                run = await self.sandbox.run(
                    solution["files"], shlex.split(command), self.config.timeout, allowed
                )
            except ValueError as exc:  # a bad file name or command: it costs one attempt
                history.append({"attempt": attempt, "error": str(exc)})
                messages.append(Message("user", FIX.format(output=str(exc))))
                continue
            history.append({"attempt": attempt, "ok": run.ok, "exit_code": run.exit_code})
            if self.ctx.stats is not None:
                self.ctx.stats.record("code.attempts", 1, ok=run.ok)
            if run.ok:
                folder = self._save(task, solution["files"])
                await self.ctx.publish("code.solved", {"task": task, "attempts": attempt})
                # starlight/skills/manager.py keeps "files" of a successful run in the skill
                # it learns from the request: keep these keys in step with it.
                return {
                    "success": True,
                    "attempts": attempt,
                    "files": solution["files"],
                    "test_command": command,
                    "test_output": run.stdout[-3000:],
                    "saved_to": str(folder),
                    "sandbox": run.backend,
                }
            messages.append(Message("user", FIX.format(output=run.summary())))

        return {
            "success": False,
            "attempts": len(history),
            "history": history,
            "last_output": run.summary() if run else "",
            "files": solution["files"] if solution else {},
        }

    def _save(self, task: str, files: dict[str, str]) -> Path:
        """Into the Starlight folder (Documents/Starlight/code/<task>); the same task again
        gets its own folder (<task>-2), so a working version is never overwritten."""
        base = self.ctx.output_dir / "code"
        slug = slugify(task, 40)
        folder, number = base / slug, 2
        while folder.exists():
            folder, number = base / f"{slug}-{number}", number + 1
        folder.mkdir(parents=True)
        for name, content in files.items():
            target = (folder / name).resolve()
            if not target.is_relative_to(folder.resolve()):
                continue  # a name from the model that points outside ("../x"): never written
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return folder
