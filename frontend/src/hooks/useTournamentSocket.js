/**
 * useTournamentSocket
 *
 * Connects to /ws/tournament and maintains a full snapshot of tournament state.
 * Automatically reconnects on drop; the server replays the full event log on
 * each new connection so state is always reconstructed correctly.
 *
 * Returns all tournament state fields plus `connStatus`:
 *   'connecting' | 'connected' | 'reconnecting'
 */

import { useReducer } from 'react'
import { useReconnectingWebSocket } from './useReconnectingWebSocket.js'

const INITIAL_STATE = {
  status: 'idle',
  tournamentType: null,
  participantNames: [],
  totalRounds: 0,
  currentRound: 0,
  pairings: [],
  matches: {},
  standings: [],
  winner: null,
  error: null,
}

const INITIAL_MATCH = {
  matchId: null,
  whiteName: null,
  blackName: null,
  roundNum: null,
  gameNum: 1,
  status: 'pending',
  result: null,
  gameOverReason: null,
  advancingName: null,
  fen: 'start',
  lastMove: null,
  turn: 'white',
  thinking: false,
  plies: [],
  moves: [],
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

function applyMatchGameEvent(match, gameEvent) {
  switch (gameEvent.type) {
    case 'GameStartEvent':
      return {
        ...match,
        status: 'live',
        whiteName: gameEvent.white_name,
        blackName: gameEvent.black_name,
        fen: gameEvent.starting_fen || 'start',
        lastMove: null,
        plies: [],
        moves: [],
        thinking: false,
      }
    case 'TurnStartEvent':
      return { ...match, turn: gameEvent.color }
    case 'MoveRequestedEvent':
      return { ...match, thinking: true }
    case 'MoveAppliedEvent':
      return {
        ...match,
        fen: gameEvent.fen_after,
        lastMove: { from: gameEvent.move_uci.slice(0, 2), to: gameEvent.move_uci.slice(2, 4) },
        thinking: false,
        moves: updateMoves(match.moves, gameEvent),
        plies: [...match.plies, {
          color: gameEvent.color,
          san: gameEvent.move_san,
          reasoning: gameEvent.reasoning,
          fen_after: gameEvent.fen_after,
          from: gameEvent.move_uci.slice(0, 2),
          to: gameEvent.move_uci.slice(2, 4),
          moveNumber: gameEvent.move_number,
        }],
      }
    case 'InvalidMoveEvent':
      return { ...match, thinking: false }
    case 'GameOverEvent':
      return { ...match, thinking: false, result: gameEvent.result, gameOverReason: gameEvent.reason }
    default:
      return match
  }
}

function reducer(state, action) {
  switch (action.type) {
    case 'TournamentStartEvent':
      return {
        ...INITIAL_STATE,
        status: 'running',
        tournamentType: action.tournament_type,
        participantNames: action.participant_names,
        totalRounds: action.total_rounds,
      }

    case 'RoundStartEvent': {
      const pairings = action.pairings.map(([matchId, whiteName, blackName]) => ({
        matchId, whiteName, blackName,
      }))
      const newMatches = { ...state.matches }
      for (const { matchId, whiteName, blackName } of pairings) {
        newMatches[matchId] = {
          ...INITIAL_MATCH,
          matchId,
          whiteName,
          blackName,
          roundNum: action.round_num,
        }
      }
      return { ...state, currentRound: action.round_num, pairings, matches: newMatches }
    }

    case 'MatchStartEvent': {
      const prev = state.matches[action.match_id] || INITIAL_MATCH
      return {
        ...state,
        matches: {
          ...state.matches,
          [action.match_id]: {
            ...prev,
            matchId: action.match_id,
            whiteName: action.white_name,
            blackName: action.black_name,
            roundNum: action.round_num,
            gameNum: action.game_num,
            status: 'live',
          },
        },
      }
    }

    case 'MatchGameEvent': {
      const prev = state.matches[action.match_id]
      if (!prev) return state
      return {
        ...state,
        matches: {
          ...state.matches,
          [action.match_id]: applyMatchGameEvent(prev, action.game_event),
        },
      }
    }

    case 'MatchCompleteEvent': {
      const prev = state.matches[action.match_id] || INITIAL_MATCH
      return {
        ...state,
        matches: {
          ...state.matches,
          [action.match_id]: {
            ...prev,
            status: 'complete',
            result: action.result?.game_result,
            gameOverReason: prev.gameOverReason,   // preserve reason captured from GameOverEvent
            advancingName: action.advancing_name,
          },
        },
      }
    }

    case 'RoundCompleteEvent':
      return {
        ...state,
        standings: (action.standings || []).map(e => ({
          name: e.participant?.model?.name || e.participant?.display_name || '',
          wins: e.wins,
          draws: e.draws,
          losses: e.losses,
          points: e.points,
        })),
      }

    case 'TournamentCompleteEvent':
      return {
        ...state,
        status: 'complete',
        winner: action.winner_name,
        standings: (action.final_standings || []).map(e => ({
          name: e.participant?.model?.name || e.participant?.display_name || '',
          wins: e.wins,
          draws: e.draws,
          losses: e.losses,
          points: e.points,
        })),
      }

    case 'error':
      return { ...state, status: 'error', error: action.message }

    default:
      return state
  }
}

const WS_URL = (() => {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${location.host}/ws/tournament`
})()

export function useTournamentSocket() {
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE)
  const connStatus = useReconnectingWebSocket(WS_URL, dispatch)
  return { ...state, connStatus }
}
