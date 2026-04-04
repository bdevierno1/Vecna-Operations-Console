/**
 * Strands/LiteLLM emit one WebSocket row per streaming delta; tool `input` JSON
 * arrives in many chunks. Merge consecutive rows so the timeline shows one line.
 *
 * Strands also often emits the same reasoning twice (e.g. wrapped `event` chunk and
 * top-level `reasoningText`) — we collapse consecutive rows with identical `human`.
 */

export type StreamEntry = { human: string; raw: string }

const TOOL_PREFIX = 'Tool args:'
const TEXT_PREFIX = 'Text:'

/** Strands interleaves these between streaming chunks; they break naive "merge last two" logic. */
function isNoiseLine(human: string): boolean {
  const h = human.trim()
  return h === 'Stream event' || h === 'Model output (delta)'
}

/**
 * Merge streaming text deltas. Providers may send:
 * - **Cumulative** full text so far (use prefix rule),
 * - **Incremental** fragments (concatenate),
 * - **Overlapping** or repeated phrases (longest suffix/prefix overlap),
 * - **Incremental without spaces** between tokens (`The` + `user` → insert a space at word boundary).
 */
function mergeStreamingBody(a: string, b: string): string {
  if (!a) return b
  if (!b) return a
  if (b.startsWith(a)) return b
  if (a.startsWith(b)) return a
  const maxK = Math.min(a.length, b.length)
  for (let k = maxK; k > 0; k--) {
    if (a.slice(-k) === b.slice(0, k)) return a + b.slice(k)
  }
  // Incremental chunks with no leading space on the next fragment
  if (/[\w)]$/.test(a) && /^[\w(]/.test(b)) return `${a} ${b}`
  return a + b
}

/** Body after "Reasoning:" — merge cumulative or incremental deltas. */
function mergeReasoningBodies(lastLine: string, nextLine: string): { combined: string } | null {
  const ra = /^Reasoning:\s*(.*)$/s.exec(lastLine)
  const rb = /^Reasoning:\s*(.*)$/s.exec(nextLine)
  if (!ra || !rb) return null
  return { combined: mergeStreamingBody(ra[1], rb[1]) }
}

/** Merge consecutive entries with the same display line (duplicate Strands emissions). */
function collapseDuplicateHumans(entries: StreamEntry[]): StreamEntry[] {
  const out: StreamEntry[] = []
  for (const e of entries) {
    const last = out[out.length - 1]
    if (last && last.human === e.human) {
      out[out.length - 1] = { human: last.human, raw: `${last.raw}\n---\n${e.raw}` }
    } else {
      out.push(e)
    }
  }
  return out
}

/** Pretty-print when valid JSON; otherwise return full fragment (streaming may be incomplete). */
function tryPrettyJson(concatenated: string): string {
  const s = concatenated.trim()
  try {
    return JSON.stringify(JSON.parse(s))
  } catch {
    return concatenated.length > 80_000 ? `${concatenated.slice(0, 80_000)}…` : concatenated
  }
}

/** Append one entry, merging with the previous row when both are tool-arg or text deltas. */
export function mergeAdjacentStreamDeltas(prev: StreamEntry[], next: StreamEntry): StreamEntry[] {
  if (prev.length === 0) return [next]
  const last = prev[prev.length - 1]

  if (last.human.startsWith(TOOL_PREFIX) && next.human.startsWith(TOOL_PREFIX)) {
    // Do not .trim() each fragment — spaces matter inside JSON strings.
    const a = last.human.slice(TOOL_PREFIX.length)
    const b = next.human.slice(TOOL_PREFIX.length)
    const combined = a + b
    const human = `${TOOL_PREFIX} ${tryPrettyJson(combined)}`
    return [...prev.slice(0, -1), { human, raw: `${last.raw}\n---\n${next.raw}` }]
  }

  if (last.human.startsWith(TEXT_PREFIX) && next.human.startsWith(TEXT_PREFIX)) {
    const a = last.human.slice(TEXT_PREFIX.length)
    const b = next.human.slice(TEXT_PREFIX.length)
    const combined = mergeStreamingBody(a, b)
    const human =
      combined.length > 80_000 ? `${TEXT_PREFIX} ${combined.slice(0, 80_000)}…` : `${TEXT_PREFIX} ${combined}`
    return [...prev.slice(0, -1), { human, raw: `${last.raw}\n---\n${next.raw}` }]
  }

  const rr = mergeReasoningBodies(last.human, next.human)
  if (rr) {
    const { combined } = rr
    const human =
      combined.length > 80_000 ? `Reasoning: ${combined.slice(0, 80_000)}…` : `Reasoning: ${combined}`
    return [...prev.slice(0, -1), { human, raw: `${last.raw}\n---\n${next.raw}` }]
  }

  return [...prev, next]
}

/** Merge tool/text chunks, then collapse duplicate human lines (same tick from Strands). */
export function appendStreamEntry(prev: StreamEntry[], next: StreamEntry): StreamEntry[] {
  if (prev.length > 0 && isNoiseLine(next.human)) {
    const last = prev[prev.length - 1]
    const folded: StreamEntry[] = [
      ...prev.slice(0, -1),
      { human: last.human, raw: `${last.raw}\n---\n${next.raw}` },
    ]
    return collapseDuplicateHumans(folded)
  }
  return collapseDuplicateHumans(mergeAdjacentStreamDeltas(prev, next))
}

/** Fold a full list (e.g. replay from DB) so stored deltas collapse the same way. */
export function foldStreamEntries(entries: StreamEntry[]): StreamEntry[] {
  return entries.reduce((acc: StreamEntry[], e) => appendStreamEntry(acc, e), [])
}
