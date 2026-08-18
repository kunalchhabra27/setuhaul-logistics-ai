"""One-off helper: sign in as a driver and print the exact JSON payload
`agentcore invoke` (or a manual boto3 invoke_agent_runtime call) needs.

Usage:
    python get_driver_jwt.py <driver-email> <driver-password>

Reads SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY from agentcore/.env.local
(same file sync_env_vars.py reads), so run this from the repo root with
SetuHaulDriverChat/agentcore/.env.local present, or pass --env-file to
point elsewhere.

This does the same thing browser devtools would show you after logging into
the driver portal locally -- it just skips the browser. Use any driver
account you've already created/used during local dev (frontend signup flow,
or an existing test driver). This script does NOT create accounts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        env[key.strip()] = value
    return env


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    parser.add_argument("password")
    parser.add_argument("--message", default="I am 2 hours late, book me a slot")
    parser.add_argument(
        "--env-file",
        default="SetuHaulDriverChat/agentcore/.env.local",
        help="Path to a .env-style file with SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY",
    )
    args = parser.parse_args()

    env = load_env_file(Path(args.env_file))
    supabase_url = env.get("SUPABASE_URL")
    supabase_key = env.get("SUPABASE_PUBLISHABLE_KEY")
    if not supabase_url or not supabase_key:
        print(
            f"Could not find SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY in {args.env_file}. "
            "Pass --env-file pointing at a .env with those two set.",
            file=sys.stderr,
        )
        return 1

    try:
        from supabase import create_client
    except ImportError:
        print("Missing dependency: pip install supabase --break-system-packages", file=sys.stderr)
        return 1

    client = create_client(supabase_url, supabase_key)
    result = client.auth.sign_in_with_password({"email": args.email, "password": args.password})

    if result.session is None or result.user is None:
        print("Sign-in failed -- check the email/password.", file=sys.stderr)
        return 1

    payload = {
        "driver_jwt": result.session.access_token,
        "driver_id": result.user.id,
        "driver_email": result.user.email,
        "message": args.message,
    }

    print("\n--- agentcore invoke payload (paste as-is) ---\n")
    print(json.dumps(payload))
    print("\n--- PowerShell one-liner ---\n")
    # Single-quote the JSON for PowerShell so its own double quotes survive.
    print(f"agentcore invoke '{json.dumps(payload)}'")
    print(f"\n(access_token expires in ~{result.session.expires_in}s -- rerun this script if it goes stale)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
