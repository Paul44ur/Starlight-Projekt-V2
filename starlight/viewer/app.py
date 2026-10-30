"""The viewer's web app: a dashboard page, a JSON API and Prometheus metrics.
It opens stats.db read-only and never writes anything."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from starlight.core.config import load_config
from starlight.core.paths import Paths

STATIC = Path(__file__).with_name("static")
HEARTBEAT_STALE = 120  # seconds without a heartbeat = the core is not running


def bucket_for(minutes: int) -> int:
    if minutes <= 60:
        return 60
    if minutes <= 24 * 60:
        return 15 * 60
    return 2 * 3600


class StatsReader:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection | None:
        if not self.db_path.is_file():
            return None
        return sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True, timeout=5)

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        db = self._connect()
        if db is None:
            return []
        try:
            return db.execute(sql, params).fetchall()
        except sqlite3.OperationalError:  # no table yet
            return []
        finally:
            db.close()

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        rows = self._rows(sql, params)
        return rows[0][0] if rows and rows[0] else None

    def count(self, name: str, since: float, where: str = "", params: tuple[Any, ...] = ()) -> int:
        sql = f"SELECT COALESCE(SUM(value), 0) FROM metrics WHERE name = ? AND ts >= ? {where}"
        return int(self._one(sql, (name, since, *params)) or 0)

    def latest(self, name: str, where: str = "", params: tuple[Any, ...] = ()) -> float | None:
        sql = f"SELECT value FROM metrics WHERE name = ? {where} ORDER BY ts DESC LIMIT 1"
        value = self._one(sql, (name, *params))
        return float(value) if value is not None else None

    def series(self, name: str, since: float, bucket: int, how: str = "AVG") -> list[list[float]]:
        func = {"AVG": "AVG(value)", "SUM": "SUM(value)", "MAX": "MAX(value)"}[how]
        rows = self._rows(
            f"SELECT CAST(ts / ? AS INTEGER) * ? AS b, {func} FROM metrics "
            "WHERE name = ? AND ts >= ? GROUP BY b ORDER BY b",
            (bucket, bucket, name, since),
        )
        return [[float(b), round(float(v), 3)] for b, v in rows]

    def summary(self, minutes: int, stop_file: Path | None = None) -> dict[str, Any]:
        now = time.time()
        since = now - minutes * 60
        bucket = bucket_for(minutes)
        heartbeat = self._one("SELECT MAX(ts) FROM metrics WHERE name = 'core.heartbeat'")
        stopped_at = self._one("SELECT MAX(ts) FROM metrics WHERE name = 'core.stopped'")
        if heartbeat and stopped_at and float(stopped_at) >= float(heartbeat):
            heartbeat_fresh = False
        else:
            heartbeat_fresh = bool(heartbeat) and now - float(heartbeat) < HEARTBEAT_STALE
        stopped = stop_file is not None and stop_file.is_file()
        if stopped:
            assert stop_file is not None
            state = "stopped"
            reason = stop_file.read_text(encoding="utf-8", errors="replace").strip()
        elif heartbeat_fresh:
            state, reason = "running", ""
        else:
            state, reason = "offline", ""

        by_module = self._rows(
            "SELECT json_extract(labels, '$.module') AS m, SUM(value) FROM metrics "
            "WHERE name = 'tool.calls' AND ts >= ? GROUP BY m ORDER BY 2 DESC",
            (since,),
        )
        failed = self._rows(
            "SELECT ts, json_extract(labels, '$.tool') FROM metrics WHERE name = 'tool.calls' "
            "AND json_extract(labels, '$.ok') = 0 AND ts >= ? ORDER BY ts DESC LIMIT 10",
            (since,),
        )
        other_errors = self._rows(
            "SELECT ts, json_extract(labels, '$.source') FROM metrics WHERE name = 'errors' "
            "AND ts >= ? ORDER BY ts DESC LIMIT 10",
            (since,),
        )
        peak = self._one(
            "SELECT MAX(c) FROM (SELECT COUNT(*) AS c FROM metrics "
            "WHERE name = 'web.requests' AND ts >= ? GROUP BY CAST(ts AS INTEGER))",
            (since,),
        )
        skills = {
            str(status): int(value)
            for status, value in self._rows(
                "SELECT json_extract(labels, '$.status') AS s, value FROM metrics m "
                "WHERE name = 'skills.count' AND ts = (SELECT MAX(ts) FROM metrics "
                "WHERE name = 'skills.count' AND json_extract(labels, '$.status') = "
                "json_extract(m.labels, '$.status'))"
            )
        }
        # The busiest single second in each interval: comparable with the per-second cap
        # (http.max_requests_per_second).
        web_series = [
            [float(b), float(v)]
            for b, v in self._rows(
                "SELECT CAST(s / ? AS INTEGER) * ? AS b, MAX(c) FROM ("
                "SELECT CAST(ts AS INTEGER) AS s, COUNT(*) AS c FROM metrics "
                "WHERE name = 'web.requests' AND ts >= ? GROUP BY s) GROUP BY b ORDER BY b",
                (bucket, bucket, since),
            )
        ]
        errors = [{"ts": ts, "what": f"tool {tool} failed"} for ts, tool in failed]
        errors += [{"ts": ts, "what": f"{source} error"} for ts, source in other_errors]
        errors.sort(key=lambda e: -float(e["ts"]))
        return {
            "now": now,
            "minutes": minutes,
            "bucket_seconds": bucket,
            "core": {"state": state, "reason": reason, "last_seen": heartbeat},
            "totals": {
                "tasks": self.count("tasks.completed", since),
                "tool_calls": self.count("tool.calls", since),
                # every failed tool call, not just the 10 listed below
                "errors": self.count("tool.calls", since, "AND json_extract(labels, '$.ok') = 0")
                + self.count("errors", since),
                "web_requests": self.count("web.requests", since),
                "peak_web_requests_per_second": int(peak or 0),
                "whatsapp_received": self.count("whatsapp.received", since),
            },
            "latest": {
                "tokens_per_second": self.latest("model.tokens_per_second"),
                "vram_used_bytes": self.latest("gpu.vram_used_bytes"),
                "vram_total_bytes": self.latest("gpu.vram_total_bytes"),
                "gpu_utilization_percent": self.latest("gpu.utilization_percent"),
                "memory_entries": self.latest("memory.entries"),
                "bench_tokens_per_second": self.latest("model.bench.tokens_per_second"),
                "bench_gpu_fraction": self.latest("model.bench.gpu_fraction"),
            },
            "skills": skills,
            "series": {
                "tokens_per_second": self.series("model.tokens_per_second", since, bucket),
                "web_requests_peak_per_second": web_series,
                "gpu_utilization_percent": self.series("gpu.utilization_percent", since, bucket),
                "vram_used_gb": [
                    [b, round(v / 2**30, 2)]
                    for b, v in self.series("gpu.vram_used_bytes", since, bucket)
                ],
                "tasks": self.series("tasks.completed", since, bucket, "SUM"),
            },
            "tool_calls_by_module": [
                {"module": str(m or "?"), "calls": int(c)} for m, c in by_module
            ],
            "recent_errors": errors[:10],
            "models": self.models(since),
        }

    def models(self, since: float) -> list[dict[str, Any]]:
        """Each model used in the period, side by side: answers, speed, time per answer,
        tool calls and how many of them failed, and its last tool-use exam score."""
        speed = {
            str(m): (int(n), float(avg))
            for m, n, avg in self._rows(
                "SELECT json_extract(labels, '$.model') AS m, COUNT(*), AVG(value) FROM metrics "
                "WHERE name = 'model.tokens_per_second' AND ts >= ? AND m IS NOT NULL "
                "GROUP BY m",
                (since,),
            )
        }
        seconds = {
            str(m): float(avg)
            for m, avg in self._rows(
                "SELECT json_extract(labels, '$.model') AS m, AVG(value) FROM metrics "
                "WHERE name = 'model.duration_s' AND ts >= ? AND m IS NOT NULL GROUP BY m",
                (since,),
            )
        }
        tools = {
            str(m): (int(calls), int(bad or 0))
            for m, calls, bad in self._rows(
                "SELECT json_extract(labels, '$.model') AS m, COUNT(*), "
                "SUM(json_extract(labels, '$.ok') = 0) FROM metrics "
                "WHERE name = 'tool.calls' AND ts >= ? AND m IS NOT NULL GROUP BY m",
                (since,),
            )
        }
        exams = {
            str(m): f"{int(score)}/{of}"
            for m, score, of in self._rows(
                "SELECT json_extract(labels, '$.provider') AS m, value, "
                "json_extract(labels, '$.of') FROM metrics e WHERE name = 'model.exam_score' "
                "AND ts = (SELECT MAX(ts) FROM metrics WHERE name = 'model.exam_score' "
                "AND json_extract(labels, '$.provider') = json_extract(e.labels, '$.provider'))"
            )
        }
        out = []
        for name in sorted(set(speed) | set(tools)):
            answers, tps = speed.get(name, (0, 0.0))
            calls, failed = tools.get(name, (0, 0))
            out.append(
                {
                    "model": name,
                    "answers": answers,
                    "tokens_per_second": round(tps, 1) if answers else None,
                    "seconds_per_answer": round(seconds[name], 1) if name in seconds else None,
                    "tool_calls": calls,
                    "tool_failures_percent": round(100 * failed / calls, 1) if calls else None,
                    "exam": exams.get(name),
                }
            )
        return out

    def prometheus(self) -> str:
        """All-time counters and current gauges in the Prometheus text format."""
        lines: list[str] = []

        def metric(name: str, kind: str, help_text: str, samples: list[tuple[str, Any]]) -> None:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")
            for labels, value in samples:
                if value is not None:
                    lines.append(f"{name}{labels} {float(value):g}")

        metric(
            "starlight_tasks_completed_total",
            "counter",
            "Tasks completed.",
            [("", self.count("tasks.completed", 0))],
        )
        calls = self._rows(
            "SELECT json_extract(labels, '$.module'), json_extract(labels, '$.ok'), SUM(value) "
            "FROM metrics WHERE name = 'tool.calls' GROUP BY 1, 2"
        )
        metric(
            "starlight_tool_calls_total",
            "counter",
            "Tool calls by module and outcome.",
            [(f'{{module="{m}",ok="{"true" if ok else "false"}"}}', c) for m, ok, c in calls],
        )
        metric(
            "starlight_web_requests_total",
            "counter",
            "Web requests made.",
            [("", self.count("web.requests", 0))],
        )
        metric(
            "starlight_errors_total",
            "counter",
            "Errors outside tool calls.",
            [("", self.count("errors", 0))],
        )
        for name, key, help_text in (
            ("starlight_model_tokens_per_second", "model.tokens_per_second", "Last speed."),
            ("starlight_gpu_vram_used_bytes", "gpu.vram_used_bytes", "VRAM in use."),
            ("starlight_gpu_utilization_percent", "gpu.utilization_percent", "GPU use."),
            ("starlight_memory_entries", "memory.entries", "Files in the Memory folder."),
        ):
            metric(name, "gauge", help_text, [("", self.latest(key))])
        return "\n".join(lines) + "\n"


def create_app(paths: Paths | None = None) -> Any:
    from fastapi import FastAPI, Query
    from fastapi.responses import HTMLResponse, PlainTextResponse

    paths = paths or Paths.discover()
    config = load_config(paths)
    reader = StatsReader(paths.stats_db)
    stop_file = paths.root / config.kill_switch.stop_file
    app = FastAPI(title="Starlight statistics", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        html = html.replace("__REFRESH__", str(config.viewer.refresh_seconds * 1000))
        return html.replace("__WEB_CAP__", str(config.http.max_requests_per_second))

    @app.get("/api/summary")
    def summary(minutes: int = Query(60, ge=5, le=60 * 24 * 30)) -> dict[str, Any]:
        return reader.summary(minutes, stop_file)

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> str:
        return reader.prometheus()

    return app


def run(paths: Paths | None = None) -> None:
    import asyncio

    import uvicorn

    from starlight.service import shutdown_file

    paths = paths or Paths.discover()
    config = load_config(paths)
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(paths),
            host=config.viewer.host,
            port=config.viewer.port,
            log_level="warning",
            log_config=None,  # keep Starlight's logging; works without a console too
        )
    )
    request = shutdown_file(paths.data, "viewer")
    request.unlink(missing_ok=True)

    async def watch_for_stop() -> None:  # `starlight service stop`
        while not server.should_exit:
            if request.exists():
                request.unlink(missing_ok=True)
                server.should_exit = True
            await asyncio.sleep(1)

    async def main() -> None:
        watcher = asyncio.create_task(watch_for_stop())
        try:
            await server.serve()
        finally:
            watcher.cancel()

    asyncio.run(main())
