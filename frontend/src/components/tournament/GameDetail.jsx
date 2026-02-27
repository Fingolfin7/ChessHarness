/**
 * GameDetail — full-screen view for a single tournament match.
 *
 * Subscribes to /ws/tournament/game/{matchId} via useGameSocket and renders
 * the same Board + MoveHistory + PlayerPanel layout as the main GameView,
 * reusing those existing components directly.
 */

import { useState, useEffect } from 'react'
import Board from '../Board.jsx'
import PlayerPanel from '../PlayerPanel.jsx'
import MoveHistory from '../MoveHistory.jsx'
import ReasoningPanel from '../ReasoningPanel.jsx'
import { useGameSocket } from '../../hooks/useGameSocket.js'

function resultText(result) {
  if (!result) return null
  if (result.reason === 'interrupted') return 'Game stopped'
  if (result.winner) return `${result.winner} wins by ${result.reason.replace(/_/g, ' ')}`
  return `Draw — ${result.reason.replace(/_/g, ' ')}`
}

export default function GameDetail({ matchId, matchInfo, onBack }) {
  const state = useGameSocket(matchId)
  const { players, fen, lastMove, turn, thinking, reasoning,
          moves, plies, invalidAttempt, result, error, phase, connStatus } = state

  const isOver = phase === 'over'
  const [viewIndex, setViewIndex] = useState(null)
  const [flipped, setFlipped] = useState(false)

  // Auto-follow live unless user is reviewing
  useEffect(() => {
    if (viewIndex === null) return
    if (viewIndex === plies.length - 2) setViewIndex(null)
  }, [plies.length]) // eslint-disable-line

  // Keyboard navigation
  useEffect(() => {
    const handleKey = (e) => {
      if (e.key === 'ArrowLeft') {
        setViewIndex(v => v === null ? (plies.length > 0 ? plies.length - 1 : null) : Math.max(0, v - 1))
      }
      if (e.key === 'ArrowRight') {
        setViewIndex(v => v === null ? null : (v >= plies.length - 1 ? null : v + 1))
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [plies.length])

  const isLive = viewIndex === null
  const currentPly = isLive ? null : plies[viewIndex]
  const displayFen = currentPly ? currentPly.fen_after : fen
  const displayLastMove = currentPly ? { from: currentPly.from, to: currentPly.to } : lastMove

  const displayReasoning = (() => {
    if (isLive) return reasoning
    const out = { white: '', black: '' }
    for (let i = viewIndex; i >= 0; i--) {
      const p = plies[i]
      if (!out[p.color]) out[p.color] = p.reasoning
      if (out.white && out.black) break
    }
    return out
  })()

  const isThinkingWhite = isLive && turn === 'white' && thinking
  const isThinkingBlack = isLive && turn === 'black' && thinking

  const goToStart = () => setViewIndex(0)
  const goBack    = () => setViewIndex(v => v === null ? plies.length - 1 : Math.max(0, v - 1))
  const goForward = () => setViewIndex(v => v === null ? null : (v >= plies.length - 1 ? null : v + 1))
  const goToLive  = () => setViewIndex(null)
  const goToPly   = (idx) => setViewIndex(idx)

  const navLabel = isLive
    ? '● Live'
    : `Move ${currentPly.moveNumber}${currentPly.color === 'white' ? 'w' : 'b'} · ${currentPly.san}`

  const lastWhiteSan = [...moves].reverse().find(m => m.white)?.white?.san
  const lastBlackSan = [...moves].reverse().find(m => m.black)?.black?.san

  // Player names: prefer socket state (once game starts) else matchInfo
  const whiteName = players.white?.name || matchInfo?.whiteName || '?'
  const blackName = players.black?.name || matchInfo?.blackName || '?'

  return (
    <div className="game-layout">
      <header className="game-header">
        <button className="btn btn-back" onClick={onBack}>← Back</button>
        <span className="tc-detail-match-id">Match {matchId}</span>
        <div className="header-controls">
          {connStatus !== 'connected' && (
            <span className="tc-conn-badge tc-conn-badge--reconnecting">
              ↻ {connStatus === 'connecting' ? 'Connecting…' : 'Reconnecting…'}
            </span>
          )}
          <button className="btn" onClick={() => setFlipped(f => !f)} title="Flip board">⇅ Flip</button>
        </div>
      </header>

      {result && (
        <div className={`result-banner ${result.result === '1/2-1/2' ? 'draw' : ''}`}>
          {resultText(result)}
        </div>
      )}
      {error && <div className="error-banner">{error}</div>}

      <div className="game-main">
        <div className="board-col">
          <Board fen={displayFen} lastMove={displayLastMove} flipped={flipped} />
        </div>

        <div className="sidebar">
          <PlayerPanel
            color="black"
            name={blackName}
            isActive={turn === 'black' && !isOver}
            isThinking={isThinkingBlack}
            invalidAttempt={invalidAttempt?.color === 'black' ? invalidAttempt : null}
            lastMoveSan={lastBlackSan}
          />
          <MoveHistory
            moves={moves}
            plies={plies}
            viewIndex={viewIndex}
            onNavigate={goToPly}
            pgn={result?.pgn}
            goToStart={goToStart}
            goBack={goBack}
            goForward={goForward}
            goToLive={goToLive}
            isLive={isLive}
            navLabel={navLabel}
          />
          <PlayerPanel
            color="white"
            name={whiteName}
            isActive={turn === 'white' && !isOver}
            isThinking={isThinkingWhite}
            invalidAttempt={invalidAttempt?.color === 'white' ? invalidAttempt : null}
            lastMoveSan={lastWhiteSan}
          />
        </div>
      </div>

      <div className="reasoning-row">
        <ReasoningPanel
          color="white"
          name={whiteName}
          reasoning={displayReasoning.white}
          isThinking={isThinkingWhite}
          isReviewing={!isLive && currentPly?.color === 'white'}
        />
        <ReasoningPanel
          color="black"
          name={blackName}
          reasoning={displayReasoning.black}
          isThinking={isThinkingBlack}
          isReviewing={!isLive && currentPly?.color === 'black'}
        />
      </div>
    </div>
  )
}
