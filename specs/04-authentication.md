# Authentication

## Overview

Authentication uses Garmin's OAuth flow, delegated to the `garth` library (a dependency of `python-garminconnect`). The flow is:

1. User provides email/password via interactive CLI
2. Garmin may require MFA (user enters code in terminal)
3. On success, the garth OAuth tokens are serialized and stored in the `users.tokens_json` column in Postgres
4. Subsequent runs load tokens from the database and refresh automatically

## Token Lifecycle

- **Token storage**: Tokens are stored in the `users.tokens_json` JSONB column in Postgres. After login, the garth token state is serialized and saved to the database. No filesystem dependency.
- **Token validity**: Garmin OAuth tokens are valid for approximately 1 year.
- **Auto-refresh**: After each successful ingestion run, if garth refreshed the token, the updated `tokens_json` is written back to the database.
- **Re-authentication**: If the refresh token itself expires or is revoked, the user must re-run the interactive login flow.

## Multi-User Token Management

The ingest pipeline:

1. Loads each active user from the `users` table
2. Constructs a `Garmin` client and loads tokens from `user.tokens_json`
3. On token load failure (expired, revoked), logs a warning and skips that user
4. After successful ingestion, persists any token refresh back to `users.tokens_json`
5. Proceeds with ingestion for users with valid tokens

## CLI Commands

### `garmin-postgres auth login`

Interactive flow:
1. Prompt for email (or accept as `--email` argument)
2. Prompt for password (hidden input)
3. Call `Garmin(email=email, password=password, return_on_mfa=True)`
4. If MFA is required:
   - Display the MFA prompt (email or SMS, per Garmin's response)
   - Prompt for MFA code
   - Call `client.resume_login(client_state, mfa_code)`
5. On success:
   - Display user info (display name, full name)
   - Create or update the `users` row
   - Save serialized tokens to `users.tokens_json`
6. Print confirmation

### `garmin-postgres auth status`

For each user in the database:
1. Try to load tokens and connect
2. Report: display name, last successful ingest, token validity status

## Security Considerations

- **No passwords stored** — Only OAuth tokens are persisted in the database. The user's Garmin password is never written to disk or the database.
- **Database credentials** — Standard `DATABASE_URL` env var, never committed to git.

## Error Handling

| Scenario | Behavior |
|---|---|
| Token expired, auto-refresh succeeds | Continue normally |
| Token expired, refresh fails | Log error, skip user, continue with other users |
| MFA required during cron run | Log error (can't prompt), skip user. User must re-auth via CLI. |
| Invalid credentials | Log error, do not create user record |
| Network error | Retry up to 3 times with exponential backoff, then fail |
| HTTP 429 (rate limit) | Retry after delay from `Retry-After` header, or 60 seconds |

## Rate Limiting Strategy

The Garmin API has undocumented rate limits. The client wrapper will:

1. Track the last request timestamp
2. Enforce a minimum 1-second delay between requests to the same endpoint
3. On 429 response, respect `Retry-After` header or wait 60 seconds
4. Log all rate-limit events for monitoring
