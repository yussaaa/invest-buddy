/**
 * Typed API client — wraps all backend calls.
 * Uses native fetch; no external HTTP library needed.
 */

import type {
  AnalysisResult,
  ChatResponse,
  History,
  MarketBreadth,
  InstrumentProfile,
  MarketEvents,
  MarketMovers,
  MarketOverview,
  PortfolioRisk,
  NewsFeed,
  NewsSettings,
  NewsSources,
  TrendPayload,
  Valuation,
  ValuationExplanation,
  OptionsChain,
  OptionsExplanation,
  OptionsStrategies,
  Quote,
  ScreenContextValue,
  ScreenPayload,
  ScreenPreset,
  SignalSources,
  SocialExplanation,
  SocialSnapshot,
  TickerVol,
  TrendingPayload,
  Technicals,
  TechnicalsExplanation,
  UserPreferences,
  LlmConnectionTest,
  LlmProviderList,
  Watchlist,
} from './types'
import { llmProviderHeaders } from './llmProvider'

const BASE_URL = '/api/v1'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...llmProviderHeaders(),
      ...options?.headers,
    },
  })
  if (!resp.ok) {
    const err = await resp.text()
    throw new Error(`API error ${resp.status}: ${err}`)
  }
  return resp.json() as Promise<T>
}

// ── Analysis ──────────────────────────────────────────────────────────────────

