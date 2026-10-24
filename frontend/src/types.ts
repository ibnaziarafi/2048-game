export type Direction = 'left' | 'right' | 'up' | 'down';
export type MoveSource = 'human' | 'ai';
export type Board = number[][];

export interface GameState {
  game_id: string;
  board: Board;
  score: number;
  game_over: boolean;
  revision: number;
  legal_moves: Direction[];
  model_version: number;
  moved?: boolean;
  score_gained?: number;
}

export interface HintResponse {
  game_id: string;
  revision: number;
  direction: Direction | null;
  legal_moves: Direction[];
  model_version: number;
}

export interface AiStats {
  algorithm: string;
  model_version: number;
  training_samples: number;
  human_moves: number;
  ai_moves: number;
  games_started: number;
  completed_games: number;
  updated_at: string | null;
}

export class ApiRequestError extends Error {
  status: number;
  code?: string;
  state?: GameState;
  constructor(message: string, status: number, code?: string, state?: GameState) {
    super(message);
    this.name = 'ApiRequestError';
    this.status = status;
    this.code = code;
    this.state = state;
  }
}
