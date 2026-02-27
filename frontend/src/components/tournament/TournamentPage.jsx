/**
 * TournamentPage — top-level tournament view.
 *
 * Two sub-views, toggled by selectedMatchId:
 *   null  → GameGrid overview (all current-round boards visible)
 *   str   → GameDetail for the selected match
 *
 * A BracketPanel overlay can be opened from the overview at any time.
 */

import { useState } from 'react'
import { useTournamentSocket } from '../../hooks/useTournamentSocket.js'
import GameGrid from './GameGrid.jsx'
import GameDetail from './GameDetail.jsx'
import BracketPanel from './BracketPanel.jsx'

function TournamentHeader({ status, currentRound, totalRounds, winner, onBracket, onNewTournament }) {
  const roundLabel = totalRounds
    ? `Round ${currentRound} / ${totalRounds}`
    : currentRound ? `Round ${currentRound}` : ''

  const statusLabel = {
    idle: 'Waiting…',
    running: '● Live',
    complete: '✓ Complete',
    error: '✕ Error',
  }[status] || ''

  return (
    <header className="game-header">
      <span className="logo">♔ ChessHarness Tournament</span>
      <span className="tc-round-label">{roundLabel}</span>
      <div className="header-controls">
        <span className={`tc-status-label tc-status-${status}`}>{statusLabel}</span>
        <button className="btn" onClick={onBracket}>Bracket</button>
        {status === 'complete' && (
          <button className="btn btn-new" onClick={onNewTournament}>New Tournament</button>
        )}
      </div>
    </header>
  )
}

export default function TournamentPage({ onNewTournament }) {
  const tournamentState = useTournamentSocket()
  const { status, currentRound, totalRounds, pairings, matches, standings, winner, error } = tournamentState

  const [selectedMatchId, setSelectedMatchId] = useState(null)
  const [bracketOpen, setBracketOpen] = useState(false)

  const handleSelectMatch = (matchId) => setSelectedMatchId(matchId)
  const handleBack = () => setSelectedMatchId(null)

  if (selectedMatchId) {
    return (
      <GameDetail
        matchId={selectedMatchId}
        matchInfo={matches[selectedMatchId]}
        onBack={handleBack}
      />
    )
  }

  return (
    <div className="tc-page">
      <TournamentHeader
        status={status}
        currentRound={currentRound}
        totalRounds={totalRounds}
        winner={winner}
        onBracket={() => setBracketOpen(true)}
        onNewTournament={onNewTournament}
      />

      {winner && (
        <div className="result-banner">
          ★ Champion: {winner}
        </div>
      )}
      {error && <div className="error-banner">{error}</div>}

      {status === 'idle' && (
        <div className="tc-idle-message">
          <p>No tournament running.</p>
          <p className="dim">Start one from the Setup screen or via the API.</p>
        </div>
      )}

      {status !== 'idle' && (
        <div className="tc-main">
          <GameGrid
            matches={matches}
            pairings={pairings}
            onSelectMatch={handleSelectMatch}
          />
        </div>
      )}

      <BracketPanel
        open={bracketOpen}
        onClose={() => setBracketOpen(false)}
        matches={matches}
        standings={standings}
        winner={winner}
      />
    </div>
  )
}
