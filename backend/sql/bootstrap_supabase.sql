-- 2048 Game / Supabase manual bootstrap. Run as the existing postgres role.
-- Target selected by manager: lywyqgebmqdirkwvivus. Verify project before running.
-- No roles, passwords, endpoints, player-data resets or cross-schema changes.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
SELECT pg_advisory_xact_lock(hashtext('game2048'));

DO $guard$
DECLARE owner_name text;
BEGIN
  IF current_user <> 'postgres' THEN
    RAISE EXCEPTION 'Run this bootstrap as the existing postgres owner role';
  END IF;
  SELECT pg_get_userbyid(nspowner) INTO owner_name
    FROM pg_namespace WHERE nspname = 'game2048';
  IF owner_name IS NOT NULL AND owner_name <> 'postgres' THEN
    RAISE EXCEPTION 'Unexpected game2048 schema owner; inspect instead of transferring ownership';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'game2048' AND (
      pg_get_userbyid(c.relowner) <> 'postgres' OR
      NOT ((c.relkind = 'r' AND c.relname IN ('schema_meta','games','requests','transitions','model'))
        OR (c.relkind = 'i' AND EXISTS (
          SELECT 1 FROM pg_index i WHERE i.indexrelid = c.oid AND i.indisprimary)))
    )
  ) OR EXISTS (
    SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'game2048'
  ) OR EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'game2048'
  ) OR EXISTS (
    SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'game2048' AND NOT t.tgisinternal
  ) THEN
    RAISE EXCEPTION 'Unexpected game2048 objects, ownership, policies or triggers; inspect first';
  END IF;
END
$guard$;

