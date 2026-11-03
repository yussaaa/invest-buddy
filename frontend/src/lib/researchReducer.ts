/**
 * State for one research run, driven by SSE events — pure, so it is testable.
 *
 * The same subtlety as the chat stream: tokens arrive before verify, so the
 * draft is shown while it streams and then *replaced* by `done.brief`. When
 * `done.replaced` is true the draft failed review and must not linger.
 */

import type { ChatFlag, ResearchBrief, ResearchSource, SignalSet } from './types'

export type ResearchPhase = 'idle' | 'gathering' | 'writing' | 'done' | 'error'

export interface ResearchToolStep {
  name: string
  done: boolean
  success?: boolean
}

export interface ResearchState {
  phase: ResearchPhase
  ticker: string | null
  question: string | null
  cached: boolean
  sources: ResearchSource[]
  socialSignals: SignalSet | null
  tools: ResearchToolStep[]
  draft: string
  brief: ResearchBrief | null
  replaced: boolean
  flags: ChatFlag[]
  error: string | null
  latencyMs: number | null
}

export const initialResearchState: ResearchState = {
  phase: 'idle',
  ticker: null,
  question: null,
  cached: false,
  sources: [],
  socialSignals: null,
  tools: [],
  draft: '',
  brief: null,
  replaced: false,
  flags: [],
  error: null,
  latencyMs: null,
}

export type ResearchAction =
  | { type: 'reset' }
  | { type: 'begin'; ticker: string; question: string | null }
  | { type: 'event'; event: string; data: Record<string, unknown> }
  | { type: 'fail'; message: string }

export function researchReducer(state: ResearchState, action: ResearchAction): ResearchState {
  switch (action.type) {
    case 'reset':
      return initialResearchState
    case 'begin':
      return { ...initialResearchState, phase: 'gathering', ticker: action.ticker, question: action.question }
    case 'fail':
      return { ...state, phase: 'error', error: action.message }
    case 'event':
      return applyEvent(state, action.event, action.data)
  }
}

function applyEvent(state: ResearchState, event: string, data: Record<string, unknown>): ResearchState {
  switch (event) {
    case 'start':
      return {
        ...state,
        cached: Boolean(data.cached),
        question: (data.question as string) ?? state.question,
      }
    case 'context':
      return { ...state, socialSignals: (data.social_signal_set as SignalSet) ?? null }
    case 'sources':
      return { ...state, sources: (data.sources as ResearchSource[]) ?? state.sources }
    case 'tool_call':
      return { ...state, tools: [...state.tools, { name: data.name as string, done: false }] }
    case 'tool_result': {
      const index = state.tools.findIndex(t => t.name === data.name && !t.done)
      if (index === -1) return state
      const tools = [...state.tools]
      tools[index] = { ...tools[index], done: true, success: Boolean(data.success) }
      return { ...state, tools }
    }
    case 'token':
      return { ...state, phase: 'writing', draft: state.draft + ((data.text as string) ?? '') }
    case 'flag':
      return { ...state, flags: [...state.flags, data as unknown as ChatFlag] }
    case 'done': {
      const brief = (data.brief as ResearchBrief) ?? null
      return {
        ...state,
        phase: 'done',
        brief,
        sources: brief?.sources ?? state.sources,
        replaced: Boolean(data.replaced),
        // The streamed text was a draft; once the brief is in, it is gone.
        draft: '',
        flags: brief?.flags ?? state.flags,
        cached: state.cached || Boolean(data.cached),
        latencyMs: (data.latency_ms as number) ?? null,
      }
    }
    case 'error':
      return { ...state, phase: 'error', error: (data.message as string) ?? 'Research failed.' }
    default:
      return state
  }
}
