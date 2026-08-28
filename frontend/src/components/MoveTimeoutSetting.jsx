import {
  clampMoveTimeout,
  formatMoveTimeout,
  MAX_MOVE_TIMEOUT,
  MIN_MOVE_TIMEOUT,
  MOVE_TIMEOUT_PRESETS,
} from '../utils/moveTimeout.js'

/**
 * Shared timeout setting used by one-off games and tournaments.
 *
 * The preset select is only a convenience; the number input remains the
 * source of truth so arbitrary values (including long reasoning runs) are
 * supported without changing max_output_tokens.
 */
export default function MoveTimeoutSetting({
  id,
  value,
  onChange,
  disabled = false,
  rowClassName = 'settings-row',
  labelClassName = 'settings-label',
}) {
  const timeout = clampMoveTimeout(value)
  const selectedPreset = MOVE_TIMEOUT_PRESETS.some(item => item.value === timeout)
    ? String(timeout)
    : 'custom'

  const handlePresetChange = (event) => {
    if (event.target.value === 'custom') return
    onChange(clampMoveTimeout(event.target.value, timeout))
  }

  const handleNumberChange = (event) => {
    onChange(clampMoveTimeout(event.target.value, timeout))
  }

  return (
    <>
      <div className={`${rowClassName} settings-row-timeout`}>
        <label className={labelClassName} htmlFor={id}>
          Move Timeout
        </label>
        <div className="settings-timeout-control">
          <select
            id={`${id}-preset`}
            className="settings-select settings-timeout-preset"
            value={selectedPreset}
            onChange={handlePresetChange}
            disabled={disabled}
            aria-label="Move timeout preset"
          >
            <option value="custom">Custom</option>
            {MOVE_TIMEOUT_PRESETS.map(preset => (
              <option key={preset.value} value={preset.value}>{preset.label}</option>
            ))}
          </select>
          <input
            id={id}
            type="number"
            min={MIN_MOVE_TIMEOUT}
            max={MAX_MOVE_TIMEOUT}
            step="1"
            className="settings-number settings-timeout-number"
            value={timeout}
            onChange={handleNumberChange}
            disabled={disabled}
            aria-describedby={`${id}-summary`}
          />
          <span className="settings-timeout-unit">seconds</span>
        </div>
      </div>
      <p className="settings-timeout-summary" id={`${id}-summary`}>
        Effective maximum per-turn deadline: <strong>{formatMoveTimeout(timeout)}</strong>.
        {' '}Retries and input fallbacks share this deadline.
      </p>
    </>
  )
}

