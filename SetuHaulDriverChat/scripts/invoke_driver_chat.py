"""One-off helper: sign in as a driver and invoke the deployed AgentCore
driver-chat runtime directly via boto3 -- the same call
infrastructure/agentcore_client.py makes in production (Vercel), just run
from your own machine using your local AWS SSO session instead of Vercel's
IAM user.

Bypasses `agentcore invoke`'s CLI wrapper entirely: that CLI always wraps
whatever you give it (including --prompt-file) into its own generic
{"prompt": ...} shape, which doesn't match main.py's custom
driver_jwt/driver_id/message payload contract -- hence the persistent
KeyError: 'driver_jwt' even with a correct payload file. Calling
invoke_agent_runtime directly sends our exact payload bytes, unmodified.

Usage (from anywhere -- paths are all relative to this script):
    python invoke_driver_chat.py <driver-email> <driver-password> [--message "..."]

Requires: pip install boto3 supabase --break-system-packages
Requires your AWS SSO session to already be active (same one `agentcore`
CLI uses) -- boto3 uses the standard credential chain automatically.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

DEFAULT_RUNTIME_ARN = (
    "arn:aws:bedrock-agentcore:us-east-1:040105285212:"
    "runtime/SetuHaulDriverChat_SetuHaulDriverChat-cTsy0LGpXz"
)
DEFAULT_REGION = "us-east-1"
DEFAULT_ENV_FILE = Path(__file__).resolve().parent.parent / "agentcore" / ".env.local"


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    parser.add_argument("password")
    parser.add_argument("--message", default="I am 2 hours late, book me a slot")
    parser.add_argument("--arn", default=DEFAULT_RUNTIME_ARN, help="AgentCore runtime ARN")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help="Path to a .env-style file with SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY",
    )
    args = parser.parse_args()

    env = load_env_file(Path(args.env_file))
    supabase_url = env.get("SUPABASE_URL")
    supabase_key = env.get("SUPABASE_PUBLISHABLE_KEY")
    if not supabase_url or not supabase_key:
        print(f"Could not find SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY in {args.env_file}.", file=sys.stderr)
        return 1

    try:
        from supabase import create_client
    except ImportError:
        print("Missing dependency: pip install supabase --break-system-packages", file=sys.stderr)
        return 1

    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        print("Missing dependency: pip install boto3 --break-system-packages", file=sys.stderr)
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
    print(f"Signed in as {result.user.email} ({result.user.id}). Invoking AgentCore runtime...\n")

    bedrock = boto3.client("bedrock-agentcore", region_name=args.region)
    runtime_session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"setuhaul-driver-{result.user.id}"))

    try:
        response = bedrock.invoke_agent_runtime(
            agentRuntimeArn=args.arn,
            runtimeSessionId=runtime_session_id,
            payload=json.dumps(payload).encode("utf-8"),
        )
        body = response["response"].read()
    except (BotoCoreError, ClientError) as exc:
        print(f"AWS call failed: {exc}", file=sys.stderr)
        return 1

    print("--- Raw response ---\n")
    try:
        parsed = json.loads(body)
        print(json.dumps(parsed, indent=2))
    except json.JSONDecodeError:
        print(body.decode("utf-8", errors="replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
