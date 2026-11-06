import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import './index.css'
import { useStore } from './store'

// exposed for the automated browser test (scripts/e2e.mjs) and debugging in DevTools
;(window as unknown as { __rm: typeof useStore }).__rm = useStore

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
