import { type ComponentProps, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Download, History, Radio, RotateCcw, Shield, Zap } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { apiUrl, wsUrl } from '@/lib/apiBase'
import { appendStreamEntry, foldStreamEntries, type StreamEntry } from '@/lib/streamMerge'
import { cn } from '@/lib/utils'

const BILLING_SESSION_KEY = 'vecna_billing_session_id'

type BillableBreakdown = {
  llm_cost_usd: number
  scan_fee_usd: number
  tool_fee_usd: number
  tool_units: number
  billable_total_usd: number
  tool_calls_by_tool?: Record<string, number>
  tool_fee_by_family?: Record<string, number>
  tool_fee_detail?: Array<{
    tool: string
    calls: number
    fee_usd: number
    family?: string | null
    pricing: string
  }>
  pricing_mode?: string
}

type ToolPricingConfig = {
  tiered_config_active: boolean
  families: Record<string, number>
  tool_to_family: Record<string, string>
  overrides: Record<string, number>
  legacy_flat_fee_per_call_usd: number
  default_families: string[]
}

type SessionBilling = {
  session_id: string
  total_cost_usd: number
  total_scan_fees_usd?: number
  total_tool_fees_usd?: number
  total_tool_units?: number
  total_billable_usd?: number
  total_credits: number
  total_input_tokens: number
  total_output_tokens: number
  operation_count: number
  wall_time_seconds: number
  model_usage: Record<string, { inputTokens?: number; outputTokens?: number; costUSD?: number }>
}

type Finding = {
  severity?: string
  title: string
  detail?: string
}

type OperationRow = {
  id: string
  target_url: string
  status: string
  credits_used: number
  cost_usd?: number
  billable_total_usd?: number
  input_tokens?: number
  output_tokens?: number
  duration_seconds?: number | null
  created_at: string
  error_message?: string | null
  session_id?: string | null
}

function fmtDuration(s: number | null | undefined): string {
  if (s == null) return '—'
  if (s < 60) return `${s.toFixed(1)}s`
  const m = Math.floor(s / 60)
  const rem = (s % 60).toFixed(0).padStart(2, '0')
  return `${m}m ${rem}s`
}

function fmtCost(usd: number): string {
  if (usd === 0) return '$0'
  return usd > 0.5 ? `$${usd.toFixed(2)}` : `$${usd.toFixed(4)}`
}

function fmtTokens(n: number): string {
  if (n === 0) return '0'
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

function fmtHistoryWhen(iso: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
  } catch {
    return iso
  }
}

function severityVariant(s?: string): ComponentProps<typeof Badge>['variant'] {
  const x = (s || '').toLowerCase()
  if (x === 'critical' || x === 'high') return 'destructive'
  if (x === 'medium') return 'warning'
  if (x === 'low' || x === 'info') return 'outline'
  return 'default'
}

function formatEvent(ev: unknown): string {
  try {
    return JSON.stringify(ev, null, 2)
  } catch {
    return String(ev)
  }
}

function eventToStreamEntry(ev: unknown): StreamEntry {
  if (ev && typeof ev === 'object' && !Array.isArray(ev)) {
    const o = ev as Record<string, unknown>
    const human =
      typeof o.human_line === 'string'
        ? o.human_line
        : typeof o.type === 'string' && o.type === 'tool_lifecycle'
          ? `Tool ${String(o.tool ?? '')} · ${String(o.phase ?? '')}`
          : typeof o.type === 'string' && o.type === 'agent_result'
            ? 'Agent cycle result'
            : 'Event'
    return { human, raw: formatEvent(ev) }
  }
  return { human: 'Event', raw: formatEvent(ev) }
}

