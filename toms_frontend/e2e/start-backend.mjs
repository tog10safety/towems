import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", "..");

function pythonPath() {
  if (process.env.TOWEMS_PYTHON) return process.env.TOWEMS_PYTHON;
  return process.platform === "win32"
    ? path.join(ROOT, ".venv-toms", "Scripts", "python.exe")
    : path.join(ROOT, ".venv-toms", "bin", "python");
}

const python = pythonPath();
if (!fs.existsSync(python)) {
  console.error(`Playwright backend launcher cannot find Python at ${python}.`);
  process.exit(1);
}

const child = spawn(
  python,
  [path.join(ROOT, "tools", "local_server.py"), "127.0.0.1:8000"],
  {
    cwd: ROOT,
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
    stdio: "inherit",
  }
);

child.on("error", (error) => {
  console.error("Playwright backend launcher failed:", error);
  process.exit(1);
});
child.on("exit", (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  else process.exit(code ?? 0);
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    if (!child.killed) child.kill(signal);
  });
}
