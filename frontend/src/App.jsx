import { useState, useRef, useCallback, useEffect } from 'react'
import ModelPicker from './components/ModelPicker.jsx'
import GameView from './components/GameView.jsx'

const INITIAL_STATE = {
  phase: 'setup',       // 'setup' | 'playing' | 'over'
  players: { white: null, black: null },
  fen: 'start',
  lastMove: null,       // { from: 'e2', to: 'e4' }
  turn: 'white',
  thinking: false,
  reasoning: { white: '', black: '' },
  moves: [],            // [{ number, white: {san, reasoning}, black: {san, reasoning} }]
  plies: [],            // flat: [{color, san, reasoning, fen_after, from, to, moveNumber}]
  invalidAttempt: null,
  result: null,
  error: null,
}

function updateMoves(moves, event) {
  const { color, move_san, reasoning, move_number } = event
  const entry = { san: move_san, reasoning }
  const idx = moves.findIndex(m => m.number === move_number)
  if (idx >= 0) {
    const updated = [...moves]
    updated[idx] = { ...updated[idx], [color]: entry }
    return updated
  }
  return [...moves, { number: move_number, [color]: entry }]
}

function applyEvent(state, event) {
  switch (event.type) {
    case 'GameStartEvent':
      return {
        ...state,
        phase: 'playing',
        players: {
          white: { name: event.white_name },
          black: { name: event.black_name },
        },
      }

    case 'TurnStartEvent':
      return { ...state, turn: event.color }

    case 'MoveRequestedEvent':
      return { ...state, thinking: true, invalidAttempt: null }

    case 'InvalidMoveEvent':
      return {
        ...state,
        thinking: false,
        invalidAttempt: {
          color: event.color,
          move: event.attempted_move,
          error: event.error,
          attempt: event.attempt_num,
        },
      }

    case 'MoveAppliedEvent':
      return {
        ...state,
        fen: event.fen_after,
        lastMove: {
          from: event.move_uci.slice(0, 2),
          to: event.move_uci.slice(2, 4),
        },
        thinking: false,
        invalidAttempt: null,
        reasoning: {
          ...state.reasoning,
          [event.color]: event.reasoning,
        },
        moves: updateMoves(state.moves, event),
        plies: [...state.plies, {
          color: event.color,
          san: event.move_san,
          reasoning: event.reasoning,
          fen_after: event.fen_after,
          from: event.move_uci.slice(0, 2),
          to: event.move_uci.slice(2, 4),
          moveNumber: event.move_number,
        }],
      }

    case 'CheckEvent':
      return state

    case 'GameOverEvent':
      return {
        ...state,
        phase: 'over',
        thinking: false,
        result: {
          result: event.result,
          reason: event.reason,
          winner: event.winner_name,
          pgn: event.pgn,
        },
      }

    case 'error':
      return { ...state, error: event.message, thinking: false }

    default:
      return state
  }
}

export default function App() {
  const [state, setState] = useState(INITIAL_STATE)
  const [models, setModels] = useState([])
  const wsRef = useRef(null)

  useEffect(() => {
    fetch('/api/models')
      .then(r => r.json())
      .then(setModels)
      .catch(() => setState(s => ({ ...s, error: 'Could not load models from server.' })))
  }, [])

  const startGame = useCallback((white, black) => {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${location.host}/ws/game`)
    wsRef.current = ws

    ws.onopen = () => ws.send(JSON.stringify({ type: 'start', white, black }))
    ws.onmessage = (e) => setState(s => applyEvent(s, JSON.parse(e.data)))
    ws.onerror = () => setState(s => ({ ...s, error: 'WebSocket connection error.', thinking: false }))
    ws.onclose = () => setState(s => s.phase === 'playing' ? { ...s, phase: 'over' } : s)
  }, [])

  const stopGame = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: 'stop' }))
  }, [])

  const newGame = useCallback(() => {
    wsRef.current?.close()
    wsRef.current = null
    setState(INITIAL_STATE)
  }, [])

  if (state.phase === 'setup') {
    return <ModelPicker models={models} onStart={startGame} error={state.error} />
  }

  return <GameView state={state} onStop={stopGame} onNewGame={newGame} />
}
