[app]
title = Timekeeper
package.name = timekeeper
package.domain = com.timekeeper

source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 0.00001

requirements = python3,kivy,android

orientation = portrait
fullscreen = 0

android.permissions = RECORD_AUDIO, INTERNET, WRITE_EXTERNAL_STORAGE, READ_EXTERNAL_STORAGE
android.api = 33
android.minapi = 21
android.ndk = 25b
android.sdk = 33
android.arch = arm64-v8a

android.gradle_dependencies = androidx.core:core:1.10.0

# FileProvider for CSV sharing
android.add_src = src/

[buildozer]
log_level = 2
warn_on_root = 1
