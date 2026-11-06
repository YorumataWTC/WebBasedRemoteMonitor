import { describe, expect, it } from 'vitest'
import { ClockSync } from './clock'
import { toPixels, trackColor } from './overlay'
import { buildMedia, HEADER_SIZE, parseMedia, TYPE_AUDIO, TYPE_VIDEO } from './protocol'

describe('media protocol', () => {
  it('round-trips header and payload', () => {
    const ts = 1_793_936_286_593_926 // realistic µs timestamp (> 2^32)
    const buf = buildMedia(TYPE_VIDEO, ts, new Uint8Array([0, 0, 0, 1, 0x65]), true)
    expect(buf.byteLength).toBe(HEADER_SIZE + 5)
    const m = parseMedia(buf)
    expect(m.kind).toBe(TYPE_VIDEO)
    expect(m.keyframe).toBe(true)
    expect(m.timestamp).toBe(ts)
    expect(Array.from(m.payload)).toEqual([0, 0, 0, 1, 0x65])
  })

  it('parses the exact byte layout produced by the Python server', () => {
    // struct.pack(">BBHQ", 2, 0, 0, 1000) + b"\x01\x02"
    const bytes = new Uint8Array([2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x03, 0xe8, 1, 2])
    const m = parseMedia(bytes.buffer)
    expect(m.kind).toBe(TYPE_AUDIO)
    expect(m.keyframe).toBe(false)
    expect(m.timestamp).toBe(1000)
    expect(Array.from(m.payload)).toEqual([1, 2])
  })
})

describe('clock sync', () => {
  it('estimates offset from symmetric round trips and prefers the minimum-RTT sample', () => {
    const c = new ClockSync()
    // true offset: server is 5_000_000 µs ahead
    c.add(1_000_000, 6_010_000, 1_020_000) // rtt 20ms, symmetric -> offset 5_000_000
    c.add(2_000_000, 7_150_000, 2_200_000) // rtt 200ms, asymmetric queueing -> worse estimate
    expect(c.offset).toBe(5_000_000)
    expect(c.serverNow(3_000_000)).toBe(8_000_000)
  })
})

describe('overlay helpers', () => {
  it('maps normalised boxes to pixels', () => {
    expect(toPixels([0.5, 0.25, 0.1, 0.2], 1280, 720)).toEqual([640, 180, 128, 144])
  })
  it('assigns stable colours per track id', () => {
    expect(trackColor(3)).toBe(trackColor(10))
  })
})
