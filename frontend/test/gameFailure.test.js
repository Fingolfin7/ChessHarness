import test from 'node:test'
import assert from 'node:assert/strict'
import { applyGameFailure } from '../src/utils/gameFailure.js'

test('provider failure ends a live game and clears pending input', () => {
  const state = applyGameFailure(
    {
      phase: 'playing',
      thinking: true,
      awaitingHumanInput: { color: 'white' },
      error: null,
    },
    { type: 'GameFailureEvent', error: '[openai] request timed out' },
  )

  assert.equal(state.phase, 'over')
  assert.equal(state.thinking, false)
  assert.equal(state.awaitingHumanInput, null)
  assert.equal(state.error, '[openai] request timed out')
})

test('failure reducer accepts the legacy websocket error shape', () => {
  const state = applyGameFailure(
    { phase: 'playing', thinking: true, awaitingHumanInput: null, error: null },
    { type: 'error', message: 'Engine subprocess unavailable' },
  )

  assert.equal(state.phase, 'over')
  assert.equal(state.thinking, false)
  assert.equal(state.error, 'Engine subprocess unavailable')
})
