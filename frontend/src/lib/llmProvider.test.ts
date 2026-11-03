import { beforeEach, describe, expect, it } from 'vitest'
import { peek, put } from './clientCache'
import { getLlmProvider, llmProviderHeaders, setLlmProvider } from './llmProvider'

describe('llmProvider', () => {
  beforeEach(() => setLlmProvider(null))

  it('sends no header for the server default', () => {
    expect(getLlmProvider()).toBeNull()
    expect(llmProviderHeaders()).toEqual({})
  })

  it('sends the chosen provider', () => {
    setLlmProvider('anthropic')
    expect(llmProviderHeaders()).toEqual({ 'X-LLM-Provider': 'anthropic' })
  })

  it('treats an empty choice as the default', () => {
    setLlmProvider('anthropic')
    setLlmProvider('')
    expect(llmProviderHeaders()).toEqual({})
  })

  it('drops generated explanations when the provider changes, and only those', () => {
    put('technicals:explain:AAPL', { explanation: 'old' })
    put('technicals:AAPL:1Y', { rsi: 50 })
    setLlmProvider('anthropic')
    expect(peek('technicals:explain:AAPL').value).toBeUndefined()
    expect(peek('technicals:AAPL:1Y').value).toEqual({ rsi: 50 })
  })

  it('keeps explanations when the choice is unchanged', () => {
    setLlmProvider('anthropic')
    put('valuation:explain:AAPL:{}', { explanation: 'kept' })
    setLlmProvider('anthropic')
    expect(peek('valuation:explain:AAPL:{}').value).toEqual({ explanation: 'kept' })
  })
})
