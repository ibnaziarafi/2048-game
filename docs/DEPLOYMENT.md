# 2048 Game live setup and recovery

Owner authorized GitHub-triggered Vercel deployment on 1 November 2026. The dedicated Supabase project is active: [2048-game](https://supabase.com/dashboard/project/lywyqgebmqdirkwvivus), PostgreSQL 17.11, Frankfurt, organization Rafi Stacks. Private database bootstrap is complete. Vercel environment setup and hosted smoke checks still need completion; do not call the game live until those checks pass. The manager owns remote database, Vercel and Git actions.

## GitHub and Vercel projects

Vercel is expected to build automatically from GitHub. Connect the product repository to two Vercel projects:

| Vercel project | Root Directory | Build |
| --- | --- | --- |
| Backend | `backend` | FastAPI/Python using `app/main.py`, `requirements.txt`, and the existing backend `vercel.json` |
| Frontend | `frontend` | Vite using `npm run build`, output `dist`, and the preserved `package-lock.json` |

Keep the root and backend `vercel.json` files free of branch deployment suppression so GitHub pushes can trigger the configured Vercel builds. The files retain only Vercel's schema reference and make no routing, framework, or host changes. Check the actual Vercel project settings to confirm GitHub connection, production branch, and each root directory; do not infer successful deployment from a pushed commit. The existing Azure workflow remains untouched and is not the canonical Vercel frontend.

Vercel's [Root Directory/build guide](https://vercel.com/docs/builds/configure-a-build), [FastAPI guide](https://vercel.com/docs/frameworks/backend/fastapi), and [Vite guide](https://vercel.com/docs/frameworks/frontend/vite) document project configuration. Vercel environment changes apply to new deployments, so redeploy after saving or changing variables.

## Environment variables

Use the Supabase project's Connect dialog to copy its actual shared **transaction-pooler** connection string. Use this project’s existing owner connection (`postgres.<project-ref>` in the shared pooler) and its existing database password. The bootstrap is owned by `postgres`; no new login or password is created. Add `sslmode=require` to the PostgreSQL URL query if missing. Never invent the host/project suffix or copy a password into source, frontend variables, or chat. Use the existing owner-authorized server-side connection credential for this project; do not generate another password or change plans.

Set the following in the Vercel **Backend** project, Production environment:

- `DATABASE_URL` — the copied Supabase transaction-pooler URL with TLS required. Server-side only.
- `GAME_DB_SCHEMA` — `game2048`.
- `CORS_ORIGINS` — `https://2048.rafistacks.dev` for the current production frontend, with no trailing slash.

Set the following in the Vercel **Frontend** project, Production environment:

- `VITE_API_BASE_URL` — `https://2048-game-backend-six.vercel.app` for the existing backend (its public health endpoint was verified before this update). Use a different origin only if the backend domain is changed.

The frontend needs no Supabase URL, public key, service-role key, or database credential. `VITE_API_BASE_URL` is included in browser JavaScript and must contain only the backend origin. The backend must fail closed if `DATABASE_URL` or its pre-provisioned private schema is missing.

For GitHub branch previews, do not point preview frontend builds at the production backend and do not point a preview backend at the production `game2048` schema. Preview runtime requires a separately isolated schema/configuration and exact preview CORS origin. Until the manager has provisioned and set those values, use production-scoped values only and do not use preview deployments for gameplay testing.

See [Supabase connection methods](https://supabase.com/docs/guides/database/connecting-to-postgres), [TLS settings](https://supabase.com/docs/guides/security/tls), and [Vercel environment variables](https://vercel.com/docs/environment-variables/managing-environment-variables).

## Database boundary

Use the new `lywyqgebmqdirkwvivus` project only. Do not reuse either existing project (`olinda-project` or the paused project), and do not create a Storage bucket. The applied, reviewed [bootstrap SQL](../backend/sql/bootstrap_supabase.sql) creates the five private application tables, seeds the model only when absent, enables RLS, and revokes browser/API-role access. Apply it only to this project using its existing owner connection; repeating it preserves valid existing game/model data.

Keep schema `game2048` out of Supabase's Data API exposed-schema list and Realtime publications. Do not grant `PUBLIC`, `anon`, `authenticated`, or `service_role` access to the application schema/tables. The browser communicates only with the FastAPI backend. The current backend uses the existing project-owner connection, which is privileged and bypasses RLS; store it only in the backend’s secret environment. RLS and revoked API grants protect browser/API access, not a leaked owner credential. A separately provisioned schema-owner login can reduce database privileges later, but no such credential is created or required by this setup.

## Launch verification

After the backend Production environment and database bootstrap are complete, push/merge through the manager's authorized Git workflow and let Vercel build the configured branch. Verify each project reports a successful deployment and record the actual generated origins privately in Vercel settings. Then test:

1. `GET /health` returns success and `GET /ai/stats` returns initialized model data from the deployed backend.
2. The deployed frontend loads without CORS errors. Create a disposable game, play a changed human move, reload that game, request a hint, apply a hint, and start/stop autoplay.
3. `training_samples` increases exactly once for the human move. Replaying the same request ID/payload does not train a second time; an AI move, no-op, or hint does not train.
4. A second disposable game remains separate. Re-read both games and the shared model through a fresh backend invocation/redeployment and confirm state persists.
5. Confirm the five application tables remain within the private schema, have row-level security enabled and no API-role grants, and that the schema is not exposed through Supabase Data API or Realtime.

Do not run the opt-in Postgres test suite against production; it creates and drops test schemas. If any smoke check fails, stop rollout, preserve the schema and model, and return traffic to the previous deployment.

## Recovery

Before database maintenance, confirm the active project's backup/export options and take a backup containing the entire `game2048` schema. Do not promise point-in-time recovery unless the current plan explicitly provides it. Keep a known-good Vercel deployment available. For an application defect, redeploy the prior compatible commit and leave the database untouched; a code rollback cannot undo model updates or gameplay.

If database restore is required, restore the complete schema/database backup with its matching application checkpoint so game revisions, request receipts, transitions, counters and learned weights remain consistent. Do not drop the live schema, reset weights, or migrate the local SQLite/player data as an ad hoc rollback. Preserve deployment identifiers and sanitized errors, never credentials, connection URLs, game IDs, or player state.

## Provisioning evidence — 1 November 2026

Supabase migrations `bootstrap_private_game2048` and `restrict_default_rls_helper_api_access` were applied to project `lywyqgebmqdirkwvivus`. All five tables are owned by `postgres` with RLS enabled; API roles have no schema usage or table grants. The public Data API rejected `Accept-Profile: game2048` with HTTP406/PGRST106. Model version/training count begin at zero; local player traces were not uploaded.

Supabase advisors report no warnings/errors after restricting API execution of the pre-existing RLS event-trigger helper. Five informational [RLS-without-policy notices](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy) are expected: browser/API roles are denied, while the server-only owner connection bypasses RLS. This is not a reason to add public policies.

## If the frontend loads but the game does not

Set Production variables on the correct Vercel projects, then redeploy. The frontend build must use `VITE_API_BASE_URL=https://2048-game-backend-six.vercel.app`; setting it to `https://2048.rafistacks.dev` sends requests to the static frontend itself. Changing a variable does not modify a previously built JavaScript bundle.

The backend `DATABASE_URL` must be a PostgreSQL connection string copied from Supabase **Connect → Transaction pooler**, not `https://lywyqgebmqdirkwvivus.supabase.co` (that is the HTTP API URL). Keep its real password private and preserve the copied pooler hostname, username and port; require `sslmode=require`. The backend also needs `GAME_DB_SCHEMA=game2048` and `CORS_ORIGINS=https://2048.rafistacks.dev`.

Connection failures log `storage_connection_failed` with an allowlisted `reason_code`, safe exception class and SQLSTATE only. Examples: `auth_failure` for rejected credentials, `host_resolution`, `network_unreachable`, `network_timeout`, `tls_failure`, `invalid_dsn` or `missing_driver`. Logs omit connection strings, passwords and raw driver errors. Use the category to troubleshoot; do not send the complete connection secret in chat or public logs.
