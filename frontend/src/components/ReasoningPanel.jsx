import { useEffect, useRef } from 'react'

export default function ReasoningPanel({ color, name, reasoning, isThinking, isReviewing }) {
  const scrollRef = useRef(null)

  useEffect(() => {
    if (!isReviewing && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [reasoning, isReviewing])

  return (
    <div className={`reasoning-panel ${color} ${isReviewing ? 'reviewing' : ''}`}>
      <div className="reasoning-header">
        <span className="reasoning-title">
          {color === 'white' ? '♔' : '♚'} {name ?? '—'}
        </span>
        <div className="reasoning-badges">
          {isThinking  && <span className="thinking-badge">thinking…</span>}
          {isReviewing && <span className="review-badge">reviewing</span>}
        </div>
      </div>
      <div className="reasoning-body" ref={scrollRef}>
        {isThinking && !reasoning
          ? <div className="typing-indicator">
              <span className="typing-dot" />
              <span className="typing-dot" />
              <span className="typing-dot" />
            </div>
          : reasoning
            ? <p style={{ whiteSpace: 'pre-wrap' }}>
                {reasoning}
                {isThinking && <span className="stream-cursor" />}
              </p>
            : <p className="reasoning-placeholder">Awaiting first move…</p>
        }
      </div>
    </div>
  )
}
