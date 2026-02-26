import { useEffect, useRef } from 'react'

function plyIndex(plies, moveNumber, color) {
  return plies.findIndex(p => p.moveNumber === moveNumber && p.color === color)
}

export default function MoveHistory({ moves, plies, viewIndex, onNavigate }) {
  const endRef = useRef(null)

  // Auto-scroll to bottom only when live (viewIndex === null)
  useEffect(() => {
    if (viewIndex === null) endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [moves.length, viewIndex])

  return (
    <div className="move-history">
      <h3 className="panel-title">Move History</h3>
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
