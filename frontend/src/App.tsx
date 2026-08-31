import React, { useEffect, useState, useCallback, useRef } from 'react';
import { createGame, makeMove } from './api';
import { Direction, GameState } from './types';
import './styles.css';

export const App: React.FC = () => {
  const [gameState, setGameState] = useState<GameState | null>(null);
  const [highScore, setHighScore] = useState<number>(() => {
    const saved = localStorage.getItem('2048_high_score');
    return saved ? parseInt(saved, 10) : 0;
  });
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [moving, setMoving] = useState<boolean>(false);

  const touchStartRef = useRef<{ x: number; y: number } | null>(null);

  // Initialize or restart game
  const startNewGame = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const newGame = await createGame();
      setGameState(newGame);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not connect to game backend server.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    startNewGame();
  }, [startNewGame]);

  // Update high score whenever score changes
  useEffect(() => {
    if (gameState && gameState.score > highScore) {
      setHighScore(gameState.score);
      localStorage.setItem('2048_high_score', gameState.score.toString());
    }
  }, [gameState, highScore]);

  // Handle player move
  const handleMove = useCallback(
    async (direction: Direction) => {
      if (!gameState || gameState.game_over || moving) return;

      setMoving(true);
      setError(null);
      try {
        const updated = await makeMove(direction, gameState.game_id);
        setGameState(updated);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Error executing move.');
      } finally {
        setMoving(false);
      }
    },
    [gameState, moving]
  );

  // Keyboard navigation handler
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Prevent browser default scroll on arrow keys
      if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', ' '].includes(e.key)) {
        e.preventDefault();
      }

      switch (e.key) {
        case 'ArrowLeft':
        case 'a':
        case 'A':
          handleMove('left');
          break;
        case 'ArrowRight':
        case 'd':
        case 'D':
          handleMove('right');
          break;
        case 'ArrowUp':
        case 'w':
        case 'W':
          handleMove('up');
          break;
        case 'ArrowDown':
        case 's':
        case 'S':
          handleMove('down');
          break;
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleMove]);

  // Touch gesture handlers for mobile
  const handleTouchStart = (e: React.TouchEvent) => {
    const touch = e.touches[0];
    touchStartRef.current = { x: touch.clientX, y: touch.clientY };
  };

  const handleTouchEnd = (e: React.TouchEvent) => {
    if (!touchStartRef.current) return;

    const touch = e.changedTouches[0];
    const deltaX = touch.clientX - touchStartRef.current.x;
    const deltaY = touch.clientY - touchStartRef.current.y;
    const minSwipeDistance = 30;

    if (Math.abs(deltaX) > Math.abs(deltaY)) {
      if (Math.abs(deltaX) > minSwipeDistance) {
        if (deltaX > 0) {
          handleMove('right');
        } else {
          handleMove('left');
        }
      }
    } else {
      if (Math.abs(deltaY) > minSwipeDistance) {
        if (deltaY > 0) {
          handleMove('down');
        } else {
          handleMove('up');
        }
      }
    }

    touchStartRef.current = null;
  };

  // Dynamic tile class generator
  const getTileClass = (val: number): string => {
    if (val === 0) return '';
    if (val <= 2048) return `tile-${val}`;
    return 'tile-super';
  };

  return (
    <div className="game-container">
      {/* Header */}
      <header className="header">
        <div className="title-section">
          <h1>2048</h1>
          <p>Join tiles to reach <strong>2048!</strong></p>
        </div>
        <div className="scores-section">
          <div className="score-box">
            <div className="score-label">SCORE</div>
            <div className="score-value">{gameState ? gameState.score : 0}</div>
          </div>
          <div className="score-box">
            <div className="score-label">BEST</div>
            <div className="score-value">{highScore}</div>
          </div>
        </div>
      </header>

      {/* Control Bar */}
      <div className="controls-bar">
        <div className="instructions">
          Use <strong>Arrow Keys</strong> or <strong>WASD</strong> to play
        </div>
        <button className="new-game-btn" onClick={startNewGame} disabled={loading}>
          {loading ? 'Starting...' : 'New Game'}
        </button>
      </div>

      {/* Error Message */}
      {error && (
        <div className="error-banner">
          <span>⚠️ {error}</span>
          <button className="retry-btn" onClick={startNewGame}>
            Retry
          </button>
        </div>
      )}

      {/* 4x4 Game Board */}
      <div
        className="board-container"
        onTouchStart={handleTouchStart}
        onTouchEnd={handleTouchEnd}
      >
        <div className="grid">
          {gameState ? (
            gameState.board.map((row, r) =>
              row.map((val, c) => (
                <div key={`${r}-${c}`} className="cell">
                  {val > 0 && (
                    <div className={`tile ${getTileClass(val)}`}>
                      {val}
                    </div>
                  )}
                </div>
              ))
            )
          ) : (
            // Skeleton 4x4 grid while loading
            Array.from({ length: 16 }).map((_, i) => (
              <div key={i} className="cell" />
            ))
          )}
        </div>

        {/* Game Over Overlay */}
        {gameState && gameState.game_over && (
          <div className="overlay">
            <h2>Game Over!</h2>
            <p>Final Score: <strong>{gameState.score}</strong></p>
            <button className="new-game-btn" onClick={startNewGame}>
              Try Again
            </button>
          </div>
        )}
      </div>

      {/* On-Screen D-Pad for Touch/Click accessibility */}
      <div className="dpad-container">
        <div className="dpad-row">
          <button className="dpad-btn" onClick={() => handleMove('up')} aria-label="Up">
            ▲
          </button>
        </div>
        <div className="dpad-row">
          <button className="dpad-btn" onClick={() => handleMove('left')} aria-label="Left">
            ◄
          </button>
          <button className="dpad-btn" onClick={() => handleMove('down')} aria-label="Down">
            ▼
          </button>
          <button className="dpad-btn" onClick={() => handleMove('right')} aria-label="Right">
            ►
          </button>
        </div>
      </div>

      {/* Footer */}
      <footer className="footer">
        <p>Phase 1: FastAPI + React + 2D List Engine</p>
      </footer>
    </div>
  );
};

export default App;
