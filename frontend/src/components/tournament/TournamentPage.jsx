/**
 * TournamentPage — top-level tournament view.
 *
 * Layout:
 *   header  — round label + controls
 *   body    — 2/3 scrollable game grid  |  1/3 sidebar (standings + details)
 *
 * Clicking a GameCard drills into GameDetail (full board).
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTournamentSocket } from '../../hooks/useTournamentSocket.js'
import GameGrid from './GameGrid.jsx'
import GameDetail from './GameDetail.jsx'
import BracketPanel from './BracketPanel.jsx'

// ── Header ────────────────────────────────────────────────────────────────── //

function TournamentHeader({ status, currentRound, totalRounds, connStatus, onBracket }) {
  const navigate = useNavigate()

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
      <span className="tc-round-label">{roundLabel}</span>
      <div className="header-controls">
        <span className={`tc-status-label tc-status-${status}`}>{statusLabel}</span>
        {connStatus !== 'connected' && (
          <span className="tc-conn-badge tc-conn-badge--reconnecting">
            ↻ {connStatus === 'connecting' ? 'Connecting…' : 'Reconnecting…'}
          </span>
        )}
        <button className="btn" onClick={onBracket}>Bracket</button>
        <button className="btn btn-back" onClick={() => navigate('/tournament/setup')}>
          ← New Tournament
        </button>
        <button className="btn btn-back" onClick={() => navigate('/game')}>
          ← Game Setup
        </button>
      </div>
    </header>
  )
}

// ── Sidebar ───────────────────────────────────────────────────────────────── //

function exportPgn() {
  fetch('/api/tournament/pgn')
    .then(res => {
      if (!res.ok) throw new Error('No completed games yet.')
      return res.blob()
    })
    .then(blob => {
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'tournament.pgn'
      a.click()
      URL.revokeObjectURL(url)
    })
    .catch(err => alert(err.message))
}

function TournamentSidebar({ status, tournamentType, participantNames, currentRound, totalRounds, standings, matches, winner }) {
  // Derive per-participant match stats from the matches map
  const matchList = Object.values(matches)

  // Build a quick lookup: how many games each participant has played / won / lost
  const playerStats = {}
  for (const name of participantNames) {
    playerStats[name] = { played: 0, won: 0, lost: 0, drawn: 0 }
  }
  for (const m of matchList) {
    if (m.status !== 'complete' || !m.result) continue
    const w = m.whiteName, b = m.blackName
    if (!playerStats[w] || !playerStats[b]) continue
    playerStats[w].played++
    playerStats[b].played++
    if (m.result === '1-0') { playerStats[w].won++; playerStats[b].lost++ }
    else if (m.result === '0-1') { playerStats[b].won++; playerStats[w].lost++ }
    else { playerStats[w].drawn++; playerStats[b].drawn++ }
  }

  // Use standings if populated, otherwise fall back to participantNames
  const rows = standings.length > 0
    ? standings
    : participantNames.map(name => ({ name, wins: null, draws: null, losses: null, points: null }))

  const typeLabel = {
    knockout: 'Knockout',
    round_robin: 'Round Robin',
    swiss: 'Swiss',
    arena: 'Arena',
  }[tournamentType] || tournamentType || '—'

  return (
    <aside className="tc-sidebar">
      {/* Tournament details */}
      <section className="tc-sidebar-section">
        <h3 className="tc-sidebar-title">Tournament</h3>
        <div className="tc-info-grid">
          <span className="tc-info-label">Format</span>
          <span className="tc-info-value">{typeLabel}</span>
          <span className="tc-info-label">Players</span>
          <span className="tc-info-value">{participantNames.length}</span>
          <span className="tc-info-label">Round</span>
          <span className="tc-info-value">
            {currentRound ? `${currentRound}${totalRounds ? ` / ${totalRounds}` : ''}` : '—'}
          </span>
          <span className="tc-info-label">Status</span>
          <span className={`tc-info-value tc-status-${status}`}>
            {{ idle: 'Waiting', running: 'Live', complete: 'Complete', error: 'Error' }[status] || status}
          </span>
        </div>
      </section>

      {/* Champion banner */}
      {winner && (
        <section className="tc-sidebar-section">
          <div className="tc-champion-badge">
            <span className="tc-champion-crown">♛</span>
            <div>
              <div className="tc-champion-label">Champion</div>
              <div className="tc-champion-name">{winner}</div>
            </div>
          </div>
        </section>
      )}

      {/* Standings / participants */}
      <section className="tc-sidebar-section tc-sidebar-section--grow">
        <h3 className="tc-sidebar-title">
          {standings.length > 0 ? 'Standings' : 'Participants'}
        </h3>
        <div className="tc-standings">
          {rows.map((row, i) => {
            const stats = playerStats[row.name] || {}
            const isWinner = row.name === winner
            const isEliminated = status !== 'idle' && !isWinner && standings.length > 0
              && row.losses != null && row.losses > 0 && status === 'complete'
            return (
              <div
                key={row.name}
                className={`tc-standing-row${isWinner ? ' tc-standing-row--winner' : ''}`}
              >
                <span className="tc-standing-rank">{i + 1}</span>
                <span className="tc-standing-name" title={row.name}>
                  {isWinner && '♛ '}{row.name}
                </span>
                <span className="tc-standing-stats">
                  {stats.played > 0
                    ? `${stats.won}W ${stats.lost}L${stats.drawn > 0 ? ` ${stats.drawn}D` : ''}`
                    : standings.length > 0 && row.wins != null
                    ? `${row.wins}W ${row.losses}L${row.draws > 0 ? ` ${row.draws}D` : ''}`
                    : '—'
                  }
                </span>
              </div>
            )
          })}
        </div>
      </section>

      {/* Current round matches */}
      {matchList.length > 0 && (
        <section className="tc-sidebar-section">
          <div className="tc-sidebar-section-header">
            <h3 className="tc-sidebar-title">Matches</h3>
            {matchList.some(m => m.status === 'complete') && (
              <button className="tc-export-btn" onClick={exportPgn} title="Download all game PGNs">
                ↓ PGN
              </button>
            )}
          </div>
          <div className="tc-match-list">
            {matchList.map(m => (
              <div key={m.matchId} className="tc-match-entry">
                <span className={`tc-match-dot tc-match-dot--${m.status}`} />
                <span className="tc-match-players">
                  {m.whiteName} <span className="tc-match-vs">vs</span> {m.blackName === 'BYE' ? 'BYE' : m.blackName}
                </span>
                {m.status === 'complete' && (
                  m.gameOverReason === 'max_retries_exceeded'
                    ? <span className="tc-match-forfeit" title="Forfeited: max retries exceeded">forfeit</span>
                    : m.result && <span className="tc-match-result">{m.result === '1/2-1/2' ? '½–½' : m.result}</span>
                )}
              </div>
            ))}
          </div>
        </section>
      )}
    </aside>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────── //