export default function App() {
  const [targetUrl, setTargetUrl] = useState('https://example.com')
  const [ops, setOps] = useState<OperationRow[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [liveLines, setLiveLines] = useState<StreamEntry[]>([])
  const [findings, setFindings] = useState<Finding[]>([])
  const [costUsd, setCostUsd] = useState(0)
  const [inputTokens, setInputTokens] = useState(0)
  const [outputTokens, setOutputTokens] = useState(0)
  const [durationSeconds, setDurationSeconds] = useState<number | null>(null)
  const [summary, setSummary] = useState('')
  const [status, setStatus] = useState<string>('idle')
  const [error, setError] = useState<string | null>(null)
  const [billingSessionId, setBillingSessionId] = useState<string | null>(null)
  const [sessionBilling, setSessionBilling] = useState<SessionBilling | null>(null)
  const [billingVisible, setBillingVisible] = useState(true)
  const [publicModelId, setPublicModelId] = useState<string>('')
  const [modelPricingDisplay, setModelPricingDisplay] = useState<string | null>(null)
  const [feeScanUsd, setFeeScanUsd] = useState<number>(0)
  const [feeToolUnitUsd, setFeeToolUnitUsd] = useState<number>(0)
  const [, setToolPricing] = useState<ToolPricingConfig | null>(null)
  const [costEstimateUnknown, setCostEstimateUnknown] = useState(false)
  const [runBillableUsd, setRunBillableUsd] = useState(0)
  const [runBreakdown, setRunBreakdown] = useState<BillableBreakdown | null>(null)
  const [wsReconnecting, setWsReconnecting] = useState(false)
  const [healthStatus, setHealthStatus] = useState<'ok' | 'degraded' | 'error' | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const wsReconnectAttemptsRef = useRef(0)
  const wsReconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wsPingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const wsOperationIdRef = useRef<string | null>(null)
  const deepLinkHandledRef = useRef(false)
  // Holds the latest connectWs so the reconnect timer can call it without the
  // callback referencing itself before declaration.
  const connectWsRef = useRef<((operationId: string) => void) | null>(null)

  // WebSocket reconnect / ping tuning
  const WS_MAX_RECONNECT_ATTEMPTS = 5
  const WS_BASE_RECONNECT_MS = 2000
  const WS_PING_INTERVAL_MS = 30000

  const refreshSessionBilling = useCallback(async (sid: string) => {
    const r = await fetch(apiUrl(`/api/billing/sessions/${sid}`))
    if (!r.ok) return
    setSessionBilling((await r.json()) as SessionBilling)
  }, [])

  useEffect(() => {
    void (async () => {
      let sid = localStorage.getItem(BILLING_SESSION_KEY)
      if (!sid) {
        const r = await fetch(apiUrl('/api/billing/sessions'), { method: 'POST' })
        if (!r.ok) return
        const j = (await r.json()) as { session_id: string }
        sid = j.session_id
        localStorage.setItem(BILLING_SESSION_KEY, sid)
      }
      setBillingSessionId(sid)
      await refreshSessionBilling(sid)
    })()
  }, [refreshSessionBilling])

  const loadOps = useCallback(async () => {
    const r = await fetch(apiUrl('/api/operations'))
    if (!r.ok) return
    const data = (await r.json()) as OperationRow[]
    setOps(data)
  }, [])

  useEffect(() => {
    void (async () => {
      await loadOps()
    })()
  }, [loadOps])

  useEffect(() => {
    void (async () => {
      const r = await fetch(apiUrl('/api/config'))
      if (!r.ok) return
      const cfg = (await r.json()) as {
        litellm_model_id: string
        model_pricing_display: string | null
        billing_visible: boolean
        vecna_scan_fee_usd?: number
        vecna_tool_unit_fee_usd?: number
        tool_pricing?: ToolPricingConfig
      }
      setPublicModelId(cfg.litellm_model_id)
      setModelPricingDisplay(cfg.model_pricing_display)
      setBillingVisible(cfg.billing_visible !== false)
      setFeeScanUsd(typeof cfg.vecna_scan_fee_usd === 'number' ? cfg.vecna_scan_fee_usd : 0)
      setFeeToolUnitUsd(typeof cfg.vecna_tool_unit_fee_usd === 'number' ? cfg.vecna_tool_unit_fee_usd : 0)
      setToolPricing(cfg.tool_pricing ?? null)
    })()
  }, [])

  useEffect(() => {
    void (async () => {
      try {
        const r = await fetch(apiUrl('/api/health/deep'))
        if (r.ok) {
          const h = (await r.json()) as { status: string }
          setHealthStatus(h.status === 'ok' ? 'ok' : h.status === 'degraded' ? 'degraded' : 'error')
        }
      } catch {
        setHealthStatus('error')
      }
    })()
  }, [])

  const _clearWsTimers = useCallback(() => {
    if (wsPingIntervalRef.current) { clearInterval(wsPingIntervalRef.current); wsPingIntervalRef.current = null }
    if (wsReconnectTimerRef.current) { clearTimeout(wsReconnectTimerRef.current); wsReconnectTimerRef.current = null }
  }, [])

  const connectWs = useCallback(
    (operationId: string) => {
      // Switching to a new operation — reset reconnect state
      if (wsOperationIdRef.current !== operationId) {
        wsReconnectAttemptsRef.current = 0
        wsOperationIdRef.current = operationId
      }
      _clearWsTimers()
      wsRef.current?.close()

      const ws = new WebSocket(wsUrl(`/ws/operations/${operationId}`))
      wsRef.current = ws

      ws.onopen = () => {
        // Successful (re)connection — clear banner; stale onclose handlers must not leave this stuck true.
        setWsReconnecting(false)
        wsReconnectAttemptsRef.current = 0
      }

      // Ping keepalive so intermediaries do not drop an idle connection
      wsPingIntervalRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }))
        }
      }, WS_PING_INTERVAL_MS)

      ws.onmessage = (m) => {
        try {
          const msg = JSON.parse(m.data as string) as Record<string, unknown>
          if (msg.type === 'replay' && Array.isArray(msg.events)) {
            const evs = msg.events as unknown[]
            setLiveLines((prev) =>
              foldStreamEntries([...prev, ...evs.map((e) => eventToStreamEntry(e))]),
            )
          }
          if (msg.type === 'stream' && msg.event) {
            setLiveLines((prev) => appendStreamEntry(prev, eventToStreamEntry(msg.event)))
          }
          if (msg.type === 'credit') {
            if (typeof msg.cost_usd === 'number') setCostUsd(msg.cost_usd)
            if (typeof msg.input_tokens === 'number') setInputTokens(msg.input_tokens)
            if (typeof msg.output_tokens === 'number') setOutputTokens(msg.output_tokens)
          }
          if (msg.type === 'findings' && Array.isArray(msg.items)) {
            setFindings(msg.items as Finding[])
          }
          if (msg.type === 'done') {
            setStatus('completed')
            setWsReconnecting(false)
            wsReconnectAttemptsRef.current = 0
            if (typeof msg.cost_usd === 'number') setCostUsd(msg.cost_usd)
            if (typeof msg.billable_total_usd === 'number') setRunBillableUsd(msg.billable_total_usd)
            if (msg.billable_breakdown && typeof msg.billable_breakdown === 'object') {
              setRunBreakdown(msg.billable_breakdown as BillableBreakdown)
            }
            if (typeof msg.input_tokens === 'number') setInputTokens(msg.input_tokens)
            if (typeof msg.output_tokens === 'number') setOutputTokens(msg.output_tokens)
            if (typeof msg.duration_seconds === 'number') setDurationSeconds(msg.duration_seconds)
            if (typeof msg.cost_estimate_unknown === 'boolean') setCostEstimateUnknown(msg.cost_estimate_unknown)
            if (msg.session_billing && typeof msg.session_billing === 'object') {
              setSessionBilling(msg.session_billing as SessionBilling)
            }
            if (typeof msg.summary === 'string') setSummary(msg.summary)
            if (Array.isArray(msg.findings)) setFindings(msg.findings as Finding[])
            void loadOps()
          }
          if (msg.type === 'retry') {
            const et = String(msg.error_type || '')
            const txt = String(msg.message || `Retrying (attempt ${String(msg.attempt)})…`)
            setLiveLines((prev) =>
              appendStreamEntry(prev, {
                human: `⚡ ${et} — ${txt}`,
                raw: JSON.stringify(msg, null, 2),
              }),
            )
          }
          if (msg.type === 'cancelled') {
            setStatus('cancelled')
            setWsReconnecting(false)
            void loadOps()
          }
          if (msg.type === 'error') {
            const et = msg.error_type ? ` [${String(msg.error_type)}]` : ''
            setError(String(msg.message || 'Error') + et)
            setStatus('failed')
            setWsReconnecting(false)
            void loadOps()
          }
        } catch {
          /* ignore */
        }
      }

      ws.onerror = () => {
        // Don't surface as an error if we're reconnecting — will show reconnecting state instead
      }

      ws.onclose = () => {
        // Replacing the socket (new op or reconnect) leaves the old socket to close async; ignore that close
        // so we don't null wsRef, clear the new ping timer, or show a bogus reconnecting state.
        if (wsRef.current !== ws) {
          return
        }
        _clearWsTimers()
        wsRef.current = null
        // Only try to reconnect while operation is still running/pending
        // and we haven't hit the attempt cap
        const terminalStatuses = ['completed', 'failed', 'cancelled', 'idle']
        setStatus((current) => {
          if (
            !terminalStatuses.includes(current) &&
            wsReconnectAttemptsRef.current < WS_MAX_RECONNECT_ATTEMPTS &&
            wsOperationIdRef.current === operationId
          ) {
            const attempt = wsReconnectAttemptsRef.current + 1
            wsReconnectAttemptsRef.current = attempt
            // Exponential backoff: 2s, 4s, 8s, 16s, 32s
            const delay = Math.min(WS_BASE_RECONNECT_MS * Math.pow(2, attempt - 1), 32000)
            setWsReconnecting(true)
            wsReconnectTimerRef.current = setTimeout(() => {
              connectWsRef.current?.(operationId)
            }, delay)
          } else if (
            !terminalStatuses.includes(current) &&
            wsOperationIdRef.current === operationId
          ) {
            setError('Connection lost — could not reconnect.')
            return 'failed'
          }
          return current
        })
      }
    },
    [loadOps, _clearWsTimers, WS_MAX_RECONNECT_ATTEMPTS, WS_BASE_RECONNECT_MS, WS_PING_INTERVAL_MS],
  )

  // Keep the reconnect timer pointed at the latest connectWs.
  useEffect(() => {
    connectWsRef.current = connectWs
  }, [connectWs])

  const startOperation = async () => {
    setError(null)
    setLiveLines([])
    setFindings([])
    setCostUsd(0)
    setRunBillableUsd(0)
    setRunBreakdown(null)
    setInputTokens(0)
    setOutputTokens(0)
    setDurationSeconds(null)
    setCostEstimateUnknown(false)
    setSummary('')
    setStatus('running')
    const r = await fetch(apiUrl('/api/operations'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target_url: targetUrl,
        session_id: billingSessionId ?? undefined,
      }),
    })
    if (!r.ok) {
      const j = await r.json().catch(() => ({}))
      setError((j as { detail?: string }).detail || r.statusText)
      setStatus('idle')
      return
    }
    const { id } = (await r.json()) as { id: string }
    setSelectedId(id)
    connectWs(id)
    void loadOps()
    if (billingSessionId) void refreshSessionBilling(billingSessionId)
  }

  const cancelOperation = async () => {
    if (!selectedId) return
    const r = await fetch(apiUrl(`/api/operations/${selectedId}/cancel`), { method: 'POST' })
    if (!r.ok) {
      const j = await r.json().catch(() => ({}))
      setError((j as { detail?: string }).detail || 'Could not cancel operation.')
      return
    }
    setStatus('cancelled')
    _clearWsTimers()
    wsRef.current?.close()
  }

  const forceError = async (errorType = 'rate_limit') => {
    setError(null)
    setLiveLines([])
    setFindings([])
    setCostUsd(0)
    setRunBillableUsd(0)
    setRunBreakdown(null)
    setInputTokens(0)
    setOutputTokens(0)
    setDurationSeconds(null)
    setCostEstimateUnknown(false)
    setSummary('')
    setStatus('running')
    const r = await fetch(apiUrl(`/api/demo/force-error?error_type=${encodeURIComponent(errorType)}`), {
      method: 'POST',
    })
    if (!r.ok) {
      const j = await r.json().catch(() => ({}))
      setError((j as { detail?: string }).detail || r.statusText)
      setStatus('idle')
      return
    }
    const { id } = (await r.json()) as { id: string }
    setSelectedId(id)
    connectWs(id)
  }

  const openPastOperation = useCallback(async (id: string) => {
    setError(null)
    setSelectedId(id)
    setLiveLines([])
    setFindings([])
    setCostUsd(0)
    setRunBillableUsd(0)
    setRunBreakdown(null)
    setInputTokens(0)
    setOutputTokens(0)
    setDurationSeconds(null)
    setCostEstimateUnknown(false)
    setSummary('')
    const r = await fetch(apiUrl(`/api/operations/${id}`))
    if (!r.ok) return
    const row = (await r.json()) as {
      target_url: string
      status: string
      events_json: unknown[] | null
      findings_json: Finding[] | null
      cost_usd: number
      scan_fee_usd?: number
      tool_fee_usd?: number
      tool_units?: number
      billable_total_usd?: number
      pricing_breakdown?: BillableBreakdown | null
      input_tokens: number
      output_tokens: number
      duration_seconds: number | null
      cost_estimate_unknown?: boolean
      summary_text: string | null
      error_message: string | null
    }
    setTargetUrl(row.target_url || '')
    setStatus(row.status)
    setCostUsd(row.cost_usd || 0)
    const llm = row.cost_usd || 0
    const scan = row.scan_fee_usd ?? 0
    const tf = row.tool_fee_usd ?? 0
    const tu = row.tool_units ?? 0
    const bill = row.billable_total_usd ?? llm + scan + tf
    setRunBillableUsd(bill)
    if (row.status === 'completed') {
      if (row.pricing_breakdown && typeof row.pricing_breakdown === 'object') {
        setRunBreakdown(row.pricing_breakdown)
      } else {
        setRunBreakdown({
          llm_cost_usd: llm,
          scan_fee_usd: scan,
          tool_fee_usd: tf,
          tool_units: tu,
          billable_total_usd: bill,
        })
      }
    } else {
      setRunBreakdown(null)
    }
    setInputTokens(row.input_tokens || 0)
    setOutputTokens(row.output_tokens || 0)
    setDurationSeconds(row.duration_seconds ?? null)
    setCostEstimateUnknown(!!row.cost_estimate_unknown)
    setSummary(row.summary_text || '')
    setFindings(row.findings_json || [])
    if (row.events_json?.length) {
      setLiveLines(foldStreamEntries(row.events_json.map((e) => eventToStreamEntry(e))))
    }
    if (row.error_message) setError(row.error_message)
    if (row.status === 'pending' || row.status === 'running') {
      connectWs(id)
    }
  }, [connectWs])

  useEffect(() => {
    if (deepLinkHandledRef.current) return
    const params = new URLSearchParams(window.location.search)
    const op = params.get('op')
    if (!op || !/^[0-9a-f-]{36}$/i.test(op)) return
    deepLinkHandledRef.current = true
    void (async () => {
      await openPastOperation(op)
    })()
    window.history.replaceState({}, '', window.location.pathname)
  }, [openPastOperation])

  useEffect(() => {
    return () => {
      wsRef.current?.close()
    }
  }, [])

  const hasRunData = useMemo(
    () =>
      costUsd > 0 ||
      runBillableUsd > 0 ||
      inputTokens > 0 ||
      durationSeconds != null,
    [costUsd, runBillableUsd, inputTokens, durationSeconds],
  )

  /** Operations attributed to the current billing session (per-run line items for pricing). */
  const sessionOps = useMemo(() => {
    if (!billingSessionId) return []
    return ops.filter((o) => o.session_id === billingSessionId)
  }, [ops, billingSessionId])

  const money = useCallback(
    (usd: number) => (billingVisible ? fmtCost(usd) : '—'),
    [billingVisible],
  )

  const downloadStructuredReport = useCallback(async () => {
    if (!selectedId) return
    const r = await fetch(apiUrl(`/api/operations/${selectedId}/report.json`))
    if (!r.ok) return
    const data = await r.json()
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `vecna-report-${selectedId.slice(0, 8)}.json`
    a.click()
    URL.revokeObjectURL(a.href)
  }, [selectedId])

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <header className="border-b border-zinc-800 bg-zinc-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-4 py-4">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-violet-600/20 text-violet-300">
            <Shield className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Vecna Operations Console</h1>
            <p className="text-sm text-zinc-500">Passive recon · Strands agent · Live telemetry</p>
          </div>
          {healthStatus && (
            <div
              className="ml-auto flex items-center gap-1.5 text-xs"
              title={`System health: ${healthStatus}`}
            >
              <span
                className={`h-2 w-2 rounded-full ${
                  healthStatus === 'ok'
                    ? 'bg-emerald-500'
                    : healthStatus === 'degraded'
                      ? 'bg-amber-400'
                      : 'bg-red-500'
                }`}
              />
              <span className="text-zinc-500">
                {healthStatus === 'ok' ? 'All systems nominal' : healthStatus === 'degraded' ? 'Degraded' : 'System error'}
              </span>
            </div>
          )}
        </div>
      </header>

      <main className="mx-auto grid max-w-7xl gap-4 px-4 py-6 lg:grid-cols-[320px_1fr]">
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Radio className="h-4 w-4 text-violet-400" />
                New operation
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <label className="text-xs font-medium uppercase tracking-wide text-zinc-500">Target URL</label>
              <Input
                value={targetUrl}
                onChange={(e) => setTargetUrl(e.target.value)}
                placeholder="https://example.com"
              />
              <Button
                className="w-full"
                onClick={() => void startOperation()}
                disabled={status === 'running' || !billingSessionId}
              >
                {status === 'running' ? 'Running…' : !billingSessionId ? 'Initializing…' : 'Deploy recon agent'}
              </Button>
              {selectedId && status !== 'running' && billingSessionId ? (
                <Button
                  variant="outline"
                  className="w-full border-zinc-700 text-zinc-300"
                  onClick={() => void startOperation()}
                  title="Start a new run using the target URL from the selected operation"
                >
                  <RotateCcw className="mr-2 h-4 w-4" />
                  Run again · same target
                </Button>
              ) : null}
              {status === 'running' && selectedId && (
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full border-red-800/40 text-red-400 hover:bg-red-950/30 hover:text-red-300"
                  onClick={() => void cancelOperation()}
                  title="Stop the current operation"
                >
                  Cancel operation
                </Button>
              )}
              {wsReconnecting && (
                <p className="text-xs text-amber-400 animate-pulse">⟳ Connection lost — reconnecting…</p>
              )}
              <Button
                variant="outline"
                size="sm"
                className="w-full border-amber-800/40 text-amber-500 hover:bg-amber-950/30 hover:text-amber-400"
                onClick={() => void forceError('rate_limit')}
                disabled={status === 'running'}
                title="Simulate rate-limit retries then a final error (demo only)"
              >
                <Zap className="mr-1.5 h-3.5 w-3.5" />
                Force error demo
              </Button>
              <p className="text-xs text-zinc-600">
                Billing session active — costs tracked in the panel below.
              </p>
              {error ? (
                <p className="flex items-start gap-2 text-sm text-red-400">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  {error}
                </p>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <History className="h-4 w-4 text-zinc-400" />
                History
              </CardTitle>
              <p className="text-xs text-zinc-600">
                Open a run to replay the stream, restore findings, and pre-fill the target URL. Share{' '}
                <code className="rounded bg-zinc-900 px-1 text-[10px]">?op=&lt;id&gt;</code> to resume.
              </p>
            </CardHeader>
            <CardContent className="p-0">
              <ScrollArea className="h-[240px]">
                <ul className="space-y-1 p-2">
                  {ops.map((o) => (
                    <li key={o.id}>
                      <button
                        type="button"
                        onClick={() => {
                          void openPastOperation(o.id)
                          const u = new URL(window.location.href)
                          u.searchParams.set('op', o.id)
                          window.history.replaceState({}, '', u.toString())
                        }}
                        className={cn(
                          'w-full rounded-md px-3 py-2 text-left text-sm transition-colors hover:bg-zinc-800',
                          selectedId === o.id && 'bg-zinc-800 ring-1 ring-violet-500/40',
                        )}
                      >
                        <div className="truncate font-medium text-zinc-200">{o.target_url}</div>
                        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
                          <Badge variant="outline">{o.status}</Badge>
                          {o.cost_usd && o.cost_usd > 0 ? (
                            <span className="text-emerald-500">{money(o.cost_usd)}</span>
                          ) : (
                            <span className="text-zinc-600">—</span>
                          )}
                          {o.duration_seconds != null && (
                            <span className="text-sky-500/80">{fmtDuration(o.duration_seconds)}</span>
                          )}
                        </div>
                        <div className="mt-1 text-[10px] text-zinc-600">{fmtHistoryWhen(o.created_at)}</div>
                      </button>
                    </li>
                  ))}
                </ul>
              </ScrollArea>
            </CardContent>
          </Card>

          {/* ── Pricing Transparency ─────────────────────────── */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <Zap className="h-4 w-4 text-amber-400" />
                Pricing Transparency
              </CardTitle>
              {publicModelId ? (
                <p className="mt-1 font-mono text-[10px] leading-relaxed text-zinc-500">
                  {publicModelId}
                  {modelPricingDisplay ? (
                    <span className="block text-zinc-600">Ref: {modelPricingDisplay}</span>
                  ) : null}
                </p>
              ) : null}
              {billingVisible && (feeScanUsd > 0 || feeToolUnitUsd > 0) ? (
                <p className="mt-2 text-[11px] leading-relaxed text-zinc-500">
                  Platform: {feeScanUsd > 0 ? `${fmtCost(feeScanUsd)} per completed scan` : 'no scan fee'}
                  {feeScanUsd > 0 && feeToolUnitUsd > 0 ? ' · ' : ''}
                  {feeToolUnitUsd > 0 ? `${fmtCost(feeToolUnitUsd)} per tool unit` : ''}
                  <span className="block text-zinc-600">
                    LLM is metered by tokens; tool units follow Strands tool call counts.
                  </span>
                </p>
              ) : null}
            </CardHeader>
            <CardContent className="space-y-4">
              {!billingVisible ? (
                <p className="rounded-md border border-amber-900/30 bg-amber-950/20 px-2 py-1.5 text-xs text-amber-200/90">
                  Dollar amounts are hidden server-side (<code className="text-[10px]">VECNA_HIDE_COSTS</code>).
                  Token counts and duration still show.
                </p>
              ) : null}
              {billingVisible && costEstimateUnknown ? (
                <p className="rounded-md border border-amber-900/30 bg-amber-950/20 px-2 py-1.5 text-xs text-amber-200/90">
                  Cost may be approximate: model not in the static table; LiteLLM priced when possible, otherwise a
                  default tier.
                </p>
              ) : null}
              {billingSessionId && sessionOps.length > 0 ? (
                <div className="rounded-md border border-zinc-800 bg-zinc-900/40">
                  <p className="border-b border-zinc-800 px-3 py-2 text-[10px] font-medium uppercase tracking-wide text-zinc-500">
                    Runs in this billing session
                    <span className="mt-0.5 block font-normal normal-case text-zinc-600">
                      Each row is one deploy. Billable totals update when that run finishes; the live panel below is
                      the selected run only.
                    </span>
                  </p>
                  <div className="max-h-[260px] overflow-auto">
                    <table className="w-full border-collapse text-left text-xs">
                      <thead>
                        <tr className="sticky top-0 z-[1] border-b border-zinc-800 bg-zinc-950/95 backdrop-blur-sm">
                          <th className="px-2 py-2 pr-3 text-[10px] font-medium uppercase tracking-wide text-zinc-500">
                            Target
                          </th>
                          <th className="whitespace-nowrap px-2 py-2 text-[10px] font-medium uppercase tracking-wide text-zinc-500">
                            Status
                          </th>
                          <th className="whitespace-nowrap px-2 py-2 text-right text-[10px] font-medium uppercase tracking-wide text-zinc-500">
                            LLM
                          </th>
                          <th className="whitespace-nowrap px-2 py-2 text-right text-[10px] font-medium uppercase tracking-wide text-zinc-500">
                            Billable
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {sessionOps.map((o) => {
                          const terminal =
                            o.status === 'completed' || o.status === 'failed' || o.status === 'cancelled'
                          const bill = o.billable_total_usd ?? 0
                          const llm = o.cost_usd ?? 0
                          // If billable wasn’t backfilled but LLM cost exists, show LLM (equals billable when fees are $0).
                          const billShow = terminal ? (bill > 0 ? bill : llm) : null
                          return (
                            <tr
                              key={o.id}
                              role="button"
                              tabIndex={0}
                              onClick={() => void openPastOperation(o.id)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                  e.preventDefault()
                                  void openPastOperation(o.id)
                                }
                              }}
                              className={cn(
                                'cursor-pointer border-b border-zinc-800/90 transition-colors hover:bg-zinc-800/70',
                                selectedId === o.id && 'bg-violet-950/25',
                              )}
                            >
                              <td className="max-w-0 px-2 py-2.5 align-middle">
                                <span
                                  className="block truncate font-mono text-[11px] leading-snug text-zinc-300"
                                  title={o.target_url}
                                >
                                  {o.target_url}
                                </span>
                              </td>
                              <td className="whitespace-nowrap px-2 py-2.5 align-middle">
                                <Badge variant="outline" className="text-[10px] capitalize">
                                  {o.status}
                                </Badge>
                              </td>
                              <td className="whitespace-nowrap px-2 py-2.5 text-right align-middle font-mono text-[11px] tabular-nums text-emerald-400/90">
                                {billingVisible ? (terminal ? money(llm) : '—') : '—'}
                              </td>
                              <td className="whitespace-nowrap px-2 py-2.5 text-right align-middle font-mono text-[11px] tabular-nums text-amber-400/90">
                                {billingVisible ? (terminal && billShow != null ? money(billShow) : '—') : '—'}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : null}
              {/* This run */}
              <div>
                <p className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500">
                  {selectedId ? 'Selected run (live detail)' : 'Last operation'}
                </p>
                {hasRunData ? (
                  <div className="grid grid-cols-2 gap-2">
                    <div className="rounded-md bg-zinc-900 p-2">
                      <p className="text-[10px] uppercase tracking-wide text-zinc-500">Billable total</p>
                      <p className="mt-0.5 font-mono text-sm font-semibold text-amber-400">
                        {money(runBillableUsd > 0 ? runBillableUsd : costUsd)}
                      </p>
                    </div>
                    <div className="rounded-md bg-zinc-900 p-2">
                      <p className="text-[10px] uppercase tracking-wide text-zinc-500">LLM (tokens)</p>
                      <p className="mt-0.5 font-mono text-sm font-semibold text-emerald-400">{money(costUsd)}</p>
                    </div>
                    {runBreakdown && (runBreakdown.scan_fee_usd > 0 || runBreakdown.tool_fee_usd > 0) ? (
                      <>
                        <div className="rounded-md bg-zinc-900 p-2">
                          <p className="text-[10px] uppercase tracking-wide text-zinc-500">Scan fee</p>
                          <p className="mt-0.5 font-mono text-sm text-zinc-300">{money(runBreakdown.scan_fee_usd)}</p>
                        </div>
                        <div className="rounded-md bg-zinc-900 p-2">
                          <p className="text-[10px] uppercase tracking-wide text-zinc-500">Tool fee</p>
                          <p className="mt-0.5 font-mono text-sm text-zinc-300">
                            {money(runBreakdown.tool_fee_usd)}
                            <span className="ml-1 text-[10px] text-zinc-500">
                              ({runBreakdown.tool_units} units)
                            </span>
                          </p>
                        </div>
                      </>
                    ) : null}
                    <div className="rounded-md bg-zinc-900 p-2">
                      <p className="text-[10px] uppercase tracking-wide text-zinc-500">Tool calls</p>
                      <p className="mt-0.5 font-mono text-sm font-semibold text-zinc-200">
                        {runBreakdown?.tool_units ?? 0}
                      </p>
                    </div>
                    <div className="rounded-md bg-zinc-900 p-2">
                      <p className="text-[10px] uppercase tracking-wide text-zinc-500">Duration</p>
                      <p className="mt-0.5 font-mono text-sm font-semibold text-sky-400">
                        {fmtDuration(durationSeconds)}
                      </p>
                    </div>
                    <div className="rounded-md bg-zinc-900 p-2">
                      <p className="text-[10px] uppercase tracking-wide text-zinc-500">Tokens in</p>
                      <p className="mt-0.5 font-mono text-sm font-semibold text-zinc-200">
                        {fmtTokens(inputTokens)}
                      </p>
                    </div>
                    <div className="rounded-md bg-zinc-900 p-2">
                      <p className="text-[10px] uppercase tracking-wide text-zinc-500">Tokens out</p>
                      <p className="mt-0.5 font-mono text-sm font-semibold text-zinc-200">
                        {fmtTokens(outputTokens)}
                      </p>
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-zinc-600">Run an operation to see cost breakdown.</p>
                )}
                {/* Per-tool call table */}
                {runBreakdown?.tool_calls_by_tool && Object.keys(runBreakdown.tool_calls_by_tool).length > 0 ? (
                  <div className="mt-3 rounded-md border border-zinc-800 bg-zinc-900/50">
                    <p className="border-b border-zinc-800 px-3 py-1.5 text-[10px] font-medium uppercase tracking-wide text-zinc-500">
                      Tool invocations
                    </p>
                    <table className="w-full text-xs">
                      <tbody>
                        {(runBreakdown.tool_fee_detail && runBreakdown.tool_fee_detail.length > 0
                          ? runBreakdown.tool_fee_detail
                          : Object.entries(runBreakdown.tool_calls_by_tool).map(([tool, calls]) => ({
                              tool,
                              calls,
                              fee_usd: 0,
                              family: null,
                              pricing: 'free',
                            }))
                        ).map((row) => (
                          <tr key={row.tool} className="border-b border-zinc-800/60 last:border-0">
                            <td className="px-3 py-1.5 font-mono text-zinc-300">
                              {row.tool.replace(/_/g, '_\u200b')}
                            </td>
                            <td className="px-3 py-1.5 text-right tabular-nums text-zinc-400">
                              ×{row.calls}
                            </td>
                            <td className="px-3 py-1.5 text-right tabular-nums text-zinc-500">
                              {row.fee_usd > 0 ? money(row.fee_usd) : '—'}
                            </td>
                            {row.family ? (
                              <td className="px-3 py-1.5 text-right text-[10px] text-zinc-600">
                                {row.family}
                              </td>
                            ) : null}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </div>

              {/* Session totals */}
              {sessionBilling && sessionBilling.operation_count > 0 && (
                <div>
                  <p className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500">
                    Session totals (cumulative)
                    <span className="ml-1 normal-case text-zinc-600">
                      ({sessionBilling.operation_count} completed op{sessionBilling.operation_count !== 1 ? 's' : ''})
                    </span>
                  </p>
                  <p className="mb-3 text-[10px] leading-relaxed text-zinc-600">
                    Sum of completed runs in this browser billing session. Starting another run does not reset this;
                    each finished run adds to the totals below.
                  </p>
                  <div className="grid grid-cols-2 gap-2">
                    <div className="rounded-md bg-zinc-900 p-2">
                      <p className="text-[10px] uppercase tracking-wide text-zinc-500">Billable total</p>
                      <p className="mt-0.5 font-mono text-sm font-semibold text-amber-400">
                        {money(
                          sessionBilling.total_billable_usd ??
                            sessionBilling.total_cost_usd +
                              (sessionBilling.total_scan_fees_usd ?? 0) +
                              (sessionBilling.total_tool_fees_usd ?? 0),
                        )}
                      </p>
                    </div>
                    <div className="rounded-md bg-zinc-900 p-2">
                      <p className="text-[10px] uppercase tracking-wide text-zinc-500">LLM (tokens)</p>
                      <p className="mt-0.5 font-mono text-sm font-semibold text-emerald-400/90">
                        {money(sessionBilling.total_cost_usd)}
                      </p>
                    </div>
                    {(sessionBilling.total_scan_fees_usd ?? 0) > 0 ||
                    (sessionBilling.total_tool_fees_usd ?? 0) > 0 ? (
                      <>
                        <div className="rounded-md bg-zinc-900 p-2">
                          <p className="text-[10px] uppercase tracking-wide text-zinc-500">Scan fees</p>
                          <p className="mt-0.5 font-mono text-sm text-zinc-300">
                            {money(sessionBilling.total_scan_fees_usd ?? 0)}
                          </p>
                        </div>
                        <div className="rounded-md bg-zinc-900 p-2">
                          <p className="text-[10px] uppercase tracking-wide text-zinc-500">Tool fees</p>
                          <p className="mt-0.5 font-mono text-sm text-zinc-300">
                            {money(sessionBilling.total_tool_fees_usd ?? 0)}
                            <span className="ml-1 text-[10px] text-zinc-500">
                              ({sessionBilling.total_tool_units ?? 0} units)
                            </span>
                          </p>
                        </div>
                      </>
                    ) : null}
                    <div className="rounded-md bg-zinc-900 p-2">
                      <p className="text-[10px] uppercase tracking-wide text-zinc-500">Wall time</p>
                      <p className="mt-0.5 font-mono text-sm font-semibold text-sky-400/80">
                        {fmtDuration(sessionBilling.wall_time_seconds)}
                      </p>
                    </div>
                    <div className="rounded-md bg-zinc-900 p-2">
                      <p className="text-[10px] uppercase tracking-wide text-zinc-500">Total in</p>
                      <p className="mt-0.5 font-mono text-sm text-zinc-300">
                        {fmtTokens(sessionBilling.total_input_tokens)}
                      </p>
                    </div>
                    <div className="rounded-md bg-zinc-900 p-2">
                      <p className="text-[10px] uppercase tracking-wide text-zinc-500">Total out</p>
                      <p className="mt-0.5 font-mono text-sm text-zinc-300">
                        {fmtTokens(sessionBilling.total_output_tokens)}
                      </p>
                    </div>
                  </div>

                  {/* Per-model breakdown */}
                  {Object.keys(sessionBilling.model_usage).length > 0 && (
                    <div className="mt-3">
                      <p className="mb-1.5 text-[10px] uppercase tracking-wide text-zinc-600">By model</p>
                      <div className="space-y-1.5">
                        {Object.entries(sessionBilling.model_usage).map(([model, u]) => (
                          <div
                            key={model}
                            className="flex items-center justify-between rounded bg-zinc-900/60 px-2 py-1.5"
                          >
                            <span className="max-w-[140px] truncate font-mono text-[10px] text-zinc-400" title={model}>
                              {model.split('/').slice(-1)[0]}
                            </span>
                            <div className="flex items-center gap-2 text-[10px]">
                              <span className="text-zinc-500">
                                {fmtTokens(u.inputTokens ?? 0)} / {fmtTokens(u.outputTokens ?? 0)} tok
                              </span>
                              <span className="font-mono font-semibold text-amber-400/80">
                                {money(u.costUSD ?? 0)}
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  <p
                    className="mt-2 truncate text-[10px] text-zinc-700"
                    title={billingSessionId ?? ''}
                  >
                    session {billingSessionId?.slice(0, 8)}…
                  </p>
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card className="min-h-[420px] lg:col-span-2">
            <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
              <CardTitle className="text-base">Live stream</CardTitle>
              <div className="flex flex-wrap items-center gap-2">
                {selectedId ? (
                  <>
                    <span className="font-mono text-xs text-zinc-500">{selectedId}</span>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-8 gap-1 border-zinc-700 text-xs"
                      onClick={() => void downloadStructuredReport()}
                    >
                      <Download className="h-3.5 w-3.5" />
                      report.json
                    </Button>
                  </>
                ) : (
                  <span className="text-xs text-zinc-500">No operation selected</span>
                )}
              </div>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[360px] w-full rounded-md border border-zinc-800 bg-black/40 p-3">
                {liveLines.length === 0 ? (
                  <p className="text-sm text-zinc-500">Waiting for events…</p>
                ) : (
                  <ul className="space-y-3">
                    {liveLines.map((line, i) => (
                      <li
                        key={`${i}-${line.human.slice(0, 24)}`}
                        className="border-l-2 border-violet-600/50 pl-3"
                      >
                        <p className="text-sm text-zinc-200">{line.human}</p>
                        <details className="mt-1">
                          <summary className="cursor-pointer text-xs text-zinc-500 hover:text-zinc-400">
                            Raw event
                          </summary>
                          <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-zinc-950/80 p-2 font-mono text-[10px] text-zinc-400">
                            {line.raw}
                          </pre>
                        </details>
                      </li>
                    ))}
                  </ul>
                )}
              </ScrollArea>
            </CardContent>
          </Card>

          <Card className="min-h-[320px]">
            <CardHeader>
              <CardTitle className="text-base">Findings</CardTitle>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[260px]">
                <ul className="space-y-3">
                  {findings.length === 0 ? (
                    <li className="text-sm text-zinc-500">No findings yet.</li>
                  ) : (
                    findings.map((f, i) => (
                      <li key={`${f.title}-${i}`} className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-3">
                        <div className="flex items-start justify-between gap-2">
                          <p className="font-medium text-zinc-100">{f.title}</p>
                          <Badge variant={severityVariant(f.severity)}>{f.severity || 'note'}</Badge>
                        </div>
                        {f.detail ? <p className="mt-2 text-sm text-zinc-400">{f.detail}</p> : null}
                      </li>
                    ))
                  )}
                </ul>
              </ScrollArea>
            </CardContent>
          </Card>

          <Card className="min-h-[320px]">
            <CardHeader>
              <CardTitle className="text-base">Report</CardTitle>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[260px]">
                <div>
                  {summary ? (
                    <div className="whitespace-pre-wrap text-sm leading-relaxed text-zinc-300">{summary}</div>
                  ) : (
                    <p className="text-sm text-zinc-500">Summary appears when the agent finishes.</p>
                  )}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>
        </div>
      </main>
    </div>
  )
}
