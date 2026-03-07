function formatProviderHint(invalidAttempt) {
  const metadata = invalidAttempt?.providerMetadata ?? {}
  const finishReason = String(metadata.finish_reason ?? '').toUpperCase()
  if (!finishReason.includes('MAX_TOKENS')) return null

  const usage = metadata.usage ?? {}
  const promptTokens = usage.prompt_token_count ?? usage.prompt_tokens
  const outputTokens = usage.candidates_token_count ?? usage.completion_tokens ?? usage.output_tokens
  if (promptTokens != null && outputTokens != null) {
    return `Output limit reached (${promptTokens} in / ${outputTokens} out)`
  }
  return 'Output token limit reached'
}

function invalidAttemptTitle(invalidAttempt) {
  if (!invalidAttempt) return ''
  const providerHint = formatProviderHint(invalidAttempt)
  return providerHint ? `${invalidAttempt.error}\n${providerHint}` : invalidAttempt.error
}

export default function PlayerPanel({
  color, name, playerType, isActive, isThinking, isAwaitingInput, invalidAttempt, lastMoveSan,
}) {
  const playerTypeLabel = playerType ? playerType.toUpperCase() : null
  const providerHint = formatProviderHint(invalidAttempt)

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
          <span className="invalid-badge" title={invalidAttemptTitle(invalidAttempt)}>
            Retry {invalidAttempt.attempt}
            {providerHint && (
              <span className="invalid-detail">{providerHint}</span>
            )}
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
