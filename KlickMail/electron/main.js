// KLICK_MAIL // optional Electron desktop wrapper.
// Serves ../client on a loopback port and loads it in a frameless-ish window,
// so the app runs from a real http:// origin (clean fetch + localStorage).
//
//   cd KlickMail/electron && npm install && npm start
//   npm run dist   # package a installer (needs electron-builder)

const { app, BrowserWindow, shell } = require("electron");
const http = require("http");
const path = require("path");
const fs = require("fs");

const CLIENT_DIR = path.join(__dirname, "..", "client");
const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".svg": "image/svg+xml", ".ico": "image/x-icon", ".json": "application/json",
  ".woff2": "font/woff2", ".map": "application/json",
};

function serve() {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      let rel = decodeURIComponent((req.url || "/").split("?")[0]);
      if (rel === "/" || rel === "") rel = "/index.html";
      const file = path.join(CLIENT_DIR, path.normalize(rel));
      if (!file.startsWith(CLIENT_DIR)) { res.statusCode = 403; return res.end("no"); }
      fs.readFile(file, (err, buf) => {
        if (err) { res.statusCode = 404; return res.end("not found"); }
        res.setHeader("Content-Type", MIME[path.extname(file)] || "application/octet-stream");
        res.setHeader("Cache-Control", "no-store");
        res.end(buf);
      });
    });
    srv.listen(0, "127.0.0.1", () => resolve(`http://127.0.0.1:${srv.address().port}/`));
  });
}

async function createWindow() {
  const url = await serve();
  const win = new BrowserWindow({
    width: 1280, height: 840, minWidth: 900, minHeight: 600,
    backgroundColor: "#000000",
    title: "KLICK_MAIL",
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  win.setMenuBarVisibility(false);
  win.loadURL(url);
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });
}

app.whenReady().then(createWindow);
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
