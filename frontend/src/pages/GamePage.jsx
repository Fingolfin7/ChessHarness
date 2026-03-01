/**
 * GamePage — manages a single game session.
 *
 * Renders ModelPicker (setup) → GameView (playing / over).
 * All WebSocket game state lives here.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import ModelPicker from '../components/ModelPicker.jsx'
import GameView from '../components/GameView.jsx'
import { useAppContext } from '../context/AppContext.jsx'

const INITIAL_STATE = {
  phase: 'setup',
  players: { white: null, black: null },
  fen: 'start',
  lastMove: null,
  turn: 'white',
  thinking: false,
  reasoning: { white: '', black: '' },
  moves: [],
  plies: [],
  invalidAttempt: null,
  result: null,
  error: null,
}

function freshGameState() {
  return { ...INITIAL_STATE, phase: 'playing' }
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
    case 'GameSnapshotEvent':
      return {
        ...INITIAL_STATE,
        phase: event.phase ?? 'playing',
        players: event.players ?? { white: null, black: null },
        fen: event.fen ?? 'start',
        lastMove: event.lastMove ?? null,
        turn: event.turn ?? 'white',
        thinking: event.thinking ?? false,
        reasoning: event.reasoning ?? { white: '', black: '' },
        moves: event.moves ?? [],
        plies: event.plies ?? [],
        invalidAttempt: event.invalidAttempt ?? null,
        result: event.result ?? null,
        error: event.error ?? null,
      }

    case 'GameStartEvent':
      return {
        ...freshGameState(),
        fen: event.starting_fen || 'start',
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

export default function GamePage() {
  const navigate = useNavigate()
  const {
    models, authProviders, authReady, defaultSettings,
    connectProvider, disconnectProvider,
    startCopilotDeviceFlow, pollCopilotDeviceFlow,
    connectOpenAIChatGPTFromCodex,
  } = useAppContext()

  const [state, setState] = useState(INITIAL_STATE)
  const wsRef = useRef(null)
  const sessionRef = useRef(0)
  const stateRef = useRef(INITIAL_STATE)
  const lastGameRef = useRef(null)  // { white, black, settings } for rematch
  const reconnectRef = useRef({ timer: null, delayMs: 1000, enabled: false })

  useEffect(() => {
    stateRef.current = state
  }, [state])

  useEffect(() => () => {
    reconnectRef.current.enabled = false
    if (reconnectRef.current.timer) clearTimeout(reconnectRef.current.timer)
    wsRef.current?.close()
  }, [])

  const startGame = useCallback((white, black, settings) => {
    lastGameRef.current = { white, black, settings }
    if (wsRef.current && wsRef.current.readyState <= 1) {
      wsRef.current.close()
    }
    sessionRef.current += 1
    const thisSession = sessionRef.current
    reconnectRef.current.enabled = true
    reconnectRef.current.delayMs = 1000
    if (reconnectRef.current.timer) clearTimeout(reconnectRef.current.timer)
    setState(s => ({ ...s, error: null }))

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${location.host}/ws/game`
    const startPayload = { type: 'start', white, black, settings }

    const connect = (mode) => {
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        if (sessionRef.current !== thisSession || wsRef.current !== ws) return
        reconnectRef.current.delayMs = 1000
        ws.send(JSON.stringify(mode === 'start' ? startPayload : { type: 'resume' }))
      }

      ws.onmessage = (e) => {
        if (sessionRef.current !== thisSession || wsRef.current !== ws) return
        setState(s => applyEvent(s, JSON.parse(e.data)))
      }

      ws.onerror = () => {
        if (sessionRef.current !== thisSession || wsRef.current !== ws) return
        setState(s => ({ ...s, error: 'WebSocket connection error.', thinking: false }))
      }

      ws.onclose = () => {
        if (sessionRef.current !== thisSession || wsRef.current !== ws) return
        if (!reconnectRef.current.enabled) return
        if (stateRef.current.phase !== 'playing') {
          setState(s => s.phase === 'playing' ? { ...s, phase: 'over' } : s)
          return
        }
        const delay = reconnectRef.current.delayMs
        reconnectRef.current.timer = setTimeout(() => {
          reconnectRef.current.delayMs = Math.min(reconnectRef.current.delayMs * 2, 30000)
          connect('resume')
        }, delay)
      }
    }

    connect('start')
  }, [])

  const stopGame = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: 'stop' }))
  }, [])

  const newGame = useCallback(() => {
    sessionRef.current += 1
    reconnectRef.current.enabled = false
    if (reconnectRef.current.timer) clearTimeout(reconnectRef.current.timer)
    wsRef.current?.close()
    wsRef.current = null
    setState(INITIAL_STATE)
  }, [])

  const rematch = useCallback(() => {
    if (!lastGameRef.current) return
    const { white, black, settings } = lastGameRef.current
    startGame(black, white, settings)  // swap colours
  }, [startGame])

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
        onChatGPTCodexConnect={connectOpenAIChatGPTFromCodex}
        onStart={startGame}
        error={state.error}
        defaultSettings={defaultSettings}
      />
    )
  }

  return <GameView state={state} onStop={stopGame} onNewGame={newGame} onRematch={rematch} />
}
