import { ScanFace, Trash2, UserPlus } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { Badge, Button, Empty } from '../components/ui'
import { api, urls, type FaceEntry } from '../lib/api'
import { useStore } from '../store'

export function FacesPanel() {
  const [items, setItems] = useState<FaceEntry[]>([])
  const [available, setAvailable] = useState<{ detection: boolean; recognition: boolean; emotion: boolean } | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const toast = useStore((s) => s.toast)
  const faces = useStore((s) => s.meta?.faces?.length ?? 0)

  useEffect(() => {
    api
      .faces()
      .then((r) => {
        setItems(r.items)
        setAvailable(r.available)
      })
      .catch(() => undefined)
  }, [])

  async function enroll(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      const entry = await api.enrollFace(name.trim())
      setItems((l) => [...l, entry])
      setName('')
      toast({ level: 'success', title: `Enrolled ${entry.name}` })
    } catch (err) {
      toast({ level: 'error', title: 'Enrollment failed', body: (err as Error).message })
    } finally {
      setBusy(false)
    }
  }

  async function remove(f: FaceEntry) {
    await api.deleteFace(f.id)
    setItems((l) => l.filter((x) => x.id !== f.id))
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1.5">
        {available &&
          (['detection', 'recognition', 'emotion'] as const).map((k) => (
            <Badge key={k} tone={available[k] ? 'ok' : 'muted'}>
              {k} {available[k] ? 'ready' : 'not installed'}
            </Badge>
          ))}
      </div>

      <form onSubmit={enroll} className="rounded-xl border border-line bg-panel-2/60 p-3">
        <div className="mb-2 text-sm font-medium">Enroll a person</div>
        <p className="mb-3 text-xs text-muted">
          Look into the camera, type the name and press enroll. The largest visible face is stored as a 128-D SFace embedding.
          Recognised people are labelled in the live view and logged as events.
        </p>
        <div className="flex gap-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Name"
            maxLength={40}
            className="min-w-0 flex-1 rounded-lg border border-line bg-bg px-3 py-1.5 text-sm outline-none focus:border-accent"
          />
          <Button variant="primary" disabled={busy || !name.trim() || !available?.recognition}>
            <UserPlus size={15} /> Enroll
          </Button>
        </div>
        <div className="mt-2 text-[11px] text-muted">{faces ? `${faces} face(s) currently visible` : 'No face visible right now'}</div>
      </form>

      {items.length === 0 ? (
        <Empty icon={<ScanFace size={28} />} title="No known faces" hint="Enrolled people will be recognised automatically." />
      ) : (
        <ul className="grid grid-cols-2 gap-2">
          {items.map((f) => (
            <li key={f.id} className="group relative overflow-hidden rounded-lg border border-line bg-panel-2/50">
              <img src={urls.face(f.id)} alt={f.name} className="aspect-square w-full object-cover" />
              <div className="flex items-center justify-between px-2 py-1.5">
                <span className="truncate text-sm">{f.name}</span>
                <button onClick={() => remove(f)} className="rounded p-1 text-muted hover:bg-alarm/15 hover:text-rose-300" title="Remove">
                  <Trash2 size={13} />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
