"""
Timekeeper background service.

Two entry paths:
  A) Alarm-triggered wakeup (PYTHON_SERVICE_ARGUMENT is empty or "alarm"):
     The AlarmManager fired us because a timer hit zero while the screen was locked.
     We read pending_alert.json, play the sound, post a notification, then exit.

  B) Foreground-service path (PYTHON_SERVICE_ARGUMENT is the DATA_DIR):
     The app started us explicitly via ServiceManager.  We wait for the timer
     with time.sleep() — this works while the foreground service holds a
     PARTIAL_WAKE_LOCK and the device isn't in full Doze mode.  Path A
     (AlarmManager) is the primary guarantee; this path is a belt-and-braces
     fallback for devices where the alarm wakeup doesn't fire.
"""
import os
import time
import json
import datetime


# ── helpers ─────────────────────────────────────────────────────────────────

def _get_context():
    from jnius import autoclass  # type: ignore
    PythonService = autoclass('org.kivy.android.PythonService')
    PythonService.mService.setAutoRestartService(False)
    return PythonService.mService


def _post_notification(ctx, is_break):
    try:
        from jnius import autoclass  # type: ignore
        NotificationManager = autoclass('android.app.NotificationManager')
        NotificationChannel = autoclass('android.app.NotificationChannel')
        Notification        = autoclass('android.app.Notification')
        Builder             = autoclass('android.app.Notification$Builder')
        JString             = autoclass('java.lang.String')

        nm = ctx.getSystemService(ctx.NOTIFICATION_SERVICE)
        ch = NotificationChannel(
            JString('tk_alert'), JString('Timekeeper Alert'),
            NotificationManager.IMPORTANCE_HIGH
        )
        ch.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC)
        nm.createNotificationChannel(ch)

        text = ('Break over! Ready for next interval.'
                if is_break else 'Interval complete! Well done!')
        b = Builder(ctx, JString('tk_alert'))
        b.setSmallIcon(ctx.getApplicationInfo().icon)
        b.setContentTitle(JString('Timekeeper'))
        b.setContentText(JString(text))
        b.setAutoCancel(True)
        nm.notify(4, b.build())
    except Exception as e:
        print(f'[Service] notification error: {e}')


def _play_sound(ctx, is_break):
    try:
        from jnius import autoclass  # type: ignore
        RingtoneManager = autoclass('android.media.RingtoneManager')
        MediaPlayer     = autoclass('android.media.MediaPlayer')
        AudioManager    = autoclass('android.media.AudioManager')

        sound_type   = (RingtoneManager.TYPE_RINGTONE if is_break
                        else RingtoneManager.TYPE_ALARM)
        audio_stream = (AudioManager.STREAM_RING if is_break
                        else AudioManager.STREAM_ALARM)
        uri = RingtoneManager.getDefaultUri(sound_type)
        if uri is None:
            uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
        if uri:
            mp = MediaPlayer()
            mp.setAudioStreamType(audio_stream)
            mp.setDataSource(ctx, uri)
            mp.prepare()
            mp.start()
            time.sleep(6)
            try:
                mp.stop()
                mp.release()
            except Exception:
                pass
        print('[Service] sound done')
    except Exception as e:
        print(f'[Service] sound error: {e}')


def _alert(ctx, is_break):
    _post_notification(ctx, is_break)
    _play_sound(ctx, is_break)
    print(f'[Service] alert done (is_break={is_break})')


# ── entry points ─────────────────────────────────────────────────────────────

def run_alarm_path(ctx, data_dir):
    """Path A — started by AlarmManager at the exact moment the timer hits zero."""
    alert_path = os.path.join(data_dir, 'pending_alert.json')
    try:
        with open(alert_path) as f:
            info = json.load(f)
        is_break = bool(info.get('is_break', False))
        print(f'[Service] alarm path — is_break={is_break}')
    except Exception as e:
        print(f'[Service] cannot read pending_alert.json: {e}')
        return

    _alert(ctx, is_break)

    # Clean up the alert file so on_resume() doesn't try to cancel a gone alarm
    try:
        os.remove(alert_path)
    except Exception:
        pass


def run_sleep_path(ctx, data_dir):
    """Path B — started by on_pause(); sleep until the timer expires (fallback)."""
    state_path = os.path.join(data_dir, 'timer_state.json')
    try:
        with open(state_path) as f:
            state = json.load(f)
    except Exception as e:
        print(f'[Service] cannot read timer_state.json: {e}')
        return

    timer_state = state.get('state', '')
    if timer_state not in ('running', 'break'):
        print(f'[Service] state is {timer_state!r} — nothing to do')
        return

    total_secs    = float(state.get('total_secs', 0))
    accum_secs    = float(state.get('accum_secs', 0))
    run_start_str = state.get('run_start_dt')

    if run_start_str:
        run_start = datetime.datetime.fromisoformat(run_start_str)
        elapsed   = accum_secs + (datetime.datetime.now() - run_start).total_seconds()
    else:
        elapsed = accum_secs

    remaining = total_secs - elapsed
    is_break  = (timer_state == 'break')

    print(f'[Service] sleep path — waiting {remaining:.1f}s (is_break={is_break})')
    if remaining > 0:
        time.sleep(remaining)

    # If pending_alert.json exists, the alarm already fired — don't double-alert
    alert_path = os.path.join(data_dir, 'pending_alert.json')
    if os.path.exists(alert_path):
        print('[Service] alarm already fired — skipping duplicate alert')
        return

    _alert(ctx, is_break)


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    try:
        ctx = _get_context()
    except Exception as e:
        print(f'[Service] context error: {e}')
        return

    arg = os.environ.get('PYTHON_SERVICE_ARGUMENT', '').strip()

    if not arg or arg == 'alarm':
        # AlarmManager wakeup — DATA_DIR stored in pending_alert.json is not
        # needed here; the file itself tells us what to do.
        # Find data_dir from the standard app files location.
        try:
            data_dir = ctx.getFilesDir().getAbsolutePath()
        except Exception:
            data_dir = ''
        run_alarm_path(ctx, data_dir)
    else:
        # Normal service start from on_pause() — arg is DATA_DIR
        run_sleep_path(ctx, arg)


main()
