export default function PlayerPanel({
  color, name, isActive, isThinking, invalidAttempt, lastMoveSan,
}) {
  return (
    <div className={`player-panel ${color} ${isActive ? 'active' : ''}`}>
      <div className="player-info">
        <span className="player-piece">{color === 'white' ? '♔' : '♚'}</span>
        <div>
          <div className="player-name">{name ?? '—'}</div>
          <div className="player-color-label">{color.toUpperCase()}</div>
        </div>
      </div>

      <div className="player-status">
        {isThinking && (
          <span className="thinking-indicator">
            <span className="dot-pulse" />
            Thinking…
          </span>
        )}
        {!isThinking && invalidAttempt && (
          <span className="invalid-badge">
            ✗ Retry {invalidAttempt.attempt}
          </span>
        )}
        {!isThinking && !invalidAttempt && lastMoveSan && (
          <span className="last-move-san">{lastMoveSan}</span>
        )}
      </div>
    </div>
  )
}