export const api = {
  analysis: {
    trigger: (
      ticker: string,
      query: string,
      userId = 'anonymous',
      depth?: string,
      useRag = false,
      fileIds: string[] = [],
    ) =>
      request<{ run_id: string; status: string; ticker: string; created_at: string }>(
        '/analysis',
        {
          method: 'POST',
          body: JSON.stringify({
            ticker,
            query,
            user_id: userId,
            depth,
            use_rag: useRag,
            file_ids: fileIds,
          }),
        }
      ),

    get: (runId: string) => request<AnalysisResult>(`/analysis/${runId}`),

    history: (userId = 'anonymous', limit = 20) =>
      request<{ total: number; items: AnalysisResult[] }>(
        `/analysis/history?user_id=${userId}&limit=${limit}`
      ),
  },

  upload: {
    file: async (file: File): Promise<{ file_id: string; filename: string; size_bytes: number }> => {
      const formData = new FormData()
      formData.append('file', file)
      const resp = await fetch(`${BASE_URL}/upload`, { method: 'POST', body: formData })
      if (!resp.ok) throw new Error(`Upload failed: ${resp.status}`)
      return resp.json()
    },
  },

  feedback: {
    submit: (runId: string, userId: string, score: 1 | -1, comment?: string) =>
      request('/feedback', {
        method: 'POST',
        body: JSON.stringify({ run_id: runId, user_id: userId, score, comment }),
      }),
  },

  watchlist: {
    getAll: (userId: string) =>
      request<Watchlist[]>(`/watchlist?user_id=${userId}`),

    create: (userId: string, name: string, tickers: string[]) =>
      request<Watchlist>('/watchlist', {
        method: 'POST',
        body: JSON.stringify({ user_id: userId, name, tickers }),
      }),

    update: (id: string, data: Partial<Watchlist>) =>
      request<Watchlist>(`/watchlist/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      }),

    delete: (id: string) =>
      request<{ deleted: string }>(`/watchlist/${id}`, { method: 'DELETE' }),
  },

  preferences: {
    get: (userId: string) =>
      request<UserPreferences>(`/preferences?user_id=${userId}`),

    update: (userId: string, prefs: Partial<UserPreferences>) =>
      request<UserPreferences>(`/preferences?user_id=${userId}`, {
        method: 'PUT',
        body: JSON.stringify(prefs),
      }),

    /** Providers this deployment can use, and the models each would run. */
    llmProviders: () => request<LlmProviderList>('/preferences/llm-providers'),

    /** A real call: a key in .env may still be out of credits or revoked. */
    testLlmProvider: (id: string, fresh = false) =>
      request<LlmConnectionTest>(
        `/preferences/llm-providers/${encodeURIComponent(id)}/test${fresh ? '?fresh=true' : ''}`,
        { method: 'POST' },
      ),
  },

  market: {
    quotes: (symbols: string[], signal?: AbortSignal) =>
      request<{ quotes: Quote[]; as_of: number }>(
        `/market/quotes?symbols=${encodeURIComponent(symbols.join(','))}`,
        { signal }
      ),

    portfolioRisk: (symbols: string[], signal?: AbortSignal) =>
      request<PortfolioRisk>(
        `/market/portfolio-risk?symbols=${encodeURIComponent(symbols.join(','))}`,
        { signal }
      ),

    history: (symbol: string, range: string, signal?: AbortSignal) =>
      request<History>(
        `/market/history?symbol=${encodeURIComponent(symbol)}&range=${range}`,
        { signal }
      ),

    profile: (symbol: string, signal?: AbortSignal) =>
      request<InstrumentProfile>(`/market/profile?symbol=${encodeURIComponent(symbol)}`, { signal }),

    overview: (signal?: AbortSignal) =>
      request<MarketOverview>('/market/overview', { signal }),

    breadth: (signal?: AbortSignal) =>
      request<MarketBreadth>('/market/breadth', { signal }),

    trend: (symbol: string, range = '2y', signal?: AbortSignal) =>
      request<TrendPayload>(
        `/market/trend?symbol=${encodeURIComponent(symbol)}&range=${range}`,
        { signal }
      ),

    valuation: (symbol: string, params: Record<string, number | string> = {}, signal?: AbortSignal) => {
      const query = new URLSearchParams({ symbol, ...Object.fromEntries(
        Object.entries(params).map(([k, v]) => [k, String(v)])
      ) })
      return request<Valuation>(`/market/valuation?${query}`, { signal })
    },

    explainValuation: (
      symbol: string,
      params: Record<string, number | string> = {},
      signal?: AbortSignal
    ) => {
      const query = new URLSearchParams({ symbol, ...Object.fromEntries(
        Object.entries(params).map(([k, v]) => [k, String(v)])
      ) })
      return request<ValuationExplanation>(`/market/valuation/explain?${query}`, { signal })
    },

    technicals: (symbol: string, signal?: AbortSignal) =>
      request<Technicals>(`/market/technicals?symbol=${encodeURIComponent(symbol)}`, { signal }),

    explainTechnicals: (symbol: string, signal?: AbortSignal) =>
      request<TechnicalsExplanation>(
        `/market/technicals/explain?symbol=${encodeURIComponent(symbol)}`,
        { signal }
      ),

    movers: (cap = 'all', limit = 10, signal?: AbortSignal) =>
      request<MarketMovers>(`/market/movers?cap=${cap}&limit=${limit}`, { signal }),

    events: (days = 7, signal?: AbortSignal) =>
      request<MarketEvents>(`/market/events?days=${days}`, { signal }),

    /** One calendar week — offset 0 is this week, -1 last week, +1 next. */
    weekEvents: (offset = 0, cap = 'large', signal?: AbortSignal) =>
      request<MarketEvents>(`/market/events/week?offset=${offset}&cap=${cap}`, { signal }),

    optionsChain: (symbol: string, horizon = 'both', signal?: AbortSignal) =>
      request<OptionsChain>(
        `/market/options/chain?symbol=${encodeURIComponent(symbol)}&horizon=${horizon}`,
        { signal }
      ),

    optionStrategies: (
      symbol: string,
      strategy = 'all',
      limit = 15,
      signal?: AbortSignal
    ) =>
      request<OptionsStrategies>(
        `/market/options/strategies?symbol=${encodeURIComponent(symbol)}` +
          `&strategy=${strategy}&limit=${limit}`,
        { signal }
      ),

    explainOptions: (symbol: string, strategy = 'all', signal?: AbortSignal) =>
      request<OptionsExplanation>(
        `/market/options/explain?symbol=${encodeURIComponent(symbol)}&strategy=${strategy}`,
        { signal }
      ),
  },

  chat: {
    /**
     * Open a streaming turn. Returns the raw Response so the caller can read
     * the body with parseSSEStream — `request` is no use here, it parses the
     * whole body as JSON.
     */
    stream: (body: ChatRequestBody, signal?: AbortSignal) =>
      fetch(`${BASE_URL}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...llmProviderHeaders() },
        body: JSON.stringify(body),
        signal,
      }),

    /** The same turn without streaming — the fallback when the stream fails. */
    send: (body: ChatRequestBody, signal?: AbortSignal) =>
      request<ChatResponse>('/chat', {
        method: 'POST',
        body: JSON.stringify(body),
        signal,
      }),

    clear: (conversationId: string) =>
      fetch(`${BASE_URL}/chat/${conversationId}`, { method: 'DELETE' }),
  },

  signals: {
    sources: (signal?: AbortSignal) => request<SignalSources>('/signals/sources', { signal }),

    trending: (source = 'apewisdom', filter = 'all-stocks', limit = 50, signal?: AbortSignal) =>
      request<TrendingPayload>(
        `/signals/trending?source=${encodeURIComponent(source)}&filter=${encodeURIComponent(filter)}&limit=${limit}`,
        { signal }
      ),

    screeners: (signal?: AbortSignal) =>
      request<{ presets: ScreenPreset[] }>('/signals/screeners', { signal }),

    screen: (preset: string, limit = 25, signal?: AbortSignal) =>
      request<ScreenPayload>(
        `/signals/screen?preset=${encodeURIComponent(preset)}&limit=${limit}`,
        { signal }
      ),

    ticker: (symbol: string, signal?: AbortSignal) =>
      request<SocialSnapshot>(`/signals/ticker?symbol=${encodeURIComponent(symbol)}`, { signal }),

    vol: (symbol: string, signal?: AbortSignal) =>
      request<TickerVol>(`/signals/ticker/vol?symbol=${encodeURIComponent(symbol)}`, { signal }),

    explain: (symbol: string, signal?: AbortSignal) =>
      request<SocialExplanation>(
        `/signals/ticker/explain?symbol=${encodeURIComponent(symbol)}`,
        { signal }
      ),

    /** Raw Response, read with parseSSEStream — like api.chat.stream. */
    researchStream: (body: { ticker: string; question?: string }, signal?: AbortSignal) =>
      fetch(`${BASE_URL}/signals/research/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...llmProviderHeaders() },
        body: JSON.stringify(body),
        signal,
      }),
  },

  news: {
    list: (params: { limit?: number; before?: string; source?: string; category?: string; symbol?: string } = {},
           signal?: AbortSignal) => {
      const query = new URLSearchParams()
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== '') query.set(key, String(value))
      }
      const qs = query.toString()
      return request<NewsFeed>(`/news${qs ? `?${qs}` : ''}`, { signal })
    },

    sources: (signal?: AbortSignal) => request<NewsSources>('/news/sources', { signal }),

    /** Deployment-wide wire settings (the SEC contact), edited in Settings. */
    settings: () => request<NewsSettings>('/news/settings'),

    /** "" clears the stored contact, falling back to the server's .env. */
    updateSettings: (body: { sec_contact: string }) =>
      request<NewsSettings>('/news/settings', { method: 'PUT', body: JSON.stringify(body) }),

    /** Raw Response, read with parseSSEStream. GET, but fetch rather than
     *  EventSource so reconnects carry an up-to-date `since` (see useNewsStream). */
    stream: (since: string | null, signal?: AbortSignal) =>
      fetch(`${BASE_URL}/news/stream${since ? `?since=${encodeURIComponent(since)}` : ''}`, {
        headers: { Accept: 'text/event-stream' },
        signal,
      }),
  },

  health: {
    check: () => request<{ status: string; checks?: Record<string, string> }>('/ready'),
  },
}

export interface ChatRequestBody {
  message: string
  conversation_id?: string | null
  user_id?: string
  screen_context?: ScreenContextValue
}
