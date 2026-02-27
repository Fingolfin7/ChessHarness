/**
 * useReconnectingWebSocket
 *
 * Manages a WebSocket that automatically reconnects with exponential backoff
 * when the connection closes unexpectedly.
 *
 * On each successful reconnect the server replays its full event log for that
 * endpoint, so the caller's reducer reconstructs state from scratch without
 * needing any special "reset before replay" logic — as long as the first
 * replayed event resets state (TournamentStartEvent / GameStartEvent do this).
 *
 * @param {string|null} url       - ws(s):// URL to connect to; null = idle
 * @param {function}    onMessage - Called with each parsed JSON message object
 * @returns {'connecting'|'connected'|'reconnecting'} current connection status
 */

import { useEffect, useRef, useState } from 'react'

const MIN_DELAY_MS = 1_000
const MAX_DELAY_MS = 30_000

export function useReconnectingWebSocket(url, onMessage) {
  const [connStatus, setConnStatus] = useState('connecting')

  // Stable ref so the effect closure always calls the latest onMessage
  // without needing it in the dependency array (which would restart the socket).
  const onMessageRef = useRef(onMessage)
  useEffect(() => { onMessageRef.current = onMessage })

  useEffect(() => {
    if (!url) {
      setConnStatus('connecting')
      return
    }

    let mounted = true
    let retryDelay = MIN_DELAY_MS
    let retryTimer = null
    let ws = null

    function connect() {
      if (!mounted) return

      ws = new WebSocket(url)

      ws.onopen = () => {
        if (!mounted) return
        setConnStatus('connected')
        retryDelay = MIN_DELAY_MS   // reset backoff on success
      }

      ws.onmessage = (e) => {
        try {
          onMessageRef.current(JSON.parse(e.data))
        } catch { /* ignore malformed frames */ }
      }

      // onerror always fires before onclose — let onclose drive the retry
      ws.onerror = () => {}

      ws.onclose = () => {
        if (!mounted) return
        setConnStatus('reconnecting')
        retryTimer = setTimeout(() => {
          retryDelay = Math.min(retryDelay * 2, MAX_DELAY_MS)
          connect()
        }, retryDelay)
      }
    }

    connect()

    return () => {
      mounted = false
      clearTimeout(retryTimer)
      ws?.close()
    }
  }, [url])   // only re-run when the URL changes (e.g. different match)

  return connStatus
}
