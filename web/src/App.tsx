import { Bell, Film, Gauge, ScanFace, SlidersHorizontal } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { Header } from './components/Header'
import { Login } from './components/Login'
import { Toasts } from './components/Toasts'
import { cx } from './lib/cx'
import { VideoStage } from './components/VideoStage'
import { api } from './lib/api'
import { connection } from './lib/connection'
import { EventsPanel } from './panels/EventsPanel'
import { FacesPanel } from './panels/FacesPanel'
import { LivePanel } from './panels/LivePanel'
import { RecordingsPanel } from './panels/RecordingsPanel'
import { SettingsPanel } from './panels/SettingsPanel'
import { useStore } from './store'

type Tab = 'live' | 'events' | 'recordings' | 'faces' | 'settings'

const TABS: { id: Tab; label: string; icon: ReactNode }[] = [
  { id: 'live', label: 'Live', icon: <Gauge size={16} /> },
  { id: 'events', label: 'Events', icon: <Bell size={16} /> },
  { id: 'recordings', label: 'Recordings', icon: <Film size={16} /> },
  { id: 'faces', label: 'Faces', icon: <ScanFace size={16} /> },
  { id: 'settings', label: 'Settings', icon: <SlidersHorizontal size={16} /> },
]

function Dashboard() {
  const [tab, setTab] = useState<Tab>('live')
  const unread = useStore((s) => s.events.filter((e) => e.level === 'alarm' && e.ts * 1000 > Date.now() - 60_000).length)

  useEffect(() => {
    connection.start()
    return () => connection.stop()
  }, [])

  return (
    <div className="flex h-full flex-col">
      <Header />
      <main className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <VideoStage />
        <aside className="flex min-h-0 flex-col border-line bg-panel max-lg:h-[45vh] max-lg:border-t lg:w-[380px] lg:border-l">
          <nav className="flex shrink-0 border-b border-line" role="tablist">
            {TABS.map((t) => (
              <button
                key={t.id}
                role="tab"
                aria-selected={tab === t.id}
                onClick={() => setTab(t.id)}
                className={cx(
                  'relative flex flex-1 flex-col items-center gap-1 py-2.5 text-[11px] transition-colors',
                  tab === t.id ? 'text-accent' : 'text-muted hover:text-text',
                )}
              >
                {t.icon}
                {t.label}
                {t.id === 'events' && unread > 0 && (
                  <span className="absolute right-3 top-1.5 rounded-full bg-alarm px-1.5 text-[10px] font-semibold text-white">{unread}</span>
                )}
                {tab === t.id && <span className="absolute inset-x-3 bottom-0 h-0.5 rounded-full bg-accent" />}
              </button>
            ))}
          </nav>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {tab === 'live' && <LivePanel />}
            {tab === 'events' && <EventsPanel />}
            {tab === 'recordings' && <RecordingsPanel />}
            {tab === 'faces' && <FacesPanel />}
            {tab === 'settings' && <SettingsPanel />}
          </div>
        </aside>
      </main>
    </div>
  )
}

export default function App() {
  const session = useStore((s) => s.session)
  const conn = useStore((s) => s.conn)

  useEffect(() => {
    api
      .session()
      .then((s) => useStore.setState({ session: { ...s, checked: true } }))
      .catch(() => useStore.setState({ session: { checked: true, authRequired: false, authenticated: true, version: '' }, conn: 'offline' }))
  }, [])

  if (!session.checked) return null
  const needLogin = session.authRequired && (!session.authenticated || conn === 'unauthorized')
  return (
    <>
      {needLogin ? <Login /> : <Dashboard />}
      <Toasts />
    </>
  )
}
