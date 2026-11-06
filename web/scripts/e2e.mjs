// End-to-end browser test: opens the real web UI in headless Chrome, verifies that H.264 is
// decoded via WebCodecs, measures frame rate / latency, exercises audio + recording and
// stores screenshots (used in the report) in ../docs/screenshots.
// Usage: node scripts/e2e.mjs [baseUrl]   (server must be running)
import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'

const base = process.argv[2] ?? 'https://localhost:8443'
const out = new URL('../../docs/screenshots/', import.meta.url)
mkdirSync(out, { recursive: true })
const exe = process.env.CHROME ?? 'C:/Program Files/Google/Chrome/Application/chrome.exe'

const browser = await chromium.launch({ executablePath: exe, headless: true, args: ['--autoplay-policy=no-user-gesture-required'] })
const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 })
const page = await ctx.newPage()
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

const fail = (msg) => {
  console.error('FAIL:', msg)
  process.exitCode = 1
}
const state = () => page.evaluate(() => {
  const s = window.__rm.getState()
  return { conn: s.conn, transport: s.transport, client: s.client, stats: s.stats, cfg: s.videoConfig, events: s.events.length, recording: s.recording }
})

const tStart = Date.now()
await page.goto(base)
if (process.env.RM_PASSWORD) {
  await page.fill('#pw', process.env.RM_PASSWORD)
  await page.click('button:has-text("Sign in")')
}
await page.waitForFunction(() => window.__rm?.getState().videoConfig !== null && window.__rm.getState().client.decoded > 0, null, { timeout: 20000, polling: 20 })
console.log('time to first frame:', Date.now() - tStart, 'ms (page load + connect + keyframe)')
await page.waitForFunction(() => window.__rm?.getState().client.decoded > 30, null, { timeout: 20000 })
await page.waitForTimeout(4000)
let s = await state()
console.log('secure context:', await page.evaluate(() => isSecureContext), '| transport:', s.transport, '| codec:', s.cfg?.codec)
console.log(`decoded ${s.client.decoded} frames, display ${s.client.fps} fps, latency ${s.client.latencyMs?.toFixed(1)} ms, rtt ${s.client.rttMs?.toFixed(2)} ms, rx ${s.client.kbps} kbit/s, dropped ${s.client.dropped}`)
if (s.transport !== 'webcodecs') fail('WebCodecs not used')
if (s.client.fps < 20) fail(`frame rate too low: ${s.client.fps}`)
if (!(s.client.latencyMs < 500)) fail(`latency too high: ${s.client.latencyMs}`)

// painted pixels are not black
const lum = await page.evaluate(() => {
  const c = document.querySelector('canvas')
  const ctx = c.getContext('2d')
  const d = ctx.getImageData(0, 0, c.width, c.height).data
  let sum = 0
  for (let i = 0; i < d.length; i += 4 * 97) sum += d[i] + d[i + 1] + d[i + 2]
  return sum / (d.length / (4 * 97)) / 3
})
console.log('mean luminance of painted frame:', lum.toFixed(1))
if (lum < 10) fail('canvas is black')

// wait for the synthetic intruder -> tracks / motion overlay
await page.waitForFunction(() => (window.__rm.getState().meta?.tracks?.length ?? 0) > 0, null, { timeout: 15000 }).catch(() => fail('no tracked object'))
await page.waitForTimeout(700)
await page.screenshot({ path: new URL('live.png', out).pathname.slice(1) })

// audio
await page.click('button[aria-label="Listen (unmute)"]')
await page.waitForTimeout(1500)
console.log('audio playing:', await page.evaluate(() => window.__rm.getState().audioOn))

// recording
await page.click('button[title="Start recording"]')
await page.waitForTimeout(3000)
await page.click('button[title^="Stop recording"]')
await page.waitForTimeout(1500)

for (const tab of ['Events', 'Recordings', 'Faces', 'Settings', 'Live']) {
  await page.click(`nav button:has-text("${tab}")`)
  await page.waitForTimeout(600)
  await page.screenshot({ path: new URL(`tab-${tab.toLowerCase()}.png`, out).pathname.slice(1) })
}
const recs = await page.evaluate(() => fetch('/api/recordings').then((r) => r.json()))
console.log('recordings on server:', recs.items.length, recs.items[0]?.file, recs.items[0]?.duration + 's')
if (!recs.items.length) fail('recording not saved')

// a second and third concurrent viewer (separate browser processes = separate PCs)
const extra = []
const extraBrowsers = []
for (let i = 0; i < 2; i++) {
  const b = await chromium.launch({ executablePath: exe, headless: true })
  extraBrowsers.push(b)
  const p = await (await b.newContext({ ignoreHTTPSErrors: true })).newPage()
  await p.goto(base)
  extra.push(p)
}
await Promise.all(extra.map((p) => p.waitForFunction(() => window.__rm?.getState().client.decoded > 10, null, { timeout: 15000 })))
await page.waitForTimeout(1500)
s = await state()
console.log('concurrent viewers (server):', s.stats.viewers)
if (s.stats.viewers < 3) fail('concurrent viewers not served')

// MJPEG fallback mode
await page.click('nav button:has-text("Settings")')
await page.click('button[aria-label="MJPEG compatibility mode"]')
await page.waitForTimeout(2500)
console.log('transport after toggle:', (await state()).transport, '| mjpeg viewers:', (await state()).stats.mjpegViewers)
await page.screenshot({ path: new URL('mjpeg-mode.png', out).pathname.slice(1) })
await page.click('button[aria-label="MJPEG compatibility mode"]')

if (errors.length) console.log('page errors:', errors)
console.log(process.exitCode ? 'E2E FAILED' : 'E2E PASSED')
await Promise.all(extraBrowsers.map((b) => b.close()))
await browser.close()
