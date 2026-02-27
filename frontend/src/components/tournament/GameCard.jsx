/**
 * GameCard — a mini board tile shown in the tournament overview grid.
 *
 * Shows:
 *   - Mini chessboard (live FEN via react-chessboard)
 *   - Player names (white on bottom, black on top, Lichess-style)
 *   - Status chip: LIVE (pulsing) | DONE (with result) | BYE | PENDING
 *   - Advancing name badge when match is complete
 *
 * Clicking the card navigates to the GameDetail view.
 */

import { useEffect, useRef, useState } from 'react'
import { Chessboard } from 'react-chessboard'

function StatusChip({ status, result, advancingName }) {
  if (status === 'live') {
    return <span className="tc-chip tc-chip--live">● LIVE</span>
  }
  if (status === 'complete') {
    const label = result === '1/2-1/2' ? '½–½' : result || 'DONE'
    return <span className="tc-chip tc-chip--done">{label}</span>
  }
  return <span className="tc-chip tc-chip--pending">PENDING</span>
}

export default function GameCard({ match, matchId, onClick }) {
  const boardWrapRef = useRef(null)
  const [boardWidth, setBoardWidth] = useState(200)

  useEffect(() => {
    const el = boardWrapRef.current
    if (!el) return
    const observer = new ResizeObserver(entries => {
      const w = Math.floor(entries[0].contentRect.width)
      if (w > 0) setBoardWidth(w)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  if (!match) return null

  const { whiteName, blackName, status, result, advancingName, fen, lastMove } = match

  const isBye = blackName === 'BYE'

  const customArrows = lastMove
    ? [[lastMove.from, lastMove.to, 'rgba(255,170,0,0.6)']]
    : []

  return (
    <div
      className={`tc-card ${status === 'live' ? 'tc-card--live' : ''} ${status === 'complete' ? 'tc-card--done' : ''}`}
      onClick={() => !isBye && onClick(matchId)}
      role={isBye ? undefined : 'button'}
      tabIndex={isBye ? undefined : 0}
      onKeyDown={e => (e.key === 'Enter' || e.key === ' ') && !isBye && onClick(matchId)}
      aria-label={`Match ${matchId}: ${whiteName} vs ${blackName}`}
    >
      {/* Match ID badge */}
      <div className="tc-card-header">
        <span className="tc-match-id">{matchId}</span>
        <StatusChip status={status} result={result} advancingName={advancingName} />
      </div>

      {/* Black player (top) */}
      <div className={`tc-player tc-player--black ${status !== 'complete' && match.turn === 'black' && status === 'live' ? 'tc-player--active' : ''}`}>
        <span className="tc-player-symbol">♚</span>
        <span className="tc-player-name">{isBye ? '—' : (blackName || '?')}</span>
        {status === 'complete' && advancingName === blackName && (
          <span className="tc-advancing">▲</span>
        )}
      </div>

      {/* Mini board */}
      <div className="tc-board-wrap" ref={boardWrapRef}>
        {isBye ? (
          <div className="tc-bye-label" style={{ width: boardWidth, height: boardWidth }}>BYE</div>
        ) : (
          <Chessboard
            id={`card-${matchId}`}
            position={fen || 'start'}
            arePiecesDraggable={false}
            customArrows={customArrows}
            boardWidth={boardWidth}
            animationDuration={150}
            customBoardStyle={{ borderRadius: '4px' }}
          />
        )}
      </div>

      {/* White player (bottom) */}
      <div className={`tc-player tc-player--white ${status !== 'complete' && match.turn === 'white' && status === 'live' ? 'tc-player--active' : ''}`}>
        <span className="tc-player-symbol">♔</span>
        <span className="tc-player-name">{whiteName || '?'}</span>
        {status === 'complete' && advancingName === whiteName && (
          <span className="tc-advancing">▲</span>
        )}
      </div>
    </div>
  )
}
