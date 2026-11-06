import { Cpu, Gauge, MonitorPlay, Network, Server } from 'lucide-react'
import type { ReactNode } from 'react'
import { Badge, Section } from '../components/ui'
import { formatDuration } from '../lib/format'
import { useStore } from '../store'

function Sparkline({ data, color, max }: { data: number[]; color: string; max?: number }) {
  const w = 120
  const h = 28
  if (data.length < 2) return <svg width={w} height={h} />
  const m = max ?? Math.max(1, ...data) * 1.15
  const step = w / (60 - 1)
  const off = w - (data.length - 1) * step
  const pts = data.map((v, i) => `${(off + i * step).toFixed(1)},${(h - (Math.min(v, m) / m) * (h - 2) - 1).toFixed(1)}`)
  return (
    <svg width={w} height={h} className="overflow-visible" aria-hidden>
      <polyline points={`${off},${h} ${pts.join(' ')} ${w},${h}`} fill={color} opacity={0.12} />
      <polyline points={pts.join(' ')} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
  )
}

function Metric({ label, value, unit, spark }: { label: string; value: ReactNode; unit?: string; spark?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2">
      <div>
        <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
        <div className="font-mono text-lg leading-tight">
          {value}
          {unit && <span className="ml-1 text-xs text-muted">{unit}</span>}
        </div>
      </div>
      {spark}
    </div>
  )
}

function KV({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5 text-sm">
      <span className="text-muted">{k}</span>
      <span className="truncate text-right font-mono text-xs">{v}</span>
    </div>
  )
}

export function LivePanel() {
  const stats = useStore((s) => s.stats)
  const client = useStore((s) => s.client)
  const history = useStore((s) => s.history)
  const cfg = useStore((s) => s.videoConfig)
  const transport = useStore((s) => s.transport)
  const meta = useStore((s) => s.meta)

  const latency = client.latencyMs
  const latencyTone = latency === null ? 'muted' : latency < 250 ? 'ok' : latency < 600 ? 'warn' : 'alarm'

  return (
    <div className="space-y-3">
      <Section title="Viewer" icon={<MonitorPlay size={13} />}>
        <Metric
          label="End-to-end latency"
          value={transport === 'webcodecs' && latency !== null ? Math.max(0, latency).toFixed(0) : '–'}
          unit="ms"
          spark={<Sparkline data={history.latency} color="#fbbf24" />}
        />
        <Metric
          label="Displayed frame rate"
          value={(transport === 'webcodecs' ? client.fps : (stats?.encodeFps ?? 0)).toFixed(0)}
          unit="fps"
          spark={<Sparkline data={history.fps} color="#34d399" max={Math.max(30, cfg?.fps ?? 30)} />}
        />
        <Metric label="Received bit rate" value={client.kbps} unit="kbit/s" spark={<Sparkline data={history.bitrate} color="#22d3ee" />} />
        <KV k="Transport" v={transport === 'webcodecs' ? 'WebSocket + WebCodecs' : 'HTTP MJPEG (fallback)'} />
        <KV k="Decoder" v={transport === 'webcodecs' ? client.hardware : 'browser <img>'} />
        <KV k="Network RTT" v={client.rttMs !== null ? `${client.rttMs.toFixed(1)} ms` : '–'} />
        <KV k="Frames decoded / skipped" v={`${client.decoded} / ${client.dropped}`} />
        <div className="py-1.5">
          <Badge tone={latencyTone}>{latency === null ? 'measuring…' : latency < 250 ? 'real-time' : latency < 600 ? 'acceptable' : 'high latency'}</Badge>
        </div>
      </Section>

      <Section title="Video stream" icon={<Network size={13} />}>
        <KV k="Codec" v={cfg ? `H.264 (${cfg.codec})` : '–'} />
        <KV k="Resolution" v={cfg ? `${cfg.width} × ${cfg.height}` : '–'} />
        <KV k="Target" v={cfg ? `${cfg.fps} fps · ${(cfg.bitrate / 1000).toFixed(0)} kbit/s` : '–'} />
        <KV k="Encoder" v={cfg?.encoder ?? '–'} />
        <KV k="Audio" v="Opus 48 kHz mono · 32 kbit/s" />
      </Section>

      <Section title="Server" icon={<Server size={13} />}>
        <KV k="Source" v={stats?.source ?? '–'} />
        <KV k="Capture / encode" v={stats ? `${stats.captureFps.toFixed(1)} / ${stats.encodeFps.toFixed(1)} fps` : '–'} />
        <KV k="Encoded bit rate" v={stats ? `${stats.bitrateKbps} kbit/s` : '–'} />
        <KV k="Encode time" v={stats ? `${stats.encodeMs.toFixed(1)} ms / frame` : '–'} />
        <KV k="Viewers (WS / MJPEG)" v={stats ? `${stats.viewers} / ${stats.mjpegViewers}` : '–'} />
        <KV k="Uptime" v={stats ? formatDuration(stats.uptime) : '–'} />
        <KV k="Camera" v={<Badge tone={stats?.captureStatus === 'ok' ? 'ok' : 'warn'}>{stats?.captureStatus ?? '–'}</Badge>} />
        <KV k="Microphone" v={<Badge tone={stats?.audioStatus.startsWith('ok') ? 'ok' : 'muted'}>{stats?.audioStatus ?? '–'}</Badge>} />
      </Section>

      <Section title="Analytics" icon={<Cpu size={13} />}>
        <KV k="Rate / cost" v={stats ? `${stats.analyticsFps.toFixed(1)} Hz · ${stats.analyticsMs.toFixed(0)} ms` : '–'} />
        <KV k="Motion score" v={meta?.motion ? `${(meta.motion.score * 100).toFixed(2)} %` : '–'} />
        <KV k="Tracked objects" v={meta?.tracks?.length ?? 0} />
        <KV k="Faces" v={meta?.faces?.map((f) => f.name ?? 'face').join(', ') || '0'} />
      </Section>
      <p className="flex items-center gap-1.5 px-1 text-[11px] text-muted">
        <Gauge size={12} /> Latency = capture timestamp → canvas paint, using NTP-style clock sync.
      </p>
    </div>
  )
}
