import { Direction, GameState } from './types';

const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || 'https://2048-game-backend-six.vercel.app'
).replace(/\/$/, '');

export async function createGame(): Promise<GameState> {
  const response = await fetch(`${API_BASE_URL}/game`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
  });

  if (!response.ok) {
    throw new Error(`Failed to start a new game (Status: ${response.status})`);
  }

  return response.json();
}

export async function makeMove(direction: Direction, gameId?: string): Promise<GameState> {
  const response = await fetch(`${API_BASE_URL}/game/move`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      direction,
      game_id: gameId,
    }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const message = errorData.detail || `Failed to make move (Status: ${response.status})`;
    throw new Error(message);
  }

  return response.json();
}
