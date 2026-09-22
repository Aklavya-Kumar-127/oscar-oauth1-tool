"""Local callback server driving the three-legged flow, plus a small UI for
the ticketed specialist operations.

Run it, open /, authorise, then work from /app. The temporary token secret
lives in this process and is never written into a page or a redirect.

The UI exists so the operations can be exercised without the terminal. It
calls the same functions the CLI does, from `specialists`, so the two front
ends cannot drift apart. Every operation route refuses to act without a stored
access token, so nothing can be sent to Oscar before the browser leg is done.
"""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import specialists, store
from .client import ApiResult, OAuthError, OscarOAuth1Client
from .config import Settings, load_settings
from .store import AccessToken

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

app = FastAPI(title="Oscar OAuth 1.0a tool")

# Temporary credentials, keyed by temporary token. Server-side only: the secret
# must never reach the browser.
_pending: dict[str, str] = {}

# Wire name -> the label Oscar's own admin UI uses, in the order a person reads
# a record rather than the order the DTO declares. The third element marks
# values that are codes rather than prose, so they can be set in the mono face
# and aligned as figures.
FIELDS = [
    ("id", "ID", True),
    ("firstName", "First name", False),
    ("lastName", "Last name", False),
    ("name", "Display name", False),
    ("referralNo", "Referral number", True),
    ("phoneNumber", "Phone", True),
    ("faxNumber", "Fax", True),
    ("emailAddress", "Email", False),
    ("streetAddress", "Clinic name", False),
    ("specialtyType", "Specialty", False),
    ("professionalLetters", "Letters", False),
    ("webSite", "Website", False),
    ("annotation", "Annotation", False),
    ("institutionId", "Institution ID", True),
    ("departmentId", "Department ID", True),
    ("eformId", "eForm ID", True),
    ("eDataUrl", "eData URL", False),
    ("eDataOscarKey", "eData Oscar key", True),
    ("eDataServiceKey", "eData service key", True),
    ("eDataServiceName", "eData service name", False),
]


def _client() -> OscarOAuth1Client:
    return OscarOAuth1Client(load_settings())


