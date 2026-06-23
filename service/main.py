"""
Timekeeper Android Foreground Service.
Runs independently of the Kivy event loop.
Keeps the process alive and shows a persistent notification.
Reads timer state from a shared JSON file written by the main app.
"""
import time
import json
import os
from datetime import datetime


def get_data_dir():
    try:
        from android.storage import app_storage_path  # type: ignore
        return app_storage_path()
    except Exception:
        return "/tmp"


def read_state(state_path):
    try:
        with open(state_path) as f:
            return json.load(f)
    except Exception:
        return {}


def run_service():
    data_dir   = get_data_dir()
    state_path = os.path.join(data_dir, "timer_state.json")

    # ── Android foreground notification setup ──
    nm          = None
    show_notif  = None
    service_obj = None

    try:
        from jnius import autoclass  # type: ignore

        PythonService = autoclass("org.kivy.android.PythonService")
        service_obj   = PythonService.mService

        NotificationManager = autoclass("android.app.NotificationManager")
        NotificationChannel = autoclass("android.app.NotificationChannel")
        Builder             = autoclass("android.app.Notification$Builder")

        nm      = service_obj.getSystemService(service_obj.NOTIFICATION_SERVICE)
        channel = NotificationChannel(
            "tk_service", "Timekeeper Timer", NotificationManager.IMPORTANCE_LOW
        )
        nm.createNotificationChannel(channel)

        def show_notif(title, text):
            b = Builder(service_obj, "tk_service")
            b.setSmallIcon(service_obj.getApplicationInfo().icon)
            b.setContentTitle(title)
            b.setContentText(text)
            b.setOngoing(True)
            return b.build()

        # Start as foreground service immediately
        service_obj.startForeground(2, show_notif("Timekeeper", "Timer running…"))

    except Exception as e:
        print(f"[Service] init error: {e}")

    # ── Main loop ──
    while True:
        try:
            state = read_state(state_path)
            s     = state.get("state", "idle")

            if nm and show_notif and s in ("running", "paused", "break"):
                task_name = state.get("task_name", "Timer")
                total     = state.get("total_secs", 1500)
                accum     = state.get("accum_secs", 0)

                if s == "running":
                    run_start = state.get("run_start_dt")
                    if run_start:
                        dt      = datetime.fromisoformat(run_start)
                        elapsed = accum + int((datetime.now() - dt).total_seconds())
                    else:
                        elapsed = accum
                else:
                    elapsed = accum

                remaining = max(0, total - elapsed)
                mins      = remaining // 60
                secs      = remaining % 60
                label     = "BREAK" if s == "break" else task_name
                notif     = show_notif(
                    "Timekeeper",
                    f"{label} — {mins:02d}:{secs:02d} remaining"
                )
                nm.notify(2, notif)

            elif nm and s == "idle":
                # Cancel notification when idle
                nm.cancel(2)
                if service_obj:
                    service_obj.stopForeground(True)

        except Exception as e:
            print(f"[Service] loop error: {e}")

        time.sleep(5)


run_service()
