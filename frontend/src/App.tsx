import React, { useEffect, useRef, useState, useCallback } from 'react';
import { createGame, getAiStats, getGame, getHint, makeMove } from './api';
import { AiStats, ApiRequestError, Direction, GameState, HintResponse, MoveSource } from './types';
import './styles.css';

type PendingMove = { game: GameState; direction: Direction; source: MoveSource; requestId: string; generation: number; retryExact?: boolean };
const QUEUE_LIMIT = 8;
const newId = (): string => crypto.randomUUID();

export const App: React.FC = () => {
  const [gameState, setGameState] = useState<GameState | null>(null);
  const [highScore, setHighScore] = useState(() => Number(localStorage.getItem('2048_high_score') || 0));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [moving, setMoving] = useState(false);
  const [pendingCount, setPendingCount] = useState(0);
  const [queueFull, setQueueFull] = useState(false);
  const [failedMove, setFailedMove] = useState<PendingMove | null>(null);
  const [failedCreateId, setFailedCreateId] = useState<string | null>(null);
  const [hint, setHint] = useState<HintResponse | null>(null);
  const [hintBusy, setHintBusy] = useState(false);
  const [stats, setStats] = useState<AiStats | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [autoPlay, setAutoPlay] = useState(false);

  const stateRef = useRef<GameState | null>(null);
  const queuesRef = useRef(new Map<number, PendingMove[]>());
  const runningGenerationsRef = useRef(new Set<number>());
  const generationRef = useRef(0);
  const loadingRef = useRef(true);
  const failedMoveRef = useRef<PendingMove | null>(null);
  const failedCreateIdRef = useRef<string | null>(null);
  const hintBusyRef = useRef(false);
  const hintTokenRef = useRef(0);
  const autoRef = useRef(false);
  const autoTimerRef = useRef<number | null>(null);
  const scheduleAutoHintRef = useRef<() => void>(() => undefined);
  const initialRequestRef = useRef<Promise<GameState> | null>(null);
  const touchStartRef = useRef<{ x: number; y: number } | null>(null);

  const publishState = useCallback((state: GameState | null) => {
    stateRef.current = state;
    setGameState(state);
    if (state) localStorage.setItem('2048_game_id', state.game_id);
  }, []);

  const clearAutomation = useCallback(() => {
    autoRef.current = false;
    setAutoPlay(false);
    if (autoTimerRef.current !== null) window.clearTimeout(autoTimerRef.current);
    autoTimerRef.current = null;
    hintTokenRef.current += 1;
    hintBusyRef.current = false;
    setHintBusy(false);
    setHint(null);
  }, []);

  const stopAutoQueue = useCallback(() => {
    const generation = generationRef.current;
    const queue = queuesRef.current.get(generation) || [];
    const remaining = queue.filter((move) => move.source !== 'ai');
    queue.splice(0, queue.length, ...remaining);
    setPendingCount(queue.length + (runningGenerationsRef.current.has(generation) ? 1 : 0));
  }, []);

  const runQueue = useCallback(async (generation: number) => {
    const queue = queuesRef.current.get(generation) || [];
    if (runningGenerationsRef.current.has(generation) || (failedMoveRef.current?.generation === generation)) return;
    runningGenerationsRef.current.add(generation);
    if (generation === generationRef.current) setMoving(true);
    try {
      while (queue.length) {
        const item = queue.shift()!;
        if (generation !== generationRef.current || item.generation !== generation) break;
        if (generation === generationRef.current) setPendingCount(queue.length + 1);
        const latest = stateRef.current;
        if (!latest || latest.game_id !== item.game.game_id || latest.game_over) continue;
        let operation: PendingMove;
        if (item.retryExact || item.source === 'ai') {
          if (item.source === 'ai' && !item.retryExact && (latest.revision !== item.game.revision || !latest.legal_moves.includes(item.direction))) {
            setHint(null);
            if (generation === generationRef.current) setError('That hint no longer matches the current board. Request a fresh hint.');
            if (autoRef.current) clearAutomation();
            continue;
          }
          operation = item;
        } else {
          // Human intent is directional, so bind it to the latest committed revision.
          operation = { ...item, game: latest };
        }
        try {
          const result = await makeMove(operation.game, operation.direction, operation.source, operation.requestId);
          if (operation.generation !== generationRef.current) continue;
          const current = stateRef.current;
          if (current?.game_id === result.game_id && result.revision >= current.revision) {
            publishState(result);
            setError(null);
            void getAiStats().then((latest) => { setStats(latest); setStatsError(null); }).catch((reason) => setStatsError(reason instanceof Error ? reason.message : 'Shared model stats are unavailable.'));
          }
        } catch (reason) {
          if (operation.generation !== generationRef.current) continue;
          if (reason instanceof ApiRequestError && reason.code === 'stale_revision' && reason.state) {
            queue.length = 0;
            publishState(reason.state);
            setError('The game changed on the server. Your queued moves were cleared.');
            clearAutomation();
            break;
          }
          queue.length = 0;
          setFailedMove(operation);
          failedMoveRef.current = operation;
          setError(reason instanceof Error ? reason.message : 'Move outcome is unknown. Retry this move or start a new game.');
          clearAutomation();
          break;
        }
      }
    } finally {
      runningGenerationsRef.current.delete(generation);
      if (generation === generationRef.current) {
        setMoving(false);
        setPendingCount(queue.length);
        if (autoRef.current && stateRef.current?.game_over) clearAutomation();
        else if (autoRef.current && !queue.length && !failedMoveRef.current) scheduleAutoHintRef.current();
      }
      if (!queue.length && failedMoveRef.current?.generation !== generation) queuesRef.current.delete(generation);
    }
  }, [clearAutomation, publishState]);

  const enqueueMove = useCallback((direction: Direction, source: MoveSource = 'human', requestId = newId(), prior?: PendingMove, decisionGame?: GameState) => {
    const current = stateRef.current;
    const generation = generationRef.current;
    const queue = queuesRef.current.get(generation) || [];
    queuesRef.current.set(generation, queue);
    if (!current || loadingRef.current || current.game_over || (failedMoveRef.current && !prior)) return;
    if (source === 'human') {
      clearAutomation();
      stopAutoQueue();
    }
    const queued = prior ? { ...prior, retryExact: true, generation } : { game: decisionGame || current, direction, source, requestId, generation };
    if (!prior && queue.length + (runningGenerationsRef.current.has(generation) ? 1 : 0) >= QUEUE_LIMIT) {
      setQueueFull(true);
      window.setTimeout(() => setQueueFull(false), 1800);
      return;
    }
    queue.push(queued);
    setPendingCount(queue.length + (runningGenerationsRef.current.has(generation) ? 1 : 0));
    void runQueue(generation);
  }, [clearAutomation, failedMove, loading, runQueue, stopAutoQueue]);

  const startNewGame = useCallback(async (retryRequestId?: string) => {
    const oldGeneration = generationRef.current;
    queuesRef.current.get(oldGeneration)?.splice(0);
    const generation = ++generationRef.current;
    queuesRef.current.set(generation, []);
    setMoving(false);
    setPendingCount(0);
    setFailedMove(null);
    failedMoveRef.current = null;
    setFailedCreateId(null);
    failedCreateIdRef.current = null;
    const createRequestId = retryRequestId || newId();
    clearAutomation();
    loadingRef.current = true;
    setLoading(true);
    setError(null);
    try {
      const state = await createGame(createRequestId);
      if (generation !== generationRef.current) return;
      setFailedCreateId(null);
      failedCreateIdRef.current = null;
      publishState(state);
    } catch (reason) {
      if (generation === generationRef.current) {
        setFailedCreateId(createRequestId);
        failedCreateIdRef.current = createRequestId;
        setError(reason instanceof Error ? reason.message : 'Could not start a game.');
      }
    } finally {
      if (generation === generationRef.current) setLoading(false);
      if (generation === generationRef.current) loadingRef.current = false;
    }
  }, [clearAutomation, publishState]);

  useEffect(() => {
    const savedId = localStorage.getItem('2048_game_id');
    const generation = ++generationRef.current;
    if (!initialRequestRef.current) {
      if (!savedId) failedCreateIdRef.current = newId();
      initialRequestRef.current = savedId ? getGame(savedId) : createGame(failedCreateIdRef.current!);
    }
    initialRequestRef.current.then((restored) => {
        if (generation === generationRef.current) publishState(restored);
      }).catch((reason) => {
        if (generation !== generationRef.current) return;
        if (savedId && reason instanceof ApiRequestError && reason.status === 404) {
          localStorage.removeItem('2048_game_id');
          setError('Your saved game is no longer available. Start a new game to continue.');
        } else {
          if (!savedId) setFailedCreateId(failedCreateIdRef.current);
          setError(reason instanceof Error ? reason.message : 'Could not load your game.');
        }
      }).finally(() => {
        if (generation === generationRef.current) setLoading(false);
        if (generation === generationRef.current) loadingRef.current = false;
      });
    void getAiStats().then(setStats).catch((reason) => setStatsError(reason instanceof Error ? reason.message : 'Shared model stats are unavailable.'));
    return () => { generationRef.current += 1; clearAutomation(); };
  }, [clearAutomation, publishState]);

  useEffect(() => {
    if (gameState && gameState.score > highScore) {
      setHighScore(gameState.score);
      localStorage.setItem('2048_high_score', String(gameState.score));
    }
  }, [gameState, highScore]);

  const requestHint = useCallback(async (apply = false) => {
    const current = stateRef.current;
    if (!current || loadingRef.current || failedMoveRef.current || current.game_over) return;
    if (hintBusyRef.current) {
      if (apply && autoRef.current) clearAutomation();
      return;
    }
    const token = ++hintTokenRef.current;
    hintBusyRef.current = true;
    setHintBusy(true);
    setError(null);
    try {
      const result = await getHint(current.game_id);
      const latest = stateRef.current;
      if (token !== hintTokenRef.current) return;
      if (!latest || latest.game_id !== result.game_id || latest.revision !== result.revision) {
        setHint(null);
        if (autoRef.current) clearAutomation();
        setError('The board changed before the hint arrived. Request a fresh hint.');
        return;
      }
      setHint(result);
      if (apply) {
        if (!result.direction || !latest.legal_moves.includes(result.direction)) {
          clearAutomation();
          setError('Autoplay stopped because there is no legal hint for this board.');
        } else enqueueMove(result.direction, 'ai', newId(), undefined, latest);
      }
    } catch (reason) {
      if (token === hintTokenRef.current) {
        if (autoRef.current) {
          clearAutomation();
          setError(`Autoplay stopped: ${reason instanceof Error ? reason.message : 'could not load a hint.'}`);
        } else setError(reason instanceof Error ? reason.message : 'Could not load a hint.');
      }
    } finally {
      if (token === hintTokenRef.current) {
        hintBusyRef.current = false;
        setHintBusy(false);
      }
    }
  }, [clearAutomation, enqueueMove, loading]);

  const scheduleAutoHint = useCallback(() => {
    if (!autoRef.current || autoTimerRef.current !== null) return;
    autoTimerRef.current = window.setTimeout(() => {
      autoTimerRef.current = null;
      if (!autoRef.current) return;
      void requestHint(true);
    }, 250);
  }, [requestHint]);
  scheduleAutoHintRef.current = scheduleAutoHint;

  const toggleAuto = useCallback(() => {
    if (autoRef.current) {
      clearAutomation();
      stopAutoQueue();
      return;
    }
    if (hintBusyRef.current || failedMoveRef.current || loadingRef.current || !stateRef.current || stateRef.current.game_over) return;
    autoRef.current = true;
    setAutoPlay(true);
    scheduleAutoHint();
  }, [clearAutomation, scheduleAutoHint, stopAutoQueue]);

  const applyHint = useCallback((suggestion: HintResponse) => {
    const current = stateRef.current;
    if (!current || current.game_id !== suggestion.game_id || current.revision !== suggestion.revision || !suggestion.direction || !current.legal_moves.includes(suggestion.direction)) {
      setHint(null);
      setError('That hint no longer matches the current board. Request a fresh hint.');
      return;
    }
    enqueueMove(suggestion.direction, 'ai', newId(), undefined, current);
    setHint(null);
  }, [enqueueMove]);

  const retryMove = useCallback(() => {
    if (!failedMove) return;
    const pending = failedMove;
    setFailedMove(null);
    failedMoveRef.current = null;
    setError(null);
    enqueueMove(pending.direction, pending.source, pending.requestId, pending);
  }, [enqueueMove, failedMove]);

  const retryCreate = useCallback(() => {
    if (failedCreateId) void startNewGame(failedCreateId);
  }, [failedCreateId, startNewGame]);

  const handleKeyDown = (event: KeyboardEvent) => {
    if (event.repeat || (event.target instanceof HTMLElement && (event.target.isContentEditable || /INPUT|TEXTAREA|SELECT/.test(event.target.tagName)))) return;
    const keys: Record<string, Direction> = { ArrowLeft: 'left', a: 'left', A: 'left', ArrowRight: 'right', d: 'right', D: 'right', ArrowUp: 'up', w: 'up', W: 'up', ArrowDown: 'down', s: 'down', S: 'down' };
    const direction = keys[event.key];
    if (direction) { event.preventDefault(); enqueueMove(direction); }
  };
  useEffect(() => { window.addEventListener('keydown', handleKeyDown); return () => window.removeEventListener('keydown', handleKeyDown); });

  const getTileClass = (value: number) => value === 0 ? '' : value <= 2048 ? `tile-${value}` : 'tile-super';
  const legalHint = Boolean(hint?.direction && gameState?.game_id === hint.game_id && gameState.revision === hint.revision && gameState.legal_moves.includes(hint.direction));

  return <main className="game-container" aria-busy={loading || moving}>
    <header className="header">
      <div className="title-section"><h1>2048</h1><p>Join tiles to reach <strong>2048!</strong></p></div>
      <div className="scores-section" aria-label="Scores"><div className="score-box"><div className="score-label">SCORE</div><div className="score-value">{gameState?.score ?? 0}</div></div><div className="score-box"><div className="score-label">BEST</div><div className="score-value">{highScore}</div></div></div>
    </header>
    <section className="controls-bar" aria-label="Game controls">
      <p className="instructions">Use <strong>Arrow Keys</strong> or <strong>WASD</strong> to play</p>
      <button className="new-game-btn" onClick={() => void startNewGame()}>{loading ? 'Starting…' : 'New Game'}</button>
    </section>
    {error && <div className="error-banner" role="alert"><span>{error}</span>{failedMove ? <button className="retry-btn" onClick={retryMove}>Retry pending move</button> : failedCreateId ? <button className="retry-btn" onClick={retryCreate}>Retry new game</button> : null}</div>}
    {queueFull && <p className="queue-notice" role="status">Move queue is full. Try again when a move finishes.</p>}
    <div className="pending-status" role="status" aria-live="polite">{loading ? 'Loading game…' : moving ? `Playing${pendingCount > 1 ? ` · ${pendingCount - 1} queued` : '…'}` : ''}</div>
    <section className="board-container" aria-label="2048 game board" onTouchStart={(event) => { const touch = event.touches[0]; touchStartRef.current = { x: touch.clientX, y: touch.clientY }; }} onTouchEnd={(event) => {
      if (!touchStartRef.current) return;
      const touch = event.changedTouches[0], dx = touch.clientX - touchStartRef.current.x, dy = touch.clientY - touchStartRef.current.y;
      if (Math.max(Math.abs(dx), Math.abs(dy)) > 30) enqueueMove(Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? 'right' : 'left') : (dy > 0 ? 'down' : 'up'));
      touchStartRef.current = null;
    }}>
      <div className="grid" role="grid" aria-label={gameState ? `Score ${gameState.score}` : 'Game loading'}>{gameState ? gameState.board.map((row, r) => row.map((value, c) => <div key={`${r}-${c}`} role="gridcell" aria-label={value ? String(value) : 'empty'} className="cell">{value > 0 && <div className={`tile ${getTileClass(value)}`}>{value}</div>}</div>)) : Array.from({ length: 16 }, (_, i) => <div key={i} className="cell" />)}</div>
      {gameState?.game_over && <div className="overlay" role="status"><h2>Game Over!</h2><p>Final Score: <strong>{gameState.score}</strong></p><button className="new-game-btn" onClick={() => void startNewGame()}>Try Again</button></div>}
    </section>
    <nav className="dpad-container" aria-label="Move controls"><div className="dpad-row"><button className="dpad-btn" disabled={loading || !gameState || gameState.game_over} onClick={() => enqueueMove('up')} aria-label="Move up">▲</button></div><div className="dpad-row"><button className="dpad-btn" disabled={loading || !gameState || gameState.game_over} onClick={() => enqueueMove('left')} aria-label="Move left">◄</button><button className="dpad-btn" disabled={loading || !gameState || gameState.game_over} onClick={() => enqueueMove('down')} aria-label="Move down">▼</button><button className="dpad-btn" disabled={loading || !gameState || gameState.game_over} onClick={() => enqueueMove('right')} aria-label="Move right">►</button></div></nav>
    <section className="ai-panel" aria-labelledby="ai-title"><div className="ai-heading"><h2 id="ai-title">Shared learning model</h2><span>{stats ? `Model v${stats.model_version}` : statsError ? 'Stats unavailable' : 'Loading stats…'}</span></div>
      <p>Human moves update a shared model. Hint and autoplay moves are tagged as AI and do not train it. More play adds data; it does not guarantee stronger play. Anonymous game moves are used for shared learning.</p>
      {stats && <dl className="stats-grid"><div><dt>Training samples</dt><dd>{stats.training_samples}</dd></div><div><dt>Human moves</dt><dd>{stats.human_moves}</dd></div><div><dt>AI moves</dt><dd>{stats.ai_moves}</dd></div><div><dt>Games started</dt><dd>{stats.games_started}</dd></div><div><dt>Completed</dt><dd>{stats.completed_games}</dd></div><div><dt>Method</dt><dd>{stats.algorithm}</dd></div></dl>}
      {statsError && <p className="stats-error" role="status">Shared model stats could not be loaded. The game remains available.</p>}
      <div className="ai-controls"><button className="ai-btn" onClick={() => { clearAutomation(); void requestHint(); }} disabled={loading || Boolean(failedMove) || hintBusy || !gameState || gameState.game_over}>{hintBusy ? 'Thinking…' : 'Hint'}</button><button className="ai-btn" onClick={() => toggleAuto()} disabled={loading || Boolean(failedMove) || (!autoPlay && hintBusy) || !gameState || gameState.game_over}>{autoPlay ? 'Stop autoplay' : 'Start autoplay'}</button>{legalHint && <><span className="hint-copy">Suggested move: <strong>{hint!.direction}</strong></span><button className="ai-btn" disabled={Boolean(failedMove)} onClick={() => applyHint(hint!)}>Apply hint</button></>}</div>
    </section>
    <footer className="footer"><p>Human moves update the shared learning model; AI moves are excluded from training.</p></footer>
  </main>;
};

export default App;
