import { Download, Film, Play, RefreshCw, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Badge, Button, Empty, Modal } from '../components/ui'
import { formatBytes, formatDuration } from '../lib/format'
import { api, urls, type RecordingItem } from '../lib/api'
import { useStore } from '../store'

export function RecordingsPanel() {
  const [items, setItems] = useState<RecordingItem[]>([])
  const [playing, setPlaying] = useState<RecordingItem | null>(null)
  const recording = useStore((s) => s.recording)
  const lastEvent = useStore((s) => s.events[0]?.id)
  const toast = useStore((s) => s.toast)

  const load = useCallback(() => {
    api
      .recordings()
      .then((r) => setItems(r.items))
      .catch(() => undefined)
  }, [])

  useEffect(load, [load, lastEvent])

  async function remove(item: RecordingItem) {
    if (!confirm(`Delete ${item.file}?`)) return
    try {
      await api.deleteRecording(item.file)
      setItems((list) => list.filter((i) => i.file !== item.file))
    } catch (e) {
      toast({ level: 'error', title: 'Delete failed', body: (e as Error).message })
    }
  }

  const total = items.reduce((a, b) => a + (b.size ?? 0), 0)

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-xs text-muted">
        <span>
          {items.length} recordings · {formatBytes(total)}
        </span>
        <Button variant="ghost" className="!px-2" onClick={load} title="Refresh">
          <RefreshCw size={14} />
        </Button>
      </div>
      {recording && (
        <div className="flex items-center gap-2 rounded-lg border border-alarm/30 bg-alarm/10 px-3 py-2 text-sm text-rose-200">
          <span className="h-2 w-2 animate-pulse rounded-full bg-alarm" />
          Recording {recording.reason} · {formatDuration(recording.duration)}
        </div>
      )}
      {items.length === 0 ? (
        <Empty icon={<Film size={28} />} title="No recordings" hint="Press Record, or enable motion-triggered recording in Settings." />
      ) : (
        <ul className="space-y-2">
          {items.map((it) => (
            <li key={it.file} className="flex gap-3 rounded-lg border border-line bg-panel-2/50 p-2">
              <button onClick={() => setPlaying(it)} className="group relative shrink-0 overflow-hidden rounded-md border border-line bg-black" title="Play">
                {it.thumb ? <img src={urls.recordingThumb(it.file)} alt="" loading="lazy" className="h-14 w-24 object-cover" /> : <div className="h-14 w-24" />}
                <span className="absolute inset-0 grid place-items-center bg-black/30 opacity-0 transition-opacity group-hover:opacity-100">
                  <Play size={18} className="fill-white" />
                </span>
              </button>
              <div className="min-w-0 flex-1">
                <div className="truncate font-mono text-xs">{new Date(it.startedAt * 1000).toLocaleString()}</div>
                <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
                  <Badge tone={it.reason === 'motion' ? 'warn' : 'accent'}>{it.reason}</Badge>
                  <span>{formatDuration(it.duration)}</span>
                  <span>· {formatBytes(it.size ?? 0)}</span>
                </div>
              </div>
              <div className="flex shrink-0 flex-col gap-1">
                <a href={urls.recording(it.file, true)} className="rounded-md p-1.5 text-muted hover:bg-white/5 hover:text-text" title="Download">
                  <Download size={14} />
                </a>
                <button onClick={() => remove(it)} className="rounded-md p-1.5 text-muted hover:bg-alarm/15 hover:text-rose-300" title="Delete">
                  <Trash2 size={14} />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <Modal open={!!playing} onClose={() => setPlaying(null)} title={playing ? `${playing.file} · ${formatDuration(playing.duration)}` : ''}>
        {playing && <video src={urls.recording(playing.file)} controls autoPlay className="aspect-video w-full bg-black" />}
      </Modal>
    </div>
  )
}
