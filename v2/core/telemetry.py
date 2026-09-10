"""Grafana Cloud, read through MCP and written through Loki.

The track requires the Grafana Cloud MCP server to be used *at runtime*, so this
is not a dashboard bolted on at the end — it decides what the interviewer asks.

    write   every question asked, and what the writer did next, becomes a Loki
            log line. Words written is the signal: a question that unlocks
            something produces paragraphs, one that falls flat produces six
            words or a skip.

    read    before choosing the next question, the agent asks Grafana which
            question shapes have actually produced writing, for this stage.
            That ranking reorders the shape pool.

Two credentials, because they are genuinely different things:

    GRAFANA_URL + GRAFANA_SERVICE_ACCOUNT_TOKEN     reads, via MCP
    LOKI_URL + LOKI_USER + LOKI_TOKEN               writes, via push API

A service-account token cannot push. That needs a Cloud Access Policy with
`logs:write`, which is a separate object in the Grafana console.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVICE = "second-unit"

# Everything not needed is switched off: fewer tools, faster start, and the
# server cannot be talked into doing something we did not intend.
MCP_ARGS = [
    "-lic",
    "exec uvx mcp-grafana -disable-write -disable-admin -disable-alerting "
    "-disable-annotations -disable-asserts -disable-athena -disable-clickhouse "
    "-disable-cloudwatch -disable-elasticsearch -disable-graphite -disable-incident "
    "-disable-influxdb -disable-oncall -disable-pyroscope -disable-quickwit "
    "-disable-rendering -disable-sift -disable-snapshot -disable-snowflake",
]

LOGS_UID = os.environ.get("GRAFANA_LOGS_UID", "grafanacloud-logs")


# ------------------------------------------------------------------ writing


@dataclass
class Outcome:
    """What happened after one question was asked."""

    shape_id: str
    stage: str
    project_id: str
    words: int                  # how much they wrote in reply
    skipped: bool = False
    seconds: float = 0.0        # how long they took — hesitation is signal too

    @property
    def unlocked(self) -> bool:
        """Did this question actually produce writing?

        Forty words is about three sentences: enough that they were thinking,
        not just acknowledging. Tuned by watching real sessions, not derived.
        """
        return not self.skipped and self.words >= 40


def _loki_config() -> tuple[str, str, str] | None:
    url, user, token = (os.environ.get(k) for k in ("LOKI_URL", "LOKI_USER", "LOKI_TOKEN"))
    return (url, user, token) if all((url, user, token)) else None


def record(outcome: Outcome) -> bool:
    """Push one event to Loki. Returns False if writing is not configured.

    Never raises: telemetry failing must not take a writing session with it.
    """
    config = _loki_config()
    if not config:
        return False
    url, user, token = config

    payload = {
        "streams": [{
            "stream": {
                "service_name": SERVICE,
                "stage": outcome.stage,
                "shape": outcome.shape_id,
                "unlocked": str(outcome.unlocked).lower(),
            },
            "values": [[
                str(time.time_ns()),
                json.dumps({
                    "project": outcome.project_id,
                    "words": outcome.words,
                    "skipped": outcome.skipped,
                    "seconds": round(outcome.seconds, 1),
                }),
            ]],
        }],
    }

    auth = base64.b64encode(f"{user}:{token}".encode()).decode()
    request = urllib.request.Request(
        url.rstrip("/") + "/loki/api/v1/push",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status in (200, 204)
    except Exception:
        return False


# ------------------------------------------------------------------ reading


class Grafana:
    """A long-lived MCP session. Spawning the server per query costs ~2s."""

    def __init__(self) -> None:
        self._session: ClientSession | None = None
        self._stack: list = []

    async def connect(self) -> None:
        params = StdioServerParameters(command="zsh", args=MCP_ARGS)
        streams = stdio_client(params)
        reader, writer = await streams.__aenter__()
        self._stack.append(streams)

        session = ClientSession(reader, writer)
        await session.__aenter__()
        self._stack.append(session)
        await session.initialize()
        self._session = session

    async def close(self) -> None:
        for ctx in reversed(self._stack):
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass
        self._stack.clear()
        self._session = None

    async def call(self, tool: str, args: dict) -> dict:
        if self._session is None:
            raise RuntimeError("not connected — call connect() first")
        result = await self._session.call_tool(tool, args)
        text = result.content[0].text if result.content else "{}"
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}

    async def rank_shapes(self, stage: str, window: str = "30d") -> list[str]:
        """Which question shapes have actually produced writing, best first.

        This is the runtime read the whole design rests on. An empty result is
        normal early on — the caller falls back to declaration order.
        """
        query = (
            f'sum by (shape) (count_over_time('
            f'{{service_name="{SERVICE}", stage="{stage}", unlocked="true"}} [{window}]))'
        )
        try:
            data = await self.call("query_loki_logs", {
                "datasourceUid": LOGS_UID,
                "logql": query,
                "queryType": "instant",
            })
        except Exception:
            return []

        rows = data.get("data") or []
        scored: list[tuple[str, float]] = []
        for row in rows:
            shape = (row.get("metric") or row.get("stream") or {}).get("shape")
            value = row.get("value") or row.get("values")
            if not shape:
                continue
            try:
                count = float(value[-1][-1] if isinstance(value[0], list) else value[-1])
            except Exception:
                count = 0.0
            scored.append((shape, count))

        return [s for s, _ in sorted(scored, key=lambda p: -p[1])]

    async def health(self) -> dict:
        """For the UI: is the runtime dependency actually live?"""
        try:
            sources = await self.call("list_datasources", {})
            return {"connected": True,
                    "datasources": len(sources) if isinstance(sources, list) else 1}
        except Exception as exc:
            return {"connected": False, "error": str(exc)[:160]}


def rank_shapes_sync(stage: str) -> list[str]:
    """Blocking wrapper, for the request handler."""
    async def go() -> list[str]:
        g = Grafana()
        try:
            await g.connect()
            return await g.rank_shapes(stage)
        finally:
            await g.close()
    try:
        return asyncio.run(go())
    except Exception:
        return []