HEAD = """
<title>Oscar OAuth 1.0a</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Crect x='5' y='11' width='14' height='10' rx='2.5' fill='%231f6b4f'/%3E%3Cpath d='M8 11V7a4 4 0 0 1 8 0v4' fill='none' stroke='%231f6b4f' stroke-width='2'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
  :root {
    color-scheme: light dark;
    --bg:       #eef2ef;
    --surface:  #ffffff;
    --raised:   #f7f9f7;
    --ink:      #16201c;
    --ink2:     #55635d;
    --ink3:     #8a968f;
    --rule:     #dbe2dd;
    --rule2:    #c7d1cb;
    --accent:   #1f6b4f;
    --accent-2: #185740;
    --on-accent:#ffffff;
    --ok:       #1f6b4f;
    --ok-bg:    #e3efe8;
    --warn:     #8a5a16;
    --warn-bg:  #f8efdd;
    --bad:      #9d3f1c;
    --bad-bg:   #f8e7df;
    --sans: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
    --mono: "IBM Plex Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
    --r: 8px;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:       #0e1311;
      --surface:  #161d19;
      --raised:   #1b241f;
      --ink:      #e7eee9;
      --ink2:     #9fada6;
      --ink3:     #74827b;
      --rule:     #262f2a;
      --rule2:    #34403a;
      --accent:   #43a077;
      --accent-2: #4fb888;
      --on-accent:#08140f;
      --ok:       #67c79e;
      --ok-bg:    #16261f;
      --warn:     #d8a761;
      --warn-bg:  #2a2318;
      --bad:      #e08a63;
      --bad-bg:   #2b1d16;
    }
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; }
  body { background: var(--bg); color: var(--ink); font-family: var(--sans);
         font-size: 15px; line-height: 1.55;
         -webkit-font-smoothing: antialiased; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px;
                   border-radius: 3px; }

  /* ---- frame ---- */
  .topbar { position: sticky; top: 0; z-index: 20; background: var(--surface);
            border-bottom: 1px solid var(--rule); }
  .topbar-in { display: flex; flex-wrap: wrap; align-items: center;
               gap: 10px 22px; padding: 12px 28px; }
  .brand { display: flex; align-items: baseline; gap: 10px; }
  .brand b { font-size: 0.97rem; font-weight: 600; letter-spacing: -0.01em; }
  .brand span { font-family: var(--mono); font-size: 0.74rem; color: var(--ink3);
                letter-spacing: 0.02em; }
  .host { font-family: var(--mono); font-size: 0.75rem; color: var(--ink2);
          background: var(--raised); border: 1px solid var(--rule);
          border-radius: 999px; padding: 3px 11px; }
  .spacer { flex: 1 1 auto; }
  nav { display: flex; flex-wrap: wrap; gap: 16px; font-size: 0.85rem; }
  .pill { display: inline-flex; align-items: center; gap: 7px;
          font-size: 0.76rem; font-weight: 600; border-radius: 999px;
          padding: 4px 12px; background: var(--ok-bg); color: var(--ok); }
  .pill.off { background: var(--bad-bg); color: var(--bad); }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
  main { padding: 26px 28px 72px; }

  /* ---- section headings ---- */
  .eyebrow { font-family: var(--mono); font-size: 0.7rem; letter-spacing: 0.12em;
             text-transform: uppercase; color: var(--ink3); margin: 0 0 12px; }

  /* ---- operation cards ---- */
  .grid { display: grid; gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(19rem, 1fr));
          align-items: start; }
  .card { background: var(--surface); border: 1px solid var(--rule);
          border-radius: var(--r); display: flex; flex-direction: column; }
  .card-h { padding: 15px 18px 13px; border-bottom: 1px solid var(--rule); }
  .card-h h2 { margin: 0; font-size: 0.95rem; font-weight: 600; }
  .card-h .verb { font-family: var(--mono); font-size: 0.7rem; font-weight: 500;
                  color: var(--ink3); letter-spacing: 0.04em; display: block;
                  margin-bottom: 3px; }
  .card-b { padding: 4px 18px 18px; }
  label { display: block; font-size: 0.76rem; color: var(--ink2);
          margin: 12px 0 4px; }
  input { width: 100%; padding: 8px 10px; font: inherit; font-size: 0.9rem;
          color: inherit; background: var(--raised);
          border: 1px solid var(--rule2); border-radius: 5px; }
  input:focus { background: var(--surface); }
  button { margin-top: 16px; width: 100%; padding: 9px 16px;
           font: inherit; font-size: 0.9rem; font-weight: 600; cursor: pointer;
           border: 0; border-radius: 5px;
           background: var(--accent); color: var(--on-accent); }
  button:hover { background: var(--accent-2); }
  .hint { font-size: 0.75rem; line-height: 1.5; color: var(--ink3);
          margin: 12px 0 0; }

  /* ---- result ---- */
  .result { background: var(--surface); border: 1px solid var(--rule);
            border-radius: var(--r); margin-bottom: 22px; overflow: hidden; }
  .result-h { display: flex; flex-wrap: wrap; align-items: center;
              gap: 10px 16px; padding: 14px 20px;
              border-bottom: 1px solid var(--rule); background: var(--raised); }
  .status { font-family: var(--mono); font-size: 0.76rem; font-weight: 500;
            border-radius: 5px; padding: 3px 9px;
            background: var(--ok-bg); color: var(--ok); }
  .status.warn { background: var(--warn-bg); color: var(--warn); }
  .status.bad  { background: var(--bad-bg);  color: var(--bad); }
  .result-h .what { font-weight: 600; font-size: 0.92rem; }
  .req { font-family: var(--mono); font-size: 0.73rem; color: var(--ink2);
         word-break: break-all; }
  .idbox { display: inline-flex; align-items: baseline; gap: 9px; }
  .idbox .k { font-size: 0.72rem; color: var(--ink3); text-transform: uppercase;
              letter-spacing: 0.1em; font-family: var(--mono); }
  .idbox .v { font-family: var(--mono); font-size: 1.4rem; font-weight: 500;
              color: var(--ok); line-height: 1; }
  .note { font-size: 0.86rem; color: var(--ink2); }
  .result-b { padding: 18px 20px; }
  .cols { display: grid; gap: 20px;
          grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); }
  @media (max-width: 900px) { .cols { grid-template-columns: 1fr; } }
  .colcap { font-family: var(--mono); font-size: 0.68rem; letter-spacing: 0.1em;
            text-transform: uppercase; color: var(--ink3); margin: 0 0 8px; }
  .scroll { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  tr + tr th, tr + tr td { border-top: 1px solid var(--rule); }
  th, td { text-align: left; padding: 7px 10px; vertical-align: top; }
  th { width: 11.5rem; font-weight: 400; color: var(--ink2); }
  td { word-break: break-word; }
  td.mono { font-family: var(--mono); font-size: 0.82rem;
            font-variant-numeric: tabular-nums; }
  td.unset { color: var(--ink3); font-style: italic; }
  pre { margin: 0; background: var(--raised); border: 1px solid var(--rule);
        border-radius: 6px; padding: 13px 15px; overflow-x: auto;
        font-family: var(--mono); font-size: 0.78rem; line-height: 1.55; }

  /* ---- auth landing ---- */
  .auth { width: 100%; background: var(--surface);
          border: 1px solid var(--rule); border-radius: var(--r);
          overflow: hidden; }
  .auth-h { padding: 18px 24px; border-bottom: 1px solid var(--rule);
            background: var(--raised); display: flex; align-items: center;
            gap: 12px; }
  .auth-h h2 { margin: 0; font-size: 1.05rem; font-weight: 600; }
  .auth-h .dot { width: 9px; height: 9px; background: var(--ok); }
  .auth-h .dot.bad { background: var(--bad); }
  .auth-b { padding: 20px 24px 24px; }
  /* Prose stays at a readable measure even though the card itself now
     spans the full page width. */
  .auth-b p, .steps { max-width: 42rem; }
  .auth-b p { margin: 0 0 14px; }
  .steps { list-style: none; margin: 0 0 18px; padding: 0;
           counter-reset: step; }
  .steps li { position: relative; padding-left: 30px; margin-bottom: 9px;
              font-size: 0.88rem; color: var(--ink2); counter-increment: step; }
  .steps li::before { content: counter(step); position: absolute; left: 0;
                      top: 1px; width: 19px; height: 19px; border-radius: 50%;
                      background: var(--raised); border: 1px solid var(--rule2);
                      font-family: var(--mono); font-size: 0.68rem;
                      color: var(--ink2); display: grid; place-items: center; }
  .actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 4px; }
  .cta { display: inline-block; padding: 10px 22px; border-radius: 5px;
         background: var(--accent); color: var(--on-accent);
         font-weight: 600; font-size: 0.9rem; }
  .cta:hover { background: var(--accent-2); text-decoration: none; }
  .cta.ghost { background: transparent; color: var(--accent);
               box-shadow: inset 0 0 0 1px var(--rule2); }
  .cta.ghost:hover { box-shadow: inset 0 0 0 1px var(--accent);
                     background: transparent; }
  .meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
          gap: 14px 24px; margin: 0 0 18px;
          padding: 14px 0 0; border-top: 1px solid var(--rule); }
  .meta div { font-size: 0.8rem; min-width: 0; }
  .meta dt { font-family: var(--mono); font-size: 0.68rem; color: var(--ink3);
             text-transform: uppercase; letter-spacing: 0.1em; }
  .meta dd { margin: 3px 0 0; color: var(--ink2); word-break: break-word; }
  .meta dd.bad-text { color: var(--bad); font-weight: 500; }
  .meta dd code { font-family: var(--mono); font-size: 0.78rem;
                  background: var(--raised); border: 1px solid var(--rule2);
                  border-radius: 4px; padding: 1px 5px; }

  /* ---- inline banner ---- */
  .banner { display: flex; align-items: flex-start; gap: 10px;
            border-radius: 6px; padding: 11px 14px; margin: 0 0 18px;
            font-size: 0.85rem; line-height: 1.5; }
  .banner .dot { margin-top: 6px; flex: none; }
  .banner.bad  { background: var(--bad-bg);  color: var(--bad); }
  .banner.warn { background: var(--warn-bg); color: var(--warn); }
</style>
"""


