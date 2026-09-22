# Integration plan: bringing this into `medozai-api`

This is a plan, not a migration in progress. Nothing in `medozai-api`
changes as a result of it; it records the target shape, the decisions that
are still open, and the order of work, so that the eventual integration
starts from an agreed design instead of a blank page. See the main
[README.md](README.md) for how this tool itself works.

Every path, model, and service named below was cross-checked directly
against `medozai-api`'s source (2026-09-21), not assumed. One correction
came out of that check that matters enough to call out up front: the
existing Oscar HTTP client is **synchronous by design**, not async — see
[Client, below](#target-shape). The rest of this document reflects what was
actually found, with the file each claim is checked against cited inline.

`medozai-api` is a multi-tenant FastAPI service (Python 3.12, SQLAlchemy,
Postgres, GCP Cloud Run) with a strict Router → Service → Repository → Model
layering. Every request carries an `x-tenant-uuid`, and most models are
tenant-scoped by a SQLAlchemy loader-criteria listener. Cross-EMR
integrations live under `app/factories/`, one subpackage per EMR/surface.

## Summary

Port this tool's OAuth 1.0a signing logic into `medozai-api` as a new,
top-level `oscar_ws1` integration — sibling to the existing OAuth2/FHIR Oscar
integration, not a change to it — so clinic admins can authorize a legacy
Oscar `/ws/services` connection once through the API instead of running this
CLI by hand, and so `get`/`find`/`add`-specialist calls become available to
other services. It ships in 8 phases (below), starting with the pure
signing/HTTP primitives (no DB, no routes, low risk) and ending with
per-tenant onboarding and a decision on this standalone tool's future.
The two hard constraints that shape every phase: the ported client stays
**synchronous** (matching the repo's existing EMR-client convention, not the
async default), and OAuth 1.0a has **no refresh grant**, so expiry can only
ever be detected and alerted on, never renewed automatically. Three items
need a decision from someone other than the implementer before the relevant
phase starts — see [Open questions](#open-questions) — and Phase 1 is
gated on none of them.

## Stakeholders

- **Implementer:** Rudra Aklavya (<akumar@medozai.com>) — owns this plan and
  the phased build-out.
- **`app/factories/` owner:** needs to confirm the new top-level
  `oscar_ws1/` package placement before Phase 1 lands (see
  [Why this is a sibling integration](#why-this-is-a-sibling-integration-not-a-merge)).
- **Downstream ticket owner (specialist operations):** decides the
  `Referral`-vs-new-model question before Phase 4; see
  [Domain mapping](#domain-mapping-the-specialist-operations).
- **Per-tenant Oscar admin (starting with `medozai-dev`):** registers the
  OAuth1 client in Oscar's admin UI, hands over the consumer key/secret, and
  is the point of contact for the reauth runbook once Phase 6 ships.
- **On-call / alerting owner:** decides whether the new `oscar_ws1` event
  constants need their own Cloud Monitoring policy before Phase 6
  (see [Open questions](#open-questions)).

## Why this is a sibling integration, not a merge

`medozai-api` already integrates with Oscar — but a different Oscar surface,
under a different auth model: `app/factories/fhir/integrations/oscar/` plus
`app/services/oscar_auth_service.py`, `oscar_token_service.py`, and
`oscar_keep_alive_service.py` implement OAuth 2.0 / OIDC via Okta
(authorization-code + PKCE + refresh-token rotation) against Oscar's FHIR
API. This tool signs OAuth 1.0a requests (HMAC-SHA1, three legs, no refresh
grant) against Oscar's legacy `/ws/services` layer. Same vendor, unrelated
auth machinery, unrelated endpoints. It becomes a **sibling** integration,
not a change to the existing one, and it needs its own name so the two don't
collide.

Naming needs more care than "put it under `factories/legacy/`", which was
the first instinct and turned out not to match how this repo actually
organizes integrations. `app/factories/` has exactly three top-level
packages — `base/` (native Medozai defaults), `fhir/` (protocol family;
`fhir/integrations/{accuro,ecw,ecw2,oscar}/` are the vendor implementations
under it), and `medozai/` (concrete Medozai-specific factories referenced
from `factories/base/registry.py::IMPLEMENTATION_REGISTRY`) — and no
`legacy/` package exists anywhere in the tree (checked: no match for
"legacy" across `app/`). Nesting the OAuth1 client under `fhir/integrations/`
would misdescribe it — it is not FHIR — so the better fit is a new
**top-level** package, sibling to `fhir/` and `medozai/`:
`app/factories/oscar_ws1/`, with tables prefixed `oscar_ws1_*` (parallel to,
not shared with, `oscar_auth_*`, which belongs to the OAuth2 integration).
This is still a judgment call for whoever owns `app/factories/` — flagged
again under [Open questions](#open-questions) — but it's grounded in the
actual layout rather than invented.

## Target shape

`app/factories/` is strictly the protocol/HTTP layer — confirmed by reading
what the existing service actually imports: `app/services/oscar_auth_service.py`
pulls from `factories.fhir.integrations.oscar.{api_service,oauth_config,token_client}`,
`models.oscar_auth`, `repositories.oscar_auth_repository`. The service,
repository, model, and router are **top-level** packages (`app/services/`,
`app/repositories/`, `app/models/`, `app/routers/v1/`), never nested inside
`app/factories/`. Called out explicitly because "port into
`app/factories/oscar_ws1/`" (phase 1, below) could otherwise be misread as
"everything lives there" — it doesn't; only the signing/HTTP code does.

| Layer | New piece | Modeled on |
| --- | --- | --- |
| Signing + client (`app/factories/oscar_ws1/`) | `oauth1.py` ported verbatim (pure, no I/O); `client.py`'s three methods ported **as synchronous httpx**, not converted to async | `factories/fhir/integrations/oscar/token_client.py` — its own docstring: *"Synchronous on purpose. The request-path refresh runs inside a locked database transaction, and holding a sync SQLAlchemy transaction open across an `await` is a hazard; the async callers wrap this in a threadpool instead."* Resolves what used to be an open question here: the repo's convention for an EMR HTTP client is sync, so `client.py`'s existing `httpx.Client` usage needs adaptation (settings source, logging), not a rewrite onto `httpx.AsyncClient`. |
| Routes (`app/routers/v1/oscar_ws1.py`) | plain `def`, not `async def`, throughout the three-leg flow | `app/routers/v1/oscar_auth.py::oscar_callback` — its own docstring: *"Deliberately a sync `def`: the code exchange is a blocking HTTP call, and FastAPI runs sync routes in a threadpool, so it never occupies the event loop. Making this `async` would."* Checked for a manual wrapping helper (`run_in_threadpool` / `asyncio.to_thread`) in `oscar_auth_service.py`, `oscar_token_service.py`, and the router itself — none exists; that line means FastAPI's own dispatch of a plain `def` route, not a helper to import. So the whole chain — route, service, repository call, `client.py` call — stays sync `def`; nothing here needs `await`. |
| Per-tenant credentials | consumer key/secret read via `get_tenant_config_secret` from a per-tenant Secret Manager blob, not a DB column | `app/utils/retrieve_tenant_config.py::get_tenant_config_secret`, used the same way by `factories/fhir/integrations/oscar/oauth_config.py::load_oscar_oauth_config` and `.../ecw2/api_service.py`. No documented mechanism was found for how a *new* tenant's secret blob actually gets created (no onboarding script, no Terraform resource per tenant) — flagged under [Open questions](#open-questions) rather than assumed. |
| Token persistence (`app/models/oscar_ws1.py`) | new `oscar_ws1_tokens` table: tenant_uuid FK, oauth_token, oauth_token_secret (encrypted), issued_at, status | `app/models/oscar_auth.py::OscarAuthToken` (`access_token` plaintext, `refresh_token_encrypted` Fernet-encrypted, `status` ACTIVE/REAUTH_REQUIRED) + `app/utils/oscar_token_crypto.py` (`MultiFernet` over a rotatable key list) |
| Pending-authorization state (same file) | new `oscar_ws1_states` table: `oauth_token`, `oauth_token_secret` (temporary), `tenant_uuid`, `expires_at`, `consumed_at` | `app/models/oscar_auth.py::OscarAuthState` — the existing OAuth2 flow already solves exactly this problem for its own leg 1↔leg 3 handoff; replicate its shape rather than inventing a new one |
| Web endpoints | `POST /v1/oscar_ws1/start`, `GET /v1/oscar_ws1/callback` | `app/routers/v1/oscar_auth.py` |
| Business logic (`app/services/oscar_ws1_service.py`) | a service orchestrating the three legs + signed calls, calling into `app/factories/oscar_ws1/` for HTTP and `app/repositories/oscar_ws1_repository.py` for storage | `app/services/oscar_auth_service.py` |
| Persistence (`app/repositories/oscar_ws1_repository.py`) | repository over the new token/state tables | `app/repositories/oscar_auth_repository.py` |
| Alerting | structured `OSCAR_WS1_REAUTH_REQUIRED`-style log event, **new event/code constants**, not the existing ones | `app/utils/oscar_alerting.py` — its own docstring warns live Cloud Monitoring alert policies match on its exact string values (`EVENT_OSCAR_AUTH_FAILURE`, `CODE_REAUTH_REQUIRED`), so reusing them for a different integration would either misfire those policies or require them to special-case two unrelated failure modes. Mint new constants in the same structured-log shape instead. |

### Tenant identity through the three-leg flow

The local callback server (`server.py`) does not carry over as-is: it holds
one pending temporary-credential pair in process memory, which is fine for a
single operator running the CLI but not for a multi-tenant API where several
clinic admins could be mid-authorization concurrently.

The OAuth2 integration already solved this exact problem and the solution is
directly reusable in shape: per `app/dependencies.py::get_oscar_auth_service`,
*"the start route takes the tenant in its body; the callback is a bare
browser redirect and reads the tenant from the PKCE state row."* Neither
endpoint carries an `x-tenant-uuid` header — a browser redirect from Oscar's
own login screen can't be made to send one. The OAuth1 flow should follow the
same pattern: `POST /v1/oscar_ws1/start` takes `tenant_uuid` in its request
body (not a header), writes a pending `oscar_ws1_states` row keyed by the
temporary `oauth_token`, and `GET /v1/oscar_ws1/callback` recovers the tenant
from that row rather than from any header the browser redirect won't carry.

## Token lifecycle: this is the part that cannot be engineered away

OAuth 1.0a has no refresh grant. The TTL is set per Oscar client in that
tenant's own admin UI and is never returned in any response — see
[README.md § When it returns 401](README.md#when-it-returns-401).

It's worth being precise about how this differs from the OAuth2 integration's
expiry problem, because the two look similar but aren't: OAuth2's
`oscar_keep_alive_service.py` (Component 2, `terraform/oscar_keepalive_scheduler.tf`,
every 2h) exists as a *backstop* for **idle** tenants only — a tenant with
live traffic keeps its own refresh chain alive through the request-path lazy
refresh (`oscar_token_service.py`, Component 3), and only a tenant that goes
fully quiet for 6h+ needs the scheduled job, with a further 3-day grace
window before it's unrecoverable. OAuth1 has no analog to Component 3 at
all — there is no request-path action, lazy or otherwise, that can extend a
token's life, because there is no refresh grant to spend. So for OAuth1 a
scheduled detect-and-alert job isn't a backstop for the idle case; it is the
*only* mechanism that exists, for every tenant, active or not. That makes it
more load-bearing here than Component 2 is for OAuth2, not equally so.

The job itself: compare `issued_at` (or a computed expiry) against a
per-tenant configured TTL, and before it lapses, flip status to
`REAUTH_REQUIRED` and emit the structured alert event described above
(`app/utils/oscar_alerting.py`-shaped, new constants) plus a human-facing
notification along the lines of `oscar_reauth_email_service.py`. It cannot
renew anything — a human has to re-walk the browser flow. This needs an
operational agreement, not just code: who administers each Oscar tenant's
client registration, what TTL they've set (never returned by Oscar itself,
so it has to be entered and kept in sync by hand per tenant), and who acts on
the alert.

## Domain mapping: the specialist operations

No `Specialist` model exists in `medozai-api` today. The closest existing
domain is `Referral` (`app/models/referral.py`, `TenantMixin` +
`AuditMixin` + `SoftDeleteMixin`), which already carries
`referring_clinic_name`, `referring_provider_contact`,
`referring_provider_email`, `referring_provider_phone`. Checked directly:
these are **flat string columns today, not a foreign key to anything** — so
the two options carry a real schema consequence, not just a code-organization
one:

1. A first-class `Specialist` model + repository/service — cleaner if a
   specialist record is looked up once and reused across many referrals, but
   requires a migration adding a nullable FK (e.g. `specialist_id`) on
   `Referral`, and a decision about whether the existing string columns stay
   as a denormalized snapshot or get backfilled/deprecated.
2. Fold `get`/`find`/`add`-specialist into `ReferralService` as a helper that
   populates the existing string columns directly — no migration, but no
   reuse of a specialist record across referrals either, and the id Oscar
   returns from `add-specialist` would have nowhere of its own to live.

This should be settled by whoever owns the downstream ticket that actually
consumes these operations, not decided speculatively here.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| OAuth1 tokens have no refresh grant — a lapsed token means a hard outage for that tenant's specialist operations until a human re-walks the browser flow. | Phase 6 expiry detection ships before Phase 7 (multi-tenant onboarding), not after, so no tenant beyond the pilot goes live without an alert path. See [Token lifecycle](#token-lifecycle-this-is-the-part-that-cannot-be-engineered-away). |
| `oauth_token`/`oauth_token_secret` and the consumer key/secret are bearer credentials against a clinic's live Oscar instance; a leak is a PHI-adjacent incident, not just an API outage. | Follow the existing `oscar_auth` encryption-at-rest precedent (`MultiFernet`) for `oscar_ws1_tokens`; resolve the open question on encrypting `oauth_token` (not just the secret) before Phase 2 merges, not after. |
| The new top-level `app/factories/oscar_ws1/` package is a judgment call, not a confirmed convention — landing Phase 1 before the `app/factories/` owner signs off risks a rename/rework later. | Get explicit sign-off on the package placement before Phase 1 starts (see [Stakeholders](#stakeholders)); the primitives themselves (`oauth1.py`, `client.py`) are pure and cheap to relocate if the package name changes, but routes/models/migrations in later phases are not. |
| Reusing the OAuth2 integration's alert event constants would misfire or dilute existing Cloud Monitoring policies that match on exact string values. | Already designed around in [Target shape](#target-shape): mint new `oscar_ws1`-scoped constants. Confirm with the alerting owner whether those constants need a new policy authored before Phase 6 ships (see [Open questions](#open-questions)). |
| The pilot tenant (`medozai-dev`) validates the happy path but not multi-tenant concurrency (two clinics mid-authorization at once) or Oscar-side rate limits/outages. | Phase 3 is manually triggered and restricted to one tenant by design; do not treat pilot success as sufficient signal for Phase 7 without at least one second tenant exercising the flow concurrently with the pilot. |
| Domain-mapping decision (Referral vs. new `Specialist` model) is deferred to a downstream ticket owner who doesn't yet exist as a named stakeholder. | Do not start Phase 4 until that owner is identified and has made the call — starting the read path against an undecided schema risks a migration redo. |

## Phased rollout

Effort is a rough order-of-magnitude estimate for one engineer already
familiar with this tool's codebase, not a committed estimate — sizing this
for real would need the `app/factories/` and alerting-policy questions
answered first (see [Risks, above](#risks-and-mitigations)).

1. **Primitives only** (~2-3 days). Port `oauth1.py` and the signing/HTTP logic in
   `client.py` into `app/factories/oscar_ws1/` as synchronous `httpx`,
   matching `token_client.py`'s documented convention — no async rewrite.
   Carry the 39 existing signing tests over unchanged, named to match the
   repo's convention (`test_oscar_ws1_client.py` alongside the existing
   `test_oscar_token_client.py` in `app/tests/test_factories/`). No DB, no
   routes, no behavior visible outside the module.
2. **Credentials and storage** (~3-4 days). Add the per-tenant Secret Manager config
   lookup, the `oscar_ws1_tokens` table, and the `oscar_ws1_states` table
   (mirroring `OscarAuthToken`/`OscarAuthState`) + repository, with
   service-layer and repository-layer tests named to match the existing
   pair — `test_oscar_ws1_service.py` / `test_oscar_ws1_repository.py`,
   alongside `test_oscar_auth_service.py` / `test_oscar_auth_repository.py`
   in `app/tests/test_services/` and `test_repositories/`. No new test
   infrastructure is needed for the Secret Manager lookup itself: `conftest.py`
   already patches `SecretManagerServiceClient` globally
   (`mock_secret_manager` fixture), so the new per-tenant credential lookup is
   covered by the existing pattern, not a new one. The migration
   follows `database/migrations/V99__oscar_auth.sql` (Flyway,
   `V<n>__<description>.sql`, sequential); the actual number can't be fixed
   here since it depends on what else has landed by the time this merges —
   the highest existing migration at the time of this check is `V99`.
3. **The three-leg flow as routes** (~3-4 days). `start` takes `tenant_uuid` in its body,
   `callback` reads it back from the pending-state row — see
   [Tenant identity, above](#tenant-identity-through-the-three-leg-flow)),
   restricted to one pilot tenant (`medozai-dev`), triggered manually by an
   admin — no scheduler yet.
4. **Read path** (~2-3 days, blocked on the domain-mapping owner — see
   [Risks, above](#risks-and-mitigations)). Wire `get-specialist` / `find-specialist` into whichever
   service owns the domain mapping decided above, behind a feature flag.
5. **Write path** (~2 days). Add `add-specialist` once the read path is validated
   against the pilot tenant.
6. **Expiry detection** (~3-4 days). Add the Cloud Scheduler job + structured alert
   (new `oscar_ws1`-scoped event constants, not the OAuth2 integration's —
   see [Target shape, above](#target-shape)) + reauth notification. Unlike
   the OAuth2 keep-alive job this is not a backstop for idle tenants; it is
   the only expiry-handling mechanism OAuth1 has, for every tenant — see
   [Token lifecycle, above](#token-lifecycle-this-is-the-part-that-cannot-be-engineered-away).
7. **Per-tenant onboarding** (~1 day per tenant, ongoing). Roll out to further tenants one at a time —
   each requires that clinic's Oscar admin to register a client and hand
   over a key/secret, so this is manual onboarding, not a global flag flip.
8. **Retire or keep the standalone tool** (~0.5 day, decision + doc update). Once the pilot tenant is validated
   end to end inside `medozai-api`, decide whether to retire this repo or
   keep it as an operator's out-of-band diagnostic tool — its `probe` and
   [README.md § When it returns 401](README.md#when-it-returns-401) workflow
   have no dependency on the API being up, which is exactly when they're
   most useful.

## Rollback / kill switch

Each phase is designed to be independently revertible without touching the
phases before it:

- **Phases 1-2** (primitives, credentials, storage) ship no routes and are
  invisible outside the module — reverting is a plain code revert, no data
  migration to undo beyond dropping the two new (empty, until Phase 3) tables.
- **Phase 3** (routes, pilot tenant only) — disable by removing/feature-flagging
  the two routes; no other tenant has state in `oscar_ws1_states` /
  `oscar_ws1_tokens` to worry about, since onboarding (Phase 7) hasn't started.
- **Phases 4-5** (read/write specialist paths) ship behind a feature flag by
  design — turning it off is the rollback, no migration involved.
- **Phase 6** (expiry alerting) — the job and alert are additive; disabling the
  Cloud Scheduler job is sufficient and does not affect the token/state tables.
- **Phase 7** (per-tenant onboarding) is inherently gradual and manual, so
  "rollback" for a given tenant means pausing further onboarding and, if that
  tenant's token has already gone live, coordinating with its Oscar admin
  before disabling the connection out from under active use.
- No phase requires a destructive migration (dropping/altering existing
  tables); the two new tables are additive, so a full rollback at any point
  before Phase 7 is a code revert plus leaving the (unused) tables in place.

## Definition of done (pilot, Phases 1-6)

- All 39 ported signing tests plus new service/repository tests pass in CI,
  named per the existing repo convention (see Phases 1-2 above).
- `medozai-dev` can complete the three-leg authorization flow end to end
  through `POST /v1/oscar_ws1/start` / `GET /v1/oscar_ws1/callback`, with the
  resulting token persisted and usable by `get`/`find`/`add`-specialist.
- A deliberately-expired token for `medozai-dev` produces the structured
  `OSCAR_WS1_REAUTH_REQUIRED`-style alert and a human-facing notification,
  verified in a non-prod environment before Phase 7 onboarding begins.
- The `app/factories/` owner has confirmed the `oscar_ws1/` package
  placement, and the domain-mapping decision (Referral vs. new model) has an
  owner and an answer, both recorded against the relevant open questions
  below rather than left implicit.
- `CLAUDE.md`'s four repo-wide rules still hold under review — see
  [Compliance with the repo's own rules](#compliance-with-the-repos-own-rules).

## Open questions

- Which downstream ticket/workflow actually consumes the specialist
  operations — this decides the `Referral`-vs-new-model question, and
  whether `Referral` needs a migration for a `specialist_id` FK.
- Who administers each Oscar tenant's client registration and TTL, for the
  reauth runbook — Oscar never returns the TTL itself, so it has to be
  entered and kept in sync by hand per tenant.
- How a new tenant's Secret Manager credential blob actually gets created.
  `get_tenant_config_secret` (in `app/utils/retrieve_tenant_config.py`) is
  how it's *read*, and that part is well-precedented; no script, runbook, or
  Terraform resource for *writing* one per tenant turned up in this repo, for
  either the existing OAuth2 integration or `ecw2`. Either such a mechanism
  exists outside this repo (ops runbook, manual `gcloud` step) or it needs to
  be built — this should be confirmed before Phase 7, not assumed.
- Whether `app/factories/oscar_ws1/` (new top-level, sibling to `fhir/` and
  `medozai/`) is the naming the `app/factories/` owner actually wants, given
  no existing precedent covers a non-FHIR EMR integration — see
  [Why this is a sibling integration, above](#why-this-is-a-sibling-integration-not-a-merge).
- Whether new Cloud Monitoring alert policies need to be authored for the new
  `oscar_ws1` event constants, or whether an existing policy can be
  broadened safely — `oscar_alerting.py` warns live policies match on exact
  string values, so this isn't a decision to make inside the code alone.
- Whether `oauth_token` (not just `oauth_token_secret`) needs encryption at
  rest — it's replayed in every `Authorization` header, so treat it as
  sensitive by default.

## Compliance with the repo's own rules

`CLAUDE.md` states four rules that bind every new resource in this repo, not
just this one; checking the target shape above against them:

- *"4-layer architecture strictly: Router → Service → Repository → Model. No
  exceptions."* — matches the table under [Target shape](#target-shape):
  router → `OscarWs1Service`-equivalent → repository → the two new models.
- *"No `db.query` outside `app/repositories/`."* — the new repository is
  where `oscar_ws1_tokens`/`oscar_ws1_states` reads and writes belong; the
  service layer must not query them directly.
- *"Services receive `RequestContext`, never construct it themselves."* —
  matches the existing `get_oscar_auth_service(ctx=Depends(...))` pattern in
  `app/dependencies.py`; the new service's DI provider should follow it
  rather than building a context inline.
- *"URL path segments use underscores, never dashes."* — `/v1/oscar_ws1/...`
  already complies; called out only because it's a real lint the repo
  enforces, not a style preference.

## Non-goals

- Not merging with the existing OAuth2/FHIR Oscar integration. Different
  auth model, different surface, kept as siblings.
- Not solving the TTL/no-refresh-token problem in code. OAuth 1.0a has no
  refresh grant; this is an operational agreement, not an engineering gap.