export default function TournamentPage() {
  const tournamentState = useTournamentSocket()
  const {
    status, tournamentType, participantNames,
    currentRound, totalRounds, pairings, matches, standings, winner, error,
    connStatus,
  } = tournamentState

  const [selectedMatchId, setSelectedMatchId] = useState(null)
  const [bracketOpen, setBracketOpen] = useState(false)

  if (selectedMatchId) {
    return (
      <GameDetail
        matchId={selectedMatchId}
        matchInfo={matches[selectedMatchId]}
        onBack={() => setSelectedMatchId(null)}
      />
    )
  }

  return (
    <div className="tc-page">
      <TournamentHeader
        status={status}
        currentRound={currentRound}
        totalRounds={totalRounds}
        connStatus={connStatus}
        onBracket={() => setBracketOpen(true)}
      />

      {error && <div className="error-banner">{error}</div>}

      {status === 'idle' ? (
        <div className="tc-idle-message">
          <p>No tournament running.</p>
          <p className="dim">Start one from the Setup screen or via the API.</p>
        </div>
      ) : (
        <div className="tc-body">
          {/* 2/3 — scrollable game grid */}
          <div className="tc-main">
            <GameGrid
              matches={matches}
              pairings={pairings}
              onSelectMatch={setSelectedMatchId}
            />
          </div>

          {/* 1/3 — sidebar */}
          <TournamentSidebar
            status={status}
            tournamentType={tournamentType}
            participantNames={participantNames}
            currentRound={currentRound}
            totalRounds={totalRounds}
            standings={standings}
            matches={matches}
            winner={winner}
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
