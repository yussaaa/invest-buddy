import { useEffect, useState } from 'react'
import { Save, Loader2, CheckCircle, AlertCircle } from 'lucide-react'
import { api } from '../lib/api'
import type { UserPreferences } from '../lib/types'

const USER_ID = 'anonymous'

const SECTORS = [
  'Technology',
  'Healthcare',
  'Finance',
  'Energy',
  'Consumer',
  'Industrials',
  'Real Estate',
  'Materials',
  'Utilities',
  'Communication',
]

type SaveState = 'idle' | 'saving' | 'saved' | 'error'

function RadioGroup<T extends string>({
  label,
  name,
  options,
  value,
  onChange,
}: {
  label: string
  name: string
  options: { value: T; label: string; description?: string }[]
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm font-semibold text-slate-300">{label}</p>
      <div className="flex flex-wrap gap-2">
        {options.map(opt => (
          <label
            key={opt.value}
            className={[
              'flex flex-col gap-0.5 px-4 py-3 rounded-lg border cursor-pointer transition-colors select-none',
              value === opt.value
                ? 'border-blue-500/70 bg-blue-500/10 text-blue-300'
                : 'border-slate-700 bg-slate-800/60 text-slate-400 hover:border-slate-500 hover:text-slate-200',
            ].join(' ')}
          >
            <input
              type="radio"
              name={name}
              value={opt.value}
              checked={value === opt.value}
              onChange={() => onChange(opt.value)}
              className="sr-only"
            />
            <span className="text-sm font-medium">{opt.label}</span>
            {opt.description && (
              <span className="text-xs opacity-70">{opt.description}</span>
            )}
          </label>
        ))}
      </div>
    </div>
  )
}

const DEFAULT_PREFS: UserPreferences = {
  user_id: USER_ID,
  risk_tolerance: 'moderate',
  investment_horizon: 'medium',
  preferred_sectors: [],
  analysis_depth: 'standard',
  preferred_metrics: [],
  recent_tickers: [],
}

export default function SettingsPage() {
  const [prefs, setPrefs] = useState<UserPreferences>(DEFAULT_PREFS)
  const [loading, setLoading] = useState(true)
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  useEffect(() => {
    api.preferences.get(USER_ID)
      .then(p => setPrefs(p))
      .catch(() => { /* use defaults */ })
      .finally(() => setLoading(false))
  }, [])

  function toggleSector(sector: string) {
    setPrefs(p => ({
      ...p,
      preferred_sectors: p.preferred_sectors.includes(sector)
        ? p.preferred_sectors.filter(s => s !== sector)
        : [...p.preferred_sectors, sector],
    }))
  }

  async function handleSave() {
    setSaveState('saving')
    setErrorMsg(null)
    try {
      const saved = await api.preferences.update(USER_ID, {
        risk_tolerance: prefs.risk_tolerance,
        investment_horizon: prefs.investment_horizon,
        analysis_depth: prefs.analysis_depth,
        preferred_sectors: prefs.preferred_sectors,
      })
      setPrefs(saved)
      setSaveState('saved')
      setTimeout(() => setSaveState('idle'), 3000)
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : 'Failed to save preferences')
      setSaveState('error')
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-3 text-slate-400 py-24 justify-center">
        <Loader2 size={20} className="animate-spin" />
        <span>Loading preferences…</span>
      </div>
    )
  }

  return (
    <div className="max-w-3xl mx-auto px-6 py-8 flex flex-col gap-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-white">Settings</h1>
        <p className="text-sm text-slate-400 mt-1">
          Customize how analyses are tailored to your profile.
        </p>
      </div>

      {/* Preferences form */}
      <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-6 flex flex-col gap-7">

        {/* Risk Tolerance */}
        <RadioGroup
          label="Risk Tolerance"
          name="risk_tolerance"
          value={prefs.risk_tolerance}
          onChange={v => setPrefs(p => ({ ...p, risk_tolerance: v }))}
          options={[
            { value: 'conservative', label: 'Conservative', description: 'Capital preservation' },
            { value: 'moderate', label: 'Moderate', description: 'Balanced growth' },
            { value: 'aggressive', label: 'Aggressive', description: 'Maximum growth' },
          ]}
        />

        <div className="border-t border-slate-700" />

        {/* Investment Horizon */}
        <RadioGroup
          label="Investment Horizon"
          name="investment_horizon"
          value={prefs.investment_horizon}
          onChange={v => setPrefs(p => ({ ...p, investment_horizon: v }))}
          options={[
            { value: 'short', label: 'Short-term', description: '< 1 year' },
            { value: 'medium', label: 'Medium-term', description: '1 – 5 years' },
            { value: 'long', label: 'Long-term', description: '5+ years' },
          ]}
        />

        <div className="border-t border-slate-700" />

        {/* Analysis Depth */}
        <RadioGroup
          label="Analysis Depth"
          name="analysis_depth"
          value={prefs.analysis_depth}
          onChange={v => setPrefs(p => ({ ...p, analysis_depth: v }))}
          options={[
            { value: 'quick', label: 'Quick', description: '~15s · fewer agents' },
            { value: 'standard', label: 'Standard', description: '~45s · all agents' },
            { value: 'deep', label: 'Deep', description: '~90s · deep research' },
          ]}
        />

        <div className="border-t border-slate-700" />

        {/* Preferred Sectors */}
        <div className="flex flex-col gap-3">
          <div>
            <p className="text-sm font-semibold text-slate-300">Preferred Sectors</p>
            <p className="text-xs text-slate-500 mt-0.5">
              Select sectors to weight in analysis (leave blank for all).
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {SECTORS.map(sector => {
              const selected = prefs.preferred_sectors.includes(sector)
              return (
                <button
                  key={sector}
                  type="button"
                  onClick={() => toggleSector(sector)}
                  className={[
                    'px-3 py-2 rounded-lg border text-sm font-medium transition-colors',
                    selected
                      ? 'border-blue-500/70 bg-blue-500/15 text-blue-300'
                      : 'border-slate-700 bg-slate-800/60 text-slate-400 hover:border-slate-500 hover:text-slate-200',
                  ].join(' ')}
                >
                  {sector}
                </button>
              )
            })}
          </div>
          {prefs.preferred_sectors.length > 0 && (
            <p className="text-xs text-slate-500">
              {prefs.preferred_sectors.length} sector{prefs.preferred_sectors.length !== 1 ? 's' : ''} selected
            </p>
          )}
        </div>
      </div>

      {/* Save button + feedback */}
      <div className="flex items-center gap-4">
        <button
          onClick={handleSave}
          disabled={saveState === 'saving'}
          className="flex items-center gap-2 px-6 py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {saveState === 'saving' ? (
            <><Loader2 size={15} className="animate-spin" /> Saving…</>
          ) : (
            <><Save size={15} /> Save Preferences</>
          )}
        </button>

        {saveState === 'saved' && (
          <div className="flex items-center gap-2 text-green-400 text-sm">
            <CheckCircle size={16} />
            Preferences saved
          </div>
        )}

        {saveState === 'error' && (
          <div className="flex items-center gap-2 text-red-400 text-sm">
            <AlertCircle size={16} />
            {errorMsg}
          </div>
        )}
      </div>
    </div>
  )
}
