/**
 * Opus audio playback: WebCodecs AudioDecoder -> Web Audio scheduling.
 *
 * Each 20 ms packet is decoded to PCM and scheduled back-to-back on the AudioContext timeline.
 * A small jitter buffer (60 ms) absorbs network jitter; if the scheduled queue grows beyond
 * 300 ms (e.g. the tab was in background), playback is re-anchored to "now" to keep audio in
 * step with the live video instead of drifting behind.
 */
export class AudioPlayer {
  private ctx: AudioContext | null = null
  private decoder: AudioDecoder | null = null
  private gain: GainNode | null = null
  private playTime = 0
  private readonly targetDelay = 0.06
  private readonly maxDelay = 0.3
  volume = 0.8

  static supported(): boolean {
    return typeof window !== 'undefined' && 'AudioDecoder' in window
  }

  /** Must be called from a user gesture (browser autoplay policy). */
  async start(sampleRate = 48000, channels = 1) {
    if (this.ctx) return
    this.ctx = new AudioContext({ sampleRate, latencyHint: 'interactive' })
    await this.ctx.resume()
    this.gain = this.ctx.createGain()
    this.gain.gain.value = this.volume
    this.gain.connect(this.ctx.destination)
    this.decoder = new AudioDecoder({
      output: (data) => this.play(data),
      error: () => this.resetDecoder(sampleRate, channels),
    })
    this.decoder.configure({ codec: 'opus', sampleRate, numberOfChannels: channels })
    this.playTime = 0
  }

  private resetDecoder(sampleRate: number, channels: number) {
    try {
      this.decoder?.reset()
      this.decoder?.configure({ codec: 'opus', sampleRate, numberOfChannels: channels })
    } catch {
      /* ignore */
    }
  }

  setVolume(v: number) {
    this.volume = v
    if (this.gain) this.gain.gain.value = v
  }

  decode(data: Uint8Array, timestamp: number) {
    if (!this.decoder || this.decoder.state !== 'configured') return
    try {
      this.decoder.decode(new EncodedAudioChunk({ type: 'key', timestamp, data }))
    } catch {
      /* drop packet */
    }
  }

  private play(data: AudioData) {
    const ctx = this.ctx
    if (!ctx || !this.gain) {
      data.close()
      return
    }
    const buffer = ctx.createBuffer(data.numberOfChannels, data.numberOfFrames, data.sampleRate)
    for (let ch = 0; ch < data.numberOfChannels; ch++) {
      const pcm = new Float32Array(data.numberOfFrames)
      data.copyTo(pcm, { planeIndex: ch, format: 'f32-planar' })
      buffer.copyToChannel(pcm, ch)
    }
    data.close()
    const now = ctx.currentTime
    if (this.playTime < now || this.playTime - now > this.maxDelay) this.playTime = now + this.targetDelay
    const src = ctx.createBufferSource()
    src.buffer = buffer
    src.connect(this.gain)
    src.start(this.playTime)
    this.playTime += buffer.duration
  }

  async stop() {
    if (this.decoder && this.decoder.state !== 'closed') this.decoder.close()
    this.decoder = null
    await this.ctx?.close().catch(() => undefined)
    this.ctx = null
    this.gain = null
  }

  get running(): boolean {
    return this.ctx !== null
  }
}
