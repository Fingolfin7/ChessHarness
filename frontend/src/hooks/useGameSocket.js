/**
 * useGameSocket
 *
 * Connects to /ws/tournament/game/{matchId} for the GameDetail drill-down view.
 * Automatically reconnects on drop; the server replays the per-match event log
 * on each new connection, so GameStartEvent resets state before moves replay.
 *
 * Returns all game state fields plus `connStatus`:
 *   'connecting' | 'connected' | 'reconnecting'
 */

import { useReducer, useMemo } from 'react'
import { useReconnectingWebSocket } from './useReconnectingWebSocket.js'

const INITIAL_STATE = {
  phase: 'playing',
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
        ...INITIAL_STATE,
        phase: 'playing',
        fen: event.starting_fen || 'start',
        players: {
          white: { name: event.white_name },
          black: { name: event.black_name },
        },
      }
    case 'TurnStartEvent':
      return { ...state, turn: event.color }
    case 'MoveRequestedEvent':
      return { ...state, thinking: true, invalidAttempt: null,
               reasoning: { ...state.reasoning, [event.color]: '' } }
    case 'ReasoningChunkEvent':
      return {
        ...state,
        reasoning: {
          ...state.reasoning,
          [event.color]: (state.reasoning[event.color] || '') + event.chunk,
        },
      }
    case 'InvalidMoveEvent':
      return {
        ...state, thinking: false,
        invalidAttempt: { color: event.color, move: event.attempted_move,
                          error: event.error, attempt: event.attempt_num },
      }
    case 'MoveAppliedEvent':
      return {
        ...state,
        fen: event.fen_after,
        lastMove: { from: event.move_uci.slice(0, 2), to: event.move_uci.slice(2, 4) },
        thinking: false,
        invalidAttempt: null,
        reasoning: { ...state.reasoning, [event.color]: event.reasoning },
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
      return { ...state, phase: 'over', thinking: false,
               result: { result: event.result, reason: event.reason,
                         winner: event.winner_name, pgn: event.pgn } }
    case 'error':
      return { ...state, error: event.message, thinking: false }
    default:
      return state
  }
}

export function useGameSocket(matchId) {
  const [state, dispatch] = useReducer(applyEvent, INITIAL_STATE)

  const url = useMemo(() => {
    if (!matchId) return null
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${location.host}/ws/tournament/game/${matchId}`
  }, [matchId])

  const connStatus = useReconnectingWebSocket(url, dispatch)

  return { ...state, connStatus }
}
