/**
 * 共享 SSE 解析（P2-05）。
 *
 * 为什么抽出来：admin 的 chat 与 app 的 stores/agent.ts 各写了一份几乎相同的
 * "按空行切块 → 取 event:/data: → JSON.parse → [DONE] 哨兵"逻辑。协议细节（空行
 * 分块、\r\n、data 跨多行、[DONE] 是裸字符串）任何一处改动都要改两遍，漏一处就是
 * 一端正常另一端卡住。这里定一处契约，两端只负责"事件怎么用"。
 *
 * 契约：
 * - `event` 缺省为 'message'（SSE 规范默认值，原实现也是这个默认值）；
 * - `data` 能 JSON.parse 就给对象，否则给原始字符串；`[DONE]` 一律原样给字符串，
 *   由调用方决定怎么收尾（admin 只置 finished，app 还要看 event 名）；
 * - 一个 data 都没有的块直接丢弃（心跳/注释行）；
 * - `signal` 触发后停止读取并 cancel 底层流，函数正常返回、不抛 AbortError。
 */

export interface SseEvent {
  event: string
  data: unknown
}

export interface ParseSseStreamOptions {
  /** 离开页面/取消时终止读取；终止不抛异常，调用方靠返回值自行收尾 */
  signal?: AbortSignal
}

/** 该错误是否是"主动取消"（fetch 被 abort 时浏览器抛 DOMException/AbortError） */
export function isAbortError(err: unknown): boolean {
  if (err instanceof DOMException) return err.name === 'AbortError'
  return (err as { name?: string } | null)?.name === 'AbortError'
}

export async function parseSseStream(
  res: Response,
  onEvent: (evt: SseEvent) => void,
  options: ParseSseStreamOptions = {}
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
    // 正常 EOF 时把最后半个块也处理掉；主动取消时不等 server 收尾，直接丢
    if (!signal?.aborted) flush()
  } catch (err) {
    if (isAbortError(err)) return
    throw err
  } finally {
    if (signal?.aborted) {
      // 释放底层连接，避免离开页面后服务端还在跑这一轮（P2-05 的验收点）
      try {
        await reader.cancel()
      } catch {
        /* 已经关闭/取消过，忽略 */
      }
    }
  }
}
