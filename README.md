# 2048 — reliable moves and shared learning AI

FastAPI game engine and React/TypeScript frontend. This version serializes rapid inputs, uses atomic persistent game updates, and learns one shared value model from unassisted player moves. Hints and autoplay use that model.

**Online launch is owner-authorized and deployment setup is in progress.** The dedicated Supabase project, private tables and server-only RPC are ready; Vercel environment configuration and hosted smoke checks remain before the live service can be verified. SQLite is for local development. The preferred hosted adapter uses the Supabase HTTPS RPC with backend-only `SUPABASE_URL` and `SUPABASE_SECRET_KEY`; an optional direct Postgres adapter uses `DATABASE_URL` and private `GAME_DB_SCHEMA`. Vercel never falls back to SQLite.

## What changes

- Keyboard, swipe and D-pad input share one bounded move queue. Requests run in order; New Game invalidates old replies.
- Every move includes a request ID and board revision. Duplicate retries replay the original result; stale revisions cannot change the board.
- Explicit game IDs isolate sessions. State, gameplay transitions, learning updates and retry receipts commit together.
- Valid human moves update a persistent linear temporal-difference model. Other players see the same evolving weights through the shared database.
- Hint, Apply Hint and stoppable autoplay choose legal moves. Assisted/AI moves do not train the learner.
- Shared counters show model updates and gameplay activity. They measure activity, not playing strength.

The model starts with heuristic weights and learns from observed human transitions. It is a small CPU model, with no external AI API, downloaded model or per-request AI bill. Human play can be weak or inconsistent; more games do **not** guarantee better scores. Anonymous client attribution is not bot-proof.

## Local development

Python 3.12 or later and Node.js 20 or later are recommended.

```sh
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

In another terminal:

```sh
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. The Vite `/api` development proxy reaches the backend on port 8000. A local SQLite database is created under `backend/data/` unless `GAME_DB_PATH` is set. Example environment values are in `backend/.env.example`; these are examples, not automatically loaded shell variables.

To point a separately hosted frontend at the backend, set `VITE_API_BASE_URL` to the backend origin **before building**. Never put database credentials in a `VITE_` variable: Vite embeds those values in public browser assets.

## Vercel and Supabase deployment

GitHub is connected to Vercel for automatic deployments. Use two Vercel projects pointed at the product repository: backend Root Directory `backend`, frontend Root Directory `frontend`. The frontend is a Vite build. Vercel production builds follow the configured production branch; Git branches create Vercel previews. The existing Azure workflow remains historical. Follow [deployment guide](docs/DEPLOYMENT.md) for environment setup, private Supabase schema verification, smoke acceptance, and rollback.

For the Vercel **Frontend** project, set only `VITE_API_BASE_URL` to the actual backend deployment origin for the corresponding Vercel environment. Do not add Supabase URLs/keys or any database credentials to frontend variables. The backend project alone receives `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, and exact `CORS_ORIGINS`. Keep the modern secret key in Vercel's encrypted server environment; it is sent only in the `apikey` header. The backend accepts a legacy `service_role` JWT during key migration. Do not set both Supabase variables partially. If they are both present, they take precedence over any leftover direct PostgreSQL `DATABASE_URL`.

Online launch has been authorized, but no live service is claimed until the database schema, Vercel environments, automatic deployment and hosted smoke checks are complete. The old process-memory sessions cannot be recovered after upgrading; players start a new game.

## Data and recovery

The database stores random game IDs, board states, directions, score gains, whether a move was human or assisted, timestamps, model weights, counters and retry receipts. No player name, email or account is collected by this application. Hosting access logs are controlled separately. Game IDs are anonymous bearer capabilities: anyone with an ID can access that game.

The preview retains records indefinitely, including retry receipts. Storage grows with play; broad public rollout needs an agreed retention policy, expiry-safe receipt pruning and abuse/rate controls. Client-supplied human/AI attribution is a convention and can be falsified. Do not treat this as curated or verified-human training data.

Keep database files, backups, credentials and player traces out of Git. Back up hosted Postgres through its provider. For SQLite, use the SQLite backup API while running or stop the server before copying its files. Do not copy only a live `.sqlite3` file while ignoring its WAL.

Roll back application code with a feature-branch revert or the host's previous deployment. Back up the database first; reverting code does not undo training updates. Restore the entire database backup to restore matching game revisions, model counters and retry receipts. Do not delete receipts while allowing old sessions to keep submitting moves.

## Checks

```sh
cd backend
python -m pytest -q
```

```sh
cd frontend
npm run build
```

Additional frontend regression and AI evaluation commands are documented alongside their test/script files. Local SQLite verification does not establish remote Postgres connectivity or production throughput. The review report records the checks actually run and any remaining limitations.

## API

- `POST /game` — create with a unique `request_id`.
- `GET /game/{game_id}` — retrieve that session.
- `POST /game/move` — send `game_id`, `direction`, `expected_revision`, `request_id` and `source` (`human` or `ai`).
- `GET /game/{game_id}/hint` — read a legal hint for a specific revision.
- `GET /ai/stats` — shared persisted learning and gameplay counters.
- `GET /health` — storage readiness.

See FastAPI's `/docs` for schemas. API responses disable caching. Use a new request ID for a new action and preserve the complete payload and ID when retrying an uncertain action. A revision conflict requires reconciliation, not blind replay.
