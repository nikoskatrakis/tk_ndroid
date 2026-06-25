[app]
title = Timekeeper
package.name = timekeeper
package.domain = com.timekeeper

source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 0.00008

requirements = python3,kivy,android

# No named service needed — PythonService (always in bootstrap) is targeted directly
# p4a auto-detects service/main.py and declares PythonService in the manifest

orientation = portrait
fullscreen = 0

android.accept_sdk_license = True

android.permissions = RECORD_AUDIO, INTERNET, WRITE_EXTERNAL_STORAGE, READ_EXTERNAL_STORAGE, WAKE_LOCK, FOREGROUND_SERVICE, USE_EXACT_ALARM, SCHEDULE_EXACT_ALARM, REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, RECEIVE_BOOT_COMPLETED
android.api = 34
android.minapi = 21
android.ndk = 28c
android.sdk = 34
android.arch = arm64-v8a
android.build_tools_version = 34.0.0

android.gradle_dependencies = androidx.core:core:1.10.0

[buildozer]
log_level = 2
warn_on_root = 1
