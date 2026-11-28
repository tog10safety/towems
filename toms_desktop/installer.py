"""Per-user Windows installer for TOWEMS Armory Desktop.

Built into ``TOWEMS-Armory-Setup.exe`` by ``installer.spec``. The application
payload is embedded as a PyInstaller data file, so the setup executable is
self-contained and needs no network or administrator rights.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import winreg
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_NAME = "TOWEMS Armory Management"
APP_VERSION = "1.0.0"
PUBLISHER = "Tactical Operations Wings"
TASK_NAME = "TOWEMS Armory Sync"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\TOWEMS-Armory"

BG = "#0d120e"
SURFACE = "#171f18"
TEXT = "#e9eedf"
MUTED = "#99a78f"
ACCENT = "#c7a955"
BORDER = "#344430"


def default_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(root) / "Programs" / "TOWEMS Armory"


def payload_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def start_menu_dir() -> Path:
    return (
        Path(os.environ.get("APPDATA") or str(Path.home()))
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "TOWEMS Armory"
    )


def ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_powershell(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def create_shortcut(target: Path, link: Path, arguments: str = "", icon: Path | None = None) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    icon_path = str(icon or target)
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut({ps_quote(str(link))}); "
        f"$s.TargetPath = {ps_quote(str(target))}; "
        f"$s.WorkingDirectory = {ps_quote(str(target.parent))}; "
        f"$s.IconLocation = {ps_quote(icon_path + ',0')}; "
        f"$s.Description = {ps_quote(APP_NAME)}; "
        f"$s.Arguments = {ps_quote(arguments)}; "
        "$s.Save()"
    )
    result = run_powershell(script)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Shortcut creation failed.")


def create_schedule(target: Path) -> tuple[bool, str]:
    result = subprocess.run(
        [
            "schtasks.exe",
            "/Create",
            "/SC",
            "WEEKLY",
            "/D",
            "SUN",
            "/ST",
            "23:00",
            "/TN",
            TASK_NAME,
            "/TR",
            f'"{target}" --sync-only',
            "/F",
        ],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def write_uninstaller(install_dir: Path) -> Path:
    path = install_dir / "uninstall.ps1"
    script = f"""$ErrorActionPreference = 'SilentlyContinue'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
