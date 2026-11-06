import { Eye, Lock } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { api } from '../lib/api'
import { useStore } from '../store'
import { Button } from './ui'

export function Login() {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.login(password)
      const session = await api.session()
      useStore.setState({ session: { ...session, checked: true } })
    } catch {
      setError('Wrong password')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid min-h-full place-items-center bg-[radial-gradient(ellipse_at_top,#0f2a3a_0%,#070b14_60%)] p-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-2xl border border-line bg-panel/90 p-8 shadow-2xl backdrop-blur">
        <div className="mb-6 flex items-center gap-3">
          <div className="grid h-11 w-11 place-items-center rounded-xl bg-accent/15 text-accent">
            <Eye size={22} />
          </div>
          <div>
            <h1 className="text-lg font-semibold">Remote Monitor</h1>
            <p className="text-xs text-muted">Sign in to view the live camera</p>
          </div>
        </div>
        <label className="mb-1.5 block text-xs font-medium text-muted" htmlFor="pw">
          Password
        </label>
        <div className="relative">
          <Lock size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            id="pw"
            type="password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-lg border border-line bg-bg py-2 pl-9 pr-3 text-sm outline-none focus:border-accent"
          />
        </div>
        {error && <p className="mt-2 text-xs text-rose-400">{error}</p>}
        <Button variant="primary" className="mt-5 w-full py-2" disabled={busy || !password}>
          {busy ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>
    </div>
  )
}
