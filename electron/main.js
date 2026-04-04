/**
 * Electron main process — opens a desktop window for the Vecna Ops UI.
 *
 * Dev (default): loads the Vite dev server (http://localhost:5173) so hot reload works.
 *   Run: backend on :8000, `npm run dev` in frontend/, then `npm start` here.
 *
 * Built UI: set ELECTRON_USE_DEV_SERVER=0 and build the frontend first; loads file from
 *   ../frontend/dist/index.html. Set VITE_API_ORIGIN when building (see README in this folder).
 */

const { app, BrowserWindow, shell } = require('electron')
const path = require('node:path')

/** Prefer dev URL when not packaged, unless ELECTRON_USE_DEV_SERVER=0 */
function useDevServer() {
  if (process.env.ELECTRON_USE_DEV_SERVER === '0') return false
  if (process.env.ELECTRON_USE_DEV_SERVER === '1') return true
  return !app.isPackaged
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 800,
    minHeight: 600,
    title: 'Vecna Operations Console',
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  win.once('ready-to-show', () => win.show())

  if (useDevServer()) {
    const url = process.env.ELECTRON_START_URL || 'http://localhost:5173'
    win
      .loadURL(url)
      .then(() => {
        if (process.env.ELECTRON_OPEN_DEVTOOLS === '1') {
          win.webContents.openDevTools({ mode: 'detach' })
        }
      })
      .catch(() => {
        win.loadURL(
          'data:text/html;charset=utf-8,' +
            encodeURIComponent(`<!DOCTYPE html>
<html><head><meta charset="utf-8"/><title>Vecna Ops</title></head>
<body style="font-family:system-ui;padding:2rem;line-height:1.5;max-width:36rem;margin:auto;">
  <h1 style="font-size:1.25rem;">Cannot reach the dev server</h1>
  <p>Start Vite first, then restart Electron:</p>
  <pre style="background:#f4f4f5;padding:1rem;border-radius:8px;overflow:auto;">cd frontend && npm run dev</pre>
  <p style="color:#71717a;font-size:0.9rem;">Backend should listen on <code>127.0.0.1:8000</code> (Vite proxies <code>/api</code> and <code>/ws</code>).</p>
</body></html>`),
        )
        if (process.env.ELECTRON_OPEN_DEVTOOLS === '1') {
          win.webContents.openDevTools({ mode: 'detach' })
        }
      })
  } else {
    const indexHtml = path.join(__dirname, '..', 'frontend', 'dist', 'index.html')
    win.loadFile(indexHtml).catch((err) => {
      console.error('Failed to load built UI:', err)
    })
  }

  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })
}

app.whenReady().then(() => {
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
