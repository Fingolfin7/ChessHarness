/**
 * BracketPanel — collapsible bracket overlay.
 *
 * Shows the full tournament bracket with match results and who advanced.
 * Rendered as a slide-in panel from the right.
 */

export default function BracketPanel({ open, onClose, matches, standings, winner }) {
  if (!open) return null

  return (
    <div className="tc-bracket-overlay" onClick={onClose}>
      <div className="tc-bracket-panel" onClick={e => e.stopPropagation()}>
        <div className="tc-bracket-header">
          <span>Bracket</span>
          <button className="tc-bracket-close" onClick={onClose}>✕</button>
        </div>

        {winner && (
          <div className="tc-champion-badge">
            ★ Champion: {winner}
          </div>
        )}

        {standings && standings.length > 0 && (
          <div className="tc-standings">
            <h3>Standings</h3>
            <table className="tc-standings-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Name</th>
                  <th>W</th>
                  <th>D</th>
                  <th>L</th>
                  <th>Pts</th>
                </tr>
              </thead>
              <tbody>
                {standings.map((entry, i) => (
                  <tr key={entry.name} className={i === 0 && winner ? 'tc-row--champion' : ''}>
                    <td>{i + 1}</td>
                    <td>{entry.name}</td>
                    <td>{entry.wins}</td>
                    <td>{entry.draws}</td>
                    <td>{entry.losses}</td>
                    <td>{typeof entry.points === 'number' ? entry.points.toFixed(1) : entry.points}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="tc-match-list">
          <h3>Matches</h3>
          {Object.values(matches).map(match => (
            <div key={match.matchId} className={`tc-bracket-match tc-bracket-match--${match.status}`}>
              <span className="tc-bracket-match-id">{match.matchId}</span>
              <span className={match.advancingName === match.whiteName ? 'tc-bracket-winner' : ''}>
                {match.whiteName || '?'}
              </span>
              <span className="tc-bracket-vs">
                {match.result || (match.status === 'live' ? '●' : '—')}
              </span>
              <span className={match.advancingName === match.blackName ? 'tc-bracket-winner' : ''}>
                {match.blackName || '?'}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
