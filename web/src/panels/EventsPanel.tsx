import { Activity, AlertTriangle, Bell, Film, Info, ScanFace, Shield, Trash2, UserCheck, UserX, Volume2 } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Button, cx, Empty, Modal } from '../components/ui'
import { api, urls } from '../lib/api'
import type { MonitorEvent } from '../lib/protocol'
import { useStore } from '../store'

const ICONS: Record<string, ReactNode> = {
  motion: <Activity size={15} />,
  motion_end: <Activity size={15} />,
  face: <ScanFace size={15} />,
  person: <UserCheck size={15} />,
  unknown_person: <UserX size={15} />,
  sound: <Volume2 size={15} />,
  recording: <Film size={15} />,
  recording_saved: <Film size={15} />,
  security: <Shield size={15} />,
}

const FILTERS: [string, string[] | null][] = [
  ['All', null],
  ['Alarms', ['motion', 'sound', 'unknown_person']],
  ['Faces', ['face', 'person', 'unknown_person']],
  ['Recordings', ['recording', 'recording_saved']],
  ['System', ['system', 'viewer', 'security', 'face_enrolled']],
]

export function EventsPanel() {
  const events = useStore((s) => s.events)
  const [filter, setFilter] = useState(0)
  const [preview, setPreview] = useState<MonitorEvent | null>(null)

  useEffect(() => {
    api
      .events()
      .then((list) => useStore.setState({ events: list }))
      .catch(() => undefined)
  }, [])

  const visible = useMemo(() => {
    const kinds = FILTERS[filter][1]
    return kinds ? events.filter((e) => kinds.includes(e.kind)) : events
  }, [events, filter])

  async function clear() {
    if (!confirm('Delete the complete event history?')) return
    await api.clearEvents()
    useStore.setState({ events: [] })
  }

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex flex-wrap items-center gap-1">
        {FILTERS.map(([name], i) => (
          <button
            key={name}
            onClick={() => setFilter(i)}
            className={cx('rounded-md px-2 py-1 text-xs', filter === i ? 'bg-accent/15 text-accent' : 'text-muted hover:bg-white/5')}
          >
            {name}
          </button>
        ))}
        <Button variant="ghost" className="ml-auto !px-2" onClick={clear} title="Clear history">
          <Trash2 size={14} />
        </Button>
      </div>

      {visible.length === 0 ? (
        <Empty icon={<Bell size={28} />} title="No events yet" hint="Motion, faces, loud noises and recordings will appear here in real time." />
      ) : (
        <ol className="space-y-1.5">
          {visible.map((e) => (
            <li
              key={e.id}
              className={cx(
                'flex gap-3 rounded-lg border p-2',
                e.level === 'alarm' ? 'border-alarm/30 bg-alarm/5' : e.level === 'warning' ? 'border-warn/30 bg-warn/5' : 'border-line bg-panel-2/50',
              )}
            >
              {e.snapshot ? (
                <button onClick={() => setPreview(e)} className="shrink-0 overflow-hidden rounded-md border border-line">
                  <img src={urls.eventSnapshot(e.snapshot)} alt="" loading="lazy" className="h-12 w-20 object-cover" />
                </button>
              ) : (
                <div className={cx('grid h-12 w-12 shrink-0 place-items-center rounded-md', e.level === 'alarm' ? 'bg-alarm/15 text-rose-300' : 'bg-white/5 text-muted')}>
                  {ICONS[e.kind] ?? (e.level === 'alarm' ? <AlertTriangle size={15} /> : <Info size={15} />)}
                </div>
              )}
              <div className="min-w-0 flex-1">
                <div className={cx('flex items-center gap-1.5 text-sm', e.level === 'alarm' && 'text-rose-200')}>
                  <span className="shrink-0 opacity-70">{ICONS[e.kind]}</span>
                  <span className="truncate">{e.message}</span>
                </div>
                <div className="mt-0.5 font-mono text-[11px] text-muted">
                  {new Date(e.ts * 1000).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'medium' })}
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}

      <Modal open={!!preview} onClose={() => setPreview(null)} title={preview ? `${preview.message} · ${new Date(preview.ts * 1000).toLocaleString()}` : ''}>
        {preview?.snapshot && <img src={urls.eventSnapshot(preview.snapshot)} alt={preview.message} className="w-full" />}
      </Modal>
    </div>
  )
}
