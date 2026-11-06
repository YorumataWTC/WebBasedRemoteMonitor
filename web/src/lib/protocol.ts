/**
 * Wire protocol (mirror of server/src/remote_monitor/protocol.py).
 *
 * Binary media message, big endian:
 *   [0] u8 type (1 video, 2 audio) | [1] u8 flags (bit0 keyframe) | [2..3] reserved
 *   [4..11] u64 capture timestamp in microseconds (UTC wall clock) | [12..] payload
 */

export const HEADER_SIZE = 12
export const TYPE_VIDEO = 1
export const TYPE_AUDIO = 2

export interface MediaMessage {
  kind: number
  keyframe: boolean
  timestamp: number // microseconds since epoch (fits in a double: < 2^53)
  payload: Uint8Array
}

export function parseMedia(buf: ArrayBuffer): MediaMessage {
  const view = new DataView(buf)
  return {
    kind: view.getUint8(0),
    keyframe: (view.getUint8(1) & 1) === 1,
    timestamp: Number(view.getBigUint64(4)),
    payload: new Uint8Array(buf, HEADER_SIZE),
  }
}

export function buildMedia(kind: number, timestamp: number, payload: Uint8Array, keyframe = false): ArrayBuffer {
  const buf = new ArrayBuffer(HEADER_SIZE + payload.byteLength)
  const view = new DataView(buf)
  view.setUint8(0, kind)
  view.setUint8(1, keyframe ? 1 : 0)
  view.setBigUint64(4, BigInt(timestamp))
  new Uint8Array(buf, HEADER_SIZE).set(payload)
  return buf
}

// ---------------------------------------------------------------- JSON messages

export type Box = [number, number, number, number] // normalised x, y, w, h

export interface Settings {
  camera_name: string
  resolution: '640x360' | '640x480' | '1280x720' | '1920x1080'
  fps: number
  bitrate_kbps: number
  gop_seconds: number
  mirror: boolean
  flip: boolean
  osd: boolean
  motion_enabled: boolean
  motion_sensitivity: number
  motion_cooldown_s: number
  tracking_enabled: boolean
  auto_record: boolean
  post_roll_s: number
  faces_enabled: boolean
  recognition_enabled: boolean
  emotion_enabled: boolean
  alert_unknown_faces: boolean
  audio_enabled: boolean
  sound_alarm_enabled: boolean
  sound_threshold_db: number
}

export interface Features {
  audio: boolean
  faces: { detection: boolean; recognition: boolean; emotion: boolean }
  auth: boolean
  mjpeg: boolean
  recording: boolean
}

export interface RecordingStatus {
  file: string
  reason: string
  startedAt: number
  duration: number
  manual: boolean
}

export interface ServerStats {
  serverTime: number
  uptime: number
  captureFps: number
  encodeFps: number
  analyticsFps: number
  bitrateKbps: number
  encodeMs: number
  analyticsMs: number
  viewers: number
  mjpegViewers: number
  audioLevel: number
  motion: boolean
  recording: RecordingStatus | null
  encoder: string | null
  resolution: string | null
  source: string | null
  captureStatus: string
  audioStatus: string
}

export interface FaceMeta {
  box: Box
  score: number
  name: string | null
  similarity: number | null
  emotion: string | null
  emotionScore: number | null
  landmarks: [number, number][]
}

export interface TrackMeta {
  id: number
  box: Box
  trail: [number, number][]
}

export interface Meta {
  ts: number
  motion?: { active: boolean; score: number; boxes: Box[] }
  tracks?: TrackMeta[]
  faces?: FaceMeta[]
}

export interface MonitorEvent {
  id: number
  ts: number
  kind: string
  level: 'info' | 'warning' | 'alarm'
  message: string
  snapshot: string | null
  data: Record<string, unknown> | null
}

export interface VideoConfig {
  codec: string
  width: number
  height: number
  fps: number
  bitrate: number
  encoder: string
}

export type ServerMessage =
  | { t: 'hello'; viewerId: number; serverTime: number; version: string; settings: Settings; features: Features; recording: RecordingStatus | null }
  | ({ t: 'video_config' } & VideoConfig)
  | { t: 'audio_config'; codec: string; sampleRate: number; channels: number }
  | ({ t: 'meta' } & Meta)
  | ({ t: 'stats' } & ServerStats)
  | { t: 'event'; event: MonitorEvent }
  | { t: 'settings'; settings: Settings }
  | { t: 'recording'; status: RecordingStatus | null }
  | { t: 'level'; db: number }
  | { t: 'pong'; c: number; s: number }
