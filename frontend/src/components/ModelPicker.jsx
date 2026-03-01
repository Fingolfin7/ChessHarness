import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ModelDropdown, { VisionIcon } from './ModelDropdown.jsx'
import { providerLabel, compareProviderNames } from '../utils/providerLabels.js'

export default function ModelPicker({
  models, authProviders, authReady,
  onConnect, onDisconnect,
  onCopilotDeviceStart, onCopilotDevicePoll,
  onChatGPTCodexConnect,
  onStart, error, defaultSettings,
}) {
  const [white, setWhite] = useState('')
  const [black, setBlack] = useState('')
  const [tokens, setTokens] = useState({})
  const [authMessage, setAuthMessage] = useState('')
  const [settings, setSettings] = useState({
    maxRetries: 3,
    showLegalMoves: true,
    boardInput: 'text',
    annotatePgn: false,
    maxOutputTokens: 5120,
    reasoningEffort: 'default',
    startingFen: '',
  })

  // Copilot device-flow state
  const [copilotFlow, setCopilotFlow] = useState(null)
  // copilotFlow = null | { device_code, user_code, verification_uri, status: 'waiting'|'expired'|'error', error? }
  const pollTimerRef = useRef(null)

  const signinProviders = useMemo(() => {
    return Object.keys(authProviders).sort(compareProviderNames)
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
      { provider: bm.provider, model_id: bm.id, name: bm.name },
      {
        max_retries: settings.maxRetries,
        show_legal_moves: settings.showLegalMoves,
        board_input: settings.boardInput,
        annotate_pgn: settings.annotatePgn,
        max_output_tokens: settings.maxOutputTokens,
        reasoning_effort: settings.reasoningEffort === 'default' ? null : settings.reasoningEffort,
        starting_fen: settings.startingFen.trim() || null,
      }
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
    if (provider === 'copilot_chat') cancelCopilotFlow()
    const result = await onDisconnect(provider)
    setAuthMessage(result ? `Disconnected ${provider}.` : `Failed to disconnect ${provider}.`)
  }

  const connectChatGPTCodex = async () => {
    setAuthMessage('Importing ChatGPT/Codex auth...')
    const result = await onChatGPTCodexConnect()
    if (result.ok) {
      if (result.verified) {
        setAuthMessage('Connected openai_chatgpt via Codex login.')
      } else {
        setAuthMessage('Connected openai_chatgpt via Codex login (verification skipped).')
      }
    } else {
      setAuthMessage(`openai_chatgpt: ${result.error}`)
    }
  }

  // ── Copilot device flow ────────────────────────────────────────────────── //

  const cancelCopilotFlow = useCallback(() => {
    clearTimeout(pollTimerRef.current)
    pollTimerRef.current = null
    setCopilotFlow(null)
  }, [])

  // Sync settings from server defaults once loaded
  useEffect(() => {
    if (defaultSettings) setSettings(defaultSettings)
  }, [defaultSettings])

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

              if (provider === 'copilot_chat') {
                return (
                  <div key="copilot_chat" className="auth-row">
                    <div className="auth-provider">
                      <strong>github copilot</strong>
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
                          setTokens(prev => ({ ...prev, copilot_chat: prev.copilot_chat ?? '' }))
                        }}>
                          Paste Token
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
                          If sign-in fails, use a{' '}
                          <button className="btn-link" onClick={cancelCopilotFlow}>
                            token paste
                          </button>{' '}
                          fallback.
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

                    {/* Manual token paste fallback (advanced). */}
                    {!copilotFlow && !connected && tokens.copilot_chat !== undefined && (
                      <>
                        <p className="auth-hint">
                          Paste a Copilot Chat access token (short-lived) or a GitHub token to be exchanged.
                        </p>
                        <input
                          type="password"
                          placeholder="Paste Copilot or GitHub token"
                          value={tokens.copilot_chat || ''}
                          onChange={e => setTokens(prev => ({ ...prev, copilot_chat: e.target.value }))}
                        />
                        <div className="auth-actions">
                          <button className="btn-inline" onClick={() => connect('copilot_chat')}>Connect</button>
                          <button className="btn-inline danger" onClick={() => setTokens(prev => { const n = {...prev}; delete n.copilot_chat; return n })}>Cancel</button>
                        </div>
                      </>
                    )}

                    {connected && (
                      <div className="auth-actions">
                        <button className="btn-inline danger" onClick={() => disconnect('copilot_chat')}>Disconnect</button>
                      </div>
                    )}
                  </div>
                )
              }

              if (provider === 'openai_chatgpt') {
                return (
                  <div key="openai_chatgpt" className="auth-row">
                    <div className="auth-provider">
                      <strong>openai (codex)</strong>
                      <span className={connected ? 'auth-connected' : 'auth-disconnected'}>
                        {connected ? 'Connected' : 'Not connected'}
                      </span>
                    </div>
                    {!connected && (
                      <div className="auth-actions">
                        <button className="btn-inline" onClick={connectChatGPTCodex}>
                          Use Codex Login
                        </button>
                      </div>
                    )}
                    <div className="auth-actions">
                      <button className="btn-inline danger" onClick={() => disconnect('openai_chatgpt')}>Disconnect</button>
                    </div>
                  </div>
                )
              }

              // All other providers: token paste
              return (
                <div key={provider} className="auth-row">
                  <div className="auth-provider">
                    <strong>{providerLabel(provider)}</strong>
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
              <ModelDropdown
                large
                value={white}
                modelsByProvider={modelsByProvider}
                onChange={setWhite}
                placeholder={!authReady ? 'Checking providers…' : availableModels.length === 0 ? 'Connect a provider' : 'Select model…'}
                disabled={!authReady}
              />
            </div>

            <div className="vs-divider">VS</div>

            <div className="player-select">
              <label>
                <span className="select-piece black-piece">♚</span>
                Black
              </label>
              <ModelDropdown
                large
                value={black}
                modelsByProvider={modelsByProvider}
                onChange={setBlack}
                placeholder={!authReady ? 'Checking providers…' : availableModels.length === 0 ? 'Connect a provider' : 'Select model…'}
                disabled={!authReady}
              />
            </div>

            {availableModels.some(m => m.supports_vision) && (
              <p className="vision-legend"><VisionIcon className="vision-icon" /> = supports image board input</p>
            )}
          </div>

          <div className="game-settings">
            <div className="settings-title">Game Settings</div>
            <div className="settings-grid">
              <div className="settings-row">
                <label className="settings-label" htmlFor="max-retries">Max Retries</label>
                <input
                  id="max-retries"
                  type="number"
                  min="1"
                  max="10"
                  className="settings-number"
                  value={settings.maxRetries}
                  onChange={e => setSettings(s => ({ ...s, maxRetries: Math.max(1, parseInt(e.target.value) || 1) }))}
                />
              </div>
              <div className="settings-row">
                <label className="settings-label" htmlFor="board-input">Board Input</label>
                <select
                  id="board-input"
                  className="settings-select"
                  value={settings.boardInput}
                  onChange={e => setSettings(s => ({ ...s, boardInput: e.target.value }))}
                >
                  <option value="text">Text (FEN + moves)</option>
                  <option value="image">Image (board screenshot)</option>
                </select>
              </div>
              <div className="settings-row">
                <label className="settings-label" htmlFor="max-output-tokens">Max Output Tokens</label>
                <input
                  id="max-output-tokens"
                  type="number"
                  min="64"
                  max="32768"
                  className="settings-number"
                  value={settings.maxOutputTokens}
                  onChange={e =>
                    setSettings(s => ({
                      ...s,
                      maxOutputTokens: Math.max(1, parseInt(e.target.value, 10) || 1),
                    }))
                  }
                />
              </div>
              <div className="settings-row">
                <label className="settings-label" htmlFor="reasoning-effort">Reasoning Effort</label>
                <select
                  id="reasoning-effort"
                  className="settings-select"
                  value={settings.reasoningEffort}
                  onChange={e => setSettings(s => ({ ...s, reasoningEffort: e.target.value }))}
                >
                  <option value="default">Default</option>
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                </select>
              </div>
              <div className="settings-row settings-row-check">
                <label className="settings-label settings-check-label" htmlFor="legal-moves">
                  <input
                    id="legal-moves"
                    type="checkbox"
                    className="settings-checkbox"
                    checked={settings.showLegalMoves}
                    onChange={e => setSettings(s => ({ ...s, showLegalMoves: e.target.checked }))}
                  />
                  Include legal move list in prompt
                </label>
              </div>
              <div className="settings-row settings-row-check">
                <label className="settings-label settings-check-label" htmlFor="annotate-pgn">
                  <input
                    id="annotate-pgn"
                    type="checkbox"
                    className="settings-checkbox"
                    checked={settings.annotatePgn}
                    onChange={e => setSettings(s => ({ ...s, annotatePgn: e.target.checked }))}
                  />
                  Export annotated PGN (include model reasoning)
                </label>
              </div>
              <div className="settings-row settings-row-fen">
                <label className="settings-label" htmlFor="starting-fen">Starting Position</label>
                <input
                  id="starting-fen"
                  type="text"
                  className="settings-fen"
                  value={settings.startingFen}
                  onChange={e => setSettings(s => ({ ...s, startingFen: e.target.value }))}
                  placeholder="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
                  spellCheck={false}
                />
              </div>
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
