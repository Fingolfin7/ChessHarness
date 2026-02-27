/**
 * GameGrid — the multi-board overview for the current tournament round.
 *
 * Renders a responsive grid of GameCard tiles for all matches in the
 * current round.  Completed rounds can be toggled to show past results.
 */

import GameCard from './GameCard.jsx'

export default function GameGrid({ matches, pairings, onSelectMatch }) {
  if (!pairings || pairings.length === 0) {
    return (
      <div className="tc-grid-empty">
        Waiting for round to start…
      </div>
    )
  }

  return (
    <div className="tc-grid">
      {pairings.map(({ matchId }) => (
        <GameCard
          key={matchId}
          matchId={matchId}
          match={matches[matchId]}
          onClick={onSelectMatch}
        />
      ))}
    </div>
  )
}
