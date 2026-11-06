import type { MonitorEvent, RecordingStatus, Settings } from './protocol'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    credentials: 'same-origin',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let msg = res.statusText
    try {
      const j = await res.json()
      msg = typeof j.detail === 'string' ? j.detail : Array.isArray(j.detail) ? j.detail.map((d: { msg: string }) => d.msg).join(', ') : msg
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, msg)
  }
  return res.json() as Promise<T>
}

export interface RecordingItem {
  file: string
  reason: string
  startedAt: number
  duration: number
  frames: number
  size: number
  thumb: boolean
}

export interface FaceEntry {
  id: string
  name: string
  created: number
}

export const api = {
  session: () => request<{ authRequired: boolean; authenticated: boolean; version: string }>('GET', '/api/session'),
  login: (password: string) => request<{ ok: boolean }>('POST', '/api/login', { password }),
  logout: () => request<{ ok: boolean }>('POST', '/api/logout'),
  patchSettings: (patch: Partial<Settings>) => request<Settings>('PATCH', '/api/settings', patch),
  events: (limit = 150) => request<MonitorEvent[]>('GET', `/api/events?limit=${limit}`),
  clearEvents: () => request('DELETE', '/api/events'),
  recordings: () => request<{ active: RecordingStatus | null; items: RecordingItem[] }>('GET', '/api/recordings'),
  startRecording: () => request<RecordingStatus>('POST', '/api/recordings/start'),
  stopRecording: () => request<unknown>('POST', '/api/recordings/stop'),
  deleteRecording: (file: string) => request('DELETE', `/api/recordings/${encodeURIComponent(file)}`),
  faces: () =>
    request<{ available: { detection: boolean; recognition: boolean; emotion: boolean }; items: FaceEntry[] }>('GET', '/api/faces'),
  enrollFace: (name: string) => request<FaceEntry>('POST', '/api/faces', { name }),
  deleteFace: (id: string) => request('DELETE', `/api/faces/${id}`),
}

export const urls = {
  snapshot: () => `/api/snapshot.jpg?t=${Date.now()}`,
  mjpeg: () => `/api/stream.mjpg?t=${Date.now()}`,
  eventSnapshot: (name: string) => `/api/snapshots/${encodeURIComponent(name)}`,
  recording: (file: string, download = false) => `/api/recordings/${encodeURIComponent(file)}${download ? '?download=true' : ''}`,
  recordingThumb: (file: string) => `/api/recordings/${encodeURIComponent(file.replace(/\.mp4$/, '.jpg'))}`,
  face: (id: string) => `/api/faces/${id}.jpg`,
}
