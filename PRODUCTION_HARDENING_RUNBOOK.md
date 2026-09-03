# Production Hardening Runbook

## Permissions contract

Discord incident controls follow least privilege:

- Request Assistance: any member able to use the request panel.
- Respond / Join / Arrived / Backup / responder priority controls: configured responder access only; P1 is never requester-selectable.
- Close: requester, current primary responder, or Manage Server according to the Discord control path.
- Dashboard access and every dashboard mutation: Discord **Manage Server** permission plus CSRF validation.
- Repair Configuration, responder routing, retention policy, and web incident controls: dashboard administrators only through the same Manage Server gate.

Do not loosen `require_guild_access()` or remove CSRF checks from POST routes. Dashboard incident state transitions remain database-first and ledgered.

## Database migrations

`db_migrations.py` owns additive production schema migrations. Both bot and dashboard apply migrations at startup and coordinate with a PostgreSQL advisory lock. New schema changes should be added as a new numbered migration instead of adding new startup `ALTER TABLE` statements.

Legacy idempotent schema creation remains for backward compatibility while the migration framework is adopted. New changes should use the versioned path.

## Backups and restore

The application cannot enable Railway infrastructure backups itself. In Railway, enable PostgreSQL backups/snapshots for the production database and document the recovery point objective for the project.

Recommended restore drill:

1. Restore the latest production backup into a **new staging PostgreSQL service**, never directly over production.
2. Point staging bot/dashboard services at the restored database.
3. Keep `APP_ENV=staging` and production Discord credentials out of staging.
4. Start the dashboard and bot; confirm migrations apply cleanly.
5. Verify incident counts, recent ledger events, responder configuration, and dispatch settings.
6. Run the automated test suite and a controlled staging rescue lifecycle.
7. Only after validation, use the provider's documented production restore procedure if a real recovery is required.

Quarterly restore drills are recommended. A backup that has never been restored should not be treated as a verified recovery plan.

## Operational logging

Search Railway logs for `operational_event`. New hardening modules emit searchable key/value records for database migrations, Discord API retries, dashboard administrative mutations, and retention cleanup. Incident lifecycle events also remain permanently recorded in `rescue_incident_events` until an explicitly enabled retention policy removes them.

## Discord API retry policy

Dashboard Discord GET operations retry rate limits (`429`), transient `5xx` responses, timeouts, and network failures with bounded exponential backoff. Non-retryable `4xx` responses fail immediately. `discord.py` continues to provide its built-in gateway/REST rate-limit handling for the bot process.

## Admin audit trail

Successful and failed dashboard mutation responses are recorded in `rescue_admin_audit_events` with guild, actor, action, target path, result, and timestamp. This is separate from the rescue incident lifecycle ledger.

## Data retention

Retention is **disabled by default**. Configure it at `/guild/<guild_id>/retention`.

- Blank retention values mean keep forever.
- Minimum configurable retention is 30 days.
- Cleanup only removes database history; it does not delete Discord channels or messages.
- Closed incidents, their responder rows, and their incident ledger entries are deleted together.
- Admin audit retention is configured independently.

## Staging safety

Use a separate Railway environment/services, database, and Discord test guild. Set:

```text
APP_ENV=staging
ALLOW_DESTRUCTIVE_SMOKE_TESTS=false
```

The code refuses destructive smoke-test helpers in production regardless of the opt-in flag. To intentionally run destructive staging checks, both a non-production `APP_ENV` and `ALLOW_DESTRUCTIVE_SMOKE_TESTS=true` are required.

## Automated smoke test

`tests/test_lifecycle_smoke.py` exercises the database-first lifecycle contract:

request state -> primary response -> support join -> arrived -> backup -> priority change -> close -> reject post-close mutation.

It uses a controlled fake database and does not touch Discord or production data.

## Deployment gate

Railway start commands run `deployment_preflight.py` before starting either production process. The preflight compiles the repository and runs all unit/smoke tests. If validation fails, the new application process does not start.

GitHub Actions performs the same compile/test checks. For an additional provider-side gate, configure Railway/GitHub deployment policy so production tracks only commits whose required `Python Check` check is successful; this last provider setting must be enabled in the Railway/GitHub UI because repository code cannot change account-level deployment policy.
