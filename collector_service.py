import os
import subprocess
import time

import servicemanager
import win32event
import win32service
import win32serviceutil


class CollectorService(win32serviceutil.ServiceFramework):
    _svc_name_ = "CollectorService"
    _svc_display_name_ = "Collector Service (collector_full.py)"
    _svc_description_ = "Runs collector_full.py in background and restarts on crash."

    def __init__(self, args):
        super().__init__(args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self.proc = None
        self.work_dir = r"D:\automate\sms"
        self.python = os.path.join(self.work_dir, "venv", "Scripts", "python.exe")
        self.script = os.path.join(self.work_dir, "collector_full.py")
        self.stdout_log = r"C:\LOG\smspg\collector_stdout.log"
        self.stderr_log = r"C:\LOG\smspg\collector_stderr.log"
        os.makedirs(os.path.dirname(self.stdout_log), exist_ok=True)

    def _start_child(self):
        out_f = open(self.stdout_log, "ab", buffering=0)
        err_f = open(self.stderr_log, "ab", buffering=0)
        err_f.write(b"\n=== starting collector_full.py ===\n")
        self.proc = subprocess.Popen(
            [self.python, self.script],
            cwd=self.work_dir,
            stdout=out_f,
            stderr=err_f,
            creationflags=subprocess.CREATE_NO_WINDOW,
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
