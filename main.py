"""
Timekeeper for Android  v0.00003
A free, open-source, ad-free time tracking app.
"""

import kivy
kivy.require("2.3.0")

import os
import csv
import json
import sqlite3
import threading
from datetime import datetime, date

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.popup import Popup
from kivy.uix.widget import Widget
from kivy.graphics import Color, Line, Rectangle
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.properties import NumericProperty, StringProperty
from kivy.utils import platform

# ─── Constants ────────────────────────────────────────────────────────────────

APP_VERSION        = "v0.00010"
APP_NAME           = "Timekeeper"
DEFAULT_TASK_MINS  = 25
DEFAULT_BREAK_MINS = 5
DEFAULT_DAILY_GOAL = 10
MANUAL_TASK_ID     = 999
COMMENT_MAX_CHARS  = 1000
MIN_RECORD_SECS    = 10

if platform == "android":
    from android.storage import app_storage_path  # type: ignore
    DATA_DIR = app_storage_path()
else:
    DATA_DIR = os.path.join(os.path.expanduser("~"), "Documents", "tk_ndroid")

os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH         = os.path.join(DATA_DIR, "timekeeper.db")
TIMER_STATE_PATH = os.path.join(DATA_DIR, "timer_state.json")

# ─── Colours ──────────────────────────────────────────────────────────────────

C_BG      = (0.12, 0.12, 0.14, 1)
C_SURFACE = (0.18, 0.18, 0.21, 1)
C_GREEN   = (0.20, 0.80, 0.40, 1)
C_ORANGE  = (1.00, 0.60, 0.10, 1)
C_RED     = (0.90, 0.20, 0.20, 1)
C_TEXT    = (0.95, 0.95, 0.95, 1)
C_SUBTEXT = (0.60, 0.60, 0.65, 1)
C_BTN     = (0.25, 0.25, 0.30, 1)
C_BTN_ACT = (0.20, 0.70, 0.40, 1)

# ─── SQLite Storage ───────────────────────────────────────────────────────────

