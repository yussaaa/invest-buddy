/**
 * Typed API client — wraps all backend calls.
 * Uses native fetch; no external HTTP library needed.
 */

import type {
  AnalysisResult,
  Heatmap,
  History,
  MarketBreadth,
  InstrumentProfile,
  MarketEvents,
  MarketOverview,
  Quote,
  Technicals,
  TechnicalsExplanation,
  UserPreferences,
  Watchlist,
} from './types'

const BASE_URL = '/api/v1'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
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
  },

  market: {
    quotes: (symbols: string[], signal?: AbortSignal) =>
      request<{ quotes: Quote[]; as_of: number }>(
        `/market/quotes?symbols=${encodeURIComponent(symbols.join(','))}`,
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

    heatmap: (index: string, range: string, limit = 150, signal?: AbortSignal) =>
      request<Heatmap>(
        `/market/heatmap?index=${index}&range=${range}&limit=${limit}`,
        { signal }
      ),

    technicals: (symbol: string, signal?: AbortSignal) =>
      request<Technicals>(`/market/technicals?symbol=${encodeURIComponent(symbol)}`, { signal }),

    explainTechnicals: (symbol: string, signal?: AbortSignal) =>
      request<TechnicalsExplanation>(
        `/market/technicals/explain?symbol=${encodeURIComponent(symbol)}`,
        { signal }
      ),

    events: (days = 7, signal?: AbortSignal) =>
      request<MarketEvents>(`/market/events?days=${days}`, { signal }),
  },

  health: {
    check: () => request<{ status: string; checks?: Record<string, string> }>('/ready'),
  },
}
