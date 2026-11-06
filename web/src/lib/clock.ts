/**
 * NTP-style clock synchronisation with the server.
 *
 * The client sends its time c, the server answers with its time s. With the round-trip time
 * rtt = now - c, the server clock is assumed to have been read at c + rtt/2, so
 * offset = s - (c + rtt/2). Samples with the smallest RTT are the most accurate (least
 * queueing), so we keep the last N samples and use the offset of the minimum-RTT one.
 * With the offset, end-to-end latency = (clientNow + offset) - captureTimestamp.
 */
export interface ClockSample {
  rtt: number
  offset: number
}

export class ClockSync {
  private samples: ClockSample[] = []
  private readonly keep: number
  constructor(keep = 8) {
    this.keep = keep
  }

  add(clientSent: number, serverTime: number, clientReceived: number): ClockSample {
    const rtt = clientReceived - clientSent
    const sample = { rtt, offset: serverTime - (clientSent + rtt / 2) }
    this.samples.push(sample)
    if (this.samples.length > this.keep) this.samples.shift()
    return sample
  }

  get ready(): boolean {
    return this.samples.length > 0
  }

  /** offset in microseconds (server - client) */
  get offset(): number {
    if (!this.samples.length) return 0
    return this.samples.reduce((a, b) => (b.rtt < a.rtt ? b : a)).offset
  }

  get rtt(): number {
    if (!this.samples.length) return 0
    return this.samples[this.samples.length - 1].rtt
  }

  /** current time on the server clock, in microseconds */
  serverNow(clientNowUs = nowUs()): number {
    return clientNowUs + this.offset
  }
}

export function nowUs(): number {
  return Math.round((performance.timeOrigin + performance.now()) * 1000)
}
