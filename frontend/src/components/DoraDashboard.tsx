// DORA delivery metrics dashboard — fetches /api/dora and renders four KPIs.
import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle, Clock, RefreshCw, TrendingDown, TrendingUp, XCircle } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { apiUrl } from '@/lib/apiBase'

type DoraMetrics = {
  deploy_frequency_per_day: number
  lead_time_hours: number
  change_failure_rate: number
  recovery_time_hours: number
  window_days: number
  commit_count: number
  deploy_count: number
  fix_commit_count: number
  computed_at: string
  targets: {
    deploy_frequency_per_day: number
    lead_time_hours: number
    change_failure_rate: number
    recovery_time_hours: number
  }
}

type Health = 'green' | 'yellow' | 'red'

function deployHealth(v: number, target: number): Health {
  if (v >= target) return 'green'
  if (v >= target * 0.5) return 'yellow'
  return 'red'
}

function lowerHealth(v: number, target: number): Health {
  if (v <= target) return 'green'
  if (v <= target * 1.5) return 'yellow'
  return 'red'
}

const healthColors: Record<Health, string> = {
  green: 'text-emerald-400',
  yellow: 'text-amber-400',
  red: 'text-red-400',
}

const healthBadge: Record<Health, 'default' | 'secondary' | 'destructive'> = {
  green: 'default',
  yellow: 'secondary',
  red: 'destructive',
}

function HealthIcon({ h }: { h: Health }) {
  if (h === 'green') return <CheckCircle className="h-4 w-4 text-emerald-400" />
  if (h === 'yellow') return <AlertTriangle className="h-4 w-4 text-amber-400" />
  return <XCircle className="h-4 w-4 text-red-400" />
}

function MetricCard({ title, value, target, health, description, icon, higherIsBetter }: {
  title: string; value: string; target: string; health: Health
  description: string; icon: React.ReactNode; higherIsBetter?: boolean
}) {
  return (
    <Card className="border-zinc-800 bg-zinc-900">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center justify-between text-sm font-medium text-zinc-400">
          <span className="flex items-center gap-2">
            {icon}
            {title}
          </span>
          <HealthIcon h={health} />
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className={`text-3xl font-bold tabular-nums ${healthColors[health]}`}>{value}</p>
        <p className="mt-1 text-xs text-zinc-500">{description}</p>
        <div className="mt-3 flex items-center gap-2">
          {higherIsBetter
            ? <TrendingUp className="h-3 w-3 text-zinc-600" />
            : <TrendingDown className="h-3 w-3 text-zinc-600" />}
          <span className="text-xs text-zinc-600">target: {target}</span>
          <Badge variant={healthBadge[health]} className="ml-auto text-xs">
            {health === 'green' ? 'on target' : health === 'yellow' ? 'at risk' : 'off target'}
          </Badge>
        </div>
      </CardContent>
    </Card>
  )
}

export function DoraDashboard() {
  const [metrics, setMetrics] = useState<DoraMetrics | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [windowDays, setWindowDays] = useState(30)

  const load = async (days: number) => {
    setLoading(true)
    setError(null)
    try {
      const r = await fetch(apiUrl(`/api/dora?window_days=${days}`))
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setMetrics(await r.json() as DoraMetrics)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load DORA metrics')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load(windowDays) }, [windowDays])

  const fmtDf = (v: number) => v >= 1 ? `${v.toFixed(1)}/d` : `${(v * 7).toFixed(1)}/w`
  const fmtHours = (h: number) => h < 1 ? `${Math.round(h * 60)}m` : `${h.toFixed(1)}h`
  const fmtPct = (v: number) => `${(v * 100).toFixed(1)}%`

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-zinc-100">DORA Metrics</h2>
          <p className="text-sm text-zinc-500">
            Delivery performance from git history · {windowDays}-day window
          </p>
        </div>
        <div className="flex items-center gap-2">
          {([7, 30, 90] as const).map((d) => (
            <Button
              key={d}
              variant={windowDays === d ? 'default' : 'outline'}
              size="sm"
              className="border-zinc-700 text-xs"
              onClick={() => setWindowDays(d)}
            >
              {d}d
            </Button>
          ))}
          <Button
            variant="outline"
            size="sm"
            className="border-zinc-700"
            onClick={() => void load(windowDays)}
            disabled={loading}
          >
            <RefreshCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-800/40 bg-red-950/30 p-4 text-sm text-red-400">
          {error}
        </div>
      )}

      {!error && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard
            title="Deploy Frequency"
            value={metrics ? fmtDf(metrics.deploy_frequency_per_day) : '—'}
            target="≥ 1 / day"
            health={metrics ? deployHealth(metrics.deploy_frequency_per_day, metrics.targets.deploy_frequency_per_day) : 'yellow'}
            description="Non-trivial commits landing on main"
            icon={<TrendingUp className="h-4 w-4" />}
            higherIsBetter
          />
          <MetricCard
            title="Lead Time"
            value={metrics ? fmtHours(metrics.lead_time_hours) : '—'}
            target="< 24 h"
            health={metrics ? lowerHealth(metrics.lead_time_hours, metrics.targets.lead_time_hours) : 'yellow'}
            description="Median commit-to-main delta"
            icon={<Clock className="h-4 w-4" />}
          />
          <MetricCard
            title="Change Failure Rate"
            value={metrics ? fmtPct(metrics.change_failure_rate) : '—'}
            target="< 15 %"
            health={metrics ? lowerHealth(metrics.change_failure_rate, metrics.targets.change_failure_rate) : 'yellow'}
            description="Fix / revert commits as % of total"
            icon={<AlertTriangle className="h-4 w-4" />}
          />
          <MetricCard
            title="Recovery Time"
            value={metrics ? (metrics.fix_commit_count < 2 ? 'N/A' : fmtHours(metrics.recovery_time_hours)) : '—'}
            target="< 1 h"
            health={metrics && metrics.fix_commit_count >= 2 ? lowerHealth(metrics.recovery_time_hours, metrics.targets.recovery_time_hours) : 'green'}
            description="Median time between consecutive fixes"
            icon={<RefreshCw className="h-4 w-4" />}
          />
        </div>
      )}

      {metrics && (
        <p className="text-xs text-zinc-600">
          {metrics.deploy_count} deployments · {metrics.commit_count} commits · {metrics.fix_commit_count} fixes ·
          computed {new Date(metrics.computed_at).toLocaleString()} · source: git log main
        </p>
      )}
    </div>
  )
}
