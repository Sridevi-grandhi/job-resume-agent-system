# Email Summary — Gmail Connection Setup

This document is manual configuration guidance only. It contains no
credentials — you create your own in Google Cloud and put them in your own
`backend/.env`.

## 1. Create a Google Cloud project

1. Go to https://console.cloud.google.com/ and create a new project (or pick
   an existing one) dedicated to CareerBoost AI.

## 2. Enable the Gmail API

1. In the project, open **APIs & Services → Library**.
2. Search for **Gmail API** and click **Enable**.

## 3. Configure the OAuth consent screen

1. **APIs & Services → OAuth consent screen**.
2. User type: **External** (unless you have a Google Workspace org and want
   **Internal**).
3. Fill in the app name (e.g. "CareerBoost AI — Email Summary"), support
   email, and developer contact email.
4. **Scopes**: add exactly this one —
   - `https://www.googleapis.com/auth/gmail.readonly`

   Do **not** add `gmail.compose`, `gmail.send`, `gmail.modify`, or any
   broader scope. This version of Email Summary is read-only: it reads
   Gmail messages to detect job-related activity and never creates a
   draft or sends anything — see the note below.
5. **Test users** (while the app is in "Testing" publishing status —
   the default, and fine for personal/development use): add the Gmail
   address(es) you'll actually connect. Google will reject the OAuth flow
   for any Google account not listed here until the app is verified (see
   §7).

## 4. Create an OAuth 2.0 Client ID

1. **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
2. Application type: **Web application**.
3. **Authorized redirect URIs** — add the exact callback URL CareerBoost's
   backend serves. For local development that's:
   ```
   http://localhost:8000/api/email/gmail/callback
   ```
   (or `http://localhost:8001/...` etc. if you've remapped `BACKEND_PORT`).
4. Save. Google shows you a **Client ID** and **Client Secret** — copy
   both; the secret is shown only once (you can always generate a new one
   later from the same screen if you lose it).

## 5. Environment variables (`backend/.env`)

Add these to your own `backend/.env` — never commit them, never put a real
value in `.env.example`:

```
GOOGLE_CLIENT_ID=<your client id>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<your client secret>
GOOGLE_REDIRECT_URI=http://localhost:8000/api/email/gmail/callback

# Required for Gmail token encryption at rest — generate once with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Keep this value stable — rotating it makes previously-connected accounts'
# stored tokens undecryptable (they'll show as an error; just reconnect).
EMAIL_TOKEN_ENCRYPTION_KEY=<generated key>
```

Optional (all have safe defaults — see root `.env.example`):
`GMAIL_SCAN_MAX_MESSAGES`, `GMAIL_INITIAL_SCAN_DAYS`.

Restart the backend after editing `.env`.

## 6. Production redirect URI

When you deploy CareerBoost somewhere other than `localhost`, you must:

1. Add the production callback URL (e.g.
   `https://yourdomain.com/api/email/gmail/callback`) as an **additional**
   Authorized redirect URI in the same OAuth client (Google allows several).
2. Set `GOOGLE_REDIRECT_URI` in the production environment to that exact
   URL — it must match one of the URIs registered in Google Cloud
   character-for-character (scheme, host, path — no trailing slash
   differences).

## 7. Verification considerations

- While the OAuth consent screen is in **Testing** status, only the Google
  accounts you explicitly added as test users (§3.5) can complete the
  OAuth flow — anyone else sees an "app hasn't been verified" / access
  blocked screen. This is fine for personal use or a small team.
- If you want CareerBoost usable by arbitrary Google accounts, Google
  requires **app verification** for the `gmail.readonly` scope (and any
  sensitive/restricted scope), which involves a security assessment and
  can take time. Do this only if you actually need public access.

## Scope choice — read-only, current version

CareerBoost requests exactly one scope: `gmail.readonly`. This scope
technically permits reading your *entire* mailbox — Gmail itself does not
limit access to only job-related messages. The job-related/unrelated
distinction (application confirmations, recruiter outreach, interview
invitations, assessments, offers, rejections vs. banking, shopping,
personal conversations, newsletters, and other unrelated notifications) is
enforced entirely by this application's own classification logic (see
`backend/app/services/application_email_classifier.py`), not by the OAuth
grant. Only job-related messages are ever processed or stored; everything
else is read transiently during a scan and discarded.

`gmail.compose` and `gmail.send` are never requested, and this build never
calls a draft-creation or send endpoint. Recruiter Drafts
(`backend/app/services/recruiter_draft_service.py`) only ever composes a
suggested reply and stores it in CareerBoost's own database for the user
to copy and send themselves — it never calls Gmail at all. A dead,
unused `GmailEmailProvider.create_draft` helper remains in
`gmail_service.py` from an earlier version of this feature; nothing
reachable from Recruiter Drafts calls it. Reintroducing real Gmail draft
creation would require re-adding `gmail.compose` to both the OAuth
consent screen (§3.4) and `gmail_service.SCOPES`.

## What CareerBoost stores

- Your Gmail address, and an encrypted access/refresh token pair (never
  plaintext, never logged, never returned by any API response).
- Minimal metadata for job-related messages it finds: sender, subject,
  received time, a short body snippet, and a classification — never your
  full inbox, and never a non-job-related email.
- Nothing is ever sent, drafted, or otherwise written back to your Gmail
  account in this version — access is read-only.
