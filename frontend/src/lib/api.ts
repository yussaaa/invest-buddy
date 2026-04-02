/**
 * Typed API client — wraps all backend calls.
 * Uses native fetch; no external HTTP library needed.
 */

import type { AnalysisResult, UserPreferences, Watchlist } from './types'

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
    trigger: (ticker: string, query: string, userId = 'anonymous', depth?: string) =>
      request<{ run_id: string; status: string; ticker: string; created_at: string }>(
        '/analysis',
        {
          method: 'POST',
          body: JSON.stringify({ ticker, query, user_id: userId, depth }),
        }
      ),

    get: (runId: string) => request<AnalysisResult>(`/analysis/${runId}`),

    history: (userId = 'anonymous', limit = 20) =>
      request<{ total: number; items: AnalysisResult[] }>(
        `/analysis/history?user_id=${userId}&limit=${limit}`
      ),
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

  health: {
    check: () => request<{ status: string; checks?: Record<string, string> }>('/ready'),
  },
}
