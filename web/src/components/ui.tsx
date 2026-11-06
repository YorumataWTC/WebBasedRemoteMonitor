/** Small, dependency-free UI primitives styled with Tailwind. */
import type { ButtonHTMLAttributes, ReactNode } from 'react'

import { cx } from '../lib/cx'

type Variant = 'primary' | 'ghost' | 'danger' | 'subtle'

export function Button({
  variant = 'subtle',
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  const styles: Record<Variant, string> = {
    primary: 'bg-accent text-slate-950 hover:bg-cyan-300 font-semibold',
    ghost: 'text-muted hover:text-text hover:bg-white/5',
    danger: 'bg-alarm/15 text-rose-300 hover:bg-alarm/25 border border-alarm/30',
    subtle: 'bg-panel-2 text-text hover:bg-[#18233c] border border-line',
  }
  return (
    <button
      className={cx(
        'inline-flex items-center justify-center gap-2 rounded-lg px-3 py-1.5 text-sm transition-colors disabled:opacity-40 disabled:pointer-events-none focus-visible:outline-2 focus-visible:outline-accent',
        styles[variant],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  )
}

export function IconButton({
  label,
  active,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; active?: boolean }) {
  return (
    <button
      aria-label={label}
      title={label}
      className={cx(
        'grid h-9 w-9 place-items-center rounded-lg transition-colors focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-40',
        active ? 'bg-accent/20 text-accent' : 'text-slate-200 hover:bg-white/10',
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  )
}

export function Toggle({ checked, onChange, disabled, label }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label?: string }) {
  return (
    <button
      role="switch"
      aria-label={label}
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx(
        'relative h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-40',
        checked ? 'bg-accent' : 'bg-[#26334f]',
      )}
    >
      <span className={cx('absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all', checked ? 'left-[18px]' : 'left-0.5')} />
    </button>
  )
}

export function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2">
      <div className="min-w-0">
        <div className="text-sm text-text">{label}</div>
        {hint && <div className="text-xs text-muted">{hint}</div>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  )
}

export function Section({ title, icon, children }: { title: string; icon?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-panel-2/60 px-4 py-3">
      <h3 className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted">
        {icon}
        {title}
      </h3>
      <div className="divide-y divide-line/60">{children}</div>
    </section>
  )
}

export function Badge({ tone = 'muted', children }: { tone?: 'muted' | 'ok' | 'warn' | 'alarm' | 'accent'; children: ReactNode }) {
  const tones = {
    muted: 'bg-white/5 text-muted border-line',
    ok: 'bg-ok/10 text-ok border-ok/30',
    warn: 'bg-warn/10 text-warn border-warn/30',
    alarm: 'bg-alarm/15 text-rose-300 border-alarm/40',
    accent: 'bg-accent/10 text-accent border-accent/30',
  }
  return <span className={cx('inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium', tones[tone])}>{children}</span>
}

export function Empty({ icon, title, hint }: { icon: ReactNode; title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center text-muted">
      <div className="opacity-60">{icon}</div>
      <div className="text-sm text-text/80">{title}</div>
      {hint && <div className="max-w-60 text-xs">{hint}</div>}
    </div>
  )
}

export function Modal({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm" onClick={onClose} role="dialog" aria-label={title}>
      <div className="w-full max-w-4xl overflow-hidden rounded-2xl border border-line bg-panel shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <div className="truncate text-sm font-medium">{title}</div>
          <button onClick={onClose} className="rounded-md px-2 py-1 text-muted hover:bg-white/5 hover:text-text" aria-label="Close">
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}
