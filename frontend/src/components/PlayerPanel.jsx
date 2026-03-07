export default function PlayerPanel({
  color, name, playerType, isActive, isThinking, isAwaitingInput, invalidAttempt, lastMoveSan,
}) {
  const playerTypeLabel = playerType ? playerType.toUpperCase() : null

  return (
    <div className={`player-panel ${color} ${isActive ? 'active' : ''}`}>
      <div className="player-info">
        <span className="player-piece">{color === 'white' ? '\u2654' : '\u265A'}</span>
        <div>
          <div className="player-name">{name ?? '-'}</div>
          <div className="player-color-label">
            {color.toUpperCase()}
            {playerTypeLabel ? ` · ${playerTypeLabel}` : ''}
          </div>
        </div>
      </div>

      <div className="player-status">
        {isThinking && (
          <span className="thinking-indicator">
            <span className="dot-pulse" />
            Thinking...
          </span>
        )}
        {!isThinking && isAwaitingInput && (
          <span className="last-move-san">Awaiting move</span>
        )}
        {!isThinking && !isAwaitingInput && invalidAttempt && (
          <span className="invalid-badge" title={invalidAttempt.error}>
            Retry {invalidAttempt.attempt}
            {invalidAttempt.error && (
              <span className="invalid-error">{invalidAttempt.error}</span>
            )}
          </span>
        )}
        {!isThinking && !isAwaitingInput && !invalidAttempt && lastMoveSan && (
          <span className="last-move-san">{lastMoveSan}</span>
        )}
      </div>
    </div>
  )
}
