#!/system/bin/sh
#
# Action button: toggle the driver at runtime, without rebooting.
#
#   loaded      -> rmmod  => the device is back to STOCK STATE immediately
#   not loaded  -> insmod => tracing available immediately
#
# Note the asymmetry, because it matters:
#   * the driver lives in RAM only, so unloading it IS a full restore --
#     nothing on any partition was ever modified;
#   * "off" from this button lasts until the next boot.  To make it permanent,
#     disable the module in the KernelSU manager (that creates
#     /data/adb/modules/coresight_etm4x/disable, and KernelSU then stops
#     running this module's scripts at all), or uninstall the module.

MODDIR=${0%/*}
STATE=/data/adb/coresight
KO=$MODDIR/coresight-etm4x.ko
VERFILE=$MODDIR/verified-kernel
LOG=$STATE/coresight.log

mkdir -p "$STATE" 2>/dev/null
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [action] $*" >> "$LOG" 2>/dev/null; }

if grep -q '^coresight_etm4x ' /proc/modules 2>/dev/null; then
    # ---- currently loaded -> unload, i.e. restore stock state -------------
    RMMOD=/system/bin/rmmod
    [ -x "$RMMOD" ] || RMMOD=rmmod
    "$RMMOD" coresight_etm4x 2>>"$LOG"
    rc=$?
    log "action: rmmod rc=$rc"
    echo "coresight_etm4x unloaded (rc=$rc)."
    echo "The kernel is back to its stock state: /sys/bus/coresight/devices/"
    echo "coresight-ete0..7 and the cs_etm/cpuN links are gone."
    echo "It will load again on the next boot -- to stop that permanently,"
    echo "disable this module in the KernelSU manager, or uninstall it."
else
    # ---- not loaded -------------------------------------------------------
    # Same guard as service.sh: never load a .ko whose kernel has moved.
    WANT=$(cat "$VERFILE" 2>/dev/null)
    HAVE=$(uname -r)
    if [ -n "$WANT" ] && [ "$WANT" != "$HAVE" ]; then
        log "action: refusing to load, kernel changed ('$WANT' -> '$HAVE')"
        echo "REFUSING TO LOAD."
        echo "  this .ko was verified for : $WANT"
        echo "  the running kernel is     : $HAVE"
        echo
        echo "The module is ABI- and CRC-matched to one kernel build.  If the"
        echo "kernel changed (system update), loading the old .ko can panic the"
        echo "module loader -- so we do nothing until it is re-verified."
        echo
        echo "To re-enable:"
        echo "  1. run the offline gate against the new device facts:"
        echo "       python tools/preflight.py <new.ko> --vendor refs/ref-coresight.ko --etm4x"
        echo "       python tools/preflight.py <new.ko> --btf refs/vmlinux.btf"
        echo "  2. if it is green, write the new version in and reboot:"
        echo "       echo \"$HAVE\" > $VERFILE"
        exit 1
    fi
    if [ ! -f "$KO" ]; then
        echo "ERROR: $KO missing."
        exit 1
    fi
    rm -f "$STATE/kernel-changed"
    INSMOD=/system/bin/insmod
    [ -x "$INSMOD" ] || INSMOD=insmod
    : > "$STATE/armed"
    sync
    "$INSMOD" "$KO" >>"$LOG" 2>&1
    rc=$?
    rm -f "$STATE/armed"
    sync
    log "action: insmod rc=$rc"
    n=0
    for d in /sys/bus/coresight/devices/coresight-ete*; do
        [ -e "$d" ] || continue
        n=$((n + 1))
    done
    echo "insmod rc=$rc, coresight-ete devices = $n"
    if [ "$n" -gt 0 ]; then
        echo "ETE tracing is live. Try:"
        echo "  simpleperf record -e cs-etm --duration 3 -a -o /data/local/tmp/etm.data"
    else
        echo "No ETE device appeared -- check $LOG"
    fi
fi
exit 0
