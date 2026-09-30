#!/system/bin/sh
#
# Runs when KernelSU is about to delete this module (the 'remove' flag, or the
# manager's uninstall).  Unload the driver right away so the device returns to
# stock state immediately instead of at the next reboot, then drop our state.
#
# Nothing outside /data was ever touched, so this is a complete restore.

if grep -q '^coresight_etm4x ' /proc/modules 2>/dev/null; then
    RMMOD=/system/bin/rmmod
    [ -x "$RMMOD" ] || RMMOD=rmmod
    "$RMMOD" coresight_etm4x 2>/dev/null
fi

# everything this module ever wrote lives under one directory
rm -rf /data/adb/coresight

exit 0
