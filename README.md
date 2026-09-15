# oscar-oauth1-tool

Standalone OAuth 1.0a (RFC 5849) client for Oscar EMR's legacy web-services
layer at `{host}/oscar/ws/services`. MD-1584.

This is deliberately **not** part of `medozai-api`. The goal is to prove the
flow end to end — fetch a token, call one endpoint, get a 200 — before any of
it is integrated.

Oscar's legacy services layer is not the FHIR API. Different credential,
different base path, different auth model: FHIR uses an OAuth2 bearer token.

> There is no "OAuth 1.1". The spec is OAuth 1.0a, RFC 5849. The "a" revision
> is the one that added `oauth_verifier`; plain 1.0 omits it and leg 3 fails.

## Layout

| Path | Contents |
|---|---|
| `src/oscar_oauth1/oauth1.py` | The signing primitives. Pure — no I/O, no framework imports. |
| `tests/test_oauth1.py` | The acceptance vectors, including RFC 5849 §3.4.1.1. |
| `src/oscar_oauth1/client.py` | The three legs and a signed-call helper. |
| `src/oscar_oauth1/server.py` | Local callback server for the browser leg. |
| `src/oscar_oauth1/probe.py` | Base path discovery. |
| `src/oscar_oauth1/cli.py` | `probe`, `serve`, `status`, `wadl`, `specialist`, `call`. |

## Setup

```bash
uv venv --python 3.12 && uv sync --extra dev
cp .env.template .env      # then fill in key, secret, host
uv run pytest -q           # 35 tests, no network
```

### Registering the client in Oscar

A human step, in Oscar's admin UI. Register a client named **RASMI** with the
callback URL below, then paste the generated key and secret into `.env`:

```
http://localhost:3000/oauth1/callback
```

The callback must match byte for byte — it is part of the leg 1 signature.

## Running the flow

```bash
uv run oscar-oauth1 probe        # which base path actually reaches Oscar
uv run oscar-oauth1 serve        # then open http://localhost:3000
```

`/oauth1/start` runs leg 1 and redirects to Oscar's login. After approving,
Oscar returns to `/oauth1/callback` with `oauth_token` and `oauth_verifier`;
the server checks the token against the one this session requested, runs leg 3,
and writes the access token to `OSCAR_TOKEN_FILE`.

```bash
uv run oscar-oauth1 specialist --spec-id 5
uv run oscar-oauth1 find-specialist --last-name Smith
uv run oscar-oauth1 wadl
```

## The two endpoints, as the WADL declares them

Read off `medozai-dev`'s own WADL, base
`https://medozai-dev.kai-oscar.com/oscar/ws/services`:

| Ticket | Method | Path | Query parameters |
|---|---|---|---|
| Get specialist | `GET` | `/consults/getProfessionalSpecialist` | `specId` (int) |
| Find specialist | `GET` | `/professionalSpecialist/search` | `referralNo` (string), `lastName` (string) |

Both answer `application/json`.

## Credentials on disk

The access token is written to `.tokens/oscar_token.json` (mode 600), which is
gitignored, as is `.env`. Delete the file to force a full re-authorisation.
Nothing else is persisted, and the temporary token secret never leaves memory.

## Why the tests come first

Every mistake in OAuth 1.0a — a wrong encoder, an unsorted parameter, a missing
`&` — produces the same unmessaged 401 as a wrong credential. Debugging that
over the wire costs hours and teaches nothing. The signing functions do no I/O
precisely so they can be proven offline, and `sign_request` returns the
signature base string, which is logged with every request because it is the
only artefact that explains a failure.

## When it returns 401

1. **`Accept` header.** A 406 is evaluated *before* auth and says nothing about
   signing. `application/fhir+json` and `text/html` both 406 here.
2. **Base path.** 403 is Cloudflare, 404 is the wrong context path. Run `probe`.
3. **Print the base string** and confirm the query parameters are in it.
4. **Signing key ends with `&`** when there is no token secret.
5. **Clock skew.** Timestamps are seconds.
6. **Nonce fresh per request** — reuse inside the window reads as a replay.
7. **Expiry.** The TTL is per client, set in the admin UI, and never returned
   in the token response — Administration Panel > Integration shows it, and the
   dev tenant's clients use 60000 s (~16.7 h). OAuth 1.0a has no refresh token:
   re-run all three legs.

That last point is structural. Any integration on this needs either a long TTL
agreed with the EMR administrator or an operational plan for re-authorising.
It cannot be solved in code.

## Observed on medozai-dev, 2026-09-15

The brief predicts `401` + `WWW-Authenticate: OAuth` on the bare services path.
**This tenant does not do that** — probed unauthenticated:

| Path | Status | Note |
|---|---|---|
| `/oscar/ws/services?_wadl` | **200** `application/xml` | 297 operations. Served without auth. |
| `/oscar/ws/services/` | 200 `text/html` | trailing slash matters |
| `/oscar/ws/services` | 404 | bare path is not routed; **not** a signing problem |
| `/oscar/ws/rs?_wadl` | 200 `application/xml` | the second surface |
| `/ws/services` | 403 | Cloudflare — never reached Oscar |
| `/kaiemr/ws/services` | 404 | wrong context path |
| `/oscar/ws/oauth/initiate` | 500 on bare `GET` | expects a signed `POST` |

So on this tenant `?_wadl` returning 200 is the liveness check, not the 401.
A bare-path 404 here means nothing about your signature.

## Other observed behaviour

- `OPTIONS` returns 204 with no `Allow` header — method discovery does not work.
  Probe the verb against a harmless id and read 405-vs-404 instead.
- A read for a missing record is 204 on some operations and 404 on others.
  Neither is an auth failure.
- Malformed requests return a branded HTML error page; the status line is the
  only signal.
- JSON request bodies are **not** signed. OAuth 1.0a folds a body into the base
  string only when it is `application/x-www-form-urlencoded`.
