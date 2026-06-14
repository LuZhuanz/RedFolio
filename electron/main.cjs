const { app, BrowserWindow, ipcMain } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const net = require("node:net");
const crypto = require("node:crypto");
const { spawn } = require("node:child_process");

let pythonProcess = null;
let serviceConfig = null;
let forceKillTimer = null;

if (process.platform === "linux" && process.env.REDFOLIO_DISABLE_ELECTRON_SANDBOX === "1") {
  app.commandLine.appendSwitch("no-sandbox");
}

function pickPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => resolve(port));
    });
    server.on("error", reject);
  });
}

function serviceRoot() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "service");
  }
  return path.join(__dirname, "..", "service");
}

function packagedServiceExecutable() {
  if (!app.isPackaged) {
    return null;
  }
  const binaryName = process.platform === "win32" ? "redfolio-service.exe" : "redfolio-service";
  const candidate = path.join(serviceRoot(), binaryName);
  return fs.existsSync(candidate) ? candidate : null;
}

function pythonExecutable() {
  return process.env.REDFOLIO_PYTHON || (process.platform === "win32" ? "python" : "python3");
}

async function waitForService(baseUrl, token) {
  const deadline = Date.now() + 30000;
  let lastError = null;

  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/api/health`, {
        headers: { "x-redfolio-token": token }
      });
      if (response.ok) {
        return;
      }
      lastError = new Error(`health returned ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }

  throw lastError || new Error("Python service did not start");
}

async function startPythonService() {
  const port = await pickPort();
  const token = crypto.randomUUID();
  const dbPath = path.join(app.getPath("userData"), "redfolio.sqlite3");
  const baseUrl = `http://127.0.0.1:${port}`;
  const serviceDir = serviceRoot();

  const packagedService = packagedServiceExecutable();
  const serviceArgs = ["--host", "127.0.0.1", "--port", String(port), "--token", token, "--db", dbPath];
  const command = packagedService || pythonExecutable();
  const args = packagedService ? serviceArgs : ["-m", "redfolio_service.main", ...serviceArgs];

  pythonProcess = spawn(command, args, {
    cwd: serviceDir,
    env: {
      ...process.env,
      PYTHONPATH: [serviceDir, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter)
    },
    stdio: ["ignore", "pipe", "pipe"]
  });

  pythonProcess.stdout.on("data", (chunk) => {
    console.log(`[redfolio-service] ${chunk.toString().trimEnd()}`);
  });
  pythonProcess.stderr.on("data", (chunk) => {
    console.error(`[redfolio-service] ${chunk.toString().trimEnd()}`);
  });
  pythonProcess.on("exit", (code, signal) => {
    if (forceKillTimer) {
      clearTimeout(forceKillTimer);
      forceKillTimer = null;
    }
    if (code !== 0 && code !== null) {
      console.error(`RedFolio service exited with code ${code}`);
    }
    if (signal) {
      console.error(`RedFolio service exited with signal ${signal}`);
    }
  });

  await waitForService(baseUrl, token);
  serviceConfig = { baseUrl, token };
}

async function createWindow() {
  const window = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 1040,
    minHeight: 680,
    title: "RedFolio",
    backgroundColor: "#f5f4ef",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    await window.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else if (!app.isPackaged) {
    await window.loadURL("http://127.0.0.1:5173");
  } else {
    await window.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

ipcMain.handle("redfolio:config", () => serviceConfig);

app.whenReady().then(async () => {
  await startPythonService();
  await createWindow();

  app.on("activate", async () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      await createWindow();
    }
  });
});

app.on("before-quit", () => {
  if (!pythonProcess || pythonProcess.killed) {
    return;
  }

  pythonProcess.kill("SIGTERM");
  forceKillTimer = setTimeout(() => {
    if (pythonProcess && !pythonProcess.killed) {
      pythonProcess.kill("SIGKILL");
    }
  }, 3000);
  forceKillTimer.unref?.();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
