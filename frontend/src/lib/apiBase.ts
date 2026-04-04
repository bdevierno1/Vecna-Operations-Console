/**
 * API and WebSocket base URL.
 *
 * - **Empty** (default): same origin — browser dev and Electron pointed at `http://localhost:5173`
 *   use Vite’s proxy to the backend.
 * - **Set** `VITE_API_ORIGIN=http://127.0.0.1:8000` when serving the built UI from `file://`
 *   (e.g. Electron loading `frontend/dist`); there is no origin, so requests must target the API explicitly.
 */
const RAW = (import.meta.env.VITE_API_ORIGIN as string | undefined)?.trim() ?? ''

export const API_ORIGIN = RAW.replace(/\/$/, '')

export function apiUrl(path: string): string {
  const p = path.startsWith('/') ? path : `/${path}`
  return `${API_ORIGIN}${p}`
}

export function wsUrl(path: string): string {
  const p = path.startsWith('/') ? path : `/${path}`
  if (API_ORIGIN) {
    const u = new URL(API_ORIGIN.includes('://') ? API_ORIGIN : `http://${API_ORIGIN}`)
    const wsProto = u.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${wsProto}//${u.host}${p}`
  }
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}${p}`
}
