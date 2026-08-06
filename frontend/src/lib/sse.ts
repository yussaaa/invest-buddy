/**
 * Reading Server-Sent Events from a fetch response.
 *
 * The browser's EventSource cannot do this: it is GET-only and takes no body,
 * and a chat turn has to POST its message and screen context. So the stream is
 * read off the response's ReadableStream and framed here.
 *
 * Separate from hooks/useSSE.ts on purpose. That one wraps EventSource for the
 * analysis page, hardcodes the event names it will accept, and captures its
 * callback once per URL — none of which suits this.
 */

export interface SSEMessage {
  event: string
  data: unknown
}

/**
 * Yield each event in an SSE body as it arrives.
 *
 * Frames are separated by a blank line. Servers differ on line endings —
 * sse_starlette emits CRLF — so both are normalised before splitting.
 */
export async function* parseSSEStream(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SSEMessage> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      if (signal?.aborted) return

      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')

      // A frame is only complete once its blank line has arrived; anything
      // after the last one is a partial frame and stays in the buffer.
      let boundary = buffer.indexOf('\n\n')
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const message = parseFrame(frame)
        if (message) yield message
        boundary = buffer.indexOf('\n\n')
      }
    }

    const trailing = parseFrame(buffer)
    if (trailing) yield trailing
  } finally {
    // Releasing matters on the abort path: without it the connection is held
    // open until garbage collection, and a user typing quickly opens several.
    reader.releaseLock()
  }
}

function parseFrame(frame: string): SSEMessage | null {
  let event = ''
  const dataLines: string[] = []

  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }

  if (!event) return null

  const raw = dataLines.join('\n')
  try {
    return { event, data: raw ? JSON.parse(raw) : {} }
  } catch {
    // A frame we cannot parse is dropped rather than ending the stream — the
    // rest of the turn is still worth showing.
    return { event, data: {} }
  }
}
