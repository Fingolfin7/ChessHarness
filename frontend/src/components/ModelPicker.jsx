import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

export default function ModelPicker({
  models, authProviders, authReady,
  onConnect, onDisconnect,
  onCopilotDeviceStart, onCopilotDevicePoll,
  onStart, error,
}) {
  const [white, setWhite] = useState('')
  const [black, setBlack] = useState('')
  const [tokens, setTokens] = useState({})
  const [authMessage, setAuthMessage] = useState('')

  // Copilot device-flow state
  const [copilotFlow, setCopilotFlow] = useState(null)
  // copilotFlow = null | { device_code, user_code, verification_uri, status: 'waiting'|'expired'|'error', error? }
  const pollTimerRef = useRef(null)

  const signinProviders = useMemo(() => {
    return Object.keys(authProviders).sort()
  }, [authProviders])

  // Only expose models from providers that are currently connected
  const availableModels = useMemo(() => {
    if (!authReady) return []
    return models.filter(m => authProviders[m.provider])
  }, [models, authProviders, authReady])

  // Group available models by provider for <optgroup> display
  const modelsByProvider = useMemo(() => {
    const groups = {}
    for (const m of availableModels) {
      if (!groups[m.provider]) groups[m.provider] = []
      groups[m.provider].push(m)
    }
    return groups
  }, [availableModels])

  // Clear selections when a previously chosen model's provider is disconnected
  useEffect(() => {
    if (!authReady) return
    if (white) {
      try {
        const wm = JSON.parse(white)
        if (!authProviders[wm.provider]) setWhite('')
      } catch { setWhite('') }
    }
    if (black) {
      try {
        const bm = JSON.parse(black)
        if (!authProviders[bm.provider]) setBlack('')
      } catch { setBlack('') }
    }
  }, [authProviders, authReady]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleStart = () => {
    if (!white || !black) return
    const wm = JSON.parse(white)
    const bm = JSON.parse(black)
    onStart(
      { provider: wm.provider, model_id: wm.id, name: wm.name },
      { provider: bm.provider, model_id: bm.id, name: bm.name }
    )
  }

  const connect = async (provider) => {
    const token = (tokens[provider] || '').trim()
    if (!token) {
      setAuthMessage(`Enter a token for ${provider} first.`)
      return
    }
    setAuthMessage(`Verifying ${provider}…`)
    const result = await onConnect(provider, token)
    if (result.ok) {
      setAuthMessage(`Connected ${provider}.`)
      setTokens(prev => ({ ...prev, [provider]: '' }))
    } else {
      setAuthMessage(`${provider}: ${result.error}`)
    }
  }

  const disconnect = async (provider) => {
    if (provider === 'copilot') cancelCopilotFlow()
    const result = await onDisconnect(provider)
    setAuthMessage(result ? `Disconnected ${provider}.` : `Failed to disconnect ${provider}.`)
  }

  // ── Copilot device flow ────────────────────────────────────────────────── //

  const cancelCopilotFlow = useCallback(() => {
    clearTimeout(pollTimerRef.current)
    pollTimerRef.current = null
    setCopilotFlow(null)
  }, [])

  // Clean up on unmount
  useEffect(() => () => clearTimeout(pollTimerRef.current), [])

  const startCopilotFlow = async () => {
    const result = await onCopilotDeviceStart()
    if (!result.ok) {
      setAuthMessage(`Copilot: ${result.error}`)
      return
    }
    setCopilotFlow({
      device_code: result.device_code,
      user_code: result.user_code,
      verification_uri: result.verification_uri,
      status: 'waiting',
    })
    const intervalMs = (result.interval ?? 5) * 1000

    const stopPoll = (newFlowState) => {
      clearTimeout(pollTimerRef.current)
      pollTimerRef.current = null
      if (newFlowState) setCopilotFlow(f => ({ ...f, ...newFlowState }))
    }

    // Use recursive setTimeout instead of setInterval so only one request is
    // ever in-flight at a time. setInterval with an async callback can fire
    // again before the previous request finishes, causing concurrent polls that
    // race each other on the same device code.
    const doPoll = async () => {
      const poll = await onCopilotDevicePoll(result.device_code)
      if (poll.status === 'connected') {
        stopPoll(null)
        setCopilotFlow(null)
        setAuthMessage('Copilot connected.')
      } else if (poll.status === 'expired') {
        stopPoll({ status: 'expired' })
      } else if (poll.status === 'error') {
        stopPoll({ status: 'error', error: poll.error || 'Unknown error.' })
      } else if (poll.status !== 'pending') {
        stopPoll({ status: 'error', error: poll.error || `Unexpected response: ${JSON.stringify(poll)}` })
      } else {
        // Still pending — schedule next poll only after this one completed
        pollTimerRef.current = setTimeout(doPoll, intervalMs)
      }
    }

    pollTimerRef.current = setTimeout(doPoll, intervalMs)
  }

  return (
    <div className="setup-screen">
      <div className="setup-card">
        {signinProviders.length > 0 && (
          <div className="auth-panel">
            <div className="auth-title">Providers</div>
            {signinProviders.map(provider => {
              const connected = authProviders[provider]

              if (provider === 'copilot') {
                return (
                  <div key="copilot" className="auth-row">
                    <div className="auth-provider">
                      <strong>GitHub Models</strong>
                      <span className={connected ? 'auth-connected' : 'auth-disconnected'}>
                        {connected ? 'Connected' : 'Not connected'}
                      </span>
                    </div>

                    {!connected && !copilotFlow && (
                      <div className="auth-actions">
                        <button className="btn-inline copilot-signin" onClick={startCopilotFlow}>
                          Sign in with GitHub
                        </button>
                        <button className="btn-inline" onClick={() => {
                          setAuthMessage('')
                          setTokens(prev => ({ ...prev, copilot: prev.copilot ?? '' }))
                        }}>
                          Paste PAT
                        </button>
                      </div>
                    )}

                    {/* Device-flow in progress */}
                    {copilotFlow && copilotFlow.status === 'waiting' && (
                      <div className="device-flow">
                        <p className="device-flow-instructions">
                          Go to{' '}
                          <a href={copilotFlow.verification_uri} target="_blank" rel="noreferrer">
                            {copilotFlow.verification_uri}
                          </a>{' '}
                          and enter:
                        </p>
                        <div className="device-code">{copilotFlow.user_code}</div>
                        <p className="device-flow-waiting">Waiting for authorization…</p>
                        <p className="device-flow-note">
                          If models fail to load after sign-in, use a{' '}
                          <button className="btn-link" onClick={cancelCopilotFlow}>
                            classic PAT
                          </button>{' '}
                          instead — no special scopes needed.
                        </p>
                        <button className="btn-inline danger" onClick={cancelCopilotFlow}>Cancel</button>
                      </div>
                    )}

                    {copilotFlow && copilotFlow.status === 'expired' && (
                      <div className="device-flow">
                        <p className="device-flow-error">Code expired. Try again.</p>
                        <button className="btn-inline" onClick={cancelCopilotFlow}>Retry</button>
                      </div>
                    )}

                    {copilotFlow && copilotFlow.status === 'error' && (
                      <div className="device-flow">
                        <p className="device-flow-error">{copilotFlow.error}</p>
                        <button className="btn-inline" onClick={cancelCopilotFlow}>Retry</button>
                      </div>
                    )}

                    {/* PAT paste — recommended method for GitHub Models */}
                    {!copilotFlow && !connected && tokens.copilot !== undefined && (
                      <>
                        <p className="auth-hint">
                          Create a{' '}
                          <a href="https://github.com/settings/tokens" target="_blank" rel="noreferrer">
                            classic PAT
                          </a>{' '}
                          at github.com/settings/tokens — no special scopes required.
                        </p>
                        <input
                          type="password"
                          placeholder="Paste GitHub classic PAT (ghp_…)"
                          value={tokens.copilot || ''}
                          onChange={e => setTokens(prev => ({ ...prev, copilot: e.target.value }))}
                        />
                        <div className="auth-actions">
                          <button className="btn-inline" onClick={() => connect('copilot')}>Connect</button>
                          <button className="btn-inline danger" onClick={() => setTokens(prev => { const n = {...prev}; delete n.copilot; return n })}>Cancel</button>
                        </div>
                      </>
                    )}

                    {connected && (
                      <div className="auth-actions">
                        <button className="btn-inline danger" onClick={() => disconnect('copilot')}>Disconnect</button>
                      </div>
                    )}
                  </div>
                )
              }

              // All other providers: token paste
              return (
                <div key={provider} className="auth-row">
                  <div className="auth-provider">
                    <strong>{provider}</strong>
                    <span className={connected ? 'auth-connected' : 'auth-disconnected'}>
                      {connected ? 'Connected' : 'Not connected'}
                    </span>
                  </div>
                  <input
                    type="password"
                    placeholder={`Paste ${provider} API key`}
                    value={tokens[provider] || ''}
                    onChange={e => setTokens(prev => ({ ...prev, [provider]: e.target.value }))}
                  />
                  <div className="auth-actions">
                    <button className="btn-inline" onClick={() => connect(provider)}>Connect</button>
                    <button className="btn-inline danger" onClick={() => disconnect(provider)}>Disconnect</button>
                  </div>
                </div>
              )
            })}
            {authMessage && <div className="auth-message">{authMessage}</div>}
          </div>
        )}

        <div className="setup-main">
          <div className="setup-logo">
            <span className="setup-king">♔</span>
            <h1>ChessHarness</h1>
            <p className="setup-subtitle">LLM Chess Arena</p>
          </div>

          {error && <div className="setup-error">{error}</div>}

          <div className="player-selects">
            <div className="player-select">
              <label>
                <span className="select-piece white-piece">♔</span>
                White
              </label>
              <select value={white} onChange={e => setWhite(e.target.value)} disabled={!authReady}>
                <option value="">
                  {!authReady ? 'Checking providers…' : availableModels.length === 0 ? 'Connect a provider' : 'Select model…'}
                </option>
                {Object.entries(modelsByProvider).map(([provider, pModels]) => (
                  <optgroup key={provider} label={provider}>
                    {pModels.map(m => (
                      <option key={`${m.provider}/${m.id}`} value={JSON.stringify(m)}>
                        {m.name}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </div>

            <div className="vs-divider">VS</div>

            <div className="player-select">
              <label>
                <span className="select-piece black-piece">♚</span>
                Black
              </label>
              <select value={black} onChange={e => setBlack(e.target.value)} disabled={!authReady}>
                <option value="">
                  {!authReady ? 'Checking providers…' : availableModels.length === 0 ? 'Connect a provider' : 'Select model…'}
                </option>
                {Object.entries(modelsByProvider).map(([provider, pModels]) => (
                  <optgroup key={provider} label={provider}>
                    {pModels.map(m => (
                      <option key={`${m.provider}/${m.id}`} value={JSON.stringify(m)}>
                        {m.name}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </div>
          </div>

          <button
            className="start-btn"
            onClick={handleStart}
            disabled={!white || !black}
          >
            Start Game
          </button>
        </div>
      </div>
    </div>
  )
}
