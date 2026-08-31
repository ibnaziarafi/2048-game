export type Direction = 'left' | 'right' | 'up' | 'down';

export type Board = number[][];

export interface GameState {
  game_id: string;
  board: Board;
  score: number;
  game_over: boolean;
}

export interface ApiError {
  message: string;
}
