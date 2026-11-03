/**
 * The model provider this browser asked for on the Settings page.
 *
 * Sent as `X-LLM-Provider` on every API call (lib/api.ts). The backend checks
 * it against the providers that have credentials and otherwise uses its own
 * default, so a stale or unknown value here costs nothing. The value is saved
 * in the user's preferences on the server, and copied to localStorage so the
 * very first request after a reload already carries it.
 */

import { invalidate } from './clientCache'

const STORAGE_KEY = 'agent-invest:llm-provider'
export const LLM_PROVIDER_HEADER = 'X-LLM-Provider'

// Explanations are generated text: one written by another provider should
// not be shown under the new choice. Their server-side cache keys already
// include the provider.
const GENERATED_PREFIXES = ['valuation:explain:', 'technicals:explain:', 'options:explain:']

let current: string | null = read()

function read(): string | null {
  try {
    return globalThis.localStorage?.getItem(STORAGE_KEY) || null
  } catch {
    return null
  }
}

/** The chosen provider, or null for the server default. */
export function getLlmProvider(): string | null {
  return current
}

export function setLlmProvider(provider: string | null | undefined): void {
  const next = provider || null
  if (next === current) return
  current = next
  try {
    if (next) globalThis.localStorage?.setItem(STORAGE_KEY, next)
    else globalThis.localStorage?.removeItem(STORAGE_KEY)
  } catch {
    // Private mode or blocked storage: the in-memory value still applies.
  }
  for (const prefix of GENERATED_PREFIXES) invalidate(prefix)
}

/** Headers to merge into a request; empty when using the server default. */
export function llmProviderHeaders(): Record<string, string> {
  return current ? { [LLM_PROVIDER_HEADER]: current } : {}
}
