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
          <span className="invalid-badge" title={invalidAttempt.error}>
            ✗ Retry {invalidAttempt.attempt}
            {invalidAttempt.error && (
              <span className="invalid-error">{invalidAttempt.error}</span>
            )}
          </span>
        )}
        {!isThinking && !invalidAttempt && lastMoveSan && (
          <span className="last-move-san">{lastMoveSan}</span>
        )}
      </div>
    </div>
  )
}
