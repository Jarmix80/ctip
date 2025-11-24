# ruff: noqa: E402
"""Windows service wrapper for collector_full.py."""
import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from sysconfig import get_paths


def _ensure_pywin32_paths():
    exe = Path(sys.executable).resolve()
    bases = {exe.parent, exe.parent.parent}
    platlib = get_paths().get("platlib")
    if platlib:
        bases.add(Path(platlib))

    extras = ("pywin32_system32", "win32", "pythonwin")
    for base in bases:
        for extra in extras:
            candidate = base / extra
            if candidate.exists():
                candidate_str = str(candidate)
                if candidate_str not in sys.path:
                    sys.path.insert(0, candidate_str)
                if hasattr(os, "add_dll_directory"):
                    try:
                        os.add_dll_directory(candidate_str)
                    except FileNotFoundError:
                        pass


_ensure_pywin32_paths()

servicemanager = importlib.import_module("servicemanager")
win32event = importlib.import_module("win32event")
win32service = importlib.import_module("win32service")
win32serviceutil = importlib.import_module("win32serviceutil")

DEFAULT_ROOT = Path(r"D:\CTIP")
DEFAULT_CONFIG_NAME = "collector_service_config.json"


def _load_config():
    base_dir = Path(os.environ.get("CTIP_SERVICE_WORKDIR", DEFAULT_ROOT))
    config_path = Path(os.environ.get("CTIP_SERVICE_CONFIG", base_dir / DEFAULT_CONFIG_NAME))

    try:
        with config_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {}
    except Exception as exc:  # pragma: no cover - log and fallback silently in Windows service
        try:
            servicemanager.LogErrorMsg(f"CollectorService config load error: {exc}")
        except Exception:
            pass
        data = {}
    return data, base_dir, config_path


class CollectorService(win32serviceutil.ServiceFramework):
    _svc_name_ = "CollectorService"
    _svc_display_name_ = "Collector Service (collector_full.py)"
    _svc_description_ = "Runs collector_full.py in background and restarts on crash."

    def __init__(self, args):
        super().__init__(args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self.proc = None
        cfg, default_base_dir, _ = _load_config()
        self.work_dir = cfg.get("work_dir") or str(default_base_dir)
        self.python = cfg.get("python") or os.path.join(
            self.work_dir, ".venv", "Scripts", "python.exe"
        )
        self.script = cfg.get("script") or os.path.join(self.work_dir, "collector_full.py")

        default_log_dir = cfg.get("log_dir") or os.path.join(self.work_dir, "logs", "collector")
        stdout_default = os.path.join(default_log_dir, "collector_stdout.log")
        stderr_default = os.path.join(default_log_dir, "collector_stderr.log")
        self.stdout_log = cfg.get("stdout_log") or stdout_default
        self.stderr_log = cfg.get("stderr_log") or stderr_default

        for log_path in (self.stdout_log, self.stderr_log):
            os.makedirs(os.path.dirname(log_path), exist_ok=True)

    def _start_child(self):
        out_f = open(self.stdout_log, "ab", buffering=0)
        err_f = open(self.stderr_log, "ab", buffering=0)
        err_f.write(b"\n=== starting collector_full.py ===\n")
        child_env = os.environ.copy()
        child_env.setdefault("PYTHONIOENCODING", "utf-8")
        self.proc = subprocess.Popen(
            [self.python, self.script],
            cwd=self.work_dir,
            stdout=out_f,
            stderr=err_f,
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=child_env,
        )
        return out_f, err_f

    def _stop_child(self):
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=15)
                except Exception:
                    pass
                if self.proc.poll() is None:
                    self.proc.kill()
        except Exception:
            pass
        finally:
            self.proc = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)
        self._stop_child()

    def SvcDoRun(self):
        self.ReportServiceStatus(win32service.SERVICE_START_PENDING)
        servicemanager.LogInfoMsg("CollectorService starting")

        try:
            out_f, err_f = self._start_child()
        except Exception as e:
            try:
                servicemanager.LogErrorMsg(f"CollectorService failed to start child: {e}")
            except Exception:
                pass
            self.ReportServiceStatus(win32service.SERVICE_STOPPED)
            return

        # CRITICAL: tell SCM we are running so it does not time out
        self.ReportServiceStatus(win32service.SERVICE_RUNNING)

        backoff = 1
        max_backoff = 60

        while True:
            if win32event.WaitForSingleObject(self.hWaitStop, 1000) == win32event.WAIT_OBJECT_0:
                self._stop_child()
                servicemanager.LogInfoMsg("CollectorService stopped by request")
                return

            if self.proc.poll() is not None:
                try:
                    out_f.close()
                    err_f.close()
                except Exception:
                    pass
                time.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
                try:
                    out_f, err_f = self._start_child()
                    backoff = 1
                except Exception as e:
                    try:
                        servicemanager.LogErrorMsg(f"CollectorService restart failed: {e}")
                    except Exception:
                        pass
                    time.sleep(backoff)
                    continue


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(CollectorService)
