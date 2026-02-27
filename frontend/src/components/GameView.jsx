import { useState, useEffect } from 'react'
import Board from './Board.jsx'
import PlayerPanel from './PlayerPanel.jsx'
import MoveHistory from './MoveHistory.jsx'
import ReasoningPanel from './ReasoningPanel.jsx'

function resultText(result) {
  if (!result) return null
  if (result.reason === 'interrupted') return 'Game stopped by user'
  if (result.winner) return `${result.winner} wins by ${result.reason.replace(/_/g, ' ')}`
  return `Draw — ${result.reason.replace(/_/g, ' ')}`
}

export default function GameView({ state, onStop, onNewGame }) {
  const { players, fen, lastMove, turn, thinking, reasoning, moves,
          plies, invalidAttempt, result, error, phase } = state
  const isOver = phase === 'over'

  // null = live; 0..plies.length-1 = reviewing that ply
  const [viewIndex, setViewIndex] = useState(null)

  // Snap back to live when a new move arrives during live view
  useEffect(() => {
    if (viewIndex === null) return   // already live, nothing to do
    // stay in review mode when browsing; only auto-follow if at the last ply
    if (viewIndex === plies.length - 2) setViewIndex(null)
  }, [plies.length]) // eslint-disable-line react-hooks/exhaustive-deps

  // Keyboard navigation (← →)
  useEffect(() => {
    const handleKey = (e) => {
      if (e.key === 'ArrowLeft') {
        setViewIndex(v => {
          if (v === null) return plies.length > 0 ? plies.length - 1 : null
          return Math.max(0, v - 1)
        })
      }
      if (e.key === 'ArrowRight') {
        setViewIndex(v => {
          if (v === null) return null
          return v >= plies.length - 1 ? null : v + 1
        })
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [plies.length])

  // ── Compute what to display ─────────────────────────────────────────── //
  const isLive = viewIndex === null
  const currentPly = isLive ? null : plies[viewIndex]

  const displayFen      = currentPly ? currentPly.fen_after : fen
  const displayLastMove = currentPly ? { from: currentPly.from, to: currentPly.to } : lastMove

  // For reasoning panels in review mode: walk backwards to find each colour's latest reasoning
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

  // ── Navigation helpers ──────────────────────────────────────────────── //
  const goToStart   = () => setViewIndex(0)
  const goBack      = () => setViewIndex(v => v === null ? plies.length - 1 : Math.max(0, v - 1))
  const goForward   = () => setViewIndex(v => v === null ? null : (v >= plies.length - 1 ? null : v + 1))
  const goToLive    = () => setViewIndex(null)
  const goToPly     = (idx) => setViewIndex(idx)

  const navLabel = isLive
    ? '● Live'
    : `Move ${currentPly.moveNumber}${currentPly.color === 'white' ? 'w' : 'b'} · ${currentPly.san}`

  const lastWhiteSan = [...moves].reverse().find(m => m.white)?.white?.san
  const lastBlackSan = [...moves].reverse().find(m => m.black)?.black?.san

  return (
    <div className="game-layout">

      {/* ── Header ── */}
      <header className="game-header">
        <span className="logo">♔ ChessHarness</span>
        <div className="header-controls">
          {!isOver && <button className="btn btn-stop" onClick={onStop}>Stop Game</button>}
          {isOver  && <button className="btn btn-new"  onClick={onNewGame}>New Game</button>}
        </div>
      </header>

      {result && (
        <div className={`result-banner ${result.result === '1/2-1/2' ? 'draw' : ''}`}>
          {resultText(result)}
        </div>
      )}
      {error && <div className="error-banner">{error}</div>}

      {/* ── Main ── */}
      <div className="game-main">

        {/* Board + nav controls */}
        <div className="board-col">
          <Board fen={displayFen} lastMove={displayLastMove} />

          <div className="nav-controls">
            <button className="nav-btn" onClick={goToStart}  disabled={plies.length === 0 || viewIndex === 0} title="First move">⏮</button>
            <button className="nav-btn" onClick={goBack}     disabled={plies.length === 0 || viewIndex === 0} title="Previous (←)">◀</button>
            <span className={`nav-label ${isLive ? 'live' : ''}`}>{navLabel}</span>
            <button className="nav-btn" onClick={goForward}  disabled={isLive} title="Next (→)">▶</button>
            <button className="nav-btn" onClick={goToLive}   disabled={isLive} title="Latest">⏭</button>
          </div>
        </div>

        {/* Sidebar — Black on top, history in middle, White on bottom (Lichess-style) */}
        <div className="sidebar">
          <PlayerPanel
            color="black"
            name={players.black?.name}
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
          />

          <PlayerPanel
            color="white"
            name={players.white?.name}
            isActive={turn === 'white' && !isOver}
            isThinking={isThinkingWhite}
            invalidAttempt={invalidAttempt?.color === 'white' ? invalidAttempt : null}
            lastMoveSan={lastWhiteSan}
          />
        </div>
      </div>

      {/* ── Reasoning ── */}
      <div className="reasoning-row">
        <ReasoningPanel
          color="white"
          name={players.white?.name}
          reasoning={displayReasoning.white}
          isThinking={isThinkingWhite}
          isReviewing={!isLive && currentPly?.color === 'white'}
        />
        <ReasoningPanel
          color="black"
          name={players.black?.name}
          reasoning={displayReasoning.black}
          isThinking={isThinkingBlack}
          isReviewing={!isLive && currentPly?.color === 'black'}
        />
      </div>

    </div>
  )
}
