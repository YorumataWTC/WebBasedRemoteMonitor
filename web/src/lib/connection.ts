/**
 * MonitorConnection - the viewer side of the streaming protocol.
 *
 * Owns the WebSocket, reconnects with exponential back-off, routes binary media to the
 * video / audio players and JSON messages into the zustand store, and runs the clock
 * synchronisation used for the end-to-end latency measurement.
 */
import { useStore } from '../store'
import { beep, notify } from './alarm'
import { AudioPlayer } from './audioPlayer'
import { ClockSync, nowUs } from './clock'
import { parseMedia, TYPE_AUDIO, TYPE_VIDEO, type ServerMessage } from './protocol'
import { VideoPlayer, webCodecsSupported } from './videoPlayer'

export class MonitorConnection {
  private ws: WebSocket | null = null
  private retry = 0
  private timer: ReturnType<typeof setTimeout> | null = null
  private pingTimer: ReturnType<typeof setInterval> | null = null
  private statsTimer: ReturnType<typeof setInterval> | null = null
  private closed = false
  private bytes = 0
  private lastBytesAt = performance.now()
  readonly clock = new ClockSync()
  readonly audio = new AudioPlayer()
  video: VideoPlayer | null = null
  private canvas: HTMLCanvasElement | null = null

  attachCanvas(canvas: HTMLCanvasElement) {
    this.canvas = canvas
    if (this.video) this.video.attach(canvas)
    else this.video = this.makePlayer(canvas)
    const cfg = useStore.getState().videoConfig
    if (cfg && webCodecsSupported() && !useStore.getState().prefs.forceMjpeg) void this.video.configure(cfg)
  }

  private makePlayer(canvas: HTMLCanvasElement) {
    const p = new VideoPlayer(canvas, () => this.send({ t: 'keyframe' }))
    p.onError = (m) => console.warn('[video]', m)
    p.onFrame = (ts) => {
      if (this.clock.ready) {
        const latency = (this.clock.serverNow() - ts) / 1000
        const c = useStore.getState().client
        // smooth display value
        const l = c.latencyMs === null ? latency : c.latencyMs * 0.9 + latency * 0.1
        useStore.setState({ client: { ...c, latencyMs: l } })
      }
    }
    return p
  }

  start() {
    this.closed = false
    const useMjpeg = !webCodecsSupported() || useStore.getState().prefs.forceMjpeg
    useStore.setState({ transport: useMjpeg ? 'mjpeg' : 'webcodecs' })
    this.connect()
    this.statsTimer = setInterval(() => this.collectStats(), 1000)
  }

  stop() {
    this.closed = true
    if (this.timer) clearTimeout(this.timer)
    if (this.pingTimer) clearInterval(this.pingTimer)
    if (this.statsTimer) clearInterval(this.statsTimer)
    this.ws?.close()
    this.video?.close()
    void this.audio.stop()
  }

