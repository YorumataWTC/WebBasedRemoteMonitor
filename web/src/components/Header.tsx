import { Activity, Eye, LogOut, Users } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useStore } from '../store'
import { cx } from './ui'

function Clock() {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  return (
    <div className="hidden text-right font-mono text-xs leading-tight text-muted sm:block">
      <div className="text-sm text-text">{now.toLocaleTimeString()}</div>
      <div>{now.toLocaleDateString(undefined, { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' })}</div>
    </div>
  )
}

export function Header() {
  const conn = useStore((s) => s.conn)
  const camera = useStore((s) => s.settings?.camera_name ?? '…')
  const viewers = useStore((s) => s.stats?.viewers ?? 0)
  const motion = useStore((s) => s.stats?.motion ?? false)
  const recording = useStore((s) => s.recording)
  const authRequired = useStore((s) => s.session.authRequired)

  const connBadge = {
    live: ['bg-ok', 'LIVE'],
    connecting: ['bg-warn', 'CONNECTING'],
    reconnecting: ['bg-warn', 'RECONNECTING'],
    offline: ['bg-alarm', 'OFFLINE'],
    unauthorized: ['bg-alarm', 'SIGNED OUT'],
  }[conn]

  async function logout() {
    await api.logout().catch(() => undefined)
    location.reload()
  }

  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-line bg-panel/80 px-4 backdrop-blur">
      <div className="flex items-center gap-2.5">
        <div className="grid h-8 w-8 place-items-center rounded-lg bg-accent/15 text-accent">
          <Eye size={18} />
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold">Remote Monitor</div>
          <div className="text-[11px] text-muted">EIE4428 · Video surveillance</div>
        </div>
      </div>

      <div className="mx-2 hidden h-6 w-px bg-line md:block" />
      <div className="hidden min-w-0 items-center gap-2 md:flex">
        <span className="truncate text-sm font-medium">{camera}</span>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-bg px-2 py-0.5 text-[11px] font-semibold tracking-wide">
          <span className={cx('h-2 w-2 rounded-full', connBadge[0], conn === 'live' && 'animate-pulse')} />
          {connBadge[1]}
        </span>
        {recording && (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-alarm/40 bg-alarm/15 px-2 py-0.5 text-[11px] font-semibold text-rose-300">
            <span className="h-2 w-2 animate-pulse rounded-full bg-alarm" /> REC
          </span>
        )}
        {motion && (
          <span className="inline-flex items-center gap-1 rounded-full border border-orange-400/40 bg-orange-400/15 px-2 py-0.5 text-[11px] font-semibold text-orange-300">
            <Activity size={12} /> MOTION
          </span>
        )}
      </div>

      <div className="ml-auto flex items-center gap-4">
        <div className="flex items-center gap-1.5 text-xs text-muted" title="Connected viewers">
          <Users size={15} />
          <span className="font-mono text-text">{viewers}</span>
        </div>
        <Clock />
        {authRequired && (
          <button onClick={logout} className="rounded-lg p-2 text-muted hover:bg-white/5 hover:text-text" title="Sign out">
            <LogOut size={17} />
          </button>
        )}
      </div>
    </header>
  )
}
