import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react'
import { useStore } from '../store'
import { cx } from '../lib/cx'

export function Toasts() {
  const toasts = useStore((s) => s.toasts)
  const dismiss = useStore((s) => s.dismissToast)
  return (
    <div className="pointer-events-none fixed bottom-4 left-1/2 z-50 flex w-full max-w-sm -translate-x-1/2 flex-col gap-2 px-4" aria-live="polite">
      {toasts.map((t) => (
        <button
          key={t.id}
          onClick={() => dismiss(t.id)}
          className={cx(
            'toast-in pointer-events-auto flex items-start gap-3 rounded-xl border px-4 py-3 text-left shadow-2xl backdrop-blur',
            t.level === 'alarm' && 'border-alarm/50 bg-[#2a0d16]/95',
            t.level === 'error' && 'border-alarm/40 bg-panel/95',
            t.level === 'warning' && 'border-warn/40 bg-panel/95',
            (t.level === 'info' || t.level === 'success') && 'border-line bg-panel/95',
          )}
        >
          <span className="mt-0.5">
            {t.level === 'alarm' && <AlertTriangle size={18} className="text-alarm" />}
            {t.level === 'error' && <XCircle size={18} className="text-alarm" />}
            {t.level === 'warning' && <AlertTriangle size={18} className="text-warn" />}
            {t.level === 'success' && <CheckCircle2 size={18} className="text-ok" />}
            {t.level === 'info' && <Info size={18} className="text-accent" />}
          </span>
          <span className="min-w-0">
            <span className="block text-sm font-medium">{t.title}</span>
            {t.body && <span className="block truncate text-xs text-muted">{t.body}</span>}
          </span>
        </button>
      ))}
    </div>
  )
}
