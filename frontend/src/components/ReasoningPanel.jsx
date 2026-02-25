import { useEffect, useRef } from 'react'

export default function ReasoningPanel({ color, name, reasoning, isThinking }) {
  const scrollRef = useRef(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [reasoning])

  return (
    <div className={`reasoning-panel ${color}`}>
      <div className="reasoning-header">
        <span className="reasoning-title">
          {color === 'white' ? '♔' : '♚'} {name ?? '—'}
        </span>
        {isThinking && <span className="thinking-badge">thinking…</span>}
      </div>
      <div className="reasoning-body" ref={scrollRef}>
        {reasoning
          ? <p>{reasoning}</p>
          : <p className="reasoning-placeholder">Awaiting first move…</p>
        }
      </div>
    </div>
  )
}
