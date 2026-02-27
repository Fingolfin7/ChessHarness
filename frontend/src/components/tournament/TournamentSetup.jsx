/**
 * TournamentSetup — dedicated tournament configuration page.
 *
 * Lets the user build a participant list (2–16 players) by picking any
 * available model for each slot, then configure draw handling before
 * launching the tournament.
 */

import { useMemo, useState } from 'react'

const MAX_PLAYERS = 16
const DRAW_OPTIONS = [
  { value: 'rematch',    label: 'Rematch (colours swap until a winner)' },
  { value: 'coin_flip',  label: 'Coin flip' },
  { value: 'seed',       label: 'Higher seed advances' },
]

function nextPowerOfTwo(n) {
  return Math.pow(2, Math.ceil(Math.log2(Math.max(n, 2))))
}

function byeCount(n) {
  return nextPowerOfTwo(n) - n
}

function ModelSelect({ index, value, models, modelsByProvider, onChange, onRemove, canRemove }) {
  return (
    <div className="ts-player-row">
      <span className="ts-player-seed">{index + 1}</span>
      <select
        className="ts-model-select"
        value={value}
        onChange={e => onChange(index, e.target.value)}
      >
        <option value="">Select model…</option>
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
      {canRemove && (
        <button className="ts-remove-btn" onClick={() => onRemove(index)} title="Remove">✕</button>
      )}
    </div>
  )
}

export default function TournamentSetup({ models, authReady, onBack, onStart, error: parentError }) {
  const [players, setPlayers] = useState(['', ''])   // array of JSON strings (or '')
  const [drawHandling, setDrawHandling] = useState('rematch')
  const [loading, setLoading] = useState(false)
  const [localError, setLocalError] = useState(null)

  const error = localError || parentError

  const modelsByProvider = useMemo(() => {
    const out = {}
    for (const m of models) {
      if (!out[m.provider]) out[m.provider] = []
      out[m.provider].push(m)
    }
    return out
  }, [models])

  const selectedCount = players.filter(Boolean).length
  const canStart = selectedCount >= 2 && !loading
  const byes = byeCount(players.length)

  const handleChange = (i, val) => {
    setPlayers(prev => { const next = [...prev]; next[i] = val; return next })
  }

  const handleRemove = (i) => {
    setPlayers(prev => prev.filter((_, idx) => idx !== i))
  }

  const handleAdd = () => {
    if (players.length < MAX_PLAYERS) setPlayers(prev => [...prev, ''])
  }

  const handleStart = async () => {
    setLocalError(null)
    const filled = players.filter(Boolean)
    if (filled.length < 2) { setLocalError('Select at least 2 models.'); return }

    const participants = filled.map(v => {
      const m = JSON.parse(v)
      return { provider: m.provider, model_id: m.id, name: m.name }
    })

    setLoading(true)
    try {
      await onStart(participants, drawHandling)
    } catch (e) {
      setLocalError(e.message || 'Failed to start tournament.')
      setLoading(false)
    }
  }

  return (
    <div className="setup-screen">
      <div className="ts-card">

        {/* Header */}
        <div className="ts-header">
          <button className="ts-back-btn" onClick={onBack}>← Back</button>
          <div className="ts-title">
            <span className="ts-crown">♛</span>
            <h1>Tournament Setup</h1>
          </div>
          <div style={{ width: 64 }} /> {/* spacer to centre title */}
        </div>

        {error && <div className="setup-error">{error}</div>}

        {/* Participants */}
        <section className="ts-section">
          <div className="ts-section-header">
            <h2>Participants</h2>
            <span className="ts-section-hint">
              {selectedCount < 2 ? 'Choose at least 2 models' : `${selectedCount} player${selectedCount !== 1 ? 's' : ''}`}
            </span>
          </div>

          <div className="ts-player-list">
            {players.map((val, i) => (
              <ModelSelect
                key={i}
                index={i}
                value={val}
                models={models}
                modelsByProvider={modelsByProvider}
                onChange={handleChange}
                onRemove={handleRemove}
                canRemove={players.length > 2}
              />
            ))}
          </div>

          {players.length < MAX_PLAYERS && (
            <button className="ts-add-btn" onClick={handleAdd}>
              + Add Player
            </button>
          )}

          {selectedCount >= 3 && byes > 0 && (
            <p className="ts-bye-note">
              ℹ Bracket needs {nextPowerOfTwo(selectedCount)} slots — top {byes} seed{byes !== 1 ? 's' : ''} will receive a bye in round 1.
            </p>
          )}
        </section>

        {/* Settings */}
        <section className="ts-section">
          <div className="ts-section-header">
            <h2>Settings</h2>
          </div>
          <div className="ts-settings-row">
            <label className="ts-settings-label" htmlFor="draw-handling">Draw handling</label>
            <select
              id="draw-handling"
              className="settings-select"
              value={drawHandling}
              onChange={e => setDrawHandling(e.target.value)}
            >
              {DRAW_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
        </section>

        {/* Start */}
        <button
          className="start-btn"
          onClick={handleStart}
          disabled={!canStart}
        >
          {loading ? 'Starting…' : 'Start Tournament'}
        </button>

      </div>
    </div>
  )
}
