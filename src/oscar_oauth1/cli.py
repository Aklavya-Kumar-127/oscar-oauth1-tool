from __future__ import annotations

import argparse
import json
import logging
import sys
from urllib.parse import urlencode

from . import store
from .client import OscarOAuth1Client
from .config import load_settings
from .probe import probe_base_paths


def _require_token(settings):
    token = store.load(settings.token_file)
    if token is None:
        sys.exit(
            f"No access token at {settings.token_file}. "
            "Run `oscar-oauth1 serve` and visit /oauth1/start."
        )
    return token


def main() -> None:
    parser = argparse.ArgumentParser(prog="oscar-oauth1")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("probe", help="check which base path reaches Oscar")
    sub.add_parser("wadl", help="fetch the WADL operation list")
    sub.add_parser("status", help="show the stored token's age")

    serve = sub.add_parser("serve", help="run the callback server")
    serve.add_argument("--port", type=int, default=3000)

    specialist = sub.add_parser("specialist", help="GET consults/getProfessionalSpecialist")
    specialist.add_argument("--spec-id", type=int, required=True)

    find = sub.add_parser("find-specialist", help="GET professionalSpecialist/search")
    find.add_argument("--referral-no")
    find.add_argument("--last-name")

    call = sub.add_parser("call", help="sign and send one arbitrary request")
    call.add_argument("path", help="path under the services base, query string included")
    call.add_argument("--method", default="GET")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        settings = load_settings()
    except RuntimeError as exc:
        sys.exit(str(exc))

    if args.command == "probe":
        print(json.dumps(probe_base_paths(settings), indent=2))
        return

    if args.command == "status":
        token = store.load(settings.token_file)
        if token is None:
            print(f"No token at {settings.token_file}")
        else:
            print(f"Token stored, age {token.age_seconds / 3600:.2f} h (no TTL is returned)")
        return

    if args.command == "serve":
        import uvicorn

        uvicorn.run("oscar_oauth1.server:app", host="127.0.0.1", port=args.port)
        return

    token = _require_token(settings)
    if args.command == "wadl":
        url = f"{settings.services_base}?_wadl"
    elif args.command == "specialist":
        url = f"{settings.services_base}/consults/getProfessionalSpecialist?specId={args.spec_id}"
    elif args.command == "find-specialist":
        query = urlencode(
            {k: v for k, v in (("referralNo", args.referral_no), ("lastName", args.last_name)) if v}
        )
        url = f"{settings.services_base}/professionalSpecialist/search?{query}"
    else:
        url = f"{settings.services_base}/{args.path.lstrip('/')}"

    with OscarOAuth1Client(settings) as client:
        result = client.call(
            "GET" if args.command in ("wadl", "specialist", "find-specialist") else args.method,
            url,
            token.oauth_token,
            token.oauth_token_secret,
        )

    print(f"HTTP {result.status_code}")
    print(result.text[:8000])