def _token():
    settings = load_settings()
    return settings, store.load(settings.token_file)


def _remaining_ttl_hours(settings: Settings, token: AccessToken) -> Optional[float]:
    """Hours left before OSCAR_TOKEN_TTL_SECONDS lapses, or None when that is
    unset and there is nothing to compute from. A negative result means the
    token is past its configured TTL, even though this tool still holds it —
    Oscar answers a 401 for it regardless of what the age display says."""
    remaining = token.remaining_seconds(settings.token_ttl_seconds)
    return None if remaining is None else remaining / 3600


_OPERATIONS_NAV = [
    ("/probe", "Probe base paths"),
    ("/api/specialist?specId=1", "Raw call"),
]


def _shell(
    body: str,
    nav_links: list[tuple[str, str]],
    settings: Settings,
    token: Optional[AccessToken],
) -> str:
    """The page frame. Every page states the host and the token state, because
    both decide whether anything below can work."""
    if token:
        age_h = token.age_seconds / 3600
        remaining_h = _remaining_ttl_hours(settings, token)
        if remaining_h is not None and remaining_h <= 0:
            pill = (
                f'<span class="pill off"><span class="dot"></span>'
                f"Expired &middot; {age_h:.1f} h old</span>"
            )
        else:
            pill = (
                f'<span class="pill"><span class="dot"></span>'
                f"Authorised &middot; {age_h:.1f} h</span>"
            )
    else:
        pill = '<span class="pill off"><span class="dot"></span>Not authorised</span>'

    nav = "".join(
        f'<a href="{html.escape(href)}">{html.escape(text)}</a>'
        for href, text in nav_links
    )
    return f"""{HEAD}
    <div class="topbar">
      <div class="topbar-in">
        <span class="brand"><b>Oscar OAuth 1.0a</b><span>RFC 5849</span></span>
        <span class="host">{html.escape(settings.services_base)}</span>
        <span class="spacer"></span>
        {pill}
        <nav>{nav}</nav>
      </div>
    </div>
    <main>{body}</main>
    """


