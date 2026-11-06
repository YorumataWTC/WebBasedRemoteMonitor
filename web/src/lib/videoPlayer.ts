/**
 * Low-latency H.264 player built on WebCodecs.
 *
 * - Every access unit arriving over the WebSocket is passed to a hardware-accelerated
 *   VideoDecoder immediately (no jitter buffer: for live monitoring, freshness beats smoothness).
 * - Decoded VideoFrames are painted onto a <canvas> as soon as they are output.
 * - Robustness: decoding only starts at a keyframe; on any decoder error, or when the decode
 *   queue backs up (slow device), the decoder is reset and a fresh keyframe is requested
 *   from the server, so the picture recovers within one round trip.
 */
import type { VideoConfig } from './protocol'

export interface VideoStats {
  fps: number
  decoded: number
  dropped: number
  decodeQueue: number
  lastTimestamp: number
  hardware: string
}

export function webCodecsSupported(): boolean {
  return typeof window !== 'undefined' && 'VideoDecoder' in window && 'EncodedVideoChunk' in window
}

export class VideoPlayer {
  private decoder: VideoDecoder | null = null
  private config: VideoConfig | null = null
  private waitingForKey = true
  private paused = false
  private frameTimes: number[] = []
  private generation = 0
  readonly stats: VideoStats = { fps: 0, decoded: 0, dropped: 0, decodeQueue: 0, lastTimestamp: 0, hardware: 'n/a' }
  onFrame: ((timestamp: number) => void) | null = null
  onError: ((message: string) => void) | null = null

  private canvas: HTMLCanvasElement
  private readonly sendKeyframeRequest: () => void
  private lastKeyRequest = -Infinity
  /** IDR that arrived while the decoder was still being configured (saves one round trip) */
  private pendingKey: { data: Uint8Array; timestamp: number } | null = null

  constructor(canvas: HTMLCanvasElement, requestKeyframe: () => void) {
    this.canvas = canvas
    this.sendKeyframeRequest = requestKeyframe
    // Hidden tab: stop decoding (saves CPU, avoids codec reclamation); resume at next IDR.
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && this.config) this.recover()
    })
  }

  /** Throttled (max 1/s per viewer) so a struggling client cannot flood the stream with IDRs. */
  private requestKeyframe() {
    const now = performance.now()
    if (now - this.lastKeyRequest < 1000) return
    this.lastKeyRequest = now
    this.sendKeyframeRequest()
  }

  attach(canvas: HTMLCanvasElement) {
    this.canvas = canvas
    if (this.config) {
      canvas.width = this.config.width
      canvas.height = this.config.height
    }
  }

  async configure(cfg: VideoConfig): Promise<boolean> {
    const gen = ++this.generation
    this.config = cfg
    this.close()
    const config: VideoDecoderConfig = {
      codec: cfg.codec,
      codedWidth: cfg.width,
      codedHeight: cfg.height,
      optimizeForLatency: true,
      hardwareAcceleration: 'prefer-hardware',
    }
    let support = await VideoDecoder.isConfigSupported(config).catch(() => null)
    if (!support?.supported) {
      config.hardwareAcceleration = 'no-preference'
      support = await VideoDecoder.isConfigSupported(config).catch(() => null)
      if (!support?.supported) {
        this.onError?.(`codec ${cfg.codec} not supported by this browser`)
        return false
      }
    }
    if (gen !== this.generation) return true // superseded by a newer configure() call
    this.stats.hardware = config.hardwareAcceleration === 'prefer-hardware' ? 'hardware preferred' : 'software / auto'
    this.canvas.width = cfg.width
    this.canvas.height = cfg.height
    this.decoder = new VideoDecoder({
      output: (frame) => this.render(frame),
      error: (e) => {
        this.onError?.(`decoder error: ${e.message}`)
        this.recover()
      },
    })
    this.decoder.configure(config)
    this.waitingForKey = true
    const pending = this.pendingKey
    this.pendingKey = null
    if (pending) this.decode(pending.data, pending.timestamp, true)
    else this.requestKeyframe()
    return true
  }

  setPaused(paused: boolean) {
    this.paused = paused
    if (!paused) {
      this.waitingForKey = true
      this.requestKeyframe()
    }
  }

  decode(data: Uint8Array, timestamp: number, keyframe: boolean) {
    const dec = this.decoder
    if (this.paused) return
    if (document.hidden) {
      this.waitingForKey = true
      return
    }
    if (!dec || dec.state !== 'configured') {
      if (keyframe) this.pendingKey = { data: data.slice(), timestamp }
      if (dec?.state === 'closed') this.recover()
      return
    }
    if (this.waitingForKey) {
      if (!keyframe) {
        this.stats.dropped++
        return
      }
      this.waitingForKey = false
    }
    // Device cannot keep up -> skip to the next keyframe instead of accumulating latency.
    if (dec.decodeQueueSize > 6) {
      this.stats.dropped++
      this.recover()
      return
    }
    try {
      dec.decode(new EncodedVideoChunk({ type: keyframe ? 'key' : 'delta', timestamp, data }))
    } catch (e) {
      this.onError?.(String(e))
      this.recover()
    }
    this.stats.decodeQueue = dec.decodeQueueSize
  }

  private render(frame: VideoFrame) {
    try {
      const ctx = this.canvas.getContext('2d')
      if (ctx) {
        if (this.canvas.width !== frame.displayWidth || this.canvas.height !== frame.displayHeight) {
          this.canvas.width = frame.displayWidth
          this.canvas.height = frame.displayHeight
        }
        ctx.drawImage(frame, 0, 0)
      }
      this.stats.decoded++
      this.stats.lastTimestamp = frame.timestamp
      const now = performance.now()
      this.frameTimes.push(now)
      while (this.frameTimes.length && now - this.frameTimes[0] > 1000) this.frameTimes.shift()
      this.stats.fps = this.frameTimes.length
      this.onFrame?.(frame.timestamp)
    } finally {
      frame.close()
    }
  }

  private recover() {
    if (!this.config) return
    this.waitingForKey = true
    // After an error callback (or when the browser reclaims the codec of an inactive tab)
    // the decoder is 'closed' and cannot be reset -> build a new one.
    if (!this.decoder || this.decoder.state === 'closed') {
      void this.configure(this.config)
      return
    }
    try {
      this.decoder.reset()
      this.decoder.configure({
        codec: this.config.codec,
        codedWidth: this.config.width,
        codedHeight: this.config.height,
        optimizeForLatency: true,
      })
    } catch {
      void this.configure(this.config)
      return
    }
    this.requestKeyframe()
  }

  close() {
    if (this.decoder && this.decoder.state !== 'closed') this.decoder.close()
    this.decoder = null
  }
}
