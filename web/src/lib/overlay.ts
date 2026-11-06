/**
 * Vector overlay of analytics results, drawn on a transparent canvas stacked on the video.
 * All coordinates from the server are normalised (0..1), so the overlay is independent of
 * the stream resolution and stays sharp at any zoom level.
 */
import type { Box, Meta } from './protocol'

export interface OverlayOptions {
  motion: boolean
  tracks: boolean
  faces: boolean
}

const TRACK_COLORS = ['#22d3ee', '#a78bfa', '#f472b6', '#fbbf24', '#34d399', '#60a5fa', '#fb923c']

export function trackColor(id: number): string {
  return TRACK_COLORS[id % TRACK_COLORS.length]
}

export function toPixels(b: Box, w: number, h: number): [number, number, number, number] {
  return [b[0] * w, b[1] * h, b[2] * w, b[3] * h]
}

function label(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, color: string, scale: number) {
  ctx.font = `600 ${Math.round(13 * scale)}px Inter, Segoe UI, sans-serif`
  const pad = 4 * scale
  const tw = ctx.measureText(text).width
  const th = 16 * scale
  const ty = Math.max(0, y - th - 2 * scale)
  ctx.fillStyle = color
  ctx.globalAlpha = 0.9
  ctx.beginPath()
  ctx.roundRect(x, ty, tw + pad * 2, th, 3 * scale)
  ctx.fill()
  ctx.globalAlpha = 1
  ctx.fillStyle = '#05080f'
  ctx.fillText(text, x + pad, ty + th - 4 * scale)
}

function corners(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, len: number) {
  ctx.beginPath()
  ctx.moveTo(x, y + len)
  ctx.lineTo(x, y)
  ctx.lineTo(x + len, y)
  ctx.moveTo(x + w - len, y)
  ctx.lineTo(x + w, y)
  ctx.lineTo(x + w, y + len)
  ctx.moveTo(x + w, y + h - len)
  ctx.lineTo(x + w, y + h)
  ctx.lineTo(x + w - len, y + h)
  ctx.moveTo(x + len, y + h)
  ctx.lineTo(x, y + h)
  ctx.lineTo(x, y + h - len)
  ctx.stroke()
}

export function drawOverlay(canvas: HTMLCanvasElement, meta: Meta | null, opt: OverlayOptions) {
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const { width: w, height: h } = canvas
  ctx.clearRect(0, 0, w, h)
  if (!meta) return
  const s = w / 1280 // line widths / fonts relative to a 720p reference

  if (opt.motion && meta.motion?.boxes?.length && !(opt.tracks && meta.tracks?.length)) {
    ctx.strokeStyle = '#fb923c'
    ctx.lineWidth = 2 * s
    ctx.setLineDash([8 * s, 6 * s])
    for (const b of meta.motion.boxes) {
      const [x, y, bw, bh] = toPixels(b, w, h)
      ctx.strokeRect(x, y, bw, bh)
    }
    ctx.setLineDash([])
  }

  if (opt.tracks && meta.tracks) {
    for (const t of meta.tracks) {
      const color = trackColor(t.id)
      const [x, y, bw, bh] = toPixels(t.box, w, h)
      ctx.strokeStyle = color
      ctx.lineWidth = 2.5 * s
      corners(ctx, x, y, bw, bh, Math.min(bw, bh) * 0.25)
      ctx.globalAlpha = 0.12
      ctx.fillStyle = color
      ctx.fillRect(x, y, bw, bh)
      ctx.globalAlpha = 1
      if (t.trail.length > 1) {
        ctx.lineWidth = 2 * s
        ctx.lineCap = 'round'
        for (let i = 1; i < t.trail.length; i++) {
          ctx.globalAlpha = i / t.trail.length
          ctx.beginPath()
          ctx.moveTo(t.trail[i - 1][0] * w, t.trail[i - 1][1] * h)
          ctx.lineTo(t.trail[i][0] * w, t.trail[i][1] * h)
          ctx.stroke()
        }
        ctx.globalAlpha = 1
      }
      label(ctx, `ID ${t.id}`, x, y, color, s)
    }
  }

  if (opt.faces && meta.faces) {
    for (const f of meta.faces) {
      const known = !!f.name
      const color = known ? '#34d399' : '#facc15'
      const [x, y, bw, bh] = toPixels(f.box, w, h)
      ctx.strokeStyle = color
      ctx.lineWidth = 2 * s
      ctx.beginPath()
      ctx.roundRect(x, y, bw, bh, 6 * s)
      ctx.stroke()
      ctx.fillStyle = color
      for (const [lx, ly] of f.landmarks) {
        ctx.beginPath()
        ctx.arc(lx * w, ly * h, 2.2 * s, 0, Math.PI * 2)
        ctx.fill()
      }
      // similarity is only present when a gallery comparison happened
      let text = known ? f.name! : f.similarity !== null ? 'Unknown' : 'Face'
      if (f.emotion) text += ` · ${f.emotion}`
      label(ctx, text, x, y, color, s)
    }
  }
}
