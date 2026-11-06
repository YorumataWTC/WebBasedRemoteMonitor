/** Audible + desktop alarm for 'alarm'-level events. */
import type { MonitorEvent } from './protocol'

let ctx: AudioContext | null = null

export function beep(pattern: number[] = [880, 660, 880], duration = 0.14) {
  // browsers only allow audio after the user interacted with the page
  if (navigator.userActivation && !navigator.userActivation.hasBeenActive) return
  try {
    ctx ??= new AudioContext()
    if (ctx.state === 'suspended') void ctx.resume()
    let t = ctx.currentTime
    for (const freq of pattern) {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'square'
      osc.frequency.value = freq
      gain.gain.setValueAtTime(0.0001, t)
      gain.gain.exponentialRampToValueAtTime(0.12, t + 0.01)
      gain.gain.exponentialRampToValueAtTime(0.0001, t + duration)
      osc.connect(gain).connect(ctx.destination)
      osc.start(t)
      osc.stop(t + duration + 0.02)
      t += duration + 0.05
    }
  } catch {
    /* audio not available */
  }
}

export async function requestNotifications(): Promise<boolean> {
  if (!('Notification' in window)) return false
  if (Notification.permission === 'granted') return true
  return (await Notification.requestPermission()) === 'granted'
}

export function notify(e: MonitorEvent, cameraName: string) {
  if (!('Notification' in window) || Notification.permission !== 'granted' || !document.hidden) return
  try {
    new Notification(`${cameraName}: ${e.message}`, {
      body: new Date(e.ts * 1000).toLocaleTimeString(),
      tag: `rm-${e.kind}`,
      icon: '/favicon.svg',
    })
  } catch {
    /* ignore */
  }
}
