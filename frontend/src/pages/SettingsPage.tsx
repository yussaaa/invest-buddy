import { useEffect, useState, type ReactNode } from 'react'
import { Save, Loader2, CheckCircle, AlertCircle, Cpu, RefreshCw, Newspaper } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { api } from '../lib/api'
import { setLlmProvider } from '../lib/llmProvider'
import type { LlmConnectionTest, LlmProviderList, NewsSettings, UserPreferences } from '../lib/types'
import { version as APP_VERSION } from '../../package.json'

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
  label?: string
  name: string
  options: { value: T; label: string; description?: string; disabled?: boolean; title?: string }[]
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div className="flex flex-col gap-2">
      {label && <Label className="text-sm font-semibold">{label}</Label>}
      <div className="flex flex-wrap gap-2">
        {options.map(opt => (
          <label
            key={opt.value}
            title={opt.title}
            className={cn(
              'flex flex-col gap-0.5 px-4 py-3 rounded-lg border transition-colors select-none',
              opt.disabled
                ? 'border-border/60 bg-card text-muted-foreground/50 cursor-not-allowed'
                : value === opt.value
                  ? 'border-primary/70 bg-primary/10 text-primary cursor-pointer'
                  : 'border-border bg-card text-muted-foreground hover:border-muted-foreground/50 hover:text-foreground cursor-pointer'
            )}
          >
            <input
              type="radio"
              name={name}
              value={opt.value}
              checked={value === opt.value}
              disabled={opt.disabled}
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

function SectionHeader({ title, description }: { title: string; description: ReactNode }) {
  return (
    <div>
      <h2 className="text-base font-semibold text-foreground">{title}</h2>
      <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
    </div>
  )
}

/** The server's message from a 4xx/5xx ("API error 422: {"detail": "..."}"). */
function apiErrorDetail(e: unknown, fallback: string): string {
  if (!(e instanceof Error)) return fallback
  try {
    const detail = JSON.parse(e.message.replace(/^API error \d+: /, '')).detail
    if (typeof detail === 'string') return detail
  } catch {
    // not JSON — fall through to the raw message
  }
  return e.message
}

function SecContactStatus({ settings, typed }: { settings: NewsSettings; typed: string }) {
  const { edgar, sec_contact } = settings
  if (edgar.state === 'live') {
    return (
      <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
        <span className="mt-[5px] h-1.5 w-1.5 shrink-0 rounded-full bg-green-500" />
        <span>
          SEC 8-K filings are on the news wire
          {sec_contact.source === 'env' && !typed && (
            <> · using the contact in the server's <span className="font-mono">.env</span>; saving one here overrides it</>
          )}
        </span>
      </p>
    )
  }
  if (edgar.state === 'not_configured') {
    return (
      <p className="text-xs text-muted-foreground">
        SEC 8-K filings stay off the news wire until a contact is saved.
      </p>
    )
  }
  return (
    <p className="flex items-start gap-1.5 text-xs text-amber-400">
      <AlertCircle size={13} className="mt-px shrink-0" /> {edgar.reason}
    </p>
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
  const [providers, setProviders] = useState<LlmProviderList | null>(null)
  const [tests, setTests] = useState<Record<string, LlmConnectionTest | 'running'>>({})
  const [loading, setLoading] = useState(true)
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [newsSettings, setNewsSettings] = useState<NewsSettings | null>(null)
  const [secContact, setSecContact] = useState('')
  const [contactError, setContactError] = useState<string | null>(null)

  useEffect(() => {
    Promise.allSettled([
      api.preferences.get(USER_ID).then(p => {
        setPrefs(p)
        setLlmProvider(p.llm_provider)
      }),
      api.preferences.llmProviders().then(list => {
        setProviders(list)
        testConnections(list)
      }),
      api.news.settings().then(settings => {
        setNewsSettings(settings)
        setSecContact(settings.sec_contact.value ?? '')
      }),
    ]).finally(() => setLoading(false))
  }, [])

  function testConnections(list: LlmProviderList, fresh = false) {
    for (const p of list.providers.filter(p => p.available)) {
      setTests(t => ({ ...t, [p.id]: 'running' }))
      api.preferences.testLlmProvider(p.id, fresh)
        .then(result => setTests(t => ({ ...t, [p.id]: result })))
        .catch(e => setTests(t => ({
          ...t,
          [p.id]: { id: p.id, ok: false, error: String(e), latency_ms: null },
        })))
    }
  }

  function connectionLabel(id: string, smartModel: string): string {
    const test = tests[id]
    if (test === undefined) return smartModel
    if (test === 'running') return `${smartModel} · testing…`
    if (test.ok) return `${smartModel} · connected ${((test.latency_ms ?? 0) / 1000).toFixed(1)}s`
    return `${smartModel} · not working`
  }

  // A saved choice whose key has since been removed is not what the server
  // uses; show the default it falls back to instead.
  const savedProvider = providers?.providers.find(
    p => p.id === prefs.llm_provider && p.available,
  )
  const selectedProvider = savedProvider?.id ?? providers?.default ?? ''
  const selectedInfo = providers?.providers.find(p => p.id === selectedProvider)
  const selectedTest = tests[selectedProvider]
  const selectedFailure =
    selectedTest && selectedTest !== 'running' && !selectedTest.ok ? selectedTest.error : null

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
    setContactError(null)
    // Server-wide, so saved separately — and only when it changed, since
    // every save restamps a value every replica re-reads.
    const contactChanged =
      newsSettings !== null && secContact.trim() !== (newsSettings.sec_contact.value ?? '')
    if (contactChanged) {
      try {
        const saved = await api.news.updateSettings({ sec_contact: secContact })
        setNewsSettings(saved)
        setSecContact(saved.sec_contact.value ?? '')
      } catch (e) {
        setContactError(apiErrorDetail(e, 'Could not save the SEC contact'))
        setSaveState('idle')
        return
      }
    }
    try {
      const saved = await api.preferences.update(USER_ID, {
        risk_tolerance: prefs.risk_tolerance,
        investment_horizon: prefs.investment_horizon,
        analysis_depth: prefs.analysis_depth,
        preferred_sectors: prefs.preferred_sectors,
        ...(providers ? { llm_provider: selectedProvider } : {}),
      })
      setPrefs(saved)
      setLlmProvider(saved.llm_provider)
      setSaveState('saved')
      setTimeout(() => setSaveState('idle'), 3000)
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : 'Failed to save preferences')
      setSaveState('error')
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-3 text-muted-foreground py-24 justify-center">
        <Loader2 size={20} className="animate-spin" />
        <span>Loading preferences...</span>
      </div>
    )
  }

  return (
    <div className="max-w-3xl mx-auto px-6 py-8 flex flex-col gap-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-foreground">Settings</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Your investment profile, the models that run your analyses, and the news wire.
        </p>
      </div>

      {/* Investment profile */}
      <section className="flex flex-col gap-3">
        <SectionHeader
          title="Investment Profile"
          description="How analyses are weighted to your goals."
        />
        <Card>
          <CardContent className="pt-6 flex flex-col gap-7">

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

            <Separator />

            {/* Investment Horizon */}
            <RadioGroup
              label="Investment Horizon"
              name="investment_horizon"
              value={prefs.investment_horizon}
              onChange={v => setPrefs(p => ({ ...p, investment_horizon: v }))}
              options={[
                { value: 'short', label: 'Short-term', description: '< 1 year' },
                { value: 'medium', label: 'Medium-term', description: '1 - 5 years' },
                { value: 'long', label: 'Long-term', description: '5+ years' },
              ]}
            />

            <Separator />

            {/* Analysis Depth */}
            <RadioGroup
              label="Analysis Depth"
              name="analysis_depth"
              value={prefs.analysis_depth}
              onChange={v => setPrefs(p => ({ ...p, analysis_depth: v }))}
              options={[
                { value: 'quick', label: 'Quick', description: '~15s - fewer agents' },
                { value: 'standard', label: 'Standard', description: '~45s - all agents' },
                { value: 'deep', label: 'Deep', description: '~90s - deep research' },
              ]}
            />

            <Separator />

            {/* Preferred Sectors */}
            <div className="flex flex-col gap-3">
              <div>
                <Label className="text-sm font-semibold">Preferred Sectors</Label>
                <p className="text-xs text-muted-foreground mt-0.5">
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
                      className={cn(
                        'px-3 py-2 rounded-lg border text-sm font-medium transition-colors',
                        selected
                          ? 'border-primary/70 bg-primary/15 text-primary'
                          : 'border-border bg-card text-muted-foreground hover:border-muted-foreground/50 hover:text-foreground'
                      )}
                    >
                      {sector}
                    </button>
                  )
                })}
              </div>
              {prefs.preferred_sectors.length > 0 && (
                <p className="text-xs text-muted-foreground">
                  {prefs.preferred_sectors.length} sector{prefs.preferred_sectors.length !== 1 ? 's' : ''} selected
                </p>
              )}
            </div>
          </CardContent>
        </Card>
      </section>

      {/* Model provider */}
      {providers && (
        <section className="flex flex-col gap-3">
          <SectionHeader
            title="Model Provider"
            description={
              <>
                The language models behind analyses, chat and explanations. Credentials come
                from the server's <span className="font-mono">.env</span>; providers without a
                key are disabled.
              </>
            }
          />
          <Card>
            <CardContent className="pt-6 flex flex-col gap-2">
              <RadioGroup
                name="llm_provider"
                value={selectedProvider}
                onChange={v => setPrefs(p => ({ ...p, llm_provider: v }))}
                options={providers.providers.map(p => ({
                  value: p.id,
                  label: p.id === providers.default ? `${p.label} · default` : p.label,
                  description: p.available ? connectionLabel(p.id, p.smart_model) : 'Not configured',
                  disabled: !p.available,
                  title: (() => {
                    const t = tests[p.id]
                    return p.reason ?? (t && t !== 'running' && !t.ok ? t.error ?? undefined : undefined)
                  })(),
                }))}
              />
              {selectedInfo && (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  <span className="flex items-center gap-1.5">
                    <Cpu size={13} />
                    Fast tier <span className="font-mono text-foreground/80">{selectedInfo.fast_model}</span>
                    · smart tier <span className="font-mono text-foreground/80">{selectedInfo.smart_model}</span>
                  </span>
                  <button
                    type="button"
                    onClick={() => testConnections(providers, true)}
                    className="flex items-center gap-1 hover:text-foreground transition-colors"
                  >
                    <RefreshCw size={12} /> Test connections
                  </button>
                </div>
              )}
              {selectedFailure && (
                <div className="flex items-start gap-2 text-xs text-red-400">
                  <AlertCircle size={14} className="mt-px shrink-0" />
                  <span>
                    This provider's last test call failed, so analyses and chat will fail on it
                    too: <span className="font-mono">{selectedFailure}</span>
                  </span>
                </div>
              )}
            </CardContent>
          </Card>
        </section>
      )}

      {/* News wire — server-wide, not part of the profile */}
      {newsSettings && (
        <section className="flex flex-col gap-3">
          <SectionHeader
            title="News Wire"
            description="Server-wide: these apply to everyone using this deployment, not to your profile."
          />
          <Card>
            <CardContent className="pt-6 flex flex-col gap-3">
              <div>
                <Label htmlFor="sec-contact" className="flex items-center gap-1.5 text-sm font-semibold">
                  <Newspaper size={14} /> Contact for SEC requests
                </Label>
                <p className="text-xs text-muted-foreground mt-0.5">
                  The SEC asks every automated client for a name and email, so it can reach whoever
                  runs it before blocking it. It is sent with each request for 8-K filings and
                  nowhere else. No sign-up needed.
                </p>
              </div>
              <Input
                id="sec-contact"
                value={secContact}
                onChange={e => {
                  setSecContact(e.target.value)
                  setContactError(null)
                }}
                placeholder={
                  newsSettings.sec_contact.source === 'env'
                    ? "Set in the server's .env — type one to override it"
                    : 'e.g. Jane Doe jane@example.com'
                }
                maxLength={200}
                autoComplete="off"
                spellCheck={false}
                aria-invalid={contactError !== null}
                className={cn('max-w-md', contactError && 'border-red-500/70')}
              />
              {contactError ? (
                <p className="flex items-start gap-1.5 text-xs text-red-400">
                  <AlertCircle size={13} className="mt-px shrink-0" /> {contactError}
                </p>
              ) : (
                <SecContactStatus settings={newsSettings} typed={secContact} />
              )}
            </CardContent>
          </Card>
        </section>
      )}

      {/* Save button + feedback */}
      <div className="flex items-center gap-4">
        <Button
          onClick={handleSave}
          disabled={saveState === 'saving'}
        >
          {saveState === 'saving' ? (
            <><Loader2 size={15} className="animate-spin" /> Saving...</>
          ) : (
            <><Save size={15} /> Save Settings</>
          )}
        </Button>

        {saveState === 'saved' && (
          <div className="flex items-center gap-2 text-green-400 text-sm">
            <CheckCircle size={16} />
            Settings saved
          </div>
        )}

        {saveState === 'error' && (
          <div className="flex items-center gap-2 text-red-400 text-sm">
            <AlertCircle size={16} />
            {errorMsg}
          </div>
        )}
      </div>

      {/* About — read from package.json so there is one place to bump it. */}
      <p className="border-t border-border pt-4 text-xs text-muted-foreground/60">
        Agent Invest · version {APP_VERSION}
      </p>
    </div>
  )
}
