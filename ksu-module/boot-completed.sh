#!/system/bin/sh
#
# CoreSight ETE loader for Xiaomi 14 Pro (shennong / SM8650) -- stage 3/3
# ----------------------------------------------------------------------
# Reaching boot-completed proves the whole boot survived with the driver
# loaded.  That is the signal post-fs-data.sh's watchdog waits for, so record
# it and write a one-shot capability report.

MODDIR=${0%/*}
STATE=/data/adb/coresight
LOG=$STATE/coresight.log
REPORT=$STATE/last-report.txt

mkdir -p "$STATE" 2>/dev/null
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [boot-completed] $*" >> "$LOG" 2>/dev/null; }

if [ -f "$STATE/DISABLE" ]; then
    log "boot completed, but DISABLE is set -> nothing recorded"
    exit 0
fi

: > "$STATE/boot_ok"
log "boot completed with the module -> success recorded"

{
    echo "generated: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "kernel   : $(uname -r)"
    echo "verified : $(cat "$MODDIR/verified-kernel" 2>/dev/null || echo '<unknown>')"
    echo

    if [ -f "$STATE/kernel-changed" ]; then
        echo "guard    : REFUSED - the kernel changed since this .ko was verified"
        sed 's/^/           /' "$STATE/kernel-changed"
        echo "           re-run the offline gate, rebuild, update verified-kernel, reboot."
    elif [ -f "$STATE/DISABLE" ]; then
        echo "guard    : DISABLE flag present"
    else
        echo "guard    : ok"
    fi

    if grep -q '^coresight_etm4x ' /proc/modules 2>/dev/null; then
        echo "module   : LOADED"
    else
        echo "module   : NOT LOADED"
    fi
    echo

    echo "ETE sources:"
    for d in /sys/bus/coresight/devices/coresight-ete*; do
        [ -e "$d" ] || continue
        n=$(basename "$d")
        echo "  $n  cpu=$(cat "$d/cpu" 2>/dev/null)  authstatus=$(cat "$d/mgmt/trcauthstatus" 2>/dev/null)  devarch=$(cat "$d/mgmt/trcdevarch" 2>/dev/null)"
    done

    echo
    echo "cs_etm PMU cpu links:"
    ls -l /sys/bus/event_source/devices/cs_etm/ 2>/dev/null | grep 'cpu[0-9]' | sed 's/^.* cs_etm\//  /'
    echo "sinks: $(ls /sys/bus/event_source/devices/cs_etm/sinks/ 2>/dev/null | tr '\n' ' ')"

    echo
    if [ -e /sys/bus/coresight/devices/coresight-ete0 ]; then
        echo "verdict: OK - ETE hardware accepted the driver; cs_etm tracing is available"
    else
        echo "verdict: FAILED - no coresight-ete0 (module not loaded, or probe rejected)"
    fi
} > "$REPORT" 2>&1

log "report written to $REPORT"
exit 0