def _expiry_status(settings: Settings, token: AccessToken) -> tuple[str, str, bool]:
    """The 'Expires at' row plus an optional banner, as (dd_html, banner_html,
    is_expired). Oscar never sends a TTL, so this is only ever as good as
    OSCAR_TOKEN_TTL_SECONDS, entered by hand from the client's own
    Administration Panel > Integration screen (see README § When it returns
    401). Unset, there is nothing to compute from, and the row says so rather
    than pretending an age is an expiry."""
    remaining_h = _remaining_ttl_hours(settings, token)
    if remaining_h is None:
        dd = (
            '<dd>Unknown &mdash; set <code>OSCAR_TOKEN_TTL_SECONDS</code> '
            "to compute one</dd>"
        )
        return dd, "", False

    expires_at = token.issued_at + settings.token_ttl_seconds
    when = datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d %H:%M")

    if remaining_h <= 0:
        dd = f'<dd class="bad-text">{when} &middot; expired {abs(remaining_h):.1f} h ago</dd>'
        banner = (
            '<div class="banner bad"><span class="dot"></span>'
            "<span>This token is past its configured TTL. Oscar will very "
            "likely answer 401 for every operation below until you "
            "re-authorise.</span></div>"
        )
        return dd, banner, True

    if remaining_h < 1:
        minutes = remaining_h * 60
        dd = f"<dd>{when} &middot; in {minutes:.0f} min</dd>"
        banner = (
            '<div class="banner warn"><span class="dot"></span>'
            f"<span>This token expires in under an hour ({minutes:.0f} min). "
            "Re-authorise soon to avoid an interruption.</span></div>"
        )
        return dd, banner, False

    return f"<dd>{when} &middot; in {remaining_h:.1f} h</dd>", "", False


def _auth_page(notice: str = "") -> str:
    """The landing page. Authorisation is the first thing anyone sees, because
    it is the first thing that has to happen. Every call to Oscar is signed
    with a token this tool only has after the browser leg."""
    settings, token = _token()

    if token is None:
        panel = """
        <div class="auth">
          <div class="auth-h"><h2>Authorisation required</h2></div>
          <div class="auth-b">
            <p>Every call to Oscar is signed with an access token this tool does
               not hold yet. Nothing below can run until the browser leg is
               done.</p>
            <ol class="steps">
              <li>Leg 1 asks Oscar for temporary credentials.</li>
              <li>You sign in and approve the RASMI client.</li>
              <li>Leg 3 exchanges the approval for an access token, stored here.</li>
            </ol>
            <div class="actions">
              <a class="cta" href="/oauth1/start">Start authorisation</a>
            </div>
            <p class="hint">OAuth 1.0a has no refresh token, so this is a manual
               step every time the token lapses. It cannot be automated.</p>
          </div>
        </div>
        """
    else:
        expiry_dd, expiry_banner, is_expired = _expiry_status(settings, token)
        dot_cls = "dot bad" if is_expired else "dot"
        heading = "Expired" if is_expired else "Authorised"
        panel = f"""
        <div class="auth">
          <div class="auth-h"><span class="{dot_cls}"></span><h2>{heading}</h2></div>
          <div class="auth-b">
            {expiry_banner}
            <dl class="meta">
              <div><dt>Token age</dt>
                   <dd>{token.age_seconds / 3600:.2f} h</dd></div>
              <div><dt>Expires at</dt>
                   {expiry_dd}</div>
              <div><dt>Stored at</dt>
                   <dd>{html.escape(str(settings.token_file))}</dd></div>
            </dl>
            <div class="actions">
              <a class="cta" href="/app">Continue to operations</a>
              <a class="cta ghost" href="/oauth1/start">Re-authorise</a>
            </div>
            <p class="hint">A 401 from any operation means the token is no longer
               accepted. Re-authorise from here; a non-401 status means the
               signature was fine and the request itself was the problem.</p>
          </div>
        </div>
        """

    return _shell(notice + panel, _OPERATIONS_NAV, settings, token)


