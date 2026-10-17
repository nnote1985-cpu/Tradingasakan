import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { parseEnv } from "node:util";

const root = fileURLToPath(new URL("../", import.meta.url));
const web = fileURLToPath(new URL("../apps/web/", import.meta.url));
const vite = fileURLToPath(new URL("../apps/web/node_modules/vite/bin/vite.js", import.meta.url));
const python = fileURLToPath(new URL(process.platform === "win32"
  ? "../.venv/Scripts/python.exe" : "../.venv/bin/python", import.meta.url));
const children = [];
let stopping = false;
const envFile = new URL("../.env", import.meta.url);
// Local development should use the file the developer just edited, even when
// an older DATABASE_URL is still exported in the parent terminal.
const fileEnv = existsSync(envFile) ? parseEnv(readFileSync(envFile, "utf8")) : {};
const backendEnv = { ...process.env, ...fileEnv };
// The API only reads; worker settings set with setx (Testnet keys, the execution flag, alerts) stay
// out of it. Otherwise TESTNET_EXECUTION_ENABLED=true meets TRADING_MODE=SHADOW from .env and the
// API refuses to start, and the Testnet keys would sit in a process that never signs.
const WORKER_ONLY = /^(TESTNET_EXECUTION_ENABLED|BINANCE_TESTNET_.*|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID|DEADMAN_PING_URL|WORKER_.*)$/i;
for (const k of Object.keys(backendEnv)) if (WORKER_ONLY.test(k)) delete backendEnv[k];
if (fileEnv.DATABASE_URL && process.env.DATABASE_URL && fileEnv.DATABASE_URL !== process.env.DATABASE_URL) {
  console.warn("Using DATABASE_URL from root .env instead of the older terminal environment value.");
}

if (!existsSync(vite)) {
  console.error("Web dependencies are missing. Run: pnpm --dir apps/web install --frozen-lockfile");
  process.exit(1);
}
if (!existsSync(python)) {
  console.error("Python environment is missing. Run: uv sync");
  process.exit(1);
}
if (!existsSync(new URL("../.env", import.meta.url))) {
  console.warn("No root .env found. Copy .env.example to .env, or set SUPABASE_URL and DATABASE_URL in your environment.");
}

async function stop(code) {
  if (stopping) return;
  stopping = true;
  process.exitCode = code;
  await Promise.all(children.map((child) => new Promise((resolve) => {
    if (!child.pid || child.exitCode !== null || child.signalCode !== null) return resolve();
    if (process.platform === "win32") {
      // Stop only the process trees started by this launcher, including reload workers.
      const killer = spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
        stdio: "ignore", windowsHide: true,
      });
      killer.once("error", () => { child.kill(); resolve(); });
      killer.once("exit", resolve);
    } else {
      try { process.kill(-child.pid, "SIGTERM"); } catch { /* Already stopped. */ }
      resolve();
    }
  })));
}

function start(name, command, args, cwd, env = process.env) {
  const child = spawn(command, args, {
    cwd,
    env,
    stdio: "inherit",
    detached: process.platform !== "win32",
    windowsHide: true,
  });
  children.push(child);
  child.once("error", (error) => {
    console.error(`${name} could not start: ${error.message}`);
    void stop(1);
  });
  child.once("exit", (code, signal) => {
    if (stopping) return;
    console.error(`${name} stopped (${signal ?? code}). Stopping the development servers.`);
    void stop(code === 0 ? 0 : 1);
  });
}

process.once("SIGINT", () => { void stop(0); });
process.once("SIGTERM", () => { void stop(0); });

console.log("Starting API at http://localhost:8000 and web at http://localhost:5173");
console.log("Press Ctrl+C to stop both servers.");
start("API", python, [
  "-m", "uvicorn", "--factory", "api.main:app_from_env", "--app-dir", "apps/api",
  "--loop", "asyncio:SelectorEventLoop",
  "--port", "8000", "--reload", "--reload-dir", "apps/api", "--reload-dir", "backend/src",
], root, backendEnv);
start("Web", process.execPath, [vite, "--port", "5173", "--strictPort"], web);