schtasks.exe /Delete /TN {ps_quote(TASK_NAME)} /F | Out-Null
Remove-Item (Join-Path $env:APPDATA 'Microsoft\\Windows\\Start Menu\\Programs\\TOWEMS Armory') -Recurse -Force
Remove-Item (Join-Path $env:USERPROFILE 'Desktop\\TOWEMS Armory.lnk') -Force
Remove-Item {ps_quote('HKCU:' + UNINSTALL_KEY)} -Recurse -Force
$data = Join-Path $env:LOCALAPPDATA 'TOWEMS Armory Management'
if (Test-Path $data) {{
    Add-Type -AssemblyName System.Windows.Forms
    $answer = [System.Windows.Forms.MessageBox]::Show(
        'The application will be removed. Do you also want to permanently delete the local armory database, receipts and backups?`n`nChoose No to retain the data for recovery or lawful retention.',
        'TOWEMS Armory Uninstall',
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Warning
    )
    if ($answer -eq [System.Windows.Forms.DialogResult]::Yes) {{
        Remove-Item $data -Recurse -Force
    }}
}}
$cleanup = "Start-Sleep -Seconds 2; Remove-Item -LiteralPath '$dir' -Recurse -Force"
Start-Process -WindowStyle Hidden powershell.exe -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-Command',$cleanup
"""
    path.write_text(script, encoding="utf-8-sig")
    return path


def register_uninstall(install_dir: Path, target: Path, uninstaller: Path) -> None:
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    values = {
        "DisplayName": APP_NAME,
        "DisplayVersion": APP_VERSION,
        "Publisher": PUBLISHER,
        "InstallLocation": str(install_dir),
        "DisplayIcon": str(target),
        "UninstallString": (
            f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{uninstaller}"'
        ),
        "NoModify": 1,
        "NoRepair": 1,
    }
    with key:
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))


def install_files(
    install_dir: Path,
    *,
    desktop_shortcut: bool,
    start_menu: bool,
    schedule: bool,
) -> tuple[Path, list[str]]:
    install_dir = Path(install_dir).expanduser().resolve()
    install_dir.mkdir(parents=True, exist_ok=True)
    payload = payload_root() / "payload" / "TOWEMS-Armory.exe"
    if not payload.exists():
        raise FileNotFoundError(
            "The installer payload is missing. Rebuild TOWEMS-Armory-Setup.exe."
        )
    target = install_dir / "TOWEMS-Armory.exe"
    if target.exists():
        try:
            target.unlink()
        except PermissionError as exc:
            raise RuntimeError(
                "TOWEMS Armory is running. Close it before installing or upgrading."
            ) from exc
    shutil.copy2(payload, target)
    docs_source = payload_root() / "docs"
    if docs_source.exists():
        docs_target = install_dir / "docs"
        if docs_target.exists():
            shutil.rmtree(docs_target, ignore_errors=True)
        shutil.copytree(docs_source, docs_target)
    readme = payload_root() / "README.md"
    if readme.exists():
        shutil.copy2(readme, install_dir / "README.md")
    uninstaller = write_uninstaller(install_dir)
    warnings: list[str] = []
    if start_menu:
        try:
            create_shortcut(
                target,
                start_menu_dir() / "TOWEMS Armory.lnk",
                icon=install_dir / "TOWEMS-Armory.exe",
            )
            create_shortcut(
                Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
                start_menu_dir() / "Uninstall TOWEMS Armory.lnk",
                arguments=f'-NoProfile -ExecutionPolicy Bypass -File "{uninstaller}"',
                icon=target,
            )
        except Exception as exc:
            warnings.append(f"Start Menu shortcut: {exc}")
    if desktop_shortcut:
        try:
            create_shortcut(
                target,
                Path(os.environ.get("USERPROFILE") or str(Path.home()))
                / "Desktop"
                / "TOWEMS Armory.lnk",
                icon=target,
            )
        except Exception as exc:
            warnings.append(f"Desktop shortcut: {exc}")
    if schedule:
        ok, detail = create_schedule(target)
        if not ok:
            warnings.append(f"Scheduled task: {detail}")
    register_uninstall(install_dir, target, uninstaller)
    try:
        with (install_dir / "install.log").open("a", encoding="utf-8") as handle:
            handle.write(
                f"Installed {APP_NAME} {APP_VERSION} to {install_dir}\n"
                f"Desktop={desktop_shortcut} StartMenu={start_menu} Schedule={schedule}\n"
            )
    except Exception:
        pass
    return target, warnings


def parse_args(argv: list[str]) -> dict:
    text = [item.strip() for item in argv]
    lower = [item.lower() for item in text]
    directory = default_dir()
    for item in text:
        if item.lower().startswith("/dir="):
            value = item.split("=", 1)[1].strip().strip('"')
            if value:
                directory = Path(value)
    return {
        "silent": "/s" in lower,
        "directory": directory,
        "desktop": "/nodesktop" not in lower,
        "start_menu": "/nostartmenu" not in lower,
        "schedule": "/noschedule" not in lower,
    }


class SetupWindow(tk.Tk):
    def __init__(self, options: dict):
        super().__init__()
        self.options = options
        self.title(f"{APP_NAME} Setup")
        self.geometry("700x620")
        self.minsize(680, 580)
        self.configure(bg=BG)
        self.resizable(True, True)
        self._build()
        self._center()

    def _center(self):
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = (self.winfo_screenwidth() - width) // 2
        y = (self.winfo_screenheight() - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Install.TButton",
            background=ACCENT,
            foreground="#151207",
            font=("Segoe UI", 10, "bold"),
            borderwidth=0,
            padding=(18, 9),
        )
        style.map("Install.TButton", background=[("active", "#d7ba66")])
        style.configure("Cancel.TButton", padding=(14, 8))
        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        style.configure("TEntry", fieldbackground="#111812", foreground=TEXT)
        style.configure("Horizontal.TProgressbar", background=ACCENT)

        frame = tk.Frame(self, bg=BG, padx=34, pady=26)
        frame.pack(fill="both", expand=True)
        logo_path = payload_root() / "resources" / "logo.png"
        if logo_path.exists():
            try:
                self.logo = tk.PhotoImage(file=str(logo_path)).subsample(9, 9)
                tk.Label(frame, image=self.logo, bg=BG).pack(anchor="w", pady=(0, 8))
            except Exception:
                pass
        tk.Label(
            frame,
            text="TOWEMS ARMORY",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="w")
        tk.Label(
            frame,
            text="OFFLINE ARMORY MANAGEMENT · WINDOWS DESKTOP",
            bg=BG,
            fg=ACCENT,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(2, 18))
        tk.Label(
            frame,
            text=(
                "This setup installs the offline armory ledger for the current "
                "Windows user. It does not require administrator rights and does "
                "not modify the TOWEMS server."
            ),
            bg=BG,
            fg=MUTED,
            justify="left",
            wraplength=620,
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(0, 18))

        path_label = tk.Label(
            frame, text="Installation folder", bg=BG, fg=TEXT, font=("Segoe UI", 9, "bold")
        )
        path_label.pack(anchor="w")
        path_row = tk.Frame(frame, bg=BG)
        path_row.pack(fill="x", pady=(5, 14))
        self.path = tk.StringVar(value=str(self.options["directory"]))
        entry = tk.Entry(
            path_row,
            textvariable=self.path,
            bg="#111812",
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=("Segoe UI", 9),
        )
        entry.pack(side="left", fill="x", expand=True, ipady=7)
        browse = ttk.Button(path_row, text="Browse…", command=self._browse)
        browse.pack(side="left", padx=(8, 0))

        self.desktop = tk.BooleanVar(value=self.options["desktop"])
        self.start_menu = tk.BooleanVar(value=self.options["start_menu"])
        self.schedule = tk.BooleanVar(value=self.options["schedule"])
        for variable, text in (
            (self.desktop, "Create a desktop shortcut"),
            (self.start_menu, "Create Start Menu shortcuts"),
            (
                self.schedule,
                "Install the Sunday 23:00 synchronization task (--sync-only)",
            ),
        ):
            ttk.Checkbutton(frame, text=text, variable=variable).pack(
                anchor="w", pady=2
            )

        info = tk.Frame(frame, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        info.pack(fill="x", pady=(18, 12))
        tk.Label(
            info,
            text=(
                "Local armory data is stored separately under "
                "%LOCALAPPDATA%\\TOWEMS Armory Management and is retained when the "
                "application is uninstalled, unless you explicitly delete it."
            ),
            bg=SURFACE,
            fg=MUTED,
            justify="left",
            wraplength=600,
            padx=14,
            pady=10,
            font=("Segoe UI", 8),
        ).pack(anchor="w")

        self.status = tk.StringVar(value="Ready to install.")
        tk.Label(
            frame,
            textvariable=self.status,
            bg=BG,
            fg=MUTED,
            anchor="w",
            font=("Segoe UI", 9),
        ).pack(fill="x", pady=(4, 4))
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 12))
        actions = tk.Frame(frame, bg=BG)
        actions.pack(fill="x", pady=(4, 0))
        self.install_button = ttk.Button(
            actions, text="INSTALL", style="Install.TButton", command=self._install
        )
        self.install_button.pack(side="right")
        self.cancel_button = ttk.Button(
            actions, text="Cancel", style="Cancel.TButton", command=self.destroy
        )
        self.cancel_button.pack(side="right", padx=(0, 8))
        manual = tk.Button(
            actions,
            text="Read user manual",
            bg=BG,
            fg=MUTED,
            activebackground=BG,
            activeforeground=TEXT,
            bd=0,
            cursor="hand2",
            command=self._manual,
        )
        manual.pack(side="left")

    def _manual(self):
        manual_path = payload_root() / "docs" / "USER_MANUAL.md"
        if manual_path.exists():
            os.startfile(str(manual_path))
        else:
            messagebox.showinfo(APP_NAME, "The user manual is not bundled in this build.")

    def _browse(self):
        selected = filedialog.askdirectory(
            title="Choose installation folder",
            initialdir=str(Path(self.path.get()).parent),
        )
        if selected:
            self.path.set(selected)

    def _install(self):
        target_dir = Path(self.path.get()).expanduser()
        if not str(target_dir).strip():
            messagebox.showwarning(APP_NAME, "Choose an installation folder.")
            return
        if target_dir.exists() and any(target_dir.iterdir()):
            if not messagebox.askyesno(
                APP_NAME,
                f"The folder already contains files:\n{target_dir}\n\n"
                "Install/upgrade into this folder? Existing TOWEMS Armory files "
                "will be replaced. Local data is stored elsewhere and is not removed.",
            ):
                return
        self.install_button.configure(state="disabled")
        self.cancel_button.configure(state="disabled")
        self.status.set("Installing…")
        self.progress.start(12)

        def worker():
            try:
                target, warnings = install_files(
                    target_dir,
                    desktop_shortcut=self.desktop.get(),
                    start_menu=self.start_menu.get(),
                    schedule=self.schedule.get(),
                )
            except Exception as exc:
                self.after(0, lambda: self._failed(exc))
            else:
                self.after(0, lambda: self._done(target, warnings))

        threading.Thread(target=worker, daemon=True).start()

    def _failed(self, exc):
        self.progress.stop()
        self.install_button.configure(state="normal")
        self.cancel_button.configure(state="normal")
        self.status.set("Installation failed.")
        messagebox.showerror(APP_NAME, f"Installation failed:\n\n{exc}")

    def _done(self, target: Path, warnings: list[str]):
        self.progress.stop()
        self.status.set("Installation complete.")
        message = f"Installed to:\n{target.parent}"
        if warnings:
            message += "\n\nWarnings:\n" + "\n".join(warnings)
        message += "\n\nLaunch TOWEMS Armory now?"
        if messagebox.askyesno(APP_NAME, message):
            subprocess.Popen([str(target)], cwd=str(target.parent))
        self.destroy()


def main() -> int:
    options = parse_args(sys.argv[1:])
    if options["silent"]:
        try:
            target, warnings = install_files(
                options["directory"],
                desktop_shortcut=options["desktop"],
                start_menu=options["start_menu"],
                schedule=options["schedule"],
            )
        except Exception as exc:
            try:
                messagebox.showerror(APP_NAME, f"Installation failed:\n{exc}")
            except Exception:
                pass
            return 1
        return 0
    window = SetupWindow(options)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