CREATE SCHEMA IF NOT EXISTS game2048 AUTHORIZATION postgres;
SET LOCAL search_path TO game2048;
CREATE TABLE IF NOT EXISTS game2048.schema_meta (singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS game2048.games (game_id TEXT PRIMARY KEY, board TEXT NOT NULL, score BIGINT NOT NULL, revision BIGINT NOT NULL, game_over INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS game2048.requests (scope TEXT NOT NULL, request_id TEXT NOT NULL, game_id TEXT, request_json TEXT NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(scope, request_id), FOREIGN KEY(game_id) REFERENCES game2048.games(game_id));
CREATE TABLE IF NOT EXISTS game2048.transitions (game_id TEXT NOT NULL, resulting_revision BIGINT NOT NULL, before_board TEXT NOT NULL, after_board TEXT NOT NULL, direction TEXT NOT NULL, score_gained BIGINT NOT NULL, source TEXT NOT NULL, terminal INTEGER NOT NULL, reward DOUBLE PRECISION NOT NULL, request_id TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(game_id, resulting_revision), FOREIGN KEY(game_id) REFERENCES game2048.games(game_id));
CREATE TABLE IF NOT EXISTS game2048.model (singleton INTEGER PRIMARY KEY, algorithm TEXT NOT NULL, feature_order TEXT NOT NULL, weights TEXT NOT NULL, model_version BIGINT NOT NULL, training_samples BIGINT NOT NULL, human_moves BIGINT NOT NULL, ai_moves BIGINT NOT NULL, games_started BIGINT NOT NULL, completed_games BIGINT NOT NULL, updated_at TEXT);

-- Refuse incompatible existing columns; never ALTER them or replace rows.
DO $columns$
DECLARE expected jsonb := '{"schema_meta":[["singleton","integer",true],["version","integer",true]],"games":[["game_id","text",true],["board","text",true],["score","bigint",true],["revision","bigint",true],["game_over","integer",true],["created_at","text",true],["updated_at","text",true]],"requests":[["scope","text",true],["request_id","text",true],["game_id","text",false],["request_json","text",true],["response_json","text",true],["created_at","text",true]],"transitions":[["game_id","text",true],["resulting_revision","bigint",true],["before_board","text",true],["after_board","text",true],["direction","text",true],["score_gained","bigint",true],["source","text",true],["terminal","integer",true],["reward","double precision",true],["request_id","text",true],["created_at","text",true]],"model":[["singleton","integer",true],["algorithm","text",true],["feature_order","text",true],["weights","text",true],["model_version","bigint",true],["training_samples","bigint",true],["human_moves","bigint",true],["ai_moves","bigint",true],["games_started","bigint",true],["completed_games","bigint",true],["updated_at","text",false]]}'::jsonb;
        constraints_expected jsonb := '{"schema_meta":["PRIMARY KEY (singleton)"],"games":["PRIMARY KEY (game_id)"],"requests":["FOREIGN KEY (game_id) REFERENCES games(game_id)","PRIMARY KEY (scope, request_id)"],"transitions":["FOREIGN KEY (game_id) REFERENCES games(game_id)","PRIMARY KEY (game_id, resulting_revision)"],"model":["PRIMARY KEY (singleton)"]}'::jsonb;
        table_name text;
        actual jsonb;
        constraints_actual jsonb;
BEGIN
  FOR table_name IN SELECT jsonb_object_keys(expected) LOOP
    SELECT jsonb_agg(jsonb_build_array(a.attname,
             format_type(a.atttypid, a.atttypmod), a.attnotnull) ORDER BY a.attnum)
      INTO actual
      FROM pg_attribute a
      WHERE a.attrelid = format('game2048.%I', table_name)::regclass
        AND a.attnum > 0 AND NOT a.attisdropped;
    IF actual IS DISTINCT FROM (expected -> table_name) THEN
      RAISE EXCEPTION 'Incompatible game2048.% columns; preserve data and inspect', table_name;
    END IF;
    SELECT jsonb_agg(pg_get_constraintdef(c.oid) ORDER BY pg_get_constraintdef(c.oid))
      INTO constraints_actual FROM pg_constraint c
      WHERE c.conrelid = format('game2048.%I', table_name)::regclass;
    IF constraints_actual IS DISTINCT FROM (constraints_expected -> table_name) THEN
      RAISE EXCEPTION 'Incompatible game2048.% constraints; preserve data and inspect', table_name;
    END IF;
  END LOOP;
END
$columns$;

INSERT INTO game2048.schema_meta(singleton, version) VALUES (1, 1)
ON CONFLICT(singleton) DO NOTHING;
INSERT INTO game2048.model(singleton, algorithm, feature_order, weights,
  model_version, training_samples, human_moves, ai_moves, games_started,
  completed_games, updated_at)
VALUES (1, 'linear-td-v1', '["bias","empty_fraction","maximum_exponent","maximum_in_corner","smoothness","monotonicity","equal_adjacent_fraction","legal_move_fraction"]', '[0.0,2.0,1.0,1.0,0.5,1.0,0.5,1.0]', 0, 0, 0, 0, 0, 0, NULL)
ON CONFLICT(singleton) DO NOTHING;

-- Validate semantic compatibility while preserving all learned values/counters.
DO $model$
DECLARE snapshot game2048.model%ROWTYPE;
BEGIN
  IF (SELECT count(*) FROM game2048.schema_meta) <> 1 OR
     NOT EXISTS (SELECT 1 FROM game2048.schema_meta WHERE singleton=1 AND version=1) THEN
    RAISE EXCEPTION 'Unsupported schema metadata; no data reset performed';
  END IF;
  IF (SELECT count(*) FROM game2048.model) <> 1 THEN
    RAISE EXCEPTION 'Unexpected model singleton rows; inspect existing model';
  END IF;
  SELECT * INTO STRICT snapshot FROM game2048.model WHERE singleton=1 FOR UPDATE;
  IF snapshot.algorithm <> 'linear-td-v1' OR snapshot.feature_order::jsonb <> '["bias","empty_fraction","maximum_exponent","maximum_in_corner","smoothness","monotonicity","equal_adjacent_fraction","legal_move_fraction"]'::jsonb
     OR jsonb_typeof(snapshot.weights::jsonb) <> 'array' THEN
    RAISE EXCEPTION 'Unsupported model algorithm, feature order or weights';
  END IF;
  IF jsonb_array_length(snapshot.weights::jsonb) <> 8 OR EXISTS (
    SELECT 1 FROM jsonb_array_elements(snapshot.weights::jsonb) AS weight(value)
    WHERE NOT CASE WHEN jsonb_typeof(value) = 'number'
      THEN (value::text)::numeric BETWEEN -10 AND 10 ELSE false END
  ) OR snapshot.model_version <> snapshot.training_samples
     OR snapshot.training_samples <> snapshot.human_moves
     OR least(snapshot.model_version, snapshot.ai_moves, snapshot.games_started,
              snapshot.completed_games) < 0 THEN
    RAISE EXCEPTION 'Incompatible model weights/counters; preserve data and inspect';
  END IF;
END
$model$;

-- Scope all permission changes to this private application schema.
REVOKE ALL ON SCHEMA game2048 FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL TABLES IN SCHEMA game2048 FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA game2048
  REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated, service_role;
ALTER TABLE game2048.schema_meta ENABLE ROW LEVEL SECURITY;
ALTER TABLE game2048.games ENABLE ROW LEVEL SECURITY;
ALTER TABLE game2048.requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE game2048.transitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE game2048.model ENABLE ROW LEVEL SECURITY;
-- No public RLS policies: direct backend postgres owner access bypasses RLS.
-- Do not add game2048 to Data API exposed schemas or Realtime publications.
COMMIT;
