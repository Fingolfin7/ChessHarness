/**
 * Shared move-timeout choices for the game and tournament setup screens.
 *
 * The timeout is a wall-clock deadline for one model turn.  It deliberately
 * does not change the model's max_output_tokens setting; a model may use its
 * full configured output capacity as long as it finishes before this deadline.
 */

export const DEFAULT_MOVE_TIMEOUT = 120
export const MIN_MOVE_TIMEOUT = 1
export const MAX_MOVE_TIMEOUT = 3600

export const MOVE_TIMEOUT_PRESETS = [
  { value: 60, label: '1 minute' },
  { value: 120, label: '2 minutes' },
  { value: 180, label: '3 minutes' },
  { value: 300, label: '5 minutes' },
  { value: 600, label: '10 minutes' },
  { value: 1200, label: '20 minutes' },
]

export function clampMoveTimeout(value, fallback = DEFAULT_MOVE_TIMEOUT) {
  const parsed = Number.parseInt(value, 10)
  const safeFallback = Number.isFinite(Number(fallback))
    ? Number(fallback)
    : DEFAULT_MOVE_TIMEOUT
  const candidate = Number.isFinite(parsed) ? parsed : safeFallback
  return Math.min(MAX_MOVE_TIMEOUT, Math.max(MIN_MOVE_TIMEOUT, candidate))
}

export function formatMoveTimeout(value) {
  const seconds = clampMoveTimeout(value)
  if (seconds < 60) {
    return `${seconds} second${seconds === 1 ? '' : 's'}`
  }

  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  if (remainder === 0) {
    return `${minutes} minute${minutes === 1 ? '' : 's'}`
  }
  return `${minutes}m ${remainder}s`
}

