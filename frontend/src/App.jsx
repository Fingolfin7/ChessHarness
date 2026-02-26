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
      return {
        ...state,
        thinking: true,
        invalidAttempt: null,
        reasoning: { ...state.reasoning, [event.color]: '' },
      }

    case 'ReasoningChunkEvent':
      return {
        ...state,
        reasoning: {
          ...state.reasoning,
          [event.color]: (state.reasoning[event.color] || '') + event.chunk,
        },
      }

    case 'InvalidMoveEvent':
      console.error('[InvalidMove]', event.color, `attempt ${event.attempt_num}:`, event.error)
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
      console.error('[WebSocket error]', event.message)
      return { ...state, error: event.message, thinking: false }

    default:
      return state
  }
}

export default function App() {
  const [state, setState] = useState(INITIAL_STATE)
  const [models, setModels] = useState([])
  const [authProviders, setAuthProviders] = useState({})
  const [authReady, setAuthReady] = useState(false)
  const wsRef = useRef(null)

  useEffect(() => {
    fetch('/api/models')
      .then(r => r.json())
      .then(setModels)
      .catch(() => setState(s => ({ ...s, error: 'Could not load models from server.' })))

    fetch('/api/auth/providers')
      .then(r => r.json())
      .then(rows => {
        const mapped = {}
        rows.forEach(row => { mapped[row.provider] = !!row.connected })
        setAuthProviders(mapped)
      })
      .catch(() => {})
      .finally(() => setAuthReady(true))
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


  const refreshModels = useCallback(() => {
    fetch('/api/models').then(r => r.json()).then(setModels).catch(() => {})
  }, [])

  const connectProvider = useCallback(async (provider, token) => {
    try {
      const res = await fetch('/api/auth/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, token }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        return { ok: false, error: data.detail || 'Connection failed.' }
      }
      setAuthProviders(prev => ({ ...prev, [provider]: true }))
      refreshModels()
      return { ok: true }
    } catch {
      return { ok: false, error: 'Network error.' }
    }
  }, [refreshModels])

  const startCopilotDeviceFlow = useCallback(async () => {
    try {
      const res = await fetch('/api/auth/copilot/device/start', { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) return { ok: false, error: data.detail || 'Failed to start.' }
      return { ok: true, ...data }
    } catch {
      return { ok: false, error: 'Network error.' }
    }
  }, [])

  const pollCopilotDeviceFlow = useCallback(async (deviceCode) => {
    try {
      const res = await fetch('/api/auth/copilot/device/poll', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_code: deviceCode }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        return { status: 'error', error: data.detail || `Server error ${res.status}` }
      }
      if (data.status === 'connected') {
        setAuthProviders(prev => ({ ...prev, copilot: true }))
        // Models are fetched in a background task on the server; give it a moment
        // then refresh so the dropdowns populate without requiring a page reload.
        setTimeout(refreshModels, 1500)
      }
      return data
    } catch {
      return { status: 'error', error: 'Network error.' }
    }
  }, [refreshModels])

  const disconnectProvider = useCallback(async (provider) => {
    try {
      const res = await fetch('/api/auth/disconnect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider }),
      })
      if (!res.ok) return false
      const data = await res.json()
      setAuthProviders(prev => ({ ...prev, [provider]: !!data.connected }))
      return true
    } catch {
      return false
    }
  }, [])

  if (state.phase === 'setup') {
    return (
      <ModelPicker
        models={models}
        authProviders={authProviders}
        authReady={authReady}
        onConnect={connectProvider}
        onDisconnect={disconnectProvider}
        onCopilotDeviceStart={startCopilotDeviceFlow}
        onCopilotDevicePoll={pollCopilotDeviceFlow}
        onStart={startGame}
        error={state.error}
      />
    )
  }

  return <GameView state={state} onStop={stopGame} onNewGame={newGame} />
}
