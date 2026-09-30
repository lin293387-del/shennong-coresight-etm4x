#!/system/bin/sh
#
# CoreSight ETE loader for Xiaomi 14 Pro (shennong / SM8650) -- stage 1/3
# ----------------------------------------------------------------------
# Xiaomi's shipping vendor_dlkm enables the whole CoreSight infrastructure but
# never builds CONFIG_CORESIGHT_SOURCE_ETM4X, so the DT nodes
# /soc/ete0..ete7 ("arm,embedded-trace-extension") have no driver, the perf
# cs_etm PMU ends up with no sources, and instruction tracing is impossible.
# This module loads the one missing driver (.ko) at boot.
#
# KernelSU module contract (https://kernelsu.org/guide/module.html):
#   * /data/adb/modules/<id>/disable  exists -> module is disabled, no script runs
#   * /data/adb/modules/<id>/remove   exists -> module is deleted on next boot
#     (and uninstall.sh runs first)
#   * no 'system' directory here, so no metamodule is required
# => disabling or removing this module restores stock behaviour, because the
#    driver only ever lives in RAM: nothing outside /data is ever written, no
#    partition, no boot image, no vendor_dlkm.
#
# Rescue paths, in order of preference:
#   1. this script's own watchdog (below) -- after ONE boot that dies between
#      "armed" and a completed boot, it writes $STATE/DISABLE and stops.
#   2. KernelSU safe mode: press volume-down 3 separate times right after the
#      first boot screen; all modules are disabled for that boot.
#   3. `adb shell su -c 'ksud module disable coresight_etm4x'`
#   4. Recovery: the HyperOS/MIUI built-in safe mode also disables KSU modules.
#

MODDIR=${0%/*}
STATE=/data/adb/coresight
LOG=$STATE/coresight.log

mkdir -p "$STATE" 2>/dev/null

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [post-fs-data] $*" >> "$LOG" 2>/dev/null; }

log "--- boot start (kernel $(uname -r)) ---"

# ---- 1. did the previous boot die after we armed the insmod? --------------
# service.sh touches 'armed' immediately before insmod and removes it as soon
# as insmod returns.  So if 'armed' is still here, the kernel did not survive
# the load (panic + watchdog reset).  A kernel panic here is deterministic, so
# disable on the first occurrence instead of burning more reboots.
if [ -f "$STATE/armed" ]; then
    rm -f "$STATE/armed"
    touch "$STATE/DISABLE"
    log "WATCHDOG: previous boot did not survive the module load -> DISABLED."
    log "WATCHDOG: delete $STATE/DISABLE to retry after fixing the team's .ko"
    exit 0
fi

# ---- 2. new boot: success is not proven yet -------------------------------
rm -f "$STATE/boot_ok"

exit 0
