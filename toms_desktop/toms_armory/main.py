"""Application entry point and headless synchronization mode."""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from . import config, db


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "TOWEMS.Armory.Desktop.1"
        )
    except Exception:
        pass


def _install_exception_hook() -> None:
    def hook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            path = config.logs_dir() / f"error-{datetime.now():%Y%m%d}.log"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{text}\n")
        except Exception:
            pass
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None,
                    "TOWEMS Armory",
                    "An unexpected error occurred. The details were written to the log "
                    f"folder:\n{config.logs_dir()}\n\n{exc_value}",
                )
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = hook


def _headless_sync() -> int:
    from .sync import run_headless_sync

    db.init_db()
    result = run_headless_sync()
    lines = [f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [{result.status.upper()}] {result.message}"]
    for item in result.errors:
        lines.append(f"  - {item.get('action', 'sync')}: {item.get('error', '')}")
    text = "\n".join(lines)
    print(text)
    try:
        with (config.logs_dir() / "sync.log").open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    except Exception:
        pass
    if result.status in {"success", "partial", "skipped"}:
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TOWEMS offline armory desktop")
    parser.add_argument(
        "--sync-only",
        action="store_true",
        help="Run one headless synchronization using the saved session and exit.",
    )
    parser.add_argument(
        "--data-dir",
        default="",
        help="Use a specific local data directory (diagnostics/portable mode).",
    )
    parser.add_argument("--version", action="store_true", help="Print the version.")
    args = parser.parse_args(argv)
    if args.data_dir:
        os.environ["TOWEMS_ARMORY_DATA_DIR"] = str(Path(args.data_dir).expanduser())
    if args.version:
        print(f"{config.APP_NAME} {config.APP_VERSION}")
        return 0
    _set_windows_app_id()
    _install_exception_hook()
    if args.sync_only:
        return _headless_sync()

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .ui.forms import ChangePasswordDialog
    from .ui.login import ensure_authenticated
    from .ui.main_window import MainWindow
    from .ui.theme import apply_theme

    app = QApplication(sys.argv[:1])
    app.setApplicationName(config.APP_NAME)
    app.setApplicationDisplayName(config.APP_NAME)
    app.setOrganizationName(config.ORGANISATION)
    app.setApplicationVersion(config.APP_VERSION)
    logo = config.resource_path("logo.png")
    if logo.exists():
        app.setWindowIcon(QIcon(str(logo)))
    apply_theme(app)
    try:
        db.init_db()
    except Exception as exc:
        QMessageBox.critical(
            None,
            "Database error",
            f"The local armory database could not be opened:\n{exc}\n\n"
            f"Data folder: {config.data_dir()}",
        )
        return 1
    actor = ensure_authenticated()
    if actor is None:
        return 0
    if getattr(actor, "must_change_password", False):
        from PySide6.QtWidgets import QDialog

        dialog = ChangePasswordDialog(None, user=actor)
        if dialog.exec() != QDialog.Accepted:
            return 0
        actor.must_change_password = False
        QMessageBox.information(
            None,
            "Password updated",
            "Your local password has been changed. Sign in again with the new password.",
        )
        actor = ensure_authenticated(username=actor.username)
        if actor is None:
            return 0
    window = MainWindow(actor)
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
