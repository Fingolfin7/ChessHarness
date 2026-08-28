/**
 * Apply a terminal failure from the single-game websocket.
 *
 * Provider and infrastructure failures do not produce a chess result, but
 * they must still end the live UI so it does not keep showing a thinking
 * player or an active Stop button while the server has already stopped.
 */
export function applyGameFailure(state, event) {
  return {
    ...state,
    phase: 'over',
    thinking: false,
    awaitingHumanInput: null,
    error: event.error || event.message || 'Game failed.',
  }
}
