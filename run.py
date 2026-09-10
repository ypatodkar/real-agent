#!/usr/bin/env python3
"""Run the standalone Second Unit Interview application."""

from __future__ import annotations

import argparse
import errno
import logging
import os
from pathlib import Path
from typing import IO

try:
    import fcntl
except ImportError:  # pragma: no cover - the primary local target is macOS/Linux
    fcntl = None

from second_unit.agent import StoryEditor
from second_unit.database import Repository
from second_unit.model import get_model
from second_unit.server import Application, create_server
from second_unit.service import InterviewService
from second_unit.speech import SpeechService


ROOT = Path(__file__).resolve().parent


def acquire_database_lock(database: str) -> IO[str] | None:
    """Prevent two local servers from recovering or writing the same database."""
    if fcntl is None:
        return None
    database_path = Path(database).expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    handle = Path(f"{database_path}.lock").open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise SystemExit(
            f"This Interview database is already open in another Second Unit process: {database_path}"
        ) from exc
    return handle


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Second Unit Interview")
    parser.add_argument(
        "--host", default=os.environ.get("SECOND_UNIT_HOST", "127.0.0.1")
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("SECOND_UNIT_PORT", "8010"))
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("SECOND_UNIT_DATABASE", str(ROOT / "data" / "interviews.db")),
    )
    return parser.parse_args()


def main() -> None:
    outline_logger = logging.getLogger("second_unit.outline_agent")
    if not outline_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "time=%(asctime)s level=%(levelname)s component=outline-agent %(message)s"
        ))
        outline_logger.addHandler(handler)
    outline_logger.setLevel(logging.INFO)
    outline_logger.propagate = False
    args = arguments()
    database_lock = acquire_database_lock(args.database)
    repository = Repository(args.database)
    recovered = repository.recover_interrupted_runs()
    model = get_model()
    service = InterviewService(repository, StoryEditor(model))
    offline = os.environ.get("SECOND_UNIT_OFFLINE", "").strip().lower() in {"1", "true", "yes"}
    speech = SpeechService(environ={}) if offline else SpeechService()
    app = Application(service, speech, ROOT / "static")
    try:
        server = create_server(app, args.host, args.port)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise SystemExit(
                f"Port {args.port} is already in use. Stop the old process or run: "
                f"python run.py --port {args.port + 1}"
            ) from exc
        raise

    capability = speech.capability()
    print(f"Second Unit Interview -> http://{args.host}:{args.port}")
    print(f"  model    {getattr(model, 'backend', 'unknown')} / {getattr(model, 'model', 'unknown')}")
    print(f"  speech   {'configured' if capability.available else 'off'} — {capability.message}")
    print(f"  database {Path(args.database).resolve()}")
    if recovered:
        print(f"  recovery {recovered} interrupted turn(s) are safe to retry")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Second Unit.")
    finally:
        server.server_close()
        if database_lock is not None:
            fcntl.flock(database_lock.fileno(), fcntl.LOCK_UN)
            database_lock.close()


if __name__ == "__main__":
    main()
