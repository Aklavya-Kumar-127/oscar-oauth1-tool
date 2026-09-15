"""Base path discovery. Run this before debugging a single signature.

  /ws/services         403  -> Cloudflare, never reached Oscar
  /kaiemr/ws/services  404  -> wrong context path (FHIR module)

The brief predicts 401 + WWW-Authenticate: OAuth on the bare services path. The
medozai-dev tenant does not do that: the bare path 404s and only the trailing
slash or ?_wadl answers. So ?_wadl is the liveness check here, not the 401.
"""

from __future__ import annotations

import httpx

from .config import Settings

CANDIDATES = (
    "/oscar/ws/services?_wadl",
    "/oscar/ws/services",
    "/ws/services",
    "/kaiemr/ws/services",
    "/oscar/ws/rs?_wadl",
)

_MEANING = {
    200: "reached Oscar; the services layer is live",
    401: "reached Oscar; the 401 is the server asking you to sign",
    403: "Cloudflare challenge — you never reached Oscar",
    404: "wrong context path, or a bare path this tenant does not route",
    406: "Accept header rejected before auth was evaluated",
}


def probe_base_paths(settings: Settings) -> list[dict]:
    results = []
    with httpx.Client(timeout=20.0, follow_redirects=False) as http:
        for path in CANDIDATES:
            url = f"{settings.host}{path}"
            try:
                response = http.get(url, headers={"Accept": "*/*"})
            except httpx.HTTPError as exc:
                results.append({"url": url, "error": str(exc)})
                continue
            results.append(
                {
                    "url": url,
                    "status": response.status_code,
                    "www_authenticate": response.headers.get("www-authenticate"),
                    "meaning": _MEANING.get(response.status_code, "unexpected"),
                }
            )
    return results
