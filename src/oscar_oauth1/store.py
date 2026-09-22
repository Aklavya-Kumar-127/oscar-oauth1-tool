"""Access token persistence.

OAuth 1.0a returns no expiry: the TTL is set per client in Oscar's admin UI and
is never sent to us, so the issue time we record is the only expiry signal that
will ever exist. There is also no refresh token: when it dies, a human walks
the authorisation screen again.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class AccessToken:
    oauth_token: str
    oauth_token_secret: str
    issued_at: float

    @property
    def age_seconds(self) -> float:
        return time.time() - self.issued_at

    def remaining_seconds(self, ttl_seconds: Optional[int]) -> Optional[float]:
        """Seconds left before `ttl_seconds` lapses, or None when it's not
        known. Oscar never sends a TTL itself, so this is only ever as good
        as whatever the caller was told to enter by hand off the client's own
        admin UI. A negative result means the token is already past it, even
        though this object still holds it — nothing here calls Oscar to find
        out."""
        if ttl_seconds is None:
            return None
        return self.issued_at + ttl_seconds - time.time()


def save(path: Path, token: str, token_secret: str) -> AccessToken:
    record = AccessToken(token, token_secret, time.time())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(record), indent=2), encoding="utf-8")
    path.chmod(0o600)
    return record


def load(path: Path) -> Optional[AccessToken]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AccessToken(
            data["oauth_token"], data["oauth_token_secret"], float(data["issued_at"])
        )
    except (json.JSONDecodeError, KeyError, ValueError):
        return None
