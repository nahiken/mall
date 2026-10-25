/**
 * 共享 SSE 解析（P2-05）。
 *
 * 与 admin 端 `mall-admin-web-master/src/utils/sse.ts` 保持同一契约（两份实现是
 * 因为两个前端不共享构建产物）：
 * - `event` 缺省 'message'；
 * - `data` 能 JSON.parse 就给对象，否则给原始字符串；`[DONE]` 原样给字符串，
 *   由调用方决定收尾；
 * - 没有 data 的块（注释/心跳）丢弃；
 * - `signal` 触发后停止读取并 cancel 底层流，正常返回、不抛 AbortError。
 *
 * 与前端的差异：不用 `instanceof DOMException`（小程序/App 端没有这个全局对象），
 * 只按 error.name 判断。
 */

export interface SseEvent {
  event: string
  data: unknown
}

export interface ParseSseStreamOptions {
  signal?: AbortSignal
}

export function isAbortError(err: unknown): boolean {
  return (err as { name?: string } | null)?.name === 'AbortError'
}

export async function parseSseStream(
  res: { body?: ReadableStream<Uint8Array> | null },
  onEvent: (evt: SseEvent) => void,
  options: ParseSseStreamOptions = {},
): Promise<void> {
  const { signal } = options
  const reader = res.body?.getReader()
  if (!reader) return
  const decoder = new TextDecoder()
  let buf = ''

  const flush = () => {
    const blocks = buf.split(/\r?\n\r?\n/)
    buf = blocks.pop() || ''
    for (const block of blocks) {
      let event = 'message'
      let data: string | null = null
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data = line.slice(5).trim()
      }
      if (data == null) continue
      if (data === '[DONE]') {
        onEvent({ event, data })
        continue
      }
      try {
        onEvent({ event, data: JSON.parse(data) })
      } catch {
        // 非 JSON 帧（错误页/半包）不打断整轮：原样交给调用方，由它忽略
        onEvent({ event, data })
      }
    }
  }

  try {
    for (;;) {
      if (signal?.aborted) break
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      flush()
    }
    if (!signal?.aborted) flush()
  } catch (err) {
    if (isAbortError(err)) return
    throw err
  } finally {
    if (signal?.aborted) {
      try {
        await reader.cancel()
      } catch {
        /* 已关闭，忽略 */
      }
    }
  }
}
