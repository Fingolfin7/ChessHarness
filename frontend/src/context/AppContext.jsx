/**
 * AppContext — shared models, auth providers, and auth callbacks.
 *
 * Consumed by GamePage and TournamentSetup (different routes that both
 * need the provider list and connection management).
 */

import { createContext, useCallback, useContext, useEffect, useState } from 'react'

const AppContext = createContext(null)

export function AppProvider({ children }) {
  const [models, setModels] = useState([])
  const [authProviders, setAuthProviders] = useState({})
  const [authReady, setAuthReady] = useState(false)
  const [defaultSettings, setDefaultSettings] = useState(null)

  const refreshModels = useCallback(() => {
    fetch('/api/models').then(r => r.json()).then(setModels).catch(() => {})
  }, [])

  useEffect(() => {
    fetch('/api/models')
      .then(r => r.json())
      .then(setModels)
      .catch(() => {})

    fetch('/api/auth/providers')
      .then(r => r.json())
      .then(rows => {
        const mapped = {}
        rows.forEach(row => { mapped[row.provider] = !!row.connected })
        setAuthProviders(mapped)
      })
      .catch(() => {})
      .finally(() => setAuthReady(true))

    fetch('/api/config')
      .then(r => r.json())
      .then(cfg => setDefaultSettings({
        maxRetries: cfg.max_retries ?? 3,
        showLegalMoves: cfg.show_legal_moves ?? true,
        boardInput: cfg.board_input ?? 'text',
        annotatePgn: cfg.annotate_pgn ?? false,
        maxOutputTokens: cfg.max_output_tokens ?? 5120,
        reasoningEffort: cfg.reasoning_effort ?? 'default',
        startingFen: cfg.starting_fen ?? '',
      }))
      .catch(() => {})
  }, [])

  const connectProvider = useCallback(async (provider, token) => {
    try {
      const res = await fetch('/api/auth/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, token }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        return { ok: false, error: data.detail || 'Connection failed.' }
      }
      setAuthProviders(prev => ({ ...prev, [provider]: true }))
      refreshModels()
      return { ok: true }
    } catch {
      return { ok: false, error: 'Network error.' }
    }
  }, [refreshModels])

  const disconnectProvider = useCallback(async (provider) => {
    try {
      const res = await fetch('/api/auth/disconnect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider }),
      })
      if (!res.ok) return false
      const data = await res.json()
      setAuthProviders(prev => ({ ...prev, [provider]: !!data.connected }))
      return true
    } catch {
      return false
    }
  }, [])

  const startCopilotDeviceFlow = useCallback(async () => {
    try {
      const res = await fetch('/api/auth/copilot_chat/device/start', { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) return { ok: false, error: data.detail || 'Failed to start.' }
      return { ok: true, ...data }
    } catch {
      return { ok: false, error: 'Network error.' }
    }
  }, [])

  const pollCopilotDeviceFlow = useCallback(async (deviceCode) => {
    try {
      const res = await fetch('/api/auth/copilot_chat/device/poll', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_code: deviceCode }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        return { status: 'error', error: data.detail || `Server error ${res.status}` }
      }
      if (data.status === 'connected') {
        setAuthProviders(prev => ({ ...prev, copilot_chat: true }))
        setTimeout(refreshModels, 1500)
      }
      return data
    } catch {
      return { status: 'error', error: 'Network error.' }
    }
  }, [refreshModels])

  const connectOpenAIChatGPTFromCodex = useCallback(async () => {
    try {
      const res = await fetch('/api/auth/openai_chatgpt/codex/connect', { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) return { ok: false, error: data.detail || 'Codex connect failed.' }
      setAuthProviders(prev => ({ ...prev, openai_chatgpt: true }))
      refreshModels()
      return { ok: true, verified: !!data.verified }
    } catch {
      return { ok: false, error: 'Network error.' }
    }
  }, [refreshModels])

  return (
    <AppContext.Provider value={{
      models,
      authProviders,
      authReady,
      defaultSettings,
      refreshModels,
      connectProvider,
      disconnectProvider,
      startCopilotDeviceFlow,
      pollCopilotDeviceFlow,
      connectOpenAIChatGPTFromCodex,
    }}>
      {children}
    </AppContext.Provider>
  )
}

export function useAppContext() {
  return useContext(AppContext)
}