def _forms() -> str:
    return f"""
    <p class="eyebrow">Operations</p>
    <div class="grid">
      <form class="card" method="get" action="/ui/specialist">
        <div class="card-h">
          <span class="verb">GET &middot; consults/getProfessionalSpecialist</span>
          <h2>Get specialist by ID</h2>
        </div>
        <div class="card-b">
          <label for="g-id">Specialist ID</label>
          <input id="g-id" name="spec_id" type="number" value="1" required>
          <button type="submit">Fetch</button>
          <p class="hint">An ID that does not exist answers 204 with an empty
             body, not 404.</p>
        </div>
      </form>

      <form class="card" method="get" action="/ui/search">
        <div class="card-h">
          <span class="verb">GET &middot; professionalSpecialist/search</span>
          <h2>Find a specialist</h2>
        </div>
        <div class="card-b">
          <label for="s-ref">Referral number</label>
          <input id="s-ref" name="referral_no" value="{specialists.DEFAULT_REFERRAL_NO}" required>
          <label for="s-last">Last name</label>
          <input id="s-last" name="last_name" placeholder="Flamingo-4" required>
          <button type="submit">Search</button>
          <p class="hint">Exact match on both fields, case-insensitive. It does
             not prefix-match, and returns one record or 404.</p>
        </div>
      </form>

      <form class="card" method="post" action="/ui/add">
        <div class="card-h">
          <span class="verb">POST &middot; professionalSpecialist/add</span>
          <h2>Add a specialist</h2>
        </div>
        <div class="card-b">
          <label for="a-first">First name</label>
          <input id="a-first" name="first_name" required>
          <label for="a-last">Last name</label>
          <input id="a-last" name="last_name" required>
          <label for="a-clinic">Clinic name</label>
          <input id="a-clinic" name="clinic_name" required>
          <label for="a-phone">Phone</label>
          <input id="a-phone" name="phone_number">
          <label for="a-ref">Referral number</label>
          <input id="a-ref" name="referral_no" value="{specialists.DEFAULT_REFERRAL_NO}">
          <button type="submit">Create</button>
          <p class="hint">Clinic name cannot be empty, and the last name plus
             referral number pair must be new. Either answers 400, and the two
             are indistinguishable.</p>
        </div>
      </form>
    </div>
    """


def _app_page(settings: Settings, token: AccessToken, result_html: str = "") -> str:
    """The operations page, reachable only with a token."""
    nav = [("/", "Authorisation"), *_OPERATIONS_NAV]
    return _shell(result_html + _forms(), nav, settings, token)


def _table(record: dict) -> str:
    """Render the record as labelled rows. Oscar returns every DTO field, most
    of them null on a typical record, so an empty value is shown as such rather
    than dropped, because a reader needs to know the field exists and is unset."""
    rows = []
    for key, label, is_code in FIELDS:
        if key not in record:
            continue
        value = record[key]
        if value is None or value == "":
            cell = '<td class="unset">not set</td>'
        else:
            cls = ' class="mono"' if is_code else ""
            cell = f"<td{cls}>{html.escape(str(value))}</td>"
        rows.append(f"<tr><th>{html.escape(label)}</th>{cell}</tr>")

    known = {f[0] for f in FIELDS}
    for key in (k for k in record if k not in known):
        rows.append(
            f"<tr><th>{html.escape(key)}</th>"
            f"<td>{html.escape(str(record[key]))}</td></tr>"
        )

    return f'<div class="scroll"><table>{"".join(rows)}</table></div>'