  send(msg: object) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg))
  }

  private connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const audio = this.audio.running ? '1' : '0'
    const ws = new WebSocket(`${proto}://${location.host}/ws?audio=${audio}`)
    ws.binaryType = 'arraybuffer'
    this.ws = ws
    useStore.setState({ conn: this.retry ? 'reconnecting' : 'connecting' })

    ws.onopen = () => {
      this.retry = 0
      useStore.setState({ conn: 'live' })
      this.ping()
      this.pingTimer = setInterval(() => this.ping(), 2000)
    }
    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') this.onJson(JSON.parse(ev.data) as ServerMessage)
      else this.onBinary(ev.data as ArrayBuffer)
    }
    ws.onclose = (ev) => {
      if (this.pingTimer) clearInterval(this.pingTimer)
      if (this.closed) return
      if (ev.code === 4401) {
        useStore.setState({ conn: 'unauthorized', session: { ...useStore.getState().session, authenticated: false } })
        return
      }
      useStore.setState({ conn: 'reconnecting' })
      const delay = Math.min(10000, 500 * 2 ** this.retry++)
      this.timer = setTimeout(() => this.connect(), delay)
    }
  }

  private ping() {
    const c = nowUs()
    this.send({ t: 'ping', c, rtt: this.clock.ready ? this.clock.rtt / 1000 : undefined })
  }

  private onBinary(buf: ArrayBuffer) {
    this.bytes += buf.byteLength
    const m = parseMedia(buf)
    if (m.kind === TYPE_VIDEO) {
      if (useStore.getState().transport === 'webcodecs') this.video?.decode(m.payload, m.timestamp, m.keyframe)
    } else if (m.kind === TYPE_AUDIO) {
      this.audio.decode(m.payload, m.timestamp)
    }
  }

  private onJson(msg: ServerMessage) {
    const st = useStore.getState()
    switch (msg.t) {
      case 'hello':
        useStore.setState({ settings: msg.settings, features: msg.features, recording: msg.recording })
        break
      case 'video_config': {
        const { t: _t, ...cfg } = msg
        void _t
        useStore.setState({ videoConfig: cfg })
        if (st.transport === 'webcodecs' && this.video) {
          void this.video.configure(cfg).then((ok) => {
            if (!ok) useStore.setState({ transport: 'mjpeg' })
          })
        }
        break
      }
      case 'meta':
        useStore.setState({ meta: msg })
        break
      case 'stats': {
        const { t: _t, ...stats } = msg
        void _t
        useStore.setState({ stats, recording: stats.recording })
        break
      }
      case 'level':
        useStore.setState({ audioLevel: msg.db })
        break
      case 'settings':
        useStore.setState({ settings: msg.settings })
        break
      case 'recording':
        useStore.setState({ recording: msg.status })
        break
      case 'pong': {
        const s = this.clock.add(msg.c, msg.s, nowUs())
        useStore.setState({ client: { ...useStore.getState().client, rttMs: s.rtt / 1000 } })
        break
      }
      case 'event': {
        const e = msg.event
        st.addEvent(e)
        if (e.level === 'alarm') {
          useStore.setState({ alarmUntil: Date.now() + 5000 })
          st.toast({ level: 'alarm', title: e.message, body: new Date(e.ts * 1000).toLocaleTimeString() })
          if (st.prefs.alarmSound) beep()
          if (st.prefs.notifications) notify(e, st.settings?.camera_name ?? 'Camera')
        } else if (e.kind === 'person' || e.kind === 'recording_saved') {
          st.toast({ level: 'info', title: e.message })
        }
        break
      }
    }
  }

  private collectStats() {
    const st = useStore.getState()
    const now = performance.now()
    const kbps = (this.bytes * 8) / (now - this.lastBytesAt)
    this.bytes = 0
    this.lastBytesAt = now
    const v = this.video?.stats
    const client = {
      ...st.client,
      kbps: Math.round(kbps),
      fps: v?.fps ?? 0,
      decoded: v?.decoded ?? 0,
      dropped: v?.dropped ?? 0,
      hardware: v?.hardware ?? 'n/a',
    }
    useStore.setState({ client })
    st.pushHistory(client.kbps, client.latencyMs, st.transport === 'webcodecs' ? client.fps : (st.stats?.encodeFps ?? 0))
  }

  // ---------------------------------------------------------------- user actions
  async enableAudio(on: boolean) {
    if (on) {
      await this.audio.start()
      this.audio.setVolume(useStore.getState().prefs.volume)
    } else {
      await this.audio.stop()
    }
    this.send({ t: 'audio', on })
    useStore.setState({ audioOn: on })
  }

  setPaused(paused: boolean) {
    this.video?.setPaused(paused)
    useStore.setState({ paused })
  }

  setTransport(mjpeg: boolean) {
    useStore.getState().setPrefs({ forceMjpeg: mjpeg })
    const transport = mjpeg || !webCodecsSupported() ? 'mjpeg' : 'webcodecs'
    useStore.setState({ transport })
    const cfg = useStore.getState().videoConfig
    if (transport === 'webcodecs' && cfg && this.canvas) {
      this.video ??= this.makePlayer(this.canvas)
      void this.video.configure(cfg)
    }
  }
}

export const connection = new MonitorConnection()