class Storage:
    def __init__(self, db_path):
        self._db = db_path
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self._db)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    name    TEXT    NOT NULL UNIQUE,
                    created TEXT    NOT NULL
                );
                CREATE TABLE IF NOT EXISTS entries (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id       INTEGER NOT NULL,
                    task_name     TEXT    NOT NULL,
                    date          TEXT    NOT NULL,
                    start_time    TEXT    NOT NULL,
                    end_time      TEXT    NOT NULL,
                    duration_secs INTEGER NOT NULL,
                    interval_num  INTEGER NOT NULL,
                    comment       TEXT    DEFAULT '',
                    FOREIGN KEY (task_id) REFERENCES tasks(id)
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            defaults = {
                "task_mins":  str(DEFAULT_TASK_MINS),
                "break_mins": str(DEFAULT_BREAK_MINS),
                "daily_goal": str(DEFAULT_DAILY_GOAL),
            }
            for k, v in defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
                )

    # ── Tasks ──
    def get_tasks(self):
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM tasks ORDER BY name"
            ).fetchall()]

    def add_task(self, name):
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO tasks (name, created) VALUES (?, ?)",
                (name.strip(), datetime.now().isoformat())
            )
            if cur.lastrowid:
                return cur.lastrowid
            return conn.execute(
                "SELECT id FROM tasks WHERE name=?", (name.strip(),)
            ).fetchone()["id"]

    def rename_task(self, task_id, new_name):
        with self._connect() as conn:
            conn.execute("UPDATE tasks SET name=? WHERE id=?", (new_name.strip(), task_id))
            conn.execute("UPDATE entries SET task_name=? WHERE task_id=?",
                         (new_name.strip(), task_id))

    def delete_task(self, task_id):
        with self._connect() as conn:
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            conn.execute("DELETE FROM entries WHERE task_id=?", (task_id,))

    # ── Entries ──
    def add_entry(self, task_id, task_name, start, end, duration_secs, interval_num, comment=""):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO entries
                   (task_id, task_name, date, start_time, end_time,
                    duration_secs, interval_num, comment)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (task_id, task_name,
                 start.strftime("%Y-%m-%d"),
                 start.strftime("%H:%M:%S"),
                 end.strftime("%H:%M:%S"),
                 duration_secs, interval_num, comment[:COMMENT_MAX_CHARS])
            )

    def get_entries(self, limit=200):
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM entries ORDER BY date DESC, start_time DESC LIMIT ?",
                (limit,)
            ).fetchall()]

    def update_entry(self, entry_id, **kwargs):
        allowed = {"task_name", "date", "start_time", "end_time",
                   "duration_secs", "interval_num", "comment"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._connect() as conn:
            conn.execute(f"UPDATE entries SET {sets} WHERE id=?",
                         (*fields.values(), entry_id))

    def delete_entry(self, entry_id):
        with self._connect() as conn:
            conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))

    def duplicate_entry(self, entry_id, new_task_name, duration_secs):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM entries WHERE id=?", (entry_id,)
            ).fetchone()
            if not row:
                return -1
            task = conn.execute(
                "SELECT id FROM tasks WHERE name=?", (new_task_name,)
            ).fetchone()
            task_id = task["id"] if task else conn.execute(
                "INSERT INTO tasks (name, created) VALUES (?, ?)",
                (new_task_name, datetime.now().isoformat())
            ).lastrowid
            cur = conn.execute(
                """INSERT INTO entries
                   (task_id, task_name, date, start_time, end_time,
                    duration_secs, interval_num, comment)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (task_id, new_task_name, row["date"], row["start_time"],
                 row["end_time"], duration_secs, row["interval_num"], "")
            )
            return cur.lastrowid

    # ── Settings ──
    def get_setting(self, key, default=None):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_setting(self, key, value):
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                (key, str(value))
            )

    # ── Counters ──
    def today_interval_count(self):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as c FROM entries WHERE date=? AND task_id != ?",
                (date.today().isoformat(), MANUAL_TASK_ID)
            ).fetchone()
            return row["c"] if row else 0

    def next_interval_num(self, task_id):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(interval_num) as m FROM entries WHERE task_id=?",
                (task_id,)
            ).fetchone()
            return (row["m"] or 0) + 1

    # ── CSV Export ──
    def export_csv(self, path):
        entries = self.get_entries(limit=100000)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "id", "task_id", "task_name", "date", "start_time",
                "end_time", "duration_secs", "interval_num", "comment"
            ])
            writer.writeheader()
            writer.writerows(entries)
        return path


# ─── Timer Engine ─────────────────────────────────────────────────────────────

class TimerState:
    IDLE         = "idle"
    RUNNING      = "running"
    PAUSED       = "paused"
    BREAK_READY  = "break_ready"   # break loaded but not yet started
    BREAK        = "break"
    BREAK_PAUSED = "break_paused"  # break clock paused mid-break


class TimerEngine:
    """
    Wall-clock based timer engine.
    Elapsed time is always computed from datetime.now(), so it is accurate
    even after the app is backgrounded and resumed.
    """

    def __init__(self, storage, on_tick, on_complete):
        self._storage      = storage
        self._on_tick      = on_tick
        self._on_complete  = on_complete
        self.state         = TimerState.IDLE
        self._total_secs   = 0
        self._accum_secs   = 0     # elapsed before current run segment
        self._run_start_dt = None  # when current run segment began
        self._start_dt     = None  # when this whole interval began (for recording)
        self._task_id      = None
        self._task_name    = ""
        self._clock_ev     = None

    # ── Properties ──

    @property
    def elapsed(self):
        if self.state in (TimerState.RUNNING, TimerState.BREAK) and self._run_start_dt:
            return self._accum_secs + int(
                (datetime.now() - self._run_start_dt).total_seconds()
            )
        return self._accum_secs

    @property
    def remaining(self):
        return max(0, self._total_secs - self.elapsed)

    @property
    def fraction(self):
        if self._total_secs == 0:
            return 0.0
        return min(1.0, self.elapsed / self._total_secs)

    # ── Controls ──

    def set_task(self, task_id, task_name):
        self._task_id   = task_id
        self._task_name = task_name

    def start(self):
        if self._task_id is None:
            return False
        if self.state == TimerState.PAUSED:
            self._resume()
        elif self.state == TimerState.BREAK_READY:
            # User chose to start the break
            self._run_start_dt = datetime.now()
            self._start_dt     = datetime.now()
            self.state         = TimerState.BREAK
            self._on_tick(0, self._total_secs, self.state)
            self._schedule()
        elif self.state == TimerState.BREAK:
            # Pause the break
            self._accum_secs   = self.elapsed
            self._run_start_dt = None
            self.state         = TimerState.BREAK_PAUSED
            if self._clock_ev:
                self._clock_ev.cancel()
            self._on_tick(self._accum_secs, self._total_secs, self.state)
        elif self.state == TimerState.BREAK_PAUSED:
            # Resume the break
            self._run_start_dt = datetime.now()
            self.state         = TimerState.BREAK
            self._schedule()
        elif self.state == TimerState.IDLE:
            self._begin_task()
        return True

    def pause(self):
        if self.state == TimerState.RUNNING:
            self._accum_secs   = self.elapsed
            self._run_start_dt = None
            self.state         = TimerState.PAUSED
            if self._clock_ev:
                self._clock_ev.cancel()
            self._on_tick(self._accum_secs, self._total_secs, self.state)

    def stop(self):
        if self.state in (TimerState.RUNNING, TimerState.PAUSED):
            self._record_entry()
            self._preload_break()   # show break arc; user presses Start to begin it
        elif self.state in (TimerState.BREAK, TimerState.BREAK_PAUSED, TimerState.BREAK_READY):
            self._reset()           # cancel break entirely → back to idle

    def sync(self):
        """Call on app resume — refreshes UI from wall-clock state."""
        e = self.elapsed
        self._on_tick(e, self._total_secs, self.state)
        if self.state in (TimerState.BREAK_READY, TimerState.BREAK_PAUSED):
            pass  # clock not running; just refreshed UI above — nothing to schedule
        elif self.state in (TimerState.RUNNING, TimerState.BREAK):
            if e >= self._total_secs:
                # Timer expired while backgrounded — trigger completion now
                self._complete()
            else:
                # Always reschedule — Kivy Clock stops firing during pause
                # even though the event object still exists
                self._schedule()

    def state_dict(self):
        """Serialise state for the background service."""
        return {
            "state":       self.state,
            "task_id":     self._task_id,
            "task_name":   self._task_name,
            "total_secs":  self._total_secs,
            "accum_secs":  self._accum_secs,
            "run_start_dt": (self._run_start_dt.isoformat()
                             if self._run_start_dt else None),
        }

    # ── Internal ──

    def _begin_task(self):
        mins               = int(self._storage.get_setting("task_mins", DEFAULT_TASK_MINS))
        self._total_secs   = mins * 60
        self._accum_secs   = 0
        self._run_start_dt = datetime.now()
        self._start_dt     = datetime.now()
        self.state         = TimerState.RUNNING
        self._on_tick(0, self._total_secs, self.state)  # flip UI to task immediately
        self._schedule()

    def _preload_break(self):
        """Set up break arc/state without starting the clock (BREAK_READY)."""
        if self._clock_ev:
            self._clock_ev.cancel()
        mins               = int(self._storage.get_setting("break_mins", DEFAULT_BREAK_MINS))
        self._total_secs   = mins * 60
        self._accum_secs   = 0
        self._run_start_dt = None
        self._start_dt     = None
        self.state         = TimerState.BREAK_READY
        self._on_tick(0, self._total_secs, self.state)

    def _begin_break(self):
        mins               = int(self._storage.get_setting("break_mins", DEFAULT_BREAK_MINS))
        self._total_secs   = mins * 60
        self._accum_secs   = 0
        self._run_start_dt = datetime.now()
        self._start_dt     = datetime.now()
        self.state         = TimerState.BREAK
        self._on_tick(0, self._total_secs, self.state)  # flip UI to break immediately
        self._schedule()

    def _resume(self):
        self._run_start_dt = datetime.now()
        self.state         = TimerState.RUNNING
        self._schedule()

    def _schedule(self):
        if self._clock_ev:
            self._clock_ev.cancel()
        self._clock_ev = Clock.schedule_interval(self._tick, 1)

    def _tick(self, dt):
        e = self.elapsed
        self._on_tick(e, self._total_secs, self.state)
        if e >= self._total_secs:
            self._complete()

    def _complete(self):
        finished = self.state
        if finished == TimerState.RUNNING:
            self._record_entry()
            self._begin_break()       # state = BREAK before popup opens
            self._on_complete(finished)
        elif finished == TimerState.BREAK:
            self._reset()             # state = IDLE before popup opens
            self._on_complete(finished)

    def _record_entry(self):
        if self._task_id is None:
            return
        elapsed = self.elapsed
        if elapsed < 1:
            return
        end_dt   = datetime.now()
        start_dt = self._start_dt or end_dt
        interval = self._storage.next_interval_num(self._task_id)
        self._storage.add_entry(
            task_id       = self._task_id,
            task_name     = self._task_name,
            start         = start_dt,
            end           = end_dt,
            duration_secs = elapsed,
            interval_num  = interval,
        )

    def _reset(self):
        if self._clock_ev:
            self._clock_ev.cancel()
        self.state         = TimerState.IDLE
        self._accum_secs   = 0
        self._run_start_dt = None
        self._start_dt     = None
        self._on_tick(0, 0, self.state)


# ─── Android Service Manager ──────────────────────────────────────────────────

class ServiceManager:
    """Starts and stops the background foreground service."""

    def __init__(self):
        self._service = None

    def start(self):
        if platform != "android":
            return
        try:
            from android import AndroidService  # type: ignore
            self._service = AndroidService("Timekeeper Timer", "Timer running in background")
            self._service.start("start")
        except Exception as e:
            print(f"[Service] start error: {e}")

    def stop(self):
        if platform != "android" or not self._service:
            return
        try:
            self._service.stop()
            self._service = None
        except Exception as e:
            print(f"[Service] stop error: {e}")


# ─── Voice Handler ────────────────────────────────────────────────────────────

class VoiceHandler:
    """
    Continuous Android SpeechRecognizer.
    Voice commands: 'Timekeeper Start', 'Timekeeper Wait', 'Timekeeper Stop'.
    Must be initialised AFTER the Kivy app is fully built (call setup()).
    """

    def __init__(self, on_command):
        self._cb        = on_command
        self._active    = False
        self._listening = False
        self._recognizer = None

    def setup(self):
        """Call this after App.build() — initialises SpeechRecognizer."""
        if platform != "android":
            return
        try:
            # Request permission
            from android.permissions import (  # type: ignore
                request_permissions, check_permission, Permission
            )
            if not check_permission(Permission.RECORD_AUDIO):
                request_permissions(
                    [Permission.RECORD_AUDIO],
                    callback=self._on_permission_result
                )
            else:
                self._init_recognizer()
        except Exception as e:
            print(f"[Voice] setup error: {e}")

    def _on_permission_result(self, permissions, grant_results):
        if grant_results and all(grant_results):
            self._init_recognizer()
            Clock.schedule_once(lambda dt: self.start_listening(), 1)
        else:
            print("[Voice] RECORD_AUDIO permission denied")

    def _init_recognizer(self):
        try:
            from jnius import autoclass  # type: ignore
            SR  = autoclass("android.speech.SpeechRecognizer")
            ctx = autoclass("org.kivy.android.PythonActivity").mActivity

            if not SR.isRecognitionAvailable(ctx):
                print("[Voice] Speech recognition not available on this device")
                return

            self._SR          = SR
            self._RI          = autoclass("android.speech.RecognizerIntent")
            self._recognizer  = SR.createSpeechRecognizer(ctx)
            self._recognizer.setRecognitionListener(self._build_listener())
            self._active      = True
            print("[Voice] SpeechRecognizer initialised OK")
        except Exception as e:
            print(f"[Voice] init recognizer error: {e}")

    def _build_listener(self):
        from jnius import PythonJavaClass, java_method  # type: ignore
        handler = self

        class Listener(PythonJavaClass):
            __javainterfaces__ = ["android/speech/RecognitionListener"]
            __javacontext__    = "app"

            @java_method("(Landroid/os/Bundle;)V")
            def onReadyForSpeech(self, params):
                pass

            @java_method("()V")
            def onBeginningOfSpeech(self):
                pass

            @java_method("(F)V")
            def onRmsChanged(self, rmsdB):
                pass

            @java_method("([B)V")
            def onBufferReceived(self, buffer):
                pass

            @java_method("()V")
            def onEndOfSpeech(self):
                pass

            # onError(int error)
            @java_method("(I)V")
            def onError(self, error):
                handler._listening = False
                # error 7 = no match, error 6 = no speech — both normal, restart quickly
                delay = 0.5 if error in (6, 7) else 2.0
                Clock.schedule_once(lambda dt: handler.start_listening(), delay)

            # onResults(Bundle results)
            @java_method("(Landroid/os/Bundle;)V")
            def onResults(self, results):
                handler._listening = False
                try:
                    from jnius import autoclass  # type: ignore
                    key     = autoclass(
                        "android.speech.SpeechRecognizer"
                    ).RESULTS_RECOGNITION
                    matches = results.getStringArrayList(key)
                    if matches and matches.size() > 0:
                        text = str(matches.get(0)).lower()
                        print(f"[Voice] heard: {text}")
                        if "timekeeper start" in text:
                            Clock.schedule_once(
                                lambda dt: handler._cb("start"), 0
                            )
                        elif "timekeeper wait" in text or "timekeeper pause" in text:
                            Clock.schedule_once(
                                lambda dt: handler._cb("wait"), 0
                            )
                        elif "timekeeper stop" in text:
                            Clock.schedule_once(
                                lambda dt: handler._cb("stop"), 0
                            )

                except Exception as e:
                    print(f"[Voice] onResults error: {e}")
                # Always restart
                Clock.schedule_once(lambda dt: handler.start_listening(), 0.3)

            @java_method("(Landroid/os/Bundle;)V")
            def onPartialResults(self, results):
                pass

            @java_method("(ILandroid/os/Bundle;)V")
            def onEvent(self, eventType, params):
                pass

        return Listener()

    def start_listening(self):
        if not self._active or self._listening or not self._recognizer:
            return
        try:
            from jnius import autoclass  # type: ignore
            Intent = autoclass("android.content.Intent")
            intent = Intent(self._RI.ACTION_RECOGNIZE_SPEECH)
            intent.putExtra(
                self._RI.EXTRA_LANGUAGE_MODEL,
                self._RI.LANGUAGE_MODEL_FREE_FORM
            )
            intent.putExtra(self._RI.EXTRA_MAX_RESULTS, 3)
            intent.putExtra(self._RI.EXTRA_PARTIAL_RESULTS, True)
            self._recognizer.startListening(intent)
            self._listening = True
        except Exception as e:
            print(f"[Voice] startListening error: {e}")
            self._listening = False

    def stop_listening(self):
        if not self._active or not self._recognizer:
            return
        try:
            self._recognizer.stopListening()
            self._listening = False
        except Exception:
            pass


# ─── Arc Timer Widget ──────────────────────────────────────────────────────────

class ArcTimer(Widget):
    fraction  = NumericProperty(0.0)
    remaining = NumericProperty(0)
    total     = NumericProperty(0)
    state     = StringProperty("idle")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(fraction=self._redraw, remaining=self._redraw,
                  total=self._redraw, state=self._redraw,
                  size=self._redraw, pos=self._redraw)

    def _arc_colour(self):
        if self.state in ("break", "break_ready", "break_paused"):
            return C_GREEN
        f = self.fraction
        if f < 0.5:
            return C_GREEN
        elif f < 0.8:
            return C_ORANGE
        else:
            return C_RED

    def _redraw(self, *args):
        self.canvas.clear()
        cx = self.center_x
        cy = self.center_y
        r  = min(self.width, self.height) * 0.42
        lw = dp(8)

        with self.canvas:
            Color(0.25, 0.25, 0.28, 1)
            Line(circle=(cx, cy, r), width=lw)
            arc_angle = 360 * (1.0 - self.fraction)
            Color(*self._arc_colour())
            Line(ellipse=(cx - r, cy - r, r * 2, r * 2, 90, 90 + arc_angle),
                 width=lw)

        from kivy.core.text import Label as CoreLabel
        mins = self.remaining // 60
        secs = self.remaining % 60
        lbl  = CoreLabel(text=f"{mins:02d}:{secs:02d}", font_size=dp(36), bold=True)
        lbl.refresh()
        tex  = lbl.texture
        with self.canvas:
            Color(*C_TEXT)
            Rectangle(texture=tex,
                      pos=(cx - tex.width / 2, cy - tex.height / 2),
                      size=tex.size)

        mins = self.total // 60
        state_map = {
            "idle":         f"{mins} min task",
            "running":      f"{mins} min task",
            "paused":       "PAUSED",
            "break_ready":  f"{mins} min break",
            "break":        f"{mins} min break",
            "break_paused": f"{mins} min break",
        }
        sub = state_map.get(self.state, "")
        if sub:
            slbl = CoreLabel(text=sub, font_size=dp(13))
            slbl.refresh()
            stex = slbl.texture
            with self.canvas:
                Color(*C_SUBTEXT)
                Rectangle(texture=stex,
                          pos=(cx - stex.width / 2,
                               cy - tex.height / 2 - dp(22)),
                          size=stex.size)


# ─── UI Helpers ───────────────────────────────────────────────────────────────

def make_button(text, callback, bg=C_BTN, font_size=dp(16), height=dp(50)):
    btn = Button(
        text=text, size_hint=(1, None), height=height,
        font_size=font_size, background_normal="",
        background_color=bg, color=C_TEXT,
    )
    btn.bind(on_release=lambda *a: callback())
    return btn


def make_label(text, font_size=dp(15), color=C_TEXT, halign="center"):
    lbl = Label(text=text, font_size=font_size, color=color,
                halign=halign, valign="middle")
    lbl.bind(size=lambda l, v: setattr(l, "text_size", v))
    return lbl


def show_popup(title, message, on_dismiss=None):
    content = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))
    content.add_widget(make_label(message, font_size=dp(14)))
    popup = Popup(title=title, content=content,
                  size_hint=(0.85, None), height=dp(200),
                  background_color=C_SURFACE, auto_dismiss=False)
    content.add_widget(make_button("OK", popup.dismiss))
    if on_dismiss:
        popup.bind(on_dismiss=lambda *a: on_dismiss())
    popup.open()


def show_input_popup(title, hint, on_confirm, prefill=""):
    content = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))
    ti = TextInput(text=prefill, hint_text=hint, multiline=False,
                   size_hint=(1, None), height=dp(44),
                   font_size=dp(15), foreground_color=C_TEXT,
                   background_color=C_SURFACE)
    content.add_widget(ti)
    popup = Popup(title=title, content=content,
                  size_hint=(0.88, None), height=dp(200),
                  background_color=C_SURFACE, auto_dismiss=False)

    def _confirm():
        val = ti.text.strip()
        if val:
            popup.dismiss()
            on_confirm(val)

    row = BoxLayout(size_hint=(1, None), height=dp(48), spacing=dp(8))
    row.add_widget(make_button("Cancel", popup.dismiss))
    row.add_widget(make_button("OK", _confirm, bg=C_BTN_ACT))
    content.add_widget(row)
    popup.bind(on_open=lambda *a: Clock.schedule_once(
        lambda dt: setattr(ti, 'focus', True), 0.1
    ))
    popup.open()


# ─── Screens ──────────────────────────────────────────────────────────────────

class MainScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(name="main", **kwargs)
        self._app = app
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))
        with root.canvas.before:
            Color(*C_BG)
            self._bg = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda w, v: setattr(self._bg, "pos", v),
                  size=lambda w, v: setattr(self._bg, "size", v))

        # Top bar: menu | task name | version
        top = BoxLayout(size_hint=(1, None), height=dp(48), spacing=dp(8))
        menu_btn = Button(text="☰", size_hint=(None, 1), width=dp(48),
                          font_size=dp(22), background_normal="",
                          background_color=C_BTN, color=C_TEXT)
        menu_btn.bind(on_release=lambda *a: self._app.go_menu())
        self._task_lbl = Label(text="No task — open menu",
                               font_size=dp(17), bold=True,
                               color=C_TEXT, halign="center", valign="middle")
        self._task_lbl.bind(size=lambda l, v: setattr(l, "text_size", v))
        ver_lbl = Label(text=APP_VERSION, font_size=dp(10), color=C_SUBTEXT,
                        size_hint=(None, 1), width=dp(64),
                        halign="right", valign="middle")
        ver_lbl.bind(size=lambda l, v: setattr(l, "text_size", v))
        top.add_widget(menu_btn)
        top.add_widget(self._task_lbl)
        top.add_widget(ver_lbl)
        root.add_widget(top)

        self._arc = ArcTimer(size_hint=(1, 1))
        root.add_widget(self._arc)

        self._goal_lbl = make_label("", font_size=dp(13), color=C_SUBTEXT)
        self._goal_lbl.size_hint = (1, None)
        self._goal_lbl.height    = dp(24)
        root.add_widget(self._goal_lbl)

        btn_row = BoxLayout(size_hint=(1, None), height=dp(56), spacing=dp(12))
        self._start_btn = Button(text="▶  Start", font_size=dp(18),
                                 background_normal="", background_color=C_BTN_ACT,
                                 color=C_TEXT)
        self._start_btn.bind(on_release=lambda *a: self._app.on_start_pause())
        self._stop_btn = Button(text="■  Stop", font_size=dp(18),
                                background_normal="", background_color=C_RED,
                                color=C_TEXT)
        self._stop_btn.bind(on_release=lambda *a: self._app.on_stop())
        btn_row.add_widget(self._start_btn)
        btn_row.add_widget(self._stop_btn)
        root.add_widget(btn_row)
        self.add_widget(root)

    def update(self, elapsed, total, state, task_name="", goal_done=0, goal_total=10):
        remaining = max(0, total - elapsed)
        fraction  = min(1.0, (elapsed / total) if total > 0 else 0.0)
        self._arc.remaining = remaining
        self._arc.fraction  = fraction
        self._arc.total     = total
        self._arc.state     = state
        self._task_lbl.text = task_name or "No task — open menu"
        self._goal_lbl.text = f"Today: {goal_done} / {goal_total} intervals"
        if state == TimerState.RUNNING:
            self._start_btn.text             = "⏸  Pause"
            self._start_btn.background_color = C_ORANGE
        elif state == TimerState.PAUSED:
            self._start_btn.text             = "▶  Resume"
            self._start_btn.background_color = C_BTN_ACT
        elif state == TimerState.BREAK_READY:
            self._start_btn.text             = "▶  Start Break"
            self._start_btn.background_color = C_BTN_ACT
        elif state == TimerState.BREAK:
            self._start_btn.text             = "⏸  Pause Break"
            self._start_btn.background_color = C_ORANGE
        elif state == TimerState.BREAK_PAUSED:
            self._start_btn.text             = "▶  Resume Break"
            self._start_btn.background_color = C_BTN_ACT
        else:
            self._start_btn.text             = "▶  Start"
            self._start_btn.background_color = C_BTN_ACT


class MenuScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(name="menu", **kwargs)
        self._app = app
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(14))
        with root.canvas.before:
            Color(*C_BG)
            rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda w, v: setattr(rect, "pos", v),
                  size=lambda w, v: setattr(rect, "size", v))

        root.add_widget(make_label("Menu", font_size=dp(22), color=C_TEXT))
        items = [
            ("➕  New Task",       self._app.go_new_task),
            ("✏️  Rename Task",     self._app.go_rename_task),
            ("🔀  Switch Task",     self._app.go_switch_task),
            ("📋  Edit Entries",    self._app.go_entries),
            ("📤  Export CSV",      self._app.do_export_csv),
            ("⚙️  Settings",        self._app.go_settings),
            ("🎤  Voice Commands",  self._app.go_shortcuts),
            ("ℹ️  About",           self._app.go_about),
            ("← Back",             self._app.go_main),
        ]
        for label, cb in items:
            root.add_widget(make_button(label, cb, height=dp(52)))
        self.add_widget(root)


class TaskListScreen(Screen):
    def __init__(self, app, mode="switch", **kwargs):
        super().__init__(name=f"tasklist_{mode}", **kwargs)
        self._app  = app
        self._mode = mode

    def on_pre_enter(self):
        self.clear_widgets()
        root = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(10))
        with root.canvas.before:
            Color(*C_BG)
            rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda w, v: setattr(rect, "pos", v),
                  size=lambda w, v: setattr(rect, "size", v))

        title = "Switch Task" if self._mode == "switch" else "Rename Task"
        root.add_widget(make_label(title, font_size=dp(20), color=C_TEXT))

        scroll = ScrollView(size_hint=(1, 1))
        inner  = BoxLayout(orientation="vertical",
                           size_hint=(1, None), spacing=dp(8), padding=dp(4))
        inner.bind(minimum_height=inner.setter("height"))

        tasks = self._app.storage.get_tasks()
        if not tasks:
            inner.add_widget(make_label("No tasks yet.", color=C_SUBTEXT))
        for t in tasks:
            btn = Button(text=t["name"], size_hint=(1, None), height=dp(50),
                         font_size=dp(16), background_normal="",
                         background_color=C_BTN, color=C_TEXT)
            tc = dict(t)
            if self._mode == "switch":
                btn.bind(on_release=lambda b, tc=tc: self._app.select_task(tc))
            else:
                btn.bind(on_release=lambda b, tc=tc: self._app.start_rename(tc))
            inner.add_widget(btn)

        scroll.add_widget(inner)
        root.add_widget(scroll)
        root.add_widget(make_button("← Back", self._app.go_menu, height=dp(48)))
        self.add_widget(root)


class EntryListScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(name="entries", **kwargs)
        self._app = app

    def on_pre_enter(self):
        self.clear_widgets()
        root = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
        with root.canvas.before:
            Color(*C_BG)
            rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda w, v: setattr(rect, "pos", v),
                  size=lambda w, v: setattr(rect, "size", v))

        root.add_widget(make_label("Entries", font_size=dp(20), color=C_TEXT))

        scroll = ScrollView(size_hint=(1, 1))
        inner  = BoxLayout(orientation="vertical",
                           size_hint=(1, None), spacing=dp(6))
        inner.bind(minimum_height=inner.setter("height"))

        entries = self._app.storage.get_entries()
        if not entries:
            inner.add_widget(make_label("No entries yet.", color=C_SUBTEXT))
        for e in entries:
            mins = e["duration_secs"] // 60
            secs = e["duration_secs"] % 60
            line = (f"{e['date']}  {e['start_time']}  "
                    f"{e['task_name']}  {mins}m{secs:02d}s")
            row  = BoxLayout(size_hint=(1, None), height=dp(52), spacing=dp(6))
            lbl  = Label(text=line, font_size=dp(12), color=C_TEXT,
                         halign="left", valign="middle",
                         size_hint=(1, 1), text_size=(None, None))
            ec   = dict(e)
            edit_btn = Button(text="Edit", size_hint=(None, 1), width=dp(56),
                              font_size=dp(13), background_normal="",
                              background_color=C_BTN, color=C_TEXT)
            edit_btn.bind(on_release=lambda b, ec=ec: self._app.go_edit_entry(ec))
            row.add_widget(lbl)
            row.add_widget(edit_btn)
            inner.add_widget(row)

        scroll.add_widget(inner)
        root.add_widget(scroll)
        root.add_widget(make_button("← Back", self._app.go_menu, height=dp(48)))
        self.add_widget(root)


class EditEntryScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(name="edit_entry", **kwargs)
        self._app   = app
        self._entry = {}

    def load(self, entry):
        self._entry = entry
        self.on_pre_enter()

    def on_pre_enter(self):
        self.clear_widgets()
        e = self._entry
        if not e:
            return
        root = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))
        with root.canvas.before:
            Color(*C_BG)
            rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda w, v: setattr(rect, "pos", v),
                  size=lambda w, v: setattr(rect, "size", v))

        root.add_widget(make_label("Edit Entry", font_size=dp(20), color=C_TEXT))

        fields = [
            ("Task name",       "task_name",     e.get("task_name", "")),
            ("Date",            "date",          e.get("date", "")),
            ("Start time",      "start_time",    e.get("start_time", "")),
            ("End time",        "end_time",      e.get("end_time", "")),
            ("Duration (secs)", "duration_secs", str(e.get("duration_secs", 0))),
        ]
        self._inputs = {}
        for label, key, val in fields:
            row = BoxLayout(size_hint=(1, None), height=dp(48), spacing=dp(8))
            row.add_widget(make_label(label, font_size=dp(13),
                                      color=C_SUBTEXT, halign="left"))
            ti = TextInput(text=str(val), multiline=False,
                           size_hint=(1, None), height=dp(40),
                           font_size=dp(14), foreground_color=C_TEXT,
                           background_color=C_SURFACE)
            self._inputs[key] = ti
            row.add_widget(ti)
            root.add_widget(row)

        root.add_widget(make_label("Comment", font_size=dp(13),
                                   color=C_SUBTEXT, halign="left"))
        comment_ti = TextInput(text=e.get("comment", ""), multiline=True,
                               size_hint=(1, None), height=dp(80),
                               font_size=dp(14), foreground_color=C_TEXT,
                               background_color=C_SURFACE)
        self._inputs["comment"] = comment_ti
        root.add_widget(comment_ti)

        btn_row = BoxLayout(size_hint=(1, None), height=dp(52), spacing=dp(8))
        btn_row.add_widget(make_button("Save",      self._save,      bg=C_BTN_ACT))
        btn_row.add_widget(make_button("Duplicate", self._duplicate, bg=C_BTN))
        btn_row.add_widget(make_button("Delete",    self._delete,    bg=C_RED))
        root.add_widget(btn_row)
        root.add_widget(make_button("← Back", self._app.go_entries, height=dp(48)))
        self.add_widget(root)

    def _save(self):
        updates = {}
        for key, ti in self._inputs.items():
            val = ti.text.strip()
            if key == "duration_secs":
                try:
                    val = int(val)
                except ValueError:
                    show_popup("Error", "Duration must be a whole number.")
                    return
            elif key == "comment":
                val = val[:COMMENT_MAX_CHARS]
            updates[key] = val
        self._app.storage.update_entry(self._entry["id"], **updates)
        show_popup("Saved", "Entry updated.", on_dismiss=self._app.go_entries)

    def _duplicate(self):
        def _do(new_task):
            try:
                dur = int(self._inputs["duration_secs"].text.strip())
            except ValueError:
                dur = self._entry["duration_secs"]
            self._app.storage.duplicate_entry(self._entry["id"], new_task, dur)
            show_popup("Done", f"Entry duplicated under '{new_task}'.",
                       on_dismiss=self._app.go_entries)
        show_input_popup("Duplicate to Task", "New task name", _do)

    def _delete(self):
        self._app.storage.delete_entry(self._entry["id"])
        show_popup("Deleted", "Entry deleted.", on_dismiss=self._app.go_entries)


class SettingsScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(name="settings", **kwargs)
        self._app = app

    def on_pre_enter(self):
        self.clear_widgets()

        # Inner BoxLayout — height driven by content so ScrollView can scroll it
        root = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(14),
                         size_hint_y=None)
        root.bind(minimum_height=root.setter("height"))

        with root.canvas.before:
            Color(*C_BG)
            rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda w, v: setattr(rect, "pos", v),
                  size=lambda w, v: setattr(rect, "size", v))

        root.add_widget(make_label("Settings", font_size=dp(22), color=C_TEXT))

        settings_def = [
            ("Task duration (mins)",   "task_mins",  str(DEFAULT_TASK_MINS)),
            ("Break duration (mins)",  "break_mins", str(DEFAULT_BREAK_MINS)),
            ("Daily goal (intervals)", "daily_goal", str(DEFAULT_DAILY_GOAL)),
        ]
        self._inputs = {}
        for label, key, default in settings_def:
            val = self._app.storage.get_setting(key, default)
            row = BoxLayout(size_hint=(1, None), height=dp(48), spacing=dp(8))
            row.add_widget(make_label(label, font_size=dp(14),
                                      color=C_TEXT, halign="left"))
            ti = TextInput(text=str(val), multiline=False,
                           size_hint=(None, None), size=(dp(80), dp(40)),
                           font_size=dp(15), foreground_color=C_TEXT,
                           background_color=C_SURFACE, input_filter="int")
            self._inputs[key] = ti
            row.add_widget(ti)
            root.add_widget(row)

        root.add_widget(make_button("Save Settings", self._save, bg=C_BTN_ACT))
        root.add_widget(make_button("← Back", self._app.go_menu))

        # ScrollView so content stays reachable when keyboard appears (portrait + landscape)
        sv = ScrollView(size_hint=(1, 1))
        sv.add_widget(root)
        self.add_widget(sv)

    def _save(self):
        for key, ti in self._inputs.items():
            val = ti.text.strip()
            if val.isdigit() and int(val) > 0:
                self._app.storage.set_setting(key, val)
        show_popup("Saved", "Settings saved.", on_dismiss=self._app.go_menu)


class AboutScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(name="about", **kwargs)
        self._app = app
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(16))
        with root.canvas.before:
            Color(*C_BG)
            rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda w, v: setattr(rect, "pos", v),
                  size=lambda w, v: setattr(rect, "size", v))

        root.add_widget(make_label(f"Timekeeper {APP_VERSION}",
                                   font_size=dp(22), color=C_TEXT))
        root.add_widget(make_label(
            "A free, open-source, ad-free time tracking app.\n\n"
            "Inspired by the Pomodoro technique.\n\n"
            "MIT Licence — free forever.",
            font_size=dp(14), color=C_SUBTEXT))
        root.add_widget(make_button("← Back", self._app.go_menu))
        self.add_widget(root)


class ShortcutsScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(name="shortcuts", **kwargs)
        self._app = app
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(16))
        with root.canvas.before:
            Color(*C_BG)
            rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda w, v: setattr(rect, "pos", v),
                  size=lambda w, v: setattr(rect, "size", v))

        root.add_widget(make_label("Voice Commands", font_size=dp(22), color=C_TEXT))
        root.add_widget(make_label(
            '"Timekeeper Start"  —  Start / Resume\n'
            '"Timekeeper Wait"   —  Pause\n'
            '"Timekeeper Stop"   —  Stop & record',
            font_size=dp(15), color=C_TEXT))
        root.add_widget(make_button("← Back", self._app.go_menu))
        self.add_widget(root)


# ─── Main App ─────────────────────────────────────────────────────────────────

class TimekeeperApp(App):
    title = f"{APP_NAME} {APP_VERSION}"

    def build(self):
        Window.clearcolor = C_BG
        Window.softinput_mode = "below_target"  # keep focused TextInput above keyboard

        self.storage        = Storage(DB_PATH)
        self._current_task  = None
        self._svc_manager   = ServiceManager()

        self.engine = TimerEngine(
            self.storage,
            on_tick    = self._on_tick,
            on_complete= self._on_complete,
        )

        self.voice = VoiceHandler(on_command=self._on_voice_command)

        self.sm = ScreenManager()
        self._main_screen      = MainScreen(app=self)
        self._menu_screen      = MenuScreen(app=self)
        self._entries_screen   = EntryListScreen(app=self)
        self._edit_screen      = EditEntryScreen(app=self)
        self._settings_screen  = SettingsScreen(app=self)
        self._about_screen     = AboutScreen(app=self)
        self._shortcuts_screen = ShortcutsScreen(app=self)
        self._switch_screen    = TaskListScreen(app=self, mode="switch")
        self._rename_screen    = TaskListScreen(app=self, mode="rename")

        for s in [
            self._main_screen, self._menu_screen,
            self._entries_screen, self._edit_screen,
            self._settings_screen, self._about_screen,
            self._shortcuts_screen, self._switch_screen,
            self._rename_screen,
        ]:
            self.sm.add_widget(s)

        # Initialise voice AFTER build (needs UI thread + activity ready)
        Clock.schedule_once(lambda dt: self.voice.setup(), 1)
        Clock.schedule_once(lambda dt: self.voice.start_listening(), 3)

        tasks = self.storage.get_tasks()
        if tasks:
            last_id = self.storage.get_setting("last_task_id")
            task = next((t for t in tasks if str(t["id"]) == str(last_id)), tasks[0])
            self._current_task = task
            self.engine.set_task(task["id"], task["name"])
        else:
            Clock.schedule_once(lambda dt: self._prompt_first_task(), 0.5)

        self._refresh_main()
        # Refresh interval counter every 60 s so it resets properly at midnight
        Clock.schedule_interval(lambda dt: self._refresh_main(), 60)
        return self.sm

    # ── Android lifecycle ──

    def on_pause(self):
        """
        Allow app to pause. The wall-clock engine keeps elapsed time accurate.
        Write state to file so the background service can read it.
        """
        self._write_timer_state()
        if self.engine.state in (TimerState.RUNNING, TimerState.BREAK, TimerState.BREAK_PAUSED):
            self._svc_manager.start()
        return True

    def on_resume(self):
        """Sync UI from wall clock after returning from background."""
        self.engine.sync()
        self._refresh_main()
        if self.engine.state == TimerState.IDLE:
            self._svc_manager.stop()

    # ── Timer state file ──

    def _write_timer_state(self):
        try:
            with open(TIMER_STATE_PATH, "w") as f:
                json.dump(self.engine.state_dict(), f)
        except Exception as e:
            print(f"[App] write state error: {e}")

    # ── Navigation ──

    def go_main(self):       self.sm.current = "main"
    def go_menu(self):       self.sm.current = "menu"
    def go_entries(self):    self.sm.current = "entries"
    def go_settings(self):   self.sm.current = "settings"
    def go_about(self):      self.sm.current = "about"
    def go_shortcuts(self):  self.sm.current = "shortcuts"
    def go_switch_task(self): self.sm.current = "tasklist_switch"
    def go_rename_task(self): self.sm.current = "tasklist_rename"

    def go_new_task(self):
        show_input_popup("New Task", "Task name", self._create_task)

    def go_edit_entry(self, entry):
        self._edit_screen.load(entry)
        self.sm.current = "edit_entry"

    # ── Task actions ──

    def _prompt_first_task(self):
        show_input_popup(
            "Welcome to Timekeeper",
            "Enter your first task name to get started:",
            self._create_task
        )

    def _create_task(self, name):
        task_id = self.storage.add_task(name)
        self._current_task = {"id": task_id, "name": name}
        self.engine.set_task(task_id, name)
        self.storage.set_setting("last_task_id", str(task_id))
        self._refresh_main()
        self.go_main()

    def select_task(self, task):
        self._current_task = task
        self.engine.set_task(task["id"], task["name"])
        self.storage.set_setting("last_task_id", str(task["id"]))
        self._refresh_main()
        self.go_main()

    def start_rename(self, task):
        show_input_popup(
            "Rename Task", "New name",
            lambda new_name: self._do_rename(task, new_name),
            prefill=task["name"]
        )

    def _do_rename(self, task, new_name):
        self.storage.rename_task(task["id"], new_name)
        if self._current_task and self._current_task["id"] == task["id"]:
            self._current_task["name"] = new_name
            self.engine.set_task(task["id"], new_name)
        self._refresh_main()
        self.go_main()

    # ── Timer controls ──

    def on_start_pause(self):
        if self._current_task is None:
            self._prompt_first_task()
            return
        if self.engine.state == TimerState.RUNNING:
            self.engine.pause()
        else:
            if not self.engine.start():
                self._prompt_first_task()
            else:
                self._write_timer_state()

    def on_stop(self):
        self.engine.stop()
        self._svc_manager.stop()
        self._write_timer_state()

    # ── Timer callbacks ──

    def _on_tick(self, elapsed, total, state):
        self._refresh_main(elapsed=elapsed, total=total, state=state)

    def _play_alert(self):
        if platform != "android":
            return
        try:
            from jnius import autoclass
            PythonActivity      = autoclass('org.kivy.android.PythonActivity')
            RingtoneManager     = autoclass('android.media.RingtoneManager')
            NotificationManager = autoclass('android.app.NotificationManager')
            NotificationChannel = autoclass('android.app.NotificationChannel')
            Notification        = autoclass('android.app.Notification')
            Builder             = autoclass('android.app.Notification$Builder')

            context = PythonActivity.mActivity

            # ── Notification (plays sound even on lock screen) ──
            nm = context.getSystemService(context.NOTIFICATION_SERVICE)
            ch = NotificationChannel(
                "tk_alert", "Timekeeper Alert", NotificationManager.IMPORTANCE_HIGH
            )
            ch.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC)
            nm.createNotificationChannel(ch)
            b = Builder(context, "tk_alert")
            b.setSmallIcon(context.getApplicationInfo().icon)
            b.setContentTitle("Timekeeper")
            b.setContentText("Timer complete — well done!")
            b.setAutoCancel(True)
            nm.notify(3, b.build())

            # ── Alarm ringtone (more dramatic; auto-stops after 5s or on popup dismiss) ──
            uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
            if uri is None:
                uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
            ringtone = RingtoneManager.getRingtone(context, uri)
            if ringtone:
                ringtone.play()
                self._alert_ringtone = ringtone
                Clock.schedule_once(lambda dt: self._stop_alert(), 5)
        except Exception as e:
            print(f"[Alert] sound error: {e}")

    def _stop_alert(self, *args):
        r = getattr(self, '_alert_ringtone', None)
        if r:
            try:
                r.stop()
            except Exception:
                pass
            self._alert_ringtone = None

    def _on_complete(self, state):
        self._play_alert()
        self._write_timer_state()
        if state == TimerState.RUNNING:
            show_popup("Interval complete!", "Starting break…",
                       on_dismiss=self._stop_alert)
        elif state == TimerState.BREAK:
            show_popup("Break over!", "Ready for next interval.",
                       on_dismiss=self._stop_alert)
            self._svc_manager.stop()

    # ── Voice ──

    def _on_voice_command(self, cmd):
        if cmd == "start":
            # Don't skip a break via voice — alarm sound can trigger false positives
            if self.engine.state != TimerState.BREAK:
                self.on_start_pause()
        elif cmd == "wait":
            self.engine.pause()
        elif cmd == "stop":
            self.on_stop()

    # ── CSV Export ──

    def do_export_csv(self):
        path = os.path.join(DATA_DIR, "timekeeper_export.csv")
        self.storage.export_csv(path)
        if platform == "android":
            self._android_share(path)
        else:
            show_popup("Exported", f"CSV saved to:\n{path}")
        self.go_main()

    def _android_share(self, path):
        try:
            from jnius import autoclass  # type: ignore
            Intent  = autoclass("android.content.Intent")
            JString = autoclass("java.lang.String")
            Activity = autoclass("org.kivy.android.PythonActivity")
            ctx      = Activity.mActivity
            with open(path, "r", encoding="utf-8") as f:
                csv_text = f.read()
            intent = Intent(Intent.ACTION_SEND)
            intent.setType(JString("text/plain"))
            # Use Intent constants for keys + JString for values to avoid
            # jnius overload-resolution errors on putExtra(String, String)
            intent.putExtra(Intent.EXTRA_SUBJECT, JString("Timekeeper CSV Export"))
            intent.putExtra(Intent.EXTRA_TEXT,    JString(csv_text))
            ctx.startActivity(Intent.createChooser(intent, JString("Share Timekeeper Export")))
        except Exception as e:
            import traceback
            show_popup("Export Error", traceback.format_exc()[-400:])

    # ── UI refresh ──

    def _refresh_main(self, elapsed=None, total=None, state=None):
        eng  = self.engine
        e    = elapsed if elapsed is not None else eng.elapsed
        t    = total   if total   is not None else eng._total_secs
        s    = state   if state   is not None else eng.state
        # When idle, show task duration so arc displays "25 min" ready for next interval
        if s == TimerState.IDLE:
            t = int(self.storage.get_setting("task_mins", DEFAULT_TASK_MINS)) * 60
        name = self._current_task["name"] if self._current_task else ""
        done = self.storage.today_interval_count()
        goal = int(self.storage.get_setting("daily_goal", DEFAULT_DAILY_GOAL))
        self._main_screen.update(e, t, s, name, done, goal)


if __name__ == "__main__":
    TimekeeperApp().run()
