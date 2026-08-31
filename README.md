# 2048 Game — Phase 1

A minimal, clean, and easy-to-understand **2048 Game** built with a **Python FastAPI** backend engine and a **React + TypeScript + Vite** frontend.

---

## Features

* **4×4 Grid Board**: Standard 2048 game dimensions.
* **4-Directional Movement**: Full support for `Left`, `Right`, `Up`, and `Down`.
* **Standard 2048 Merge Rules**: Tiles merge once per move (e.g., `[2, 2, 2, 2]` becomes `[4, 4, 0, 0]`).
* **Score Tracking**: Accumulates score based on merged tile values.
* **Random Tile Generation**: Places a `2` (90% chance) or `4` (10% chance) into an empty cell after every valid move.
* **Game-Over Detection**: Detects when no empty cells remain and no adjacent merges are possible.
* **Keyboard & Gesture Controls**: Arrow keys, WASD keys, mobile touch swipes, and on-screen D-Pad buttons.
* **FastAPI Backend**: Lightweight REST API.
* **React + Vite Frontend**: Clean modern UI with smooth CSS animations.

---

## Architecture

```text
React (TypeScript + Vite)
         ↓ HTTP REST API
FastAPI (Python 3.12+)
         ↓
    Game Class
         ↓
  2D Python List
```

### File Structure

```text
2048-game/
│
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py        # FastAPI API endpoints
│   │   ├── game.py        # Game engine class & logic
│   │   └── models.py      # Pydantic request/response schemas
│   │
│   ├── tests/
│   │   ├── test_game.py   # Unit tests for game engine
│   │   └── test_api.py    # FastAPI endpoint integration tests
│   │
│   └── requirements.txt   # Python dependencies
│
├── frontend/
│   ├── src/
│   │   ├── App.tsx        # React UI & Keyboard / Touch event handling
│   │   ├── api.ts         # API communication functions
│   │   ├── types.ts       # TypeScript interfaces
│   │   ├── styles.css     # CSS grid, tile colors & animations
│   │   └── main.tsx       # React entrypoint
│   │
│   ├── index.html
│   ├── package.json
│   ├── tsconfig.json
│   └── vite.config.ts
│
├── README.md
└── .gitignore
```

---

## DSA Concepts (Data Structures & Algorithms)

### 1. 2D Array Representation
The entire game board is stored as a 2D Python list (`board[row][column]`) of size $4 \times 4$:
```python
board = [
    [0, 0, 2, 0],
    [0, 0, 0, 0],
    [0, 2, 0, 0],
    [0, 0, 0, 4]
]
```
- `0` represents an empty cell.
- Positive integers represent tile values.

### 2. Matrix Traversal & Operations
- **Row Traversal**: Iterating over elements in `board[r]`.
- **Column Traversal**: Accessing `board[r][c]` for fixed $c$.
- **Matrix Transposition**: Swapping rows and columns (`board[r][c]` $\leftrightarrow$ `board[c][r]`).

### 3. Time & Space Complexity
- **Time Complexity per Move**: $O(N^2)$ where $N = 4$. Since $N$ is fixed, each move executes in $O(1)$ constant time ($16$ cell checks).
- **Space Complexity**: $O(N^2) = O(1)$ space for the $4 \times 4$ board representation.

---

## Movement Algorithm

The core engine uses a single row-processing algorithm (`_process_row`) combined with matrix transformations:

### 3-Step Row Processing (`_process_row`)

```text
Step 1: Remove zeroes
[2, 0, 2, 4]  ──►  [2, 2, 4]

Step 2: Merge adjacent equal tiles
[2, 2, 4]     ──►  [4, 4] (Score += 4)

Step 3: Pad with zeroes to length 4
[4, 4]        ──►  [4, 4, 0, 0]
```

### Directional Transformations

To avoid writing four different movement functions, we reuse `_process_row` for all directions:

* **LEFT**: Process each row directly.
* **RIGHT**: Reverse each row $\rightarrow$ process $\rightarrow$ reverse back.
* **UP**: Transpose matrix $\rightarrow$ process rows $\rightarrow$ transpose back.
* **DOWN**: Transpose matrix $\rightarrow$ reverse each row $\rightarrow$ process $\rightarrow$ reverse back $\rightarrow$ transpose back.

---

## How to Run

### 1. Run Backend

```bash
cd backend
python -m venv venv
# Windows:
.\venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
Backend server will run at: `http://localhost:8000`

### 2. Run Backend Tests

```bash
cd backend
pytest
```

### 3. Run Frontend

```bash
cd frontend
npm install
npm run dev
```
Frontend application will run at: `http://localhost:5173`

---

## Test Results

All 17 backend unit and integration tests pass:
- 12 Game Engine tests (movement, no-double-merge, scoring, invalid moves, game over, available moves)
- 5 API Endpoint tests (`POST /game`, `POST /game/move`, error handling)

---

## Known Limitations (Phase 1)

- Game states are stored in-memory (`games = {}`). Server restart resets active sessions.
- No user authentication or persistent database (intentional per Phase 1 scope).

---

## Future Roadmap

```text
Phase 1 (Current)
├── Functional 2048 Game
├── FastAPI REST Backend
├── React Frontend UI
└── Engine Unit Tests

Phase 2 (Next Step)
└── AI Player Implementation (Auto-play agent)

Phase 3
└── Search Algorithms (Minimax / Heuristics)

Phase 4
└── Expectimax AI Solver

Phase 5
└── AI Performance Comparison Dashboard
```