def _status_class(code: int) -> str:
    # 204 is a success status, but on this layer it means the record is not
    # there. Colouring it green while the summary says "no such specialist"
    # would contradict itself, so it reads as an absence rather than a win.
    if code == 204:
        return " warn"
    if 200 <= code < 300:
        return ""
    if 400 <= code < 500:
        return " warn"
    return " bad"


def _card(
    status_label: str,
    status_cls: str,
    title: str,
    body: str,
    header_middle: str = "",
    header_trailing: str = "",
) -> str:
    """The shared 'result card' shell: a status badge, a title, an optional
    middle bit (the request line), a spacer, an optional trailing bit (the
    summary), and a body."""
    return f"""
    <div class="result">
      <div class="result-h">
        <span class="status{status_cls}">{status_label}</span>
        <span class="what">{html.escape(title)}</span>
        {header_middle}
        <span class="spacer"></span>
        {header_trailing}
      </div>
      <div class="result-b">{body}</div>
    </div>
    """


def _result_block(
    title: str,
    result: ApiResult,
    not_found_note: str = "",
) -> str:
    """One API result: the status, the id, the record as a table, and the raw
    JSON beside it.

    The branded error page is summarised rather than dumped, since its status line is
    the only signal it carries.
    """
    ok = 200 <= result.status_code < 300
    record = None
    if ok and not specialists.is_error_page(result.text):
        try:
            parsed = json.loads(result.text)
            if isinstance(parsed, dict):
                record = parsed
        except ValueError:
            record = None

    spec_id = specialists.record_id(record) if record is not None else None

    if spec_id is not None:
        summary = (
            f'<span class="idbox"><span class="k">id</span>'
            f'<span class="v">{spec_id}</span></span>'
        )
    elif result.status_code == 204:
        summary = '<span class="note">No content: no such specialist.</span>'
    elif result.status_code == 404 and not_found_note:
        summary = f'<span class="note">{html.escape(not_found_note)}</span>'
    elif ok:
        summary = '<span class="note">200, but no id in the response.</span>'
    else:
        summary = '<span class="note">Request rejected.</span>'

    if record is not None:
        pretty = json.dumps(record, indent=2)
        body = f"""
        <div class="cols">
          <div><p class="colcap">Record</p>{_table(record)}</div>
          <div><p class="colcap">Raw JSON</p>
               <pre>{html.escape(pretty)}</pre></div>
        </div>
        """
    elif specialists.is_error_page(result.text):
        body = (
            '<p class="hint">Oscar answered with its branded error page. The '
            "status line is the only signal it carries. There is no "
            "message, code or stack trace in it.</p>"
        )
    elif not result.text.strip():
        body = '<p class="hint">Empty body.</p>'
    else:
        body = f"<pre>{html.escape(result.text[:4000])}</pre>"

    # The request line comes straight from what was actually sent, rather
    # than a hand-typed copy of the path each caller builds separately.
    req = f'<span class="req">{html.escape(f"{result.method} {result.url}")}</span>'

    return _card(
        f"HTTP {result.status_code}",
        _status_class(result.status_code),
        title,
        body,
        header_middle=req,
        header_trailing=summary,
    )


