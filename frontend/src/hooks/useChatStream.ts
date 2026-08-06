/**
 * Drives one chat turn and keeps the transcript.
 *
 * The important subtlety: tokens are streamed before the backend has verified
 * the reply, so a draft that turns out to contain advice or an invented figure
 * is rendered and then replaced when `done` arrives. That is why the assistant
 * message is rewritten from `done.content` rather than being left as the sum of
 * the tokens.
 */

import { useCallback, useRef, useState } from 'react'
import { api, type ChatRequestBody } from '@/lib/api'
import { parseSSEStream } from '@/lib/sse'
import type {
  ChatFlag,
  ChatMessage,
  ChatToolActivity,
  ScreenContextValue,
  SignalSet,
} from '@/lib/types'

let messageCounter = 0
const nextId = () => `m${++messageCounter}`

export function useChatStream() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [signalSet, setSignalSet] = useState<SignalSet | null>(null)
  const [pending, setPending] = useState(false)
  const conversationId = useRef<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const patchLast = useCallback((patch: Partial<ChatMessage>) => {
    setMessages(prev => {
      if (!prev.length) return prev
      const next = [...prev]
      next[next.length - 1] = { ...next[next.length - 1], ...patch }
      return next
    })
  }, [])

  const appendToLast = useCallback((text: string) => {
    setMessages(prev => {
      if (!prev.length) return prev
      const next = [...prev]
      const last = next[next.length - 1]
      next[next.length - 1] = { ...last, content: last.content + text }
      return next
    })
  }, [])

  const send = useCallback(
    async (message: string, screen: ScreenContextValue) => {
      if (!message.trim() || pending) return

      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller

      setPending(true)
      setMessages(prev => [
        ...prev,
        { id: nextId(), role: 'user', content: message },
        { id: nextId(), role: 'assistant', content: '', streaming: true },
      ])

      const body: ChatRequestBody = {
        message,
        conversation_id: conversationId.current,
        screen_context: screen,
      }

      const tools: ChatToolActivity[] = []

      try {
        const response = await api.chat.stream(body, controller.signal)
        if (!response.ok || !response.body) {
          throw new Error(`Chat failed (${response.status})`)
        }

        for await (const { event, data } of parseSSEStream(
          response.body,
          controller.signal,
        )) {
          const payload = data as Record<string, unknown>

          switch (event) {
            case 'conversation':
              conversationId.current = payload.conversation_id as string
              break

            case 'context':
              // Arrives before the first token — the chips render while the
              // model is still writing.
              setSignalSet((payload.signal_set as SignalSet) ?? null)
              break

            case 'tool_call':
              tools.push({
                name: payload.name as string,
                arguments: payload.arguments as Record<string, unknown>,
                success: true,
                latency_ms: 0,
                cache_hit: false,
              })
              patchLast({ tools: [...tools] })
              break

            case 'tool_result': {
              const match = tools.find(t => t.name === payload.name)
              if (match) {
                match.success = payload.success as boolean
                match.latency_ms = payload.latency_ms as number
                match.cache_hit = payload.cache_hit as boolean
                match.error = payload.error as string | null
              }
              patchLast({ tools: [...tools] })
              break
            }

            case 'token':
              appendToLast(payload.text as string)
              break

            case 'flag':
              patchLast({ flags: [payload as unknown as ChatFlag] })
              break

            case 'done':
              // Replace rather than append: what streamed may have been a draft
              // the guardrail rejected.
              patchLast({
                content: payload.content as string,
                streaming: false,
                replaced: Boolean(payload.replaced),
                signalsCited: (payload.signals_cited as string[]) ?? [],
              })
              break

            case 'error':
              patchLast({
                content: (payload.message as string) ?? 'Something went wrong.',
                streaming: false,
                error: true,
              })
              break
          }
        }
      } catch (err) {
        if ((err as Error).name === 'AbortError') return
        patchLast({
          content:
            'I could not reach the agent. The backend may be starting up, or no ' +
            'model provider is configured.',
          streaming: false,
          error: true,
        })
      } finally {
        setPending(false)
      }
    },
    [pending, patchLast, appendToLast],
  )

  const reset = useCallback(() => {
    abortRef.current?.abort()
    const id = conversationId.current
    if (id) void api.chat.clear(id).catch(() => {})
    conversationId.current = null
    setMessages([])
    setSignalSet(null)
    setPending(false)
  }, [])

  return { messages, signalSet, pending, send, reset }
}
