import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

const integer = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 })

function signed(value) {
  const rounded = Math.round(value)
  return `${rounded > 0 ? '+' : ''}${rounded}`
}

function formatDate(value) {
  if (!value) return 'Not updated yet'
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'UTC',
  }).format(new Date(value)) + ' UTC'
}

function RatingHistory({ competitorId, canBenchmark, onBenchmark }) {
  const [state, setState] = useState({ loading: true, error: '', history: [] })

  useEffect(() => {
    const controller = new AbortController()
    setState({ loading: true, error: '', history: [] })
    fetch(`/api/ratings/${encodeURIComponent(competitorId)}/history`, {
      signal: controller.signal,
    })
      .then(async response => {
        if (!response.ok) throw new Error('Could not load rating history.')
        return response.json()
      })
      .then(data => setState({ loading: false, error: '', history: data.history || [] }))
      .catch(error => {
        if (error.name !== 'AbortError') {
          setState({ loading: false, error: error.message, history: [] })
        }
      })
    return () => controller.abort()
  }, [competitorId])

  return (
    <div className="rating-history">
      <div className="rating-history-header">
        <div>
          <strong>Rating periods</strong>
          <span>Updates are applied once each game, match, or round closes.</span>
        </div>
        {canBenchmark && (
          <button type="button" className="rating-benchmark-btn" onClick={onBenchmark}>
            Benchmark vs Stockfish
          </button>
        )}
      </div>
      {state.loading && <p className="ratings-muted">Loading history…</p>}
      {state.error && <p className="ratings-error">{state.error}</p>}
      {!state.loading && !state.error && state.history.length === 0 && (
        <p className="ratings-muted">No completed rating periods yet.</p>
      )}
      {state.history.length > 0 && (
        <ol className="rating-history-list">
          {state.history.map(period => (
            <li key={period.batch_id}>
              <time dateTime={period.finalized_at || undefined}>{formatDate(period.finalized_at)}</time>
              <span>{integer.format(period.rating_before)} → {integer.format(period.rating_after)}</span>
              <strong className={period.rating_change >= 0 ? 'rating-delta-up' : 'rating-delta-down'}>
                {signed(period.rating_change)}
              </strong>
              <small>RD {integer.format(period.rd_before)} → {integer.format(period.rd_after)}</small>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

function RatingRow({ row, rank, expanded, onToggle, anchorAvailable, onBenchmark }) {
  const status = row.is_anchor ? 'Fixed anchor' : row.is_provisional ? 'Provisional' : 'Established'
  const conservative = row.rating - (2 * row.rd)

  return (
    <>
      <tr
        className={`rating-row${row.is_anchor ? ' rating-row--anchor' : ''}`}
        tabIndex={0}
        role="button"
        aria-expanded={expanded}
        onClick={onToggle}
        onKeyDown={event => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            onToggle()
          }
        }}
      >
        <td className="rating-rank" data-label="Rank">{row.is_anchor ? '—' : rank}</td>
        <td className="rating-identity" data-label="Competitor">
          <strong>{row.name}</strong>
          <span>{row.kind === 'engine' ? 'Engine benchmark' : row.competitor_id}</span>
        </td>
        <td className="rating-number" data-label="Rating">
          <strong>{integer.format(row.rating)}</strong>
          {!row.is_anchor && <span title="Conservative score: rating minus two rating deviations">floor {integer.format(conservative)}</span>}
        </td>
        <td className="rating-number" data-label="RD">{integer.format(row.rd)}</td>
        <td className="rating-record" data-label="W-D-L">
          {row.wins}<span>–</span>{row.draws}<span>–</span>{row.losses}
        </td>
        <td className="rating-number" data-label="Games">{row.games}</td>
        <td data-label="State">
          <span className={`rating-state rating-state--${row.is_anchor ? 'anchor' : row.is_provisional ? 'provisional' : 'established'}`}>
            {status}
          </span>
          {row.is_anchor && !row.available && <span className="rating-state-note">Unavailable</span>}
        </td>
        <td className="rating-expand" aria-hidden="true">{expanded ? '▾' : '›'}</td>
      </tr>
      {expanded && (
        <tr className="rating-history-row">
          <td colSpan="8">
            <RatingHistory
              competitorId={row.competitor_id}
              canBenchmark={!row.is_anchor && anchorAvailable}
              onBenchmark={() => onBenchmark(row.competitor_id)}
            />
          </td>
        </tr>
      )}
    </>
  )
}

export default function RatingsPage() {
  const navigate = useNavigate()
  const [payload, setPayload] = useState(null)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(null)

  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/ratings', { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error('Could not load ratings.')
        return response.json()
      })
      .then(setPayload)
      .catch(reason => {
        if (reason.name !== 'AbortError') setError(reason.message)
      })
    return () => controller.abort()
  }, [])

  const rows = useMemo(() => {
    const values = payload?.ratings || []
    return [...values].sort((a, b) => {
      if (a.is_anchor !== b.is_anchor) return a.is_anchor ? 1 : -1
      return (b.rating - 2 * b.rd) - (a.rating - 2 * a.rd)
    })
  }, [payload])

  const anchor = rows.find(row => row.is_anchor)
  const lastUpdated = rows.reduce((latest, row) => {
    if (!row.updated_at) return latest
    return !latest || row.updated_at > latest ? row.updated_at : latest
  }, null)
  let modelRank = 0

  return (
    <main className="ratings-page">
      <header className="ratings-header">
        <div>
          <h1>Ratings</h1>
          <p>
            Glicko-2 · {payload?.pool_id || 'standard-v1'} · models ranked by rating − 2×RD
          </p>
        </div>
        {lastUpdated && <time dateTime={lastUpdated}>Updated {formatDate(lastUpdated)}</time>}
      </header>

      {error && <div className="ratings-notice ratings-notice--error" role="alert">{error}</div>}
      {payload && !payload.enabled && (
        <div className="ratings-notice" role="status">Ratings are disabled in config.yaml.</div>
      )}
      {payload?.enabled && anchor && !anchor.available && (
        <div className="ratings-notice ratings-notice--warning" role="status">
          <strong>{anchor.name} is unavailable.</strong>{' '}
          Ratings remain valid within this pool, but models cannot be benchmarked against the configured anchor until its executable is installed.
        </div>
      )}

      {!payload && !error && <div className="ratings-empty">Loading ratings…</div>}
      {payload?.enabled && rows.length === 0 && (
        <div className="ratings-empty">
          <strong>No rated games yet.</strong>
          <span>Run a model-vs-model game or configure Stockfish to seed the pool.</span>
        </div>
      )}

      {rows.length > 0 && (
        <div className="ratings-table-region" role="region" aria-label="Glicko-2 leaderboard" tabIndex={0}>
          <table className="ratings-table">
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Competitor</th>
                <th scope="col" title="Current Glicko-2 rating">Rating</th>
                <th scope="col" title="Rating deviation: lower means more certain">RD</th>
                <th scope="col">W–D–L</th>
                <th scope="col">Games</th>
                <th scope="col">State</th>
                <th scope="col"><span className="sr-only">Details</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => {
                if (!row.is_anchor) modelRank += 1
                return (
                  <RatingRow
                    key={row.competitor_id}
                    row={row}
                    rank={row.is_anchor ? null : modelRank}
                    expanded={expanded === row.competitor_id}
                    onToggle={() => setExpanded(current => current === row.competitor_id ? null : row.competitor_id)}
                    anchorAvailable={Boolean(anchor?.available)}
                    onBenchmark={competitorId => navigate(`/tournament/setup?benchmark=${encodeURIComponent(competitorId)}`)}
                  />
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <footer className="ratings-footnote">
        Rating deviation (RD) measures uncertainty; lower is more certain. Ratings are comparable only inside this ruleset pool. Humans and non-standard games are recorded but never rated.
      </footer>
    </main>
  )
}
