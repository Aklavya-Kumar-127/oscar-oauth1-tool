from __future__ import annotations

import argparse
import json
import logging
import sys

from . import specialists, store
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


def _report_id(body: str, label: str) -> None:
    """Both the add and the lookup are ticketed as "record the id", so pull it
    out rather than leaving it to be read off the raw body by eye."""
    spec_id = specialists.specialist_id(body)
    if spec_id is not None:
        print()
        print(f"{label}: {spec_id}")


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
    # Oscar answers 400 unless both are present, though the WADL marks neither
    # required. Requiring them here turns that into an argparse error instead of
    # a branded HTML error page that looks like an outage.
    find.add_argument("--referral-no", required=True)
    find.add_argument("--last-name", required=True)

    add = sub.add_parser("add-specialist", help="POST professionalSpecialist/add")
    add.add_argument("--first-name", required=True)
    add.add_argument("--last-name", required=True)
    # Oscar stores the clinic name in streetAddress, which is how its own
    # admin UI labels the field, so the flag is named for the meaning.
    # It must be non-empty: an empty string is rejected with a bare 400, which
    # is what the ticket's own sample payload sends.
    add.add_argument("--clinic-name", required=True)
    add.add_argument("--phone-number", default="")
    add.add_argument("--referral-no", default=specialists.DEFAULT_REFERRAL_NO)

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
    with OscarOAuth1Client(settings) as client:
        if args.command == "specialist":
            result = specialists.get_specialist(client, settings, token, args.spec_id)
        elif args.command == "find-specialist":
            result = specialists.find_specialist(
                client, settings, token, args.referral_no, args.last_name
            )
        elif args.command == "add-specialist":
            # A write is worth echoing before it happens; the URL itself is
            # already logged by the client's signing step, at INFO level.
            print(f"creating {args.first_name} {args.last_name} at {args.clinic_name}")
            result = specialists.add_specialist(
                client,
                settings,
                token,
                first_name=args.first_name,
                last_name=args.last_name,
                clinic_name=args.clinic_name,
                phone_number=args.phone_number,
                referral_no=args.referral_no,
            )
        else:
            url = (
                f"{settings.services_base}?_wadl"
                if args.command == "wadl"
                else f"{settings.services_base}/{args.path.lstrip('/')}"
            )
            method = "GET" if args.command == "wadl" else args.method
            result = client.call(
                method, url, token.oauth_token, token.oauth_token_secret
            )

    print(f"HTTP {result.status_code}")
    # Only the specialist operations are guaranteed JSON on success, so only
    # they get the branded-error-page summary. `call` is a passthrough to an
    # arbitrary path and can legitimately answer with real HTML or XML.
    specialist_ops = ("specialist", "find-specialist", "add-specialist")
    if args.command in specialist_ops and specialists.is_error_page(result.text):
        # Every Oscar failure arrives as the same branded HTML page. Paging it
        # out buries the one line that matters, so summarise it instead.
        print("(branded OSCAR Pro error page: the status line is the only signal)")
    else:
        print(result.text[:8000])

    if result.status_code == 200:
        if args.command == "add-specialist":
            _report_id(result.text, "new specialist id")
        elif args.command == "find-specialist":
            _report_id(result.text, "specialist id")
    elif args.command == "specialist" and result.status_code == 204:
        # Matches the web UI's handling of the same response: 204 here means
        # the id does not exist, not that the body was merely empty.
        print("no specialist with that id")
    elif args.command == "find-specialist" and result.status_code == 404:
        # The lookup is an exact match on the pair, so a 404 means no such
        # specialist, not a missing endpoint, and not an auth failure.
        print("no specialist matches that referralNo and lastName")
