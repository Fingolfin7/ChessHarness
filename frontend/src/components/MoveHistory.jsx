import { useEffect, useRef } from 'react'

function plyIndex(plies, moveNumber, color) {
  return plies.findIndex(p => p.moveNumber === moveNumber && p.color === color)
}

function buildPGN(moves) {
  return moves.map(m => {
    let s = `${m.number}.`
    if (m.white?.san) s += ` ${m.white.san}`
    if (m.black?.san) s += ` ${m.black.san}`
    return s
  }).join(' ')
}

function downloadPGN(pgn, moves) {
  const text = pgn || buildPGN(moves)
  const blob = new Blob([text], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'chess-game.pgn'
  a.click()
  URL.revokeObjectURL(url)
}

export default function MoveHistory({ moves, plies, viewIndex, onNavigate, pgn,
  goToStart, goBack, goForward, goToLive, isLive, navLabel }) {
  const endRef = useRef(null)

  // Auto-scroll to bottom only when live (viewIndex === null)
  useEffect(() => {
    if (viewIndex === null) endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [moves.length, viewIndex])

  return (
    <div className="move-history">
      <div className="move-history-header">
        <div className="move-history-title-row">
          <h3 className="panel-title">Move History</h3>
          {moves.length > 0 && (
            <button
              className="export-pgn-btn"
              onClick={() => downloadPGN(pgn, moves)}
              title="Download PGN file"
            >
              Export PGN
            </button>
          )}
        </div>
        <div className="nav-controls">
          <button className="nav-btn" onClick={goToStart} disabled={plies.length === 0 || viewIndex === 0} title="First move">⏮</button>
          <button className="nav-btn" onClick={goBack}    disabled={plies.length === 0 || viewIndex === 0} title="Previous (←)">◀</button>
          <span className={`nav-label ${isLive ? 'live' : ''}`}>{navLabel}</span>
          <button className="nav-btn" onClick={goForward} disabled={isLive} title="Next (→)">▶</button>
          <button className="nav-btn" onClick={goToLive}  disabled={isLive} title="Latest">⏭</button>
        </div>
      </div>
      <div className="moves-list">
        {moves.length === 0 && <p className="moves-empty">Game starting…</p>}
        {moves.map(m => {
          const wIdx = plyIndex(plies, m.number, 'white')
          const bIdx = plyIndex(plies, m.number, 'black')
          return (
            <div key={m.number} className="move-row">
              <span className="move-number">{m.number}.</span>
              <span
                className={`move-san ${wIdx !== -1 && viewIndex === wIdx ? 'current' : ''}`}
                onClick={() => wIdx !== -1 && onNavigate(wIdx)}
                title={m.white?.reasoning || ''}
              >
                {m.white?.san ?? '…'}
              </span>
              <span
                className={`move-san ${bIdx !== -1 && viewIndex === bIdx ? 'current' : ''}`}
                onClick={() => bIdx !== -1 && onNavigate(bIdx)}
                title={m.black?.reasoning || ''}
              >
                {m.black?.san ?? ''}
              </span>
            </div>
          )
        })}
        <div ref={endRef} />
      </div>
    </div>
  )
}
