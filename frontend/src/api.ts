import { AiStats, ApiRequestError, Direction, GameState, HintResponse, MoveSource } from './types';

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');
const url = (path: string) => `${API_BASE_URL}${path}`;
const id = () => crypto.randomUUID();
const REQUEST_TIMEOUT_MS = 10_000;
const DIRECTIONS = new Set<Direction>(['left', 'right', 'up', 'down']);

function isGameState(value: unknown): value is GameState {
  if (!value || typeof value !== 'object') return false;
  const state = value as Partial<GameState>;
  return typeof state.game_id === 'string' && state.game_id.length > 0
    && Array.isArray(state.board) && state.board.length === 4
    && state.board.every((row) => Array.isArray(row) && row.length === 4 && row.every((tile) => Number.isSafeInteger(tile) && tile >= 0))
    && Number.isSafeInteger(state.score) && (state.score as number) >= 0
    && typeof state.game_over === 'boolean'
    && Number.isSafeInteger(state.revision) && (state.revision as number) >= 0
    && Array.isArray(state.legal_moves) && state.legal_moves.every((move) => DIRECTIONS.has(move))
    && Number.isSafeInteger(state.model_version) && (state.model_version as number) >= 0;
}

function isMoveResult(value: unknown): value is GameState {
  return isGameState(value) && typeof (value as GameState).moved === 'boolean'
    && Number.isSafeInteger((value as GameState).score_gained) && (value as GameState).score_gained! >= 0;
}

function isHint(value: unknown): value is HintResponse {
  if (!value || typeof value !== 'object') return false;
  const hint = value as Partial<HintResponse>;
  return typeof hint.game_id === 'string' && Number.isSafeInteger(hint.revision) && (hint.revision as number) >= 0
    && (hint.direction === null || (typeof hint.direction === 'string' && DIRECTIONS.has(hint.direction as Direction)))
    && Array.isArray(hint.legal_moves) && hint.legal_moves.every((move) => DIRECTIONS.has(move))
    && Number.isSafeInteger(hint.model_version) && (hint.model_version as number) >= 0;
}

function isAiStats(value: unknown): value is AiStats {
  if (!value || typeof value !== 'object') return false;
  const stats = value as Partial<AiStats>;
  return typeof stats.algorithm === 'string'
    && ['model_version', 'training_samples', 'human_moves', 'ai_moves', 'games_started', 'completed_games'].every((key) => Number.isSafeInteger(stats[key as keyof AiStats]) && (stats[key as keyof AiStats] as number) >= 0)
    && (stats.updated_at === null || typeof stats.updated_at === 'string');
}

async function send<T>(path: string, init: RequestInit, valid: (value: unknown) => value is T): Promise<T> {
  // Fetch can reject after the server has committed. Retry the identical serialized
  // request once; mutations carry a stable request ID so this cannot double-apply.
  let firstError: unknown;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const controller = new AbortController();
    let timeout: ReturnType<typeof globalThis.setTimeout> | undefined;
    try {
      const timedOut = new Promise<never>((_, reject) => {
        timeout = globalThis.setTimeout(() => {
          controller.abort();
          reject(new Error('Request timed out. Retrying the same request.'));
        }, REQUEST_TIMEOUT_MS);
      });
      return await Promise.race([
        fetch(url(path), { ...init, signal: controller.signal }).then((response) => readResponse(response, valid)),
        timedOut,
      ]);
    } catch (error) {
      if (attempt === 0) firstError = error;
      if (error instanceof ApiRequestError && error.code !== 'invalid_response') throw error;
      if (attempt === 1) throw new Error(firstError instanceof Error ? firstError.message : 'Network request failed.');
    } finally {
      if (timeout !== undefined) globalThis.clearTimeout(timeout);
    }
  }
  throw new Error(firstError instanceof Error ? firstError.message : 'Network request failed.');
}

async function readResponse<T>(response: Response, valid: (value: unknown) => value is T): Promise<T> {
  let body: unknown;
  try { body = await response.json(); }
  catch {
    if (response.ok) throw new ApiRequestError('The server response was incomplete. Retry the same request.', response.status, 'invalid_response');
    body = {};
  }
  if (!response.ok) {
    const errorBody = body as { detail?: { code?: string; message?: string; state?: GameState } | string; message?: string };
    const detail = errorBody?.detail;
    const message = typeof detail === 'string' ? detail : detail?.message || `${errorBody?.message || 'Request failed'} (Status: ${response.status})`;
    throw new ApiRequestError(message, response.status, typeof detail === 'object' ? detail?.code : undefined, typeof detail === 'object' ? detail?.state : undefined);
  }
  if (!valid(body)) throw new ApiRequestError('The server returned an invalid response. Retry the same request.', response.status, 'invalid_response');
  return body;
}

function post(body: unknown): RequestInit {
  return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}

export async function createGame(requestId: string = id()): Promise<GameState> {
  return send('/game', post({ request_id: requestId }), isGameState);
}

export async function getGame(gameId: string): Promise<GameState> {
  return send(`/game/${encodeURIComponent(gameId)}`, { method: 'GET' }, isGameState);
}

export async function makeMove(game: GameState, direction: Direction, source: MoveSource, requestId: string = id()): Promise<GameState> {
  return send('/game/move', post({
    game_id: game.game_id, direction, expected_revision: game.revision, request_id: requestId, source,
  }), isMoveResult);
}

export async function getHint(gameId: string): Promise<HintResponse> {
  return send(`/game/${encodeURIComponent(gameId)}/hint`, { method: 'GET' }, isHint);
}

export async function getAiStats(): Promise<AiStats> {
  return send('/ai/stats', { method: 'GET' }, isAiStats);
}
