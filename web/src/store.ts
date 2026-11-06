import { create } from 'zustand'
import type {
  Features,
  Meta,
  MonitorEvent,
  RecordingStatus,
  ServerStats,
  Settings,
  VideoConfig,
} from './lib/protocol'

export type ConnState = 'connecting' | 'live' | 'reconnecting' | 'offline' | 'unauthorized'

export interface ClientStats {
  fps: number
  latencyMs: number | null
  rttMs: number | null
  decoded: number
  dropped: number
  hardware: string
  kbps: number
}

export interface Prefs {
  showMotion: boolean
  showTracks: boolean
  showFaces: boolean
  alarmSound: boolean
  notifications: boolean
  forceMjpeg: boolean
  volume: number
}

export interface Toast {
  id: number
  level: 'info' | 'warning' | 'alarm' | 'error' | 'success'
  title: string
  body?: string
}

const PREFS_KEY = 'rm.prefs'
const defaultPrefs: Prefs = {
  showMotion: true,
  showTracks: true,
  showFaces: true,
  alarmSound: true,
  notifications: false,
  forceMjpeg: false,
  volume: 0.8,
}

function loadPrefs(): Prefs {
  try {
    return { ...defaultPrefs, ...JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}') }
  } catch {
    return defaultPrefs
  }
}

const HISTORY = 60

interface State {
  session: { checked: boolean; authRequired: boolean; authenticated: boolean; version: string }
  conn: ConnState
  transport: 'webcodecs' | 'mjpeg'
  settings: Settings | null
  features: Features | null
  videoConfig: VideoConfig | null
  stats: ServerStats | null
  client: ClientStats
  meta: Meta | null
  audioLevel: number
  events: MonitorEvent[]
  recording: RecordingStatus | null
  toasts: Toast[]
  prefs: Prefs
  history: { bitrate: number[]; latency: number[]; fps: number[] }
  alarmUntil: number
  audioOn: boolean
  paused: boolean

  setPrefs: (p: Partial<Prefs>) => void
  toast: (t: Omit<Toast, 'id'>) => void
  dismissToast: (id: number) => void
  pushHistory: (bitrate: number, latency: number | null, fps: number) => void
  addEvent: (e: MonitorEvent) => void
}

let toastId = 1

export const useStore = create<State>((set, get) => ({
  session: { checked: false, authRequired: false, authenticated: false, version: '' },
  conn: 'connecting',
  transport: 'webcodecs',
  settings: null,
  features: null,
  videoConfig: null,
  stats: null,
  client: { fps: 0, latencyMs: null, rttMs: null, decoded: 0, dropped: 0, hardware: 'n/a', kbps: 0 },
  meta: null,
  audioLevel: -120,
  events: [],
  recording: null,
  toasts: [],
  prefs: loadPrefs(),
  history: { bitrate: [], latency: [], fps: [] },
  alarmUntil: 0,
  audioOn: false,
  paused: false,

  setPrefs: (p) => {
    const prefs = { ...get().prefs, ...p }
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify(prefs))
    } catch {
      /* private mode */
    }
    set({ prefs })
  },
  toast: (t) => {
    const id = toastId++
    set({ toasts: [...get().toasts.slice(-4), { ...t, id }] })
    setTimeout(() => get().dismissToast(id), t.level === 'alarm' ? 8000 : 4500)
  },
  dismissToast: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
  pushHistory: (bitrate, latency, fps) => {
    const h = get().history
    const push = (arr: number[], v: number) => [...arr.slice(-(HISTORY - 1)), v]
    set({
      history: {
        bitrate: push(h.bitrate, bitrate),
        latency: push(h.latency, latency ?? 0),
        fps: push(h.fps, fps),
      },
    })
  },
  addEvent: (e) => set({ events: [e, ...get().events.filter((x) => x.id !== e.id)].slice(0, 300) }),
}))