def _plain_result(title: str, message: str, bad: bool = True) -> str:
    """A result-shaped panel for something that never reached Oscar."""
    body = f'<p class="note">{html.escape(message)}</p>'
    return _card("not sent", " bad" if bad else "", title, body)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Authorisation is the landing page, whether or not a token is held."""
    return _auth_page()


@app.get("/app", response_class=HTMLResponse)
def operations() -> str:
    settings, token = _token()
    if token is None:
        return _auth_page()
    return _app_page(settings, token)


@app.get("/oauth1/start")
def start() -> RedirectResponse:
    with _client() as client:
        temp = client.initiate()
        _pending[temp.oauth_token] = temp.oauth_token_secret
        return RedirectResponse(client.authorize_url(temp.oauth_token))


@app.get("/oauth1/callback")
def callback(
    request: Request,
    oauth_token: str = Query(...),
    oauth_verifier: Optional[str] = Query(None),
):
    # A token this session did not request is a session-fixation attempt.
    temporary_secret = _pending.pop(oauth_token, None)
    if temporary_secret is None:
        raise HTTPException(400, "oauth_token does not match this session's request")

    settings = load_settings()
    with OscarOAuth1Client(settings) as client:
        try:
            parsed = client.exchange(oauth_token, temporary_secret, oauth_verifier)
        except OAuthError as exc:
            raise HTTPException(502, str(exc)) from exc

    record = store.save(
        settings.token_file, parsed["oauth_token"], parsed.get("oauth_token_secret", "")
    )

    # Oscar redirects the browser here, so that's the common case and gets
    # the tool landing page. Anything asking for JSON (a script driving the
    # flow itself) gets the same machine-readable confirmation this endpoint
    # always returned, rather than a page meant to be read, not parsed.
    if "text/html" not in request.headers.get("accept", ""):
        return JSONResponse(
            {
                "status": "access token stored",
                "file": str(settings.token_file),
                "issued_at": record.issued_at,
            }
        )
    return HTMLResponse(
        _auth_page(
            _plain_result(
                "Authorisation complete",
                "Leg 3 exchanged the verifier for an access token, now stored on disk.",
                bad=False,
            )
        )
    )


@app.get("/ui/specialist", response_class=HTMLResponse)
def ui_specialist(spec_id: int = Query(...)) -> str:
    settings, token = _token()
    if token is None:
        return _auth_page()
    with OscarOAuth1Client(settings) as client:
        result = specialists.get_specialist(client, settings, token, spec_id)
    return _app_page(settings, token, _result_block(f"Specialist {spec_id}", result))


@app.get("/ui/search", response_class=HTMLResponse)
def ui_search(referral_no: str = Query(...), last_name: str = Query(...)) -> str:
    settings, token = _token()
    if token is None:
        return _auth_page()
    with OscarOAuth1Client(settings) as client:
        result = specialists.find_specialist(
            client, settings, token, referral_no, last_name
        )
    return _app_page(
        settings,
        token,
        _result_block(
            f"{last_name} / {referral_no}",
            result,
            not_found_note="No specialist matches that referral number and last name.",
        ),
    )


@app.post("/ui/add", response_class=HTMLResponse)
def ui_add(
    first_name: str = Form(...),
    last_name: str = Form(...),
    # Deliberately not Form(...): FastAPI reads an empty field as missing and
    # answers a raw 422, which throws the user out of the UI. An empty clinic
    # name is a known-bad request, so refuse it here and say why.
    clinic_name: str = Form(""),
    phone_number: str = Form(""),
    referral_no: str = Form(specialists.DEFAULT_REFERRAL_NO),
) -> str:
    settings, token = _token()
    # The auth gate comes first: without a token there is nothing to validate
    # for, and the caller needs to be told the real reason nothing happened.
    if token is None:
        return _auth_page()

    if not clinic_name.strip():
        return _app_page(
            settings,
            token,
            _plain_result(
                f"Add {first_name} {last_name}",
                "Clinic name cannot be empty. Oscar rejects that with a "
                "bare 400. Nothing was sent.",
            ),
        )

    with OscarOAuth1Client(settings) as client:
        result = specialists.add_specialist(
            client,
            settings,
            token,
            first_name=first_name,
            last_name=last_name,
            clinic_name=clinic_name,
            phone_number=phone_number,
            referral_no=referral_no,
        )

    block = _result_block(f"Add {first_name} {last_name}", result)
    if result.status_code == 400:
        block += (
            '<div class="result"><div class="result-b">'
            '<p class="hint">A 400 here means either an empty clinic name or '
            "that this last name and referral number pair already exists. The "
            "two are indistinguishable over the wire, so search for the "
            "pair to tell them apart.</p></div></div>"
        )
    return _app_page(settings, token, block)


@app.get("/probe")
def probe() -> JSONResponse:
    from .probe import probe_base_paths

    return JSONResponse(probe_base_paths(load_settings()))


@app.get("/api/specialist")
def specialist(specId: int = Query(...)) -> JSONResponse:
    """The raw call, kept as it was: status, body and the signature base string.

    The base string is a debugging affordance for this local tool only. It
    carries the consumer key and access token, so nothing shaped like this
    belongs in a service that anyone else can reach.
    """
    settings, token = _token()
    if token is None:
        raise HTTPException(409, "No access token: visit /oauth1/start first")

    with OscarOAuth1Client(settings) as client:
        result = specialists.get_specialist(client, settings, token, specId)

    return JSONResponse(
        {
            "status_code": result.status_code,
            "body": result.text[:4000],
            "base_string": result.base_string,
        },
        status_code=200,
    )
