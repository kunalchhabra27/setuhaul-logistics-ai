"""Patch agentcore/agentcore.json's runtime envVars from agentcore/.env.local.

`agentcore deploy` reads its runtime configuration from agentcore.json, NOT
from .env.local -- that file only feeds local `agentcore dev` testing. This
script closes that gap for a local (non-CI) deploy: fill in your real values
in the gitignored agentcore/.env.local, run this script from
SetuHaulDriverChat/, then run `agentcore deploy` -- the values you just typed
now travel with the deploy. Nothing here is committed or sent anywhere except
into agentcore.json (also gitignored-adjacent in spirit, but note: this repo
does NOT currently gitignore agentcore.json itself, so double check `git
status` before committing anything after running this -- you do not want
real secrets landing in agentcore.json in git history).

Usage (from SetuHaulDriverChat/):
    python scripts/sync_env_vars.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # SetuHaulDriverChat/
ENV_FILE = HERE / "agentcore" / ".env.local"
CONFIG_FILE = HERE / "agentcore" / "agentcore.json"

# Only these matter to the deployed Runtime -- FRONTEND_ORIGIN, ENVIRONMENT,
# LOG_LEVEL, OTEL_*, TWILIO_* are Vercel/FastAPI-only concerns, skipped even
# if present in .env.local.
RUNTIME_KEYS = [
    "HUGGINGFACEHUB_API_TOKEN",
    "DRIVER_CHAT_LLM_MODEL",
    "DRIVER_CHAT_LLM_PROVIDER",
    "SUPABASE_URL",
    "SUPABASE_PUBLISHABLE_KEY",
    "GOOGLE_API_KEY",
    "DRIVER_CHAT_TRANSCRIPTION_MODEL",
    "REDIS_URL",
    "LANGSMITH_TRACING",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "LANGCHAIN_API_KEY",
    "LANGCHAIN_PROJECT",
]


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise SystemExit(f"Missing {path} -- fill in the template there first.")
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> None:
    env = parse_env_file(ENV_FILE)
    config = json.loads(CONFIG_FILE.read_text())

    env_vars = []
    for key in RUNTIME_KEYS:
        value = env.get(key)
        if value:
            env_vars.append({"name": key, "value": value})

    if not any(v["name"] == "HUGGINGFACEHUB_API_TOKEN" for v in env_vars):
        print(
            "WARNING: HUGGINGFACEHUB_API_TOKEN is empty in .env.local -- the "
            "deployed chatbot will ImportError on langchain_huggingface and "
            "silently fall back to the regex parser on every message without it."
        )

    config["runtimes"][0]["envVars"] = env_vars
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")

    print(f"Wrote {len(env_vars)} runtime env vars into {CONFIG_FILE}:")
    for v in env_vars:
        print(f"  - {v['name']}")
    print(
        "\nIMPORTANT: agentcore.json is NOT gitignored (unlike .env.local) and now "
        "contains real secret values in plain text. After `agentcore deploy` "
        "finishes, run this before touching git at all:\n"
        "    git checkout -- agentcore/agentcore.json\n"
        "to discard this local-only mutation and restore the clean, secret-free "
        "version -- never commit the patched file."
    )


if __name__ == "__main__":
    main()
