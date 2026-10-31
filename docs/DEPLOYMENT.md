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

Use the Supabase project's HTTPS project URL and a backend-only API key. The backend calls a narrow PostgREST RPC; it no longer needs a database connection string or database password. Use an existing modern `sb_secret_` key where available. It is sent in the `apikey` header only, not as Bearer auth. A legacy `service_role` JWT is accepted during key migration and is sent in both `apikey` and `Authorization: Bearer` headers. Never use a publishable/anon key. Do not paste either key into source, frontend variables or chat.

Set the following in the Vercel **Backend** project, Production environment:

- `SUPABASE_URL` — `https://lywyqgebmqdirkwvivus.supabase.co` (HTTPS project origin only; no path, query or credentials).
- `SUPABASE_SECRET_KEY` — the existing server-only Supabase secret key. The key value stays in Vercel's encrypted environment settings.
- `CORS_ORIGINS` — `https://2048.rafistacks.dev` for the current production frontend, with no trailing slash.

Set the following in the Vercel **Frontend** project, Production environment:

- `VITE_API_BASE_URL` — `https://2048-game-backend-six.vercel.app` for the existing backend. Use a different origin only if the backend domain is changed.

The frontend needs no Supabase URL, API key, service-role key, or database credential. `VITE_API_BASE_URL` is included in browser JavaScript and must contain only the backend origin. The backend requires both Supabase variables together; a partial pair fails closed. If both are present, they take precedence over any leftover `DATABASE_URL`. An HTTP API URL supplied as `DATABASE_URL` is rejected with fixed guidance to set `SUPABASE_URL` plus `SUPABASE_SECRET_KEY` for HTTP storage, or use Connect > Transaction pooler for direct PostgreSQL.

The optional direct PostgreSQL adapter can still be configured with `DATABASE_URL` and `GAME_DB_SCHEMA=game2048` when the Supabase HTTP pair is absent. The Vercel setup in this guide uses the HTTP adapter and does not set either direct-PostgreSQL variable.

For GitHub branch previews, do not point preview frontend builds at the production backend and do not point a preview backend at production Supabase storage. Preview runtime requires a separately isolated Supabase project/RPC setup and exact preview CORS origin. Until the manager has provisioned and set those values, use production-scoped values only and do not use preview deployments for gameplay testing.

See [Supabase connection methods](https://supabase.com/docs/guides/database/connecting-to-postgres), [TLS settings](https://supabase.com/docs/guides/security/tls), and [Vercel environment variables](https://vercel.com/docs/environment-variables/managing-environment-variables).

## Database boundary

Use the new `lywyqgebmqdirkwvivus` project only. Do not reuse either existing project (`olinda-project` or the paused project), and do not create a Storage bucket. The applied, reviewed [bootstrap SQL](../backend/sql/bootstrap_supabase.sql) creates the five private application tables, seeds the model only when absent, enables RLS, and revokes browser/API-role access. Apply it only to this project using its existing owner connection; repeating it preserves valid existing game/model data.

Keep schema `game2048` and helper schema `game2048_rpc` out of Supabase's Data API exposed-schema list and Realtime publications. The manager-reviewed additive RPC migration in `backend/sql/supabase_http.sql` is installed. It adds a narrow public SECURITY INVOKER wrapper and private SECURITY DEFINER function owned by `postgres`. `service_role` can execute the wrapper/helper but receives no direct application-table grants. The helper verifies the caller role and locks/updates the existing private tables atomically. The backend Supabase secret key has elevated access and bypasses RLS; store it only in the backend's encrypted environment. The browser communicates only with FastAPI and never receives this key.

## Launch verification

After the reviewed RPC migration and backend Production environment are complete, push/merge through the manager's authorized Git workflow and let Vercel build the configured branch. Verify each project reports a successful deployment and record the actual generated origins privately in Vercel settings. Then test:

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

Supabase advisors report no warnings/errors after restricting API execution of the pre-existing RLS event-trigger helper. Five informational [RLS-without-policy notices](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy) are expected: browser/API roles are denied. The server-only RPC checks `service_role` and accesses tables through its private definer function. This is not a reason to add public table policies.

## If the frontend loads but the game does not

Set Production variables on the correct Vercel projects, then redeploy. The frontend build must use `VITE_API_BASE_URL=https://2048-game-backend-six.vercel.app`; setting it to `https://2048.rafistacks.dev` sends requests to the static frontend itself. Changing a variable does not modify a previously built JavaScript bundle.

The backend needs `SUPABASE_URL=https://lywyqgebmqdirkwvivus.supabase.co` and the existing server-only `SUPABASE_SECRET_KEY`. Do not place the HTTPS project URL in `DATABASE_URL`; that variable is only for the optional direct PostgreSQL adapter. The backend also needs `CORS_ORIGINS=https://2048.rafistacks.dev`. The RPC migration is installed; Vercel secrets and hosted health/smoke checks remain to be completed.

The optional direct PostgreSQL adapter logs `storage_connection_failed` with an allowlisted `reason_code`, safe exception class and SQLSTATE only. Supabase HTTP RPC failures return the generic storage error; response bodies and authentication headers are not logged. Never send the full key, URL, or raw driver/HTTP error in chat or public logs.
