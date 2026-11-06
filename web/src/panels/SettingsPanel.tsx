import { Bell, Camera, Mic, MonitorSmartphone, Radar, ScanFace } from 'lucide-react'
import { useRef, useState } from 'react'
import { Row, Section, Toggle } from '../components/ui'
import { requestNotifications } from '../lib/alarm'
import { api } from '../lib/api'
import { connection } from '../lib/connection'
import type { Settings } from '../lib/protocol'
import { webCodecsSupported } from '../lib/videoPlayer'
import { useStore } from '../store'

const select = 'rounded-lg border border-line bg-bg px-2 py-1 text-sm outline-none focus:border-accent'

function Slider({ value, min, max, step = 1, unit, onChange }: { value: number; min: number; max: number; step?: number; unit?: string; onChange: (v: number) => void }) {
  const [local, setLocal] = useState<number | null>(null) // non-null only while dragging
  const shown = local ?? value
  const commit = () => {
    if (local !== null) onChange(local)
    setLocal(null)
  }
  return (
    <div className="flex items-center gap-2">
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={shown}
        onChange={(e) => setLocal(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        className="w-28"
      />
      <span className="w-16 text-right font-mono text-xs text-muted">
        {shown}
        {unit}
      </span>
    </div>
  )
}

export function SettingsPanel() {
  const settings = useStore((s) => s.settings)
  const features = useStore((s) => s.features)
  const prefs = useStore((s) => s.prefs)
  const setPrefs = useStore((s) => s.setPrefs)
  const transport = useStore((s) => s.transport)
  const toast = useStore((s) => s.toast)
  const nameTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const [nameDraft, setNameDraft] = useState<string | null>(null)

  if (!settings) return null

  async function patch(p: Partial<Settings>) {
    useStore.setState({ settings: { ...settings!, ...p } }) // optimistic
    try {
      const s = await api.patchSettings(p)
      useStore.setState({ settings: s })
    } catch (e) {
      toast({ level: 'error', title: 'Setting rejected', body: (e as Error).message })
      useStore.setState({ settings })
    }
  }

  const facesOk = features?.faces.detection

  return (
    <div className="space-y-3">
      <Section title="Camera & stream" icon={<Camera size={13} />}>
        <Row label="Camera name">
          <input
            value={nameDraft ?? settings.camera_name}
            onBlur={() => setNameDraft(null)}
            maxLength={40}
            className={`${select} w-36`}
            onChange={(e) => {
              setNameDraft(e.target.value)
              clearTimeout(nameTimer.current)
              const v = e.target.value
              nameTimer.current = setTimeout(() => v.trim() && patch({ camera_name: v.trim() }), 600)
            }}
          />
        </Row>
        <Row label="Resolution">
          <select className={select} value={settings.resolution} onChange={(e) => patch({ resolution: e.target.value as Settings['resolution'] })}>
            <option value="640x360">640 × 360</option>
            <option value="640x480">640 × 480 (VGA)</option>
            <option value="1280x720">1280 × 720 (HD)</option>
            <option value="1920x1080">1920 × 1080 (Full HD)</option>
          </select>
        </Row>
        <Row label="Frame rate">
          <select className={select} value={settings.fps} onChange={(e) => patch({ fps: Number(e.target.value) })}>
            {[5, 10, 15, 20, 25, 30].map((f) => (
              <option key={f} value={f}>
                {f} fps
              </option>
            ))}
          </select>
        </Row>
        <Row label="Bit rate" hint="H.264 target (CBR-like with VBV cap)">
          <Slider value={settings.bitrate_kbps} min={300} max={8000} step={100} unit="k" onChange={(v) => patch({ bitrate_kbps: v })} />
        </Row>
        <Row label="Keyframe interval" hint="Longer = better compression">
          <Slider value={settings.gop_seconds} min={0.5} max={10} step={0.5} unit="s" onChange={(v) => patch({ gop_seconds: v })} />
        </Row>
        <Row label="Mirror">
          <Toggle label="Mirror" checked={settings.mirror} onChange={(v) => patch({ mirror: v })} />
        </Row>
        <Row label="Flip vertically">
          <Toggle label="Flip vertically" checked={settings.flip} onChange={(v) => patch({ flip: v })} />
        </Row>
        <Row label="Time-stamp OSD" hint="Burned into video and recordings">
          <Toggle label="Time-stamp OSD" checked={settings.osd} onChange={(v) => patch({ osd: v })} />
        </Row>
      </Section>

      <Section title="Motion detection" icon={<Radar size={13} />}>
        <Row label="Motion alarm">
          <Toggle label="Motion alarm" checked={settings.motion_enabled} onChange={(v) => patch({ motion_enabled: v })} />
        </Row>
        <Row label="Sensitivity">
          <Slider value={settings.motion_sensitivity} min={1} max={100} unit="%" onChange={(v) => patch({ motion_sensitivity: v })} />
        </Row>
        <Row label="Alarm hold time" hint="Quiet time before the alarm ends">
          <Slider value={settings.motion_cooldown_s} min={1} max={30} step={0.5} unit="s" onChange={(v) => patch({ motion_cooldown_s: v })} />
        </Row>
        <Row label="Object tracking">
          <Toggle label="Object tracking" checked={settings.tracking_enabled} onChange={(v) => patch({ tracking_enabled: v })} />
        </Row>
        <Row label="Record on motion" hint="With pre-roll from the last keyframes">
          <Toggle label="Record on motion" checked={settings.auto_record} onChange={(v) => patch({ auto_record: v })} />
        </Row>
        <Row label="Post-roll">
          <Slider value={settings.post_roll_s} min={1} max={60} unit="s" onChange={(v) => patch({ post_roll_s: v })} />
        </Row>
      </Section>

      <Section title="Face analytics" icon={<ScanFace size={13} />}>
        <Row label="Face detection" hint={facesOk ? 'YuNet CNN' : 'models missing: remote-monitor models'}>
          <Toggle label="Face detection" checked={settings.faces_enabled} disabled={!facesOk} onChange={(v) => patch({ faces_enabled: v })} />
        </Row>
        <Row label="Face recognition" hint="SFace embeddings">
          <Toggle label="Face recognition" checked={settings.recognition_enabled} disabled={!features?.faces.recognition} onChange={(v) => patch({ recognition_enabled: v })} />
        </Row>
        <Row label="Emotion recognition" hint="7 facial expressions">
          <Toggle label="Emotion recognition" checked={settings.emotion_enabled} disabled={!features?.faces.emotion} onChange={(v) => patch({ emotion_enabled: v })} />
        </Row>
        <Row label="Alarm on unknown person">
          <Toggle label="Alarm on unknown person" checked={settings.alert_unknown_faces} onChange={(v) => patch({ alert_unknown_faces: v })} />
        </Row>
      </Section>

      <Section title="Audio" icon={<Mic size={13} />}>
        <Row label="Stream audio" hint={features?.audio ? 'Opus, 48 kHz' : 'disabled on server'}>
          <Toggle label="Stream audio" checked={settings.audio_enabled} disabled={!features?.audio} onChange={(v) => patch({ audio_enabled: v })} />
        </Row>
        <Row label="Loud-noise alarm">
          <Toggle label="Loud-noise alarm" checked={settings.sound_alarm_enabled} disabled={!features?.audio} onChange={(v) => patch({ sound_alarm_enabled: v })} />
        </Row>
        <Row label="Noise threshold">
          <Slider value={settings.sound_threshold_db} min={-60} max={0} unit=" dB" onChange={(v) => patch({ sound_threshold_db: v })} />
        </Row>
      </Section>

      <Section title="This viewer" icon={<MonitorSmartphone size={13} />}>
        <Row label="Alarm sound">
          <Toggle label="Alarm sound" checked={prefs.alarmSound} onChange={(v) => setPrefs({ alarmSound: v })} />
        </Row>
        <Row label="Desktop notifications" hint="When the tab is in background">
          <Toggle label="Desktop notifications"
            checked={prefs.notifications}
            onChange={async (v) => setPrefs({ notifications: v && (await requestNotifications()) })}
          />
        </Row>
        <Row label="MJPEG compatibility mode" hint={webCodecsSupported() ? 'Use only if video does not play' : 'WebCodecs unavailable (needs HTTPS)'}>
          <Toggle label="MJPEG compatibility mode" checked={transport === 'mjpeg'} disabled={!webCodecsSupported()} onChange={(v) => connection.setTransport(v)} />
        </Row>
      </Section>
      <p className="flex items-center gap-1.5 px-1 text-[11px] text-muted">
        <Bell size={12} /> Server settings apply to all viewers and are saved on the server.
      </p>
    </div>
  )
}
