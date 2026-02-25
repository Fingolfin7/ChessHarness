import { useEffect, useRef } from 'react'

export default function MoveHistory({ moves }) {
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [moves.length])

  return (
    <div className="move-history">
      <h3 className="panel-title">Move History</h3>
      <div className="moves-list">
        {moves.length === 0 && (
          <p className="moves-empty">Game starting…</p>
        )}
        {moves.map(m => (
          <div key={m.number} className="move-row">
            <span className="move-number">{m.number}.</span>
            <span className="move-san">{m.white?.san ?? '…'}</span>
            <span className="move-san">{m.black?.san ?? ''}</span>
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </div>
  )
}
