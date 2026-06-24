"""
Timekeeper background service.
Runs as an Android foreground service (exempt from Doze mode).
Reads the timer state file, sleeps until the timer expires,
then plays a sound and posts a notification — regardless of whether
the main app is open or the screen is locked.
"""
import os
import time
import json
import datetime


def main():
    # Disable auto-restart — if something goes wrong, don't loop forever
    try:
        from jnius import autoclass  # type: ignore
        PythonService = autoclass('org.kivy.android.PythonService')
        PythonService.mService.setAutoRestartService(False)
        data_dir = os.environ.get('PYTHON_SERVICE_ARGUMENT', '').strip()
    except Exception as e:
        print(f'[Service] init error: {e}')
        return

    if not data_dir:
        print('[Service] no DATA_DIR argument — exiting')
        return

    state_path = os.path.join(data_dir, 'timer_state.json')

    try:
        with open(state_path) as f:
            state = json.load(f)
    except Exception as e:
        print(f'[Service] cannot read state file: {e}')
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

    print(f'[Service] waiting {remaining:.1f}s (is_break={is_break})')

    if remaining > 0:
        time.sleep(remaining)

    _alert(is_break)


def _alert(is_break):
    try:
        from jnius import autoclass  # type: ignore

        PythonService       = autoclass('org.kivy.android.PythonService')
        RingtoneManager     = autoclass('android.media.RingtoneManager')
        MediaPlayer         = autoclass('android.media.MediaPlayer')
        AudioManager        = autoclass('android.media.AudioManager')
        NotificationManager = autoclass('android.app.NotificationManager')
        NotificationChannel = autoclass('android.app.NotificationChannel')
        Notification        = autoclass('android.app.Notification')
        Builder             = autoclass('android.app.Notification$Builder')
        JString             = autoclass('java.lang.String')

        ctx = PythonService.mService

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

        print('[Service] alert done')

    except Exception as e:
        print(f'[Service] alert error: {e}')


main()
