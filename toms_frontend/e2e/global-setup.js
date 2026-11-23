import { spawnSync } from "node:child_process";
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

export default function globalSetup() {
  const python = pythonPath();
  if (!fs.existsSync(python)) {
    throw new Error(`Playwright seed cannot find Python at ${python}.`);
  }

  const result = spawnSync(
    python,
    [path.join(ROOT, "tools", "staging_manage.py"), "seed_playwright"],
    {
      cwd: ROOT,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
      encoding: "utf8",
    }
  );

  if (result.error) {
    throw new Error(`Playwright seed failed to start: ${result.error.message}`);
  }
  if (result.status !== 0) {
    throw new Error(
      `Playwright seed failed (${result.status}):\n${result.stderr || result.stdout}`
    );
  }
  if (result.stdout.trim()) console.log(result.stdout.trim());
}
