# 2048 — reliable moves and shared learning AI

FastAPI game engine and React/TypeScript frontend. This version serializes rapid inputs, uses atomic persistent game updates, and learns one shared value model from unassisted player moves. Hints and autoplay use that model.

**Online activation needs a Postgres database. No database is configured yet.** SQLite is supported for local development; Vercel requires `DATABASE_URL` and never silently falls back to temporary storage.

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

## Keep Vercel for the backend

The existing repository uses an Azure static frontend workflow and a Vercel backend. They can remain separate.

1. Configure a Postgres database and obtain its server connection URL. Prefer a provider-supported pooled endpoint for serverless use, with TLS as the provider requires. Provisioning, pricing and provider limits are owner decisions.
2. In the Vercel backend project, set Root Directory to `backend`. Its supported FastAPI entrypoint is `app/main.py`; Python dependencies are in `requirements.txt`.
3. Add `DATABASE_URL` as a **server-only** environment secret. Add `CORS_ORIGINS` as a comma-separated list of the actual frontend origins. Use separate preview and production databases so testing cannot change the production model.
4. After deployment approval, deploy backend and run the Postgres integration/smoke checks. Missing storage configuration is an explicit failure, rather than a temporary in-memory game.
5. Set the Azure frontend build environment `VITE_API_BASE_URL` to the configured backend origin, rebuild, and verify keyboard/swipe/reset/hint/autoplay against that deployment.

There is no configured database or verified live deployment in this change. Never paste a connection URL into a GitHub issue or commit. Vercel's [FastAPI deployment documentation](https://vercel.com/docs/frameworks/backend/fastapi) describes the supported entrypoint and dependency setup.

The old process-memory sessions cannot be recovered after upgrading; players start a new game. Draft PRs skip the existing Azure preview deployment job. The root and backend `vercel.json` disable Git deployments for `fix/rapid-moves-shared-ai` only, using Vercel's [branch deployment setting](https://vercel.com/docs/project-configuration/git-configuration). Other branches retain existing behavior. Merging or marking a PR ready requires the owner's release decision.

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
