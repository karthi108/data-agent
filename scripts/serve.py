"""Run the web UI locally.

uv run python scripts/serve.py --demo     free scripted preview (no API calls)
uv run python scripts/serve.py            real agent (paid; reads OPENAI_API_KEY from .env)
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from data_agent import paths


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--demo", action="store_true", help="scripted model, no API calls")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    if args.demo:
        os.environ["DATA_AGENT_DEMO"] = "1"
    else:
        env = Path(paths.REPO_ROOT / ".env")
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                key, sep, value = line.strip().partition("=")
                if sep and key and not key.startswith("#"):
                    os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    from data_agent.web.app import app_from_env

    uvicorn.run(app_from_env(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
