/**
 * TournamentSetup — dedicated tournament configuration page.
 *
 * Lets the user build a participant list (2–16 players) by picking any
 * available model for each slot, then configure the tournament format before
 * launching the tournament.
 *
 * If a tournament is already running (409), navigates to /tournament
 * instead of showing an error.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useAppContext } from '../../context/AppContext.jsx'
import ModelDropdown, { VisionIcon } from '../ModelDropdown.jsx'

const MAX_PLAYERS = 16
const FORMAT_OPTIONS = [
  { value: 'knockout', label: 'Knockout (single elimination)' },
  { value: 'round_robin', label: 'Round Robin (both colours)' },
]
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

function ModelSelect({ index, value, modelsByProvider, onChange, onRemove, canRemove }) {
  return (
    <div className="ts-player-row">
      <span className="ts-player-seed">{index + 1}</span>
      <ModelDropdown
        value={value}
        modelsByProvider={modelsByProvider}
        onChange={val => onChange(index, val)}
        placeholder="Select model…"
      />
      {canRemove && (
        <button className="ts-remove-btn" onClick={() => onRemove(index)} title="Remove">✕</button>
      )}
    </div>
  )
}

export default function TournamentSetup() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const benchmarkCompetitorId = searchParams.get('benchmark')
  const benchmarkApplied = useRef(false)
  const { models, authProviders, authReady, defaultSettings } = useAppContext()

  const [players, setPlayers] = useState(['', ''])
  const [tournamentType, setTournamentType] = useState('knockout')
  const [drawHandling, setDrawHandling] = useState('rematch')
  const [settings, setSettings] = useState({
    maxRetries: 3,
    showLegalMoves: true,
    boardInput: 'text',
    annotatePgn: false,
    maxOutputTokens: 5120,
    reasoningEffort: 'default',
    startingFen: '',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!defaultSettings || benchmarkCompetitorId) return
    setSettings(s => ({
      ...s,
      maxRetries: defaultSettings.maxRetries,
      showLegalMoves: defaultSettings.showLegalMoves,
      boardInput: defaultSettings.boardInput,
      annotatePgn: defaultSettings.annotatePgn,
      maxOutputTokens: defaultSettings.maxOutputTokens,
      reasoningEffort: defaultSettings.reasoningEffort,
    }))
  }, [benchmarkCompetitorId, defaultSettings])

  // Only show models from providers that are currently connected.
  const availableModels = useMemo(() => {
    if (!authReady) return []
    return models.filter(m => authProviders[m.provider] && m.available !== false)
  }, [models, authProviders, authReady])

  const modelsByProvider = useMemo(() => {
    const out = {}
    for (const m of availableModels) {
      if (!out[m.provider]) out[m.provider] = []
      out[m.provider].push(m)
    }
    return out
  }, [availableModels])

  // Ratings history links here with a stable competitor identity. A double
  // round robin gives the model one game with each colour against the anchor.
  useEffect(() => {
    const competitorId = benchmarkCompetitorId
    if (!competitorId || benchmarkApplied.current || !authReady) return
    if (availableModels.length === 0) return

    const model = availableModels.find(
      item => `llm:${item.provider}:${item.id}` === competitorId,
    )
    const anchor = availableModels.find(item => item.provider === 'engine')
    if (!model || !anchor) {
      setError('The selected model or Stockfish benchmark is not currently available.')
      benchmarkApplied.current = true
      return
    }

    setPlayers([JSON.stringify(model), JSON.stringify(anchor)])
    setTournamentType('round_robin')
    setSettings({
      maxRetries: 3,
      showLegalMoves: true,
      boardInput: 'text',
      annotatePgn: false,
      maxOutputTokens: 5120,
      reasoningEffort: 'default',
      startingFen: '',
    })
    benchmarkApplied.current = true
  }, [authReady, availableModels, benchmarkCompetitorId])

  // Clear selected participants whose providers become disconnected.
  useEffect(() => {
    if (!authReady) return
    setPlayers(prev =>
      prev.map(v => {
        if (!v) return v
        try {
          const m = JSON.parse(v)
          return authProviders[m.provider] ? v : ''
        } catch {
          return ''
        }
      }),
    )
  }, [authProviders, authReady])

  const selectedCount = players.filter(Boolean).length
  const canStart = selectedCount >= 2 && !loading
  const byes = byeCount(selectedCount)
  const roundRobinRounds = selectedCount % 2 === 0
    ? Math.max(0, 2 * (selectedCount - 1))
    : 2 * selectedCount
  const roundRobinGames = selectedCount * Math.max(0, selectedCount - 1)

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
    setError(null)
    const filled = players.filter(Boolean)
    if (filled.length < 2) { setError('Select at least 2 models.'); return }

    const participants = filled.map(v => {
      const m = JSON.parse(v)
      return { provider: m.provider, model_id: m.id, name: m.name }
    })

    setLoading(true)
    try {
      const res = await fetch('/api/tournament/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tournament_type: tournamentType,
          draw_handling: drawHandling,
          benchmark: Boolean(benchmarkCompetitorId),
          participants,
          settings: {
            max_retries: settings.maxRetries,
            show_legal_moves: settings.showLegalMoves,
            board_input: settings.boardInput,
            annotate_pgn: settings.annotatePgn,
            max_output_tokens: settings.maxOutputTokens,
            reasoning_effort: settings.reasoningEffort,
            starting_fen: settings.startingFen.trim() || null,
            ...(benchmarkCompetitorId ? { move_timeout: 120 } : {}),
          },
        }),
      })

      if (res.status === 409) {
        // Tournament already running — go watch it
        navigate('/tournament')
        return
      }

      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setError(data.detail || `Server error ${res.status}`)
        setLoading(false)
        return
      }

      navigate('/tournament')
    } catch (e) {
      setError(e.message || 'Failed to start tournament.')
      setLoading(false)
    }
  }

  return (
    <div className="setup-screen">
      <div className="ts-card">

        {/* Header */}
        <div className="ts-header">
          <button className="ts-back-btn" onClick={() => navigate('/game')}>← Back</button>
          <div className="ts-title">
            <span className="ts-crown">♛</span>
            <h1>Tournament Setup</h1>
          </div>
          <div />
        </div>

        {error && <div className="setup-error">{error}</div>}

        {benchmarkCompetitorId && !error && (
          <p className="ts-bye-note">
            Benchmark mode uses the canonical standard-v1 settings and plays both colours.
          </p>
        )}

        {!authReady && (
          <p className="ts-bye-note">Checking providers…</p>
        )}

        {authReady && availableModels.length === 0 && (
          <div className="setup-error">
            No models available. Connect a provider on the{' '}
            <button className="btn-link" onClick={() => navigate('/game')}>game setup</button> page first.
          </div>
        )}

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

          {Object.values(modelsByProvider).flat().some(m => m.supports_vision) && (
            <p className="ts-vision-legend"><VisionIcon className="ts-vision-icon" /> = supports image board input</p>
          )}

          {tournamentType === 'knockout' && selectedCount >= 3 && byes > 0 && (
            <p className="ts-bye-note">
              ℹ Bracket needs {nextPowerOfTwo(selectedCount)} slots — top {byes} seed{byes !== 1 ? 's' : ''} will receive a bye in round 1.
            </p>
          )}

          {tournamentType === 'round_robin' && selectedCount >= 2 && (
            <p className="ts-bye-note">
              ℹ {roundRobinGames} games across {roundRobinRounds} rounds — every pairing plays twice with colours swapped.
            </p>
          )}
        </section>

        {/* Settings */}
        <section className="ts-section">
          <div className="ts-section-header">
            <h2>Settings</h2>
          </div>

          <div className="ts-settings-row">
            <label className="ts-settings-label" htmlFor="tournament-format">Format</label>
            <select
              id="tournament-format"
              className="settings-select"
              value={tournamentType}
              disabled={Boolean(benchmarkCompetitorId)}
              onChange={e => setTournamentType(e.target.value)}
            >
              {FORMAT_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>

          {tournamentType === 'knockout' && (
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
          )}

          <div className="ts-settings-row">
            <label className="ts-settings-label" htmlFor="ts-max-retries">Max Retries</label>
            <input
              id="ts-max-retries"
              type="number"
              min="1"
              max="10"
              className="settings-number"
              value={settings.maxRetries}
              disabled={Boolean(benchmarkCompetitorId)}
              onChange={e => setSettings(s => ({ ...s, maxRetries: Math.max(1, parseInt(e.target.value) || 1) }))}
            />
          </div>

          <div className="ts-settings-row">
            <label className="ts-settings-label" htmlFor="ts-board-input">Board Input</label>
            <select
              id="ts-board-input"
              className="settings-select"
              value={settings.boardInput}
              disabled={Boolean(benchmarkCompetitorId)}
              onChange={e => setSettings(s => ({ ...s, boardInput: e.target.value }))}
            >
              <option value="text">Text (FEN + moves)</option>
              <option value="image">Image (board screenshot)</option>
            </select>
          </div>

          <div className="ts-settings-row">
            <label className="ts-settings-label" htmlFor="ts-max-tokens">Max Output Tokens</label>
            <input
              id="ts-max-tokens"
              type="number"
              min="64"
              max="32768"
              className="settings-number"
              value={settings.maxOutputTokens}
              disabled={Boolean(benchmarkCompetitorId)}
              onChange={e => setSettings(s => ({ ...s, maxOutputTokens: Math.max(1, parseInt(e.target.value, 10) || 1) }))}
            />
          </div>

          <div className="ts-settings-row">
            <label className="ts-settings-label" htmlFor="ts-reasoning">Reasoning Effort</label>
            <select
              id="ts-reasoning"
              className="settings-select"
              value={settings.reasoningEffort}
              disabled={Boolean(benchmarkCompetitorId)}
              onChange={e => setSettings(s => ({ ...s, reasoningEffort: e.target.value }))}
            >
              <option value="default">Default</option>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </select>
          </div>

          <div className="ts-settings-row ts-settings-row--check">
            <label className="ts-settings-label settings-check-label" htmlFor="ts-legal-moves">
              <input
                id="ts-legal-moves"
                type="checkbox"
                className="settings-checkbox"
                checked={settings.showLegalMoves}
                disabled={Boolean(benchmarkCompetitorId)}
                onChange={e => setSettings(s => ({ ...s, showLegalMoves: e.target.checked }))}
              />
              Include legal move list in prompt
            </label>
          </div>

          <div className="ts-settings-row ts-settings-row--check">
            <label className="ts-settings-label settings-check-label" htmlFor="ts-annotate-pgn">
              <input
                id="ts-annotate-pgn"
                type="checkbox"
                className="settings-checkbox"
                checked={settings.annotatePgn}
                disabled={Boolean(benchmarkCompetitorId)}
                onChange={e => setSettings(s => ({ ...s, annotatePgn: e.target.checked }))}
              />
              Export annotated PGN (include model reasoning)
            </label>
          </div>

          <div className="ts-settings-row ts-settings-row--fen">
            <label className="ts-settings-label" htmlFor="ts-starting-fen">Starting Position</label>
            <input
              id="ts-starting-fen"
              type="text"
              className="settings-fen"
              value={settings.startingFen}
              disabled={Boolean(benchmarkCompetitorId)}
              onChange={e => setSettings(s => ({ ...s, startingFen: e.target.value }))}
              placeholder="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
              spellCheck={false}
            />
          </div>
        </section>

        {/* Start */}
        <button
          className="start-btn"
          onClick={handleStart}
          disabled={!canStart || availableModels.length === 0}
        >
          {loading ? 'Starting…' : 'Start Tournament'}
        </button>

      </div>
    </div>
  )
}
