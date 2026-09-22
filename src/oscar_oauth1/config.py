from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    host: str
    context_path: str
    consumer_key: str
    consumer_secret: str
    callback_url: str
    token_file: Path
    token_ttl_seconds: Optional[int]

    @property
    def oauth_base(self) -> str:
        return f"{self.host}{self.context_path}/ws/oauth"

    @property
    def services_base(self) -> str:
        return f"{self.host}{self.context_path}/ws/services"


def load_settings() -> Settings:
    load_dotenv()
    missing = [
        name
        for name in ("OSCAR_HOST", "OSCAR_CONSUMER_KEY", "OSCAR_CONSUMER_SECRET")
        if not os.getenv(name)
    ]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Copy .env.template to .env and fill it in."
        )

    # Oscar never returns this: it has to be read off the client's own
    # Administration Panel > Integration screen and entered here by hand.
    # Left unset, expiry can only ever be guessed from issue time.
    raw_ttl = os.getenv("OSCAR_TOKEN_TTL_SECONDS", "").strip()
    token_ttl_seconds = int(raw_ttl) if raw_ttl else None

    return Settings(
        host=os.environ["OSCAR_HOST"].rstrip("/"),
        context_path="/" + os.getenv("OSCAR_CONTEXT_PATH", "/oscar").strip("/"),
        consumer_key=os.environ["OSCAR_CONSUMER_KEY"],
        consumer_secret=os.environ["OSCAR_CONSUMER_SECRET"],
        callback_url=os.getenv("OSCAR_CALLBACK_URL", "http://localhost:3000/oauth1/callback"),
        token_file=Path(os.getenv("OSCAR_TOKEN_FILE", ".tokens/oscar_token.json")),
        token_ttl_seconds=token_ttl_seconds,
    )
