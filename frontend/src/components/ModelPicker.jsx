import { useState } from 'react'

export default function ModelPicker({ models, onStart, error }) {
  const [white, setWhite] = useState('')
  const [black, setBlack] = useState('')

  const handleStart = () => {
    if (!white || !black) return
    const wm = JSON.parse(white)
    const bm = JSON.parse(black)
    onStart(
      { provider: wm.provider, model_id: wm.id, name: wm.name },
      { provider: bm.provider, model_id: bm.id, name: bm.name }
    )
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
