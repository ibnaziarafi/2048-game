-- Server-only HTTP storage facade. Existing game2048 tables/data are untouched
-- by deployment. Apply as postgres; keep game2048 and game2048_rpc unexposed.
BEGIN;
SET LOCAL lock_timeout = '5s';
DO $guard$
BEGIN
  IF current_user <> 'postgres' OR NOT EXISTS (
    SELECT 1 FROM pg_namespace WHERE nspname='game2048' AND nspowner='postgres'::regrole
  ) THEN RAISE EXCEPTION 'Expected postgres-owned, bootstrapped game2048 schema'; END IF;
  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='game2048_rpc' AND nspowner<>'postgres'::regrole)
     OR EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
       WHERE (n.nspname='game2048_rpc' AND p.proname='storage')
          OR (n.nspname='public' AND p.proname='game2048_storage')
       GROUP BY p.proowner HAVING p.proowner<>'postgres'::regrole) THEN
    RAISE EXCEPTION 'Unexpected RPC schema/function ownership; inspect first';
  END IF;
END
$guard$;
CREATE SCHEMA IF NOT EXISTS game2048_rpc AUTHORIZATION postgres;
REVOKE ALL ON SCHEMA game2048_rpc FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA game2048_rpc TO service_role;

CREATE OR REPLACE FUNCTION game2048_rpc.storage(p_action text, p_payload jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' SET lock_timeout = '5s' AS $rpc$
DECLARE
  m game2048.model%ROWTYPE;
  g game2048.games%ROWTYPE;
  receipt game2048.requests%ROWTYPE;
  req jsonb;
  result jsonb;
  snapshot jsonb;
  proposed_board jsonb;
  moves jsonb;
  row_value jsonb;
  cell jsonb;
  cell_number bigint;
  game_id_text text;
  request_id_text text;
  scope_text text;
  source_text text;
  revision_number bigint;
  gain bigint;
  changed boolean;
  terminal boolean;
  now_text text;
BEGIN
  -- SECURITY DEFINER changes current_user, but not the authenticated caller's
  -- SET ROLE. Do not trust JWT user_metadata or user-supplied payload identity.
  IF current_setting('role', true) IS DISTINCT FROM 'service_role' THEN
    RAISE insufficient_privilege USING MESSAGE='Server storage access denied';
  END IF;
  IF p_action NOT IN ('snapshot','commit_create','commit_move') OR p_action IS NULL
     OR jsonb_typeof(p_payload) IS DISTINCT FROM 'object' THEN
    RETURN jsonb_build_object('status','error','code','invalid_payload');
  END IF;
  IF p_action = 'snapshot' THEN
    game_id_text := (p_payload->>'game_id')::uuid::text;
    request_id_text := (p_payload->>'request_id')::uuid::text;
    scope_text := coalesce(game_id_text,'create');
    -- ONE statement gives one MVCC snapshot of model, game and receipt.
    SELECT jsonb_build_object('status','ok','schema_version',
      (SELECT version FROM game2048.schema_meta WHERE singleton=1),
      'model',to_jsonb(model_row) || jsonb_build_object(
        'weights',model_row.weights::jsonb,'feature_order',model_row.feature_order::jsonb),
      'game',(SELECT to_jsonb(game_row) || jsonb_build_object('board',game_row.board::jsonb)
              FROM game2048.games game_row WHERE game_row.game_id=game_id_text),
      'receipt',(SELECT jsonb_build_object('request',r.request_json::jsonb,
                   'response',r.response_json::jsonb)
                 FROM game2048.requests r WHERE r.scope=scope_text AND r.request_id=request_id_text))
      INTO snapshot FROM game2048.model model_row WHERE model_row.singleton=1;
    IF snapshot IS NULL OR snapshot->>'schema_version' IS DISTINCT FROM '1' THEN
      RETURN jsonb_build_object('status','error','code','storage_unavailable');
    END IF;
    RETURN snapshot;
  END IF;

  -- All mutations use the same first lock, including AI/no-op/create requests.
  SELECT * INTO STRICT m FROM game2048.model WHERE singleton=1 FOR UPDATE;
  request_id_text := (p_payload->'request'->>'request_id')::uuid::text;
  IF request_id_text IS NULL THEN
    RETURN jsonb_build_object('status','error','code','invalid_payload');
  END IF;
  IF p_action='commit_create' THEN
    scope_text := 'create';
    req := jsonb_build_object('request_id',request_id_text);
  ELSE
    game_id_text := (p_payload->'request'->>'game_id')::uuid::text;
    IF game_id_text IS NULL THEN
      RETURN jsonb_build_object('status','error','code','invalid_payload');
    END IF;
    scope_text := game_id_text;
    req := p_payload->'request';
    IF coalesce(req->>'direction','') NOT IN ('left','right','up','down')
       OR coalesce(req->>'source','') NOT IN ('human','ai')
       OR coalesce(req->>'expected_revision','') !~ '^(0|[1-9][0-9]*)$'
       OR req->>'request_id' IS DISTINCT FROM request_id_text
       OR req->>'game_id' IS DISTINCT FROM game_id_text THEN
      RETURN jsonb_build_object('status','error','code','invalid_payload');
    END IF;
    req := jsonb_build_object('game_id',game_id_text,'request_id',request_id_text,
      'direction',req->>'direction','source',req->>'source',
      'expected_revision',(req->>'expected_revision')::bigint);
  END IF;
  IF p_payload->'request' IS DISTINCT FROM req THEN
    RETURN jsonb_build_object('status','error','code','invalid_payload');
  END IF;
  SELECT * INTO receipt FROM game2048.requests
    WHERE scope=scope_text AND request_id=request_id_text;
  IF FOUND THEN
    IF receipt.request_json::jsonb IS DISTINCT FROM req THEN
      RETURN jsonb_build_object('status','error','code','idempotency_conflict');
    END IF;
    RETURN jsonb_build_object('status','ok','result',receipt.response_json::jsonb);
  END IF;

  IF p_action='commit_move' THEN
    SELECT * INTO g FROM game2048.games WHERE game_id=game_id_text;
    IF NOT FOUND THEN
      RETURN jsonb_build_object('status','error','code','game_not_found');
    END IF;
    IF g.revision<>(req->>'expected_revision')::bigint THEN
      RETURN jsonb_build_object('status','error','code','stale_revision',
        'game',to_jsonb(g) || jsonb_build_object('board',g.board::jsonb),
        'model',to_jsonb(m) || jsonb_build_object('weights',m.weights::jsonb,'feature_order',m.feature_order::jsonb));
    END IF;
  END IF;

  -- Validate the narrow Python-computed proposal; no dynamic SQL or arbitrary
  -- table operation is accepted. Python remains responsible for 2048/TD rules.
  proposed_board := p_payload->'game'->'board';
  moves := p_payload->'legal_moves';
  IF jsonb_typeof(proposed_board) IS DISTINCT FROM 'array' OR jsonb_array_length(proposed_board)<>4
     OR jsonb_typeof(moves) IS DISTINCT FROM 'array' OR jsonb_array_length(moves)>4 THEN
    RETURN jsonb_build_object('status','error','code','invalid_payload');
  END IF;
  FOR row_value IN SELECT value FROM jsonb_array_elements(proposed_board) LOOP
    IF jsonb_typeof(row_value) IS DISTINCT FROM 'array' OR jsonb_array_length(row_value)<>4 THEN
      RETURN jsonb_build_object('status','error','code','invalid_payload');
    END IF;
    FOR cell IN SELECT value FROM jsonb_array_elements(row_value) LOOP
      IF jsonb_typeof(cell) IS DISTINCT FROM 'number' OR cell::text !~ '^(0|[1-9][0-9]*)$' THEN
        RETURN jsonb_build_object('status','error','code','invalid_payload');
      END IF;
      cell_number := (cell::text)::bigint;
      IF cell_number=1 OR (cell_number>0 AND (cell_number & (cell_number-1))<>0) THEN
        RETURN jsonb_build_object('status','error','code','invalid_payload');
      END IF;
    END LOOP;
  END LOOP;
  IF EXISTS (SELECT 1 FROM jsonb_array_elements(moves) AS d(value)
      WHERE jsonb_typeof(value) IS DISTINCT FROM 'string'
         OR (value #>> '{}') NOT IN ('left','right','up','down'))
     OR (SELECT count(*) FROM jsonb_array_elements(moves)) <>
        (SELECT count(DISTINCT value) FROM jsonb_array_elements(moves)) THEN
    RETURN jsonb_build_object('status','error','code','invalid_payload');
  END IF;
  terminal := jsonb_array_length(moves)=0;
  now_text := to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"');
  IF p_action='commit_create' THEN
    game_id_text := (p_payload->'game'->>'game_id')::uuid::text;
    IF game_id_text IS NULL OR terminal OR
       (SELECT count(*) FROM jsonb_array_elements(proposed_board) r,
          LATERAL jsonb_array_elements(r.value) c WHERE c.value<>'0'::jsonb)<>2 OR
       EXISTS (SELECT 1 FROM jsonb_array_elements(proposed_board) r,
          LATERAL jsonb_array_elements(r.value) c WHERE c.value NOT IN ('0'::jsonb,'2'::jsonb,'4'::jsonb)) THEN
      RETURN jsonb_build_object('status','error','code','invalid_payload');
    END IF;
    INSERT INTO game2048.games VALUES(game_id_text,proposed_board::text,0,0,0,now_text,now_text);
    UPDATE game2048.model SET games_started=games_started+1 WHERE singleton=1 RETURNING * INTO m;
    result := jsonb_build_object('game_id',game_id_text,'board',proposed_board,'score',0,
      'revision',0,'game_over',false,'legal_moves',moves,'model_version',m.model_version);
  ELSE
    source_text := req->>'source';
    IF jsonb_typeof(p_payload->'moved') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(p_payload->'game'->'game_over') IS DISTINCT FROM 'boolean'
       OR coalesce(p_payload->>'score_gained','') !~ '^(0|[1-9][0-9]*)$'
       OR coalesce(p_payload->'game'->>'score','') !~ '^(0|[1-9][0-9]*)$'
       OR coalesce(p_payload->'game'->>'revision','') !~ '^(0|[1-9][0-9]*)$' THEN
      RETURN jsonb_build_object('status','error','code','invalid_payload');
    END IF;
    changed := (p_payload->>'moved')::boolean;
    gain := (p_payload->>'score_gained')::bigint;
    revision_number := (p_payload->'game'->>'revision')::bigint;
    IF (p_payload->'game'->>'game_over')::boolean<>terminal
       OR revision_number<>g.revision+changed::integer
       OR (p_payload->'game'->>'score')::bigint<>g.score+gain
       OR (changed AND proposed_board=g.board::jsonb)
       OR (NOT changed AND (proposed_board<>g.board::jsonb OR gain<>0)) THEN
      RETURN jsonb_build_object('status','error','code','invalid_payload');
    END IF;
    IF changed AND source_text='human' THEN
      IF coalesce(p_payload->>'model_version','') !~ '^(0|[1-9][0-9]*)$' THEN
        RETURN jsonb_build_object('status','error','code','invalid_payload');
      END IF;
      IF (p_payload->>'model_version')::bigint<>m.model_version THEN
        RETURN jsonb_build_object('status','retry','code','model_conflict');
      END IF;
      IF jsonb_typeof(p_payload->'weights') IS DISTINCT FROM 'array'
         OR jsonb_array_length(p_payload->'weights')<>8 OR EXISTS (
           SELECT 1 FROM jsonb_array_elements(p_payload->'weights') AS w(value)
           WHERE NOT CASE WHEN jsonb_typeof(value)='number'
             THEN (value::text)::numeric BETWEEN -10 AND 10 ELSE false END) THEN
        RETURN jsonb_build_object('status','error','code','invalid_payload');
      END IF;
    END IF;
    IF changed THEN
      IF jsonb_typeof(p_payload->'reward') IS DISTINCT FROM 'number'
         OR (p_payload->>'reward')::numeric NOT BETWEEN -1 AND 1 THEN
        RETURN jsonb_build_object('status','error','code','invalid_payload');
      END IF;
      UPDATE game2048.games SET board=proposed_board::text,score=g.score+gain,
        revision=revision_number,game_over=terminal::integer,updated_at=now_text
        WHERE game_id=game_id_text AND revision=g.revision;
      IF NOT FOUND THEN RAISE EXCEPTION 'Game revision CAS failed'; END IF;
      IF source_text='human' THEN
        UPDATE game2048.model SET weights=(p_payload->'weights')::text,
          model_version=model_version+1,training_samples=training_samples+1,human_moves=human_moves+1,
          completed_games=completed_games+terminal::integer,updated_at=now_text
          WHERE singleton=1 RETURNING * INTO m;
      ELSE
        UPDATE game2048.model SET ai_moves=ai_moves+1,
          completed_games=completed_games+terminal::integer WHERE singleton=1 RETURNING * INTO m;
      END IF;
      INSERT INTO game2048.transitions VALUES(game_id_text,revision_number,g.board,proposed_board::text,
        req->>'direction',gain,source_text,terminal::integer,(p_payload->>'reward')::double precision,
        request_id_text,now_text);
    END IF;
    result := jsonb_build_object('game_id',game_id_text,'board',proposed_board,'score',g.score+gain,
      'revision',revision_number,'game_over',terminal,'legal_moves',moves,
      'model_version',m.model_version,'moved',changed,'score_gained',gain);
  END IF;
  INSERT INTO game2048.requests VALUES(scope_text,request_id_text,game_id_text,req::text,result::text,now_text);
  RETURN jsonb_build_object('status','ok','result',result);
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range OR invalid_parameter_value THEN
  -- The exception block rolls back any writes before reporting invalid input.
  RETURN jsonb_build_object('status','error','code','invalid_payload');
END
$rpc$;
REVOKE ALL ON FUNCTION game2048_rpc.storage(text,jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION game2048_rpc.storage(text,jsonb) TO service_role;

CREATE OR REPLACE FUNCTION public.game2048_storage(p_action text, p_payload jsonb)
RETURNS jsonb LANGUAGE sql SECURITY INVOKER SET search_path = '' AS $wrapper$
  SELECT game2048_rpc.storage(p_action,p_payload);
$wrapper$;
REVOKE ALL ON FUNCTION public.game2048_storage(text,jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.game2048_storage(text,jsonb) TO service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
