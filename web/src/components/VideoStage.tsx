import {
  Camera,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Circle,
  Expand,
  Layers,
  Loader2,
  Minus,
  Pause,
  Play,
  Plus,
  RotateCcw,
  Square,
  Volume2,
  VolumeX,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type PointerEvent, type WheelEvent } from 'react'
import { api, urls } from '../lib/api'
import { connection } from '../lib/connection'
import { drawOverlay } from '../lib/overlay'
import { useStore } from '../store'
import { IconButton } from './ui'
import { cx } from '../lib/cx'

function VuMeter({ db }: { db: number }) {
  const pct = Math.max(0, Math.min(1, (db + 60) / 60))
  return (
    <div className="hidden h-1.5 w-16 overflow-hidden rounded-full bg-white/10 sm:block" title={`Audio level ${db.toFixed(0)} dBFS`}>
      <div
        className={cx('h-full rounded-full transition-[width] duration-100', pct > 0.85 ? 'bg-alarm' : pct > 0.6 ? 'bg-warn' : 'bg-ok')}
        style={{ width: `${pct * 100}%` }}
      />
    </div>
  )
}

export function VideoStage() {
  const videoCanvas = useRef<HTMLCanvasElement>(null)
  const overlayCanvas = useRef<HTMLCanvasElement>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const imgRef = useRef<HTMLImageElement>(null)

  const transport = useStore((s) => s.transport)
  const cfg = useStore((s) => s.videoConfig)
  const settings = useStore((s) => s.settings)
  const conn = useStore((s) => s.conn)
  const client = useStore((s) => s.client)
  const prefs = useStore((s) => s.prefs)
  const setPrefs = useStore((s) => s.setPrefs)
  const recording = useStore((s) => s.recording)
  const alarmUntil = useStore((s) => s.alarmUntil)
  const audioLevel = useStore((s) => s.audioLevel)
  const audioOn = useStore((s) => s.audioOn)
  const paused = useStore((s) => s.paused)
  const features = useStore((s) => s.features)
  const toast = useStore((s) => s.toast)

  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [now, setNow] = useState(() => Date.now())
  const [dragging, setDragging] = useState(false)
  const [showOverlayMenu, setShowOverlayMenu] = useState(false)
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null)

  const [vw, vh] = cfg ? [cfg.width, cfg.height] : (settings?.resolution ?? '1280x720').split('x').map(Number)

  // attach WebCodecs player to the canvas
  useEffect(() => {
    if (videoCanvas.current) connection.attachCanvas(videoCanvas.current)
  }, [transport])

  // redraw overlay whenever new metadata or overlay preferences arrive
  useEffect(() => {
    const draw = () => {
      const c = overlayCanvas.current
      if (!c) return
      const st = useStore.getState()
      drawOverlay(c, st.meta, { motion: st.prefs.showMotion, tracks: st.prefs.showTracks, faces: st.prefs.showFaces })
    }
    draw()
    return useStore.subscribe((s, prev) => {
      if (s.meta !== prev.meta || s.prefs !== prev.prefs) requestAnimationFrame(draw)
    })
  }, [vw, vh])

  // tick for alarm flash expiry
  useEffect(() => {
    if (alarmUntil <= Date.now()) return
    const id = setTimeout(() => setNow(Date.now()), alarmUntil - Date.now() + 50)
    return () => clearTimeout(id)
  }, [alarmUntil])
  const alarm = alarmUntil > now

  const clampPan = useCallback(
    (x: number, y: number, z: number) => {
      const max = (z - 1) / (2 * z)
      return { x: Math.max(-max, Math.min(max, x)), y: Math.max(-max, Math.min(max, y)) }
    },
    [],
  )
  const setZoomClamped = (z: number) => {
    const nz = Math.max(1, Math.min(6, z))
    setZoom(nz)
    setPan((p) => clampPan(p.x, p.y, nz))
  }
  const nudge = (dx: number, dy: number) => setPan((p) => clampPan(p.x + dx / zoom, p.y + dy / zoom, zoom))

  function onWheel(e: WheelEvent) {
    setZoomClamped(zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15))
  }
  function onPointerDown(e: PointerEvent) {
    if (zoom === 1) return
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
    drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y }
    setDragging(true)
  }
  function onPointerMove(e: PointerEvent) {
    const d = drag.current
    const el = stageRef.current
    if (!d || !el) return
    const r = el.getBoundingClientRect()
    setPan(clampPan(d.px + (e.clientX - d.x) / r.width / zoom, d.py + (e.clientY - d.y) / r.height / zoom, zoom))
  }

  function fullscreen() {
    const el = stageRef.current?.parentElement
    if (!el) return
    if (document.fullscreenElement) void document.exitFullscreen()
    else void el.requestFullscreen()
  }

  async function snapshot() {
    const name = `snapshot_${new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)}`
    const out = document.createElement('canvas')
    out.width = vw
    out.height = vh
    const ctx = out.getContext('2d')!
    try {
      if (transport === 'webcodecs' && videoCanvas.current) ctx.drawImage(videoCanvas.current, 0, 0, vw, vh)
      else if (imgRef.current) ctx.drawImage(imgRef.current, 0, 0, vw, vh)
      if (overlayCanvas.current) ctx.drawImage(overlayCanvas.current, 0, 0, vw, vh)
      const a = document.createElement('a')
      a.href = out.toDataURL('image/png')
      a.download = `${name}.png`
      a.click()
      toast({ level: 'success', title: 'Snapshot saved', body: `${name}.png` })
    } catch {
      window.open(urls.snapshot(), '_blank')
    }
  }

  async function toggleRecord() {
    try {
      if (recording) {
        await api.stopRecording()
        useStore.setState({ recording: null })
      } else {
        const st = await api.startRecording()
        useStore.setState({ recording: st })
      }
    } catch (e) {
      toast({ level: 'error', title: 'Recording failed', body: String((e as Error).message) })
    }
  }

  const live = conn === 'live' && (transport === 'mjpeg' || client.decoded > 0)

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-black">
      <div
        ref={stageRef}
        className={cx('group relative min-h-0 flex-1 overflow-hidden select-none', alarm && 'alarm-pulse')}
        onWheel={onWheel}
        onDoubleClick={fullscreen}
      >
        <div className="absolute inset-0 grid place-items-center" style={{ containerType: 'size' }}>
          {/* largest box with the video's aspect ratio that fits the stage (container query units) */}
          <div className="relative overflow-hidden" style={{ aspectRatio: `${vw} / ${vh}`, width: `min(100cqw, calc(100cqh * ${vw / vh}))` }}>
            <div
              className={cx('absolute inset-0', zoom > 1 && 'cursor-grab active:cursor-grabbing')}
              style={{ transform: `scale(${zoom}) translate(${pan.x * 100}%, ${pan.y * 100}%)`, transition: dragging ? 'none' : 'transform 120ms ease-out' }}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={() => {
                drag.current = null
                setDragging(false)
              }}
            >
              {transport === 'webcodecs' ? (
                <canvas ref={videoCanvas} className="h-full w-full object-contain" />
              ) : (
                <img ref={imgRef} src={conn === 'live' && !paused ? urls.mjpeg() : undefined} alt="Live video (MJPEG)" className="h-full w-full object-contain" />
              )}
              <canvas ref={overlayCanvas} width={vw} height={vh} className="pointer-events-none absolute inset-0 h-full w-full" />
            </div>
          </div>
        </div>

        {!live && (
          <div className="absolute inset-0 grid place-items-center bg-black/60">
            <div className="flex flex-col items-center gap-3 text-sm text-muted">
              <Loader2 className="animate-spin text-accent" size={28} />
              {conn === 'live' ? 'Waiting for keyframe…' : conn === 'reconnecting' ? 'Connection lost – reconnecting…' : 'Connecting to camera…'}
            </div>
          </div>
        )}

        {/* top-left status chips */}
        <div className="pointer-events-none absolute left-3 top-3 flex flex-wrap gap-1.5 font-mono text-[11px]">
          <span className="rounded bg-black/60 px-1.5 py-0.5 text-white/90">
            {transport === 'webcodecs' ? `WebCodecs · ${cfg?.codec ?? '…'}` : 'MJPEG fallback'}
          </span>
          {client.latencyMs !== null && transport === 'webcodecs' && (
            <span className="rounded bg-black/60 px-1.5 py-0.5 text-white/90">{Math.max(0, client.latencyMs).toFixed(0)} ms</span>
          )}
          {zoom > 1 && <span className="rounded bg-black/60 px-1.5 py-0.5 text-accent">{zoom.toFixed(1)}×</span>}
        </div>

        {/* digital PTZ pad */}
        <div className="absolute right-3 top-3 flex flex-col items-center gap-1 rounded-xl border border-white/10 bg-black/55 p-1.5 opacity-0 backdrop-blur transition-opacity group-hover:opacity-100 focus-within:opacity-100">
          <div className="grid grid-cols-3 gap-0.5">
            <span />
            <IconButton label="Pan up" onClick={() => nudge(0, 0.1)} disabled={zoom === 1}><ChevronUp size={16} /></IconButton>
            <span />
            <IconButton label="Pan left" onClick={() => nudge(0.1, 0)} disabled={zoom === 1}><ChevronLeft size={16} /></IconButton>
            <IconButton label="Reset view" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }) }}><RotateCcw size={14} /></IconButton>
            <IconButton label="Pan right" onClick={() => nudge(-0.1, 0)} disabled={zoom === 1}><ChevronRight size={16} /></IconButton>
            <span />
            <IconButton label="Pan down" onClick={() => nudge(0, -0.1)} disabled={zoom === 1}><ChevronDown size={16} /></IconButton>
            <span />
          </div>
          <div className="flex gap-0.5">
            <IconButton label="Zoom out" onClick={() => setZoomClamped(zoom / 1.4)}><Minus size={16} /></IconButton>
            <IconButton label="Zoom in" onClick={() => setZoomClamped(zoom * 1.4)}><Plus size={16} /></IconButton>
          </div>
        </div>
      </div>

      {/* control bar */}
      <div className="flex h-12 shrink-0 items-center gap-1 border-t border-line bg-panel px-2">
        <IconButton label={paused ? 'Resume live view' : 'Pause live view'} onClick={() => connection.setPaused(!paused)}>
          {paused ? <Play size={18} /> : <Pause size={18} />}
        </IconButton>
        {features?.audio && (
          <>
            <IconButton label={audioOn ? 'Mute audio' : 'Listen (unmute)'} active={audioOn} onClick={() => void connection.enableAudio(!audioOn)}>
              {audioOn ? <Volume2 size={18} /> : <VolumeX size={18} />}
            </IconButton>
            {audioOn && (
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={prefs.volume}
                aria-label="Volume"
                className="w-20"
                onChange={(e) => {
                  const v = Number(e.target.value)
                  setPrefs({ volume: v })
                  connection.audio.setVolume(v)
                }}
              />
            )}
            <VuMeter db={audioLevel} />
          </>
        )}
        <div className="mx-1 h-6 w-px bg-line" />
        <IconButton label="Take snapshot" onClick={snapshot}><Camera size={18} /></IconButton>
        <button
          onClick={toggleRecord}
          className={cx(
            'inline-flex h-9 items-center gap-2 rounded-lg px-3 text-sm font-medium transition-colors',
            recording ? 'bg-alarm/20 text-rose-300 hover:bg-alarm/30' : 'text-slate-200 hover:bg-white/10',
          )}
          title={recording ? 'Stop recording' : 'Start recording'}
        >
          {recording ? <Square size={14} className="fill-current" /> : <Circle size={14} className="fill-alarm text-alarm" />}
          <span className="hidden sm:inline">{recording ? `Stop · ${Math.floor(recording.duration)}s` : 'Record'}</span>
        </button>

        <div className="relative ml-auto">
          <IconButton label="Overlays" active={showOverlayMenu} onClick={() => setShowOverlayMenu((v) => !v)}>
            <Layers size={18} />
          </IconButton>
          {showOverlayMenu && (
            <div className="absolute bottom-11 right-0 z-20 w-48 rounded-xl border border-line bg-panel-2 p-2 text-sm shadow-xl">
              {(
                [
                  ['showMotion', 'Motion regions'],
                  ['showTracks', 'Object tracks'],
                  ['showFaces', 'Faces & emotions'],
                ] as const
              ).map(([k, label]) => (
                <label key={k} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 hover:bg-white/5">
                  <input type="checkbox" checked={prefs[k]} onChange={(e) => setPrefs({ [k]: e.target.checked })} />
                  {label}
                </label>
              ))}
            </div>
          )}
        </div>
        <IconButton label="Fullscreen" onClick={fullscreen}><Expand size={17} /></IconButton>
      </div>
    </div>
  )
}
