/**
 * ModelDropdown — custom select that can render the vision eye icon inside
 * both the trigger and each option row.
 *
 * Props:
 *   value            – JSON-stringified model object, or ''
 *   modelsByProvider – { [providerName]: model[] }
 *   onChange(val)    – called with JSON-stringified model or '' on selection
 *   placeholder      – text shown when nothing is selected
 *   disabled         – greyed-out / non-interactive
 *   large            – use the larger game-page styling variant
 */

import { useEffect, useRef, useState } from 'react'
import { providerLabel, compareProviderNames } from '../utils/providerLabels.js'

export function VisionIcon({ className }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      className={className || 'vision-icon'} aria-label="Supports image board input">
      <path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  )
}

function ChevronIcon({ open }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="10" height="6" viewBox="0 0 10 6"
      className="model-dd-chevron"
      style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>
      <path fill="currentColor" d="M5 6L0 0h10z" />
    </svg>
  )
}

export default function ModelDropdown({ value, modelsByProvider, onChange, placeholder, disabled, large }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  const selectedModel = value ? JSON.parse(value) : null

  useEffect(() => {
    if (!open) return
    const handler = (e) => { if (!ref.current?.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  return (
    <div className={`model-dd${large ? ' model-dd--large' : ''}`} ref={ref}>
      <button
        type="button"
        className="model-dd-trigger"
        onClick={() => !disabled && setOpen(o => !o)}
        disabled={disabled}
      >
        <span className={`model-dd-label${!selectedModel ? ' model-dd-dim' : ''}`}>
          {selectedModel ? selectedModel.name : placeholder}
        </span>
        {selectedModel?.supports_vision && <VisionIcon className="model-dd-eye" />}
        <ChevronIcon open={open} />
      </button>

      {open && (
        <div className="model-dd-menu">
          <button type="button" className="model-dd-item model-dd-dim"
            onClick={() => { onChange(''); setOpen(false) }}>
            {placeholder}
          </button>
          {Object.entries(modelsByProvider)
            .sort(([a], [b]) => compareProviderNames(a, b))
            .map(([provider, pModels]) => (
            <div key={provider}>
              <div className="model-dd-group-label">{providerLabel(provider)}</div>
              {pModels.map(m => {
                const v = JSON.stringify(m)
                return (
                  <button key={v} type="button"
                    className={`model-dd-item${value === v ? ' model-dd-item--active' : ''}`}
                    onClick={() => { onChange(v); setOpen(false) }}>
                    <span className="model-dd-item-name">{m.name}</span>
                    {m.supports_vision && <VisionIcon className="model-dd-eye" />}
                  </button>
                )
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
