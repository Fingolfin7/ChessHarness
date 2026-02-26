import { useMemo, useState } from 'react'

export default function ModelPicker({ models, authProviders, onConnect, onDisconnect, onStart, error }) {
  const [white, setWhite] = useState('')
  const [black, setBlack] = useState('')
  const [tokens, setTokens] = useState({})
  const [authMessage, setAuthMessage] = useState('')

  const signinProviders = useMemo(() => {
    const preferred = ['openai', 'copilot']
    const available = new Set(models.map(m => m.provider))
    return preferred.filter(p => available.has(p))
  }, [models])

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
    const result = await onConnect(provider, token)
    setAuthMessage(result ? `Connected ${provider}.` : `Failed to connect ${provider}.`)
    setTokens(prev => ({ ...prev, [provider]: '' }))
  }

  const disconnect = async (provider) => {
    const result = await onDisconnect(provider)
    setAuthMessage(result ? `Disconnected ${provider}.` : `Failed to disconnect ${provider}.`)
  }

  return (
    <div className="setup-screen">
      <div className="setup-card">
        <div className="setup-logo">
          <span className="setup-king">♔</span>
          <h1>ChessHarness</h1>
          <p className="setup-subtitle">LLM Chess Arena</p>
        </div>

        {error && <div className="setup-error">{error}</div>}

        {signinProviders.length > 0 && (
          <div className="auth-panel">
            <div className="auth-title">Sign in providers</div>
            {signinProviders.map(provider => {
              const connected = authProviders[provider]
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
                    placeholder={`Paste ${provider} token`}
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

        <div className="player-selects">
          <div className="player-select">
            <label>
              <span className="select-piece white-piece">♔</span>
              White
            </label>
            <select value={white} onChange={e => setWhite(e.target.value)}>
              <option value="">Select model…</option>
              {models.map(m => (
                <option key={`${m.provider}/${m.id}`} value={JSON.stringify(m)}>
                  {m.name}
                </option>
              ))}
            </select>
          </div>

          <div className="vs-divider">VS</div>

          <div className="player-select">
            <label>
              <span className="select-piece black-piece">♚</span>
              Black
            </label>
            <select value={black} onChange={e => setBlack(e.target.value)}>
              <option value="">Select model…</option>
              {models.map(m => (
                <option key={`${m.provider}/${m.id}`} value={JSON.stringify(m)}>
                  {m.name}
                </option>
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
  )
}
