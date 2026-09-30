# coresight-etm4x for Xiaomi 14 Pro (shennong / SM8650)

Builds the **missing** APSS program-flow-trace driver module (`coresight-etm4x.ko`)
for the Xiaomi 14 Pro (`shennong`, SM8650, HyperOS `OS3.0.306.0.WNBCNXM`,
kernel `6.1.138-android14-11`), and packages it as a KernelSU/Magisk module.

## The finding

`/sys/bus/coresight/devices/` on the device lists 129 CoreSight devices — every
funnel, replicator, TMC (ETF/ETR), CTI, TPDM, TPDA, STM and traceNoc is present
and probes successfully. `qdss_region@82800000` is still reserved for trace
buffers. The live DT still contains the CPU trace sources:

```
/soc/ete0 .. /soc/ete7      compatible = "arm,embedded-trace-extension"
                            (no "status" property -> enabled)
funnel_ete                  wired to all eight ETE ports
```

…yet `/sys/bus/platform/devices/soc:ete0` never gets a driver, there is no
`/sys/bus/coresight/devices/coresight-ete0`, and `cs_etm/nr_addr_filters` exists
with **no** `cpus` attribute. `grep -c etm4 /proc/kallsyms` is `0`.

The reason is purely a build/packaging decision, not hardware fusing:

| config file (branch `shennong-u-oss`) | variant | `CONFIG_CORESIGHT_SOURCE_ETM4X` |
|---|---|---|
| `arch/arm64/configs/vendor/pineapple_GKI.config` | **shipping / GKI** | **absent** |
| `arch/arm64/configs/vendor/pineapple_consolidate.config` | internal debug (28 lines: LKDTM, RCU/lock torture, DEBUG_PAGEALLOC …) | `=m` |

The 13 `CONFIG_CORESIGHT_*` lines in `pineapple_GKI.config` map 1:1 to the
modules shipped in `/vendor_dlkm` — the ETM/ETE driver is simply left out of the
production config and is only enabled together with the internal debug knobs.

`drivers/hwtracing/coresight/coresight-etm4x-core.c` already supports this DT
without any change, including the Qualcomm specific bits:

```c
/* 1955-1958: sysreg access cannot use TRCPDCR */
if (!desc.access.io_mem ||
    fwnode_property_present(dev_fwnode(dev), "qcom,skip-power-up"))
        drvdata->skip_power_up = true;

/* 2157-2163: ETE is reached through the sysreg interface, so no "reg" needed */
static const struct of_device_id etm4_sysreg_match[] = {
        { .compatible = "arm,coresight-etm4x-sysreg" },
        { .compatible = "arm,embedded-trace-extension" },
        {}
};
```

## Why a module built from a different tree still loads

Three kernel behaviours add up.

1. **`kernel/module/version.c :: same_magic()`** — when the module carries a
   `__versions` section (`CONFIG_MODVERSIONS`) the kernel compares *only* the
   trailing vermagic flags:

   ```c
   if (has_crcs) {
           amagic += strcspn(amagic, " ");
           bmagic += strcspn(bmagic, " ");
   }
   return strcmp(amagic, bmagic) == 0;
   ```

   So `6.1.25-…` vs `6.1.138-…` does not matter; only
   `SMP preempt mod_unload modversions aarch64` must match.

2. **`check_version()` compares CRCs exactly, and only tolerates *missing*
   entries.** There is no "CRC 0 means anything goes" escape hatch — an earlier
   revision of this repository assumed there was one and the device rejected the
   result with `coresight_etm4x: disagrees about version of symbol module_layout`
   (`insmod: Exec format error`). The real code is:

   ```c
   for (i = 0; i < num_versions; i++) {
           if (strcmp(versions[i].name, symname) != 0)
                   continue;
           crcval = *crc;
           if (versions[i].crc == crcval)   /* exact match, no special case */
                   return 1;
           goto bad_version;
   }
   pr_warn_once("%s: no symbol version for %s\n", info->name, symname);
   return 1;                                /* absent -> accepted */
   ```

   So each imported symbol must either be **absent** from `__versions` or carry
   the **exact** CRC the kernel publishes. `refs/kernel.symvers` supplies the
   latter: `tools/collect_crcs.py` harvests the CRCs from the vendor modules
   already installed on the device (they import the same kernel symbols and load
   successfully, so the CRC they recorded *is* the expected one). Anything
   modpost still cannot resolve is left out and accepted via the `return 1`
   above.

   The one thing that must *not* happen is modpost inventing a CRC. It does that
   for any symbol exported by a module built in the same run: it runs genksyms
   over **this** tree's headers, which yields a different value than the
   `coresight.ko` already on the device publishes. The first working revision
   still tripped over exactly this:

   ```
   coresight_register   ours 0x459d07a5   device 0xa815b2ed   -> rejected
   module_layout        ours 0xea759d7f   device 0xea759d7f   -> fine (came from symvers)
   ```

   The CI therefore trims `drivers/hwtracing/coresight/Makefile` down to the
   `coresight-etm4x` object, so `coresight_register`, `cscfg_*`,
   `etm_perf_symlink` and friends stay unresolved and are simply omitted from
   `__versions` - which this kernel accepts. The device already ships
   `coresight.ko`, `coresight-tmc.ko`, funnel and replicator, so nothing else
   needs building.

3. **`kernel/module/signing.c`** — with `CONFIG_MODULE_SIG_PROTECT=y`
   (present in the device config) signature enforcement is disabled:

   ```c
   #ifndef CONFIG_MODULE_SIG_PROTECT
           return security_locked_down(LOCKDOWN_MODULE_SIGNATURE);
   #else
           return 0;
   #endif
   ```

   The same patch also forces `sig_enforce = false`.

## Layout

```
.github/workflows/build.yml   clone kernel -> fetch AOSP clang -> build -> package
ksu-module/                   KernelSU/Magisk module skeleton (module.prop + scripts)
tools/repair_tree.py          disable Kconfig sources missing from the OSS snapshot
tools/collect_crcs.py         harvest expected symbol CRCs from the device's own modules
tools/modversions.py          dump / zero / compare `__versions` CRCs
tools/check_symbols.py        verify every imported symbol exists in /proc/kallsyms
refs/kernel.symvers           the harvested symbol -> CRC map, fed to modpost
```

Two tree fixes are needed because Xiaomi's OSS snapshot does not match the tree
that produced the shipping build:

* `drivers/misc/Kconfig` sources `drivers/misc/hwid/Kconfig`, but `hwid/` was
  stripped from the release, so *every* `defconfig`/`olddefconfig` run dies with
  `can't open file`. `repair_tree.py` comments the dangling statement out; the
  matching `obj-$(CONFIG_MI_HARDWARE_ID) += hwid/` line then evaluates to
  nothing, which is what we want anyway.
* `coresight-tmc-usb.c` still calls the 2-argument form of `usb_qdss_alloc_req()`
  while `include/linux/usb/usb_qdss.h` declares the 3-argument one.
  `CORESIGHT_SOURCE_ETM4X` only selects `CORESIGHT_LINKS_AND_SINKS` — not
  `CORESIGHT_LINK_AND_SINK_TMC` — so leaving TMC out of the config fragment
  keeps `coresight-tmc.ko` (and that file) out of the build entirely.

## Verification of the produced module

| property | module built here | device kernel / modules | verdict |
|---|---|---|---|
| `vermagic` | `6.1.25-g1c27eb534afc SMP preempt mod_unload modversions aarch64` | `6.1.138-android14-11-g0c3d559bcd85-ab14529422 SMP preempt mod_unload modversions aarch64` | **OK** — `same_magic()` drops the first token when `__versions` exists, leaving an identical ` SMP preempt mod_unload modversions aarch64` |
| `__versions` section | present; entries carry the exact CRC this kernel publishes; module-exported symbols are omitted rather than guessed | exact-CRC check, absent entries accepted | **OK** — see `tools/collect_crcs.py` |
| signature | unsigned | `CONFIG_MODULE_SIG_PROTECT=y` | **OK** — that option forces `sig_enforce = false` |
| CFI / LTO mode | `LTO_NONE` + `CFI_CLANG` + `SHADOW_CALL_STACK`, clang 17.0.2 r487747c | identical | **OK** |
| DT match table | `arm,embedded-trace-extension`, `qcom,skip-power-up` present in the object | DT has `ete0..7` with exactly those properties | **OK** |
| imported symbols | 61 undefined symbols, 9 of them `coresight_*` | all 61 present in `/proc/kallsyms`, including every `coresight_*` | **OK** |

Reproduce the symbol row with:

```sh
su -c 'cat /proc/kallsyms' | awk '{print $3}' | sort -u > kallsyms.txt
tools/check_symbols.py dist/coresight-etm4x.ko kallsyms.txt
```

`kmemleak`-style deep checks are not possible without loading it; that is
exactly what the module is for.

## Installing

Either install `ksu-coresight-etm4x.zip` from the KernelSU (or Magisk) manager,
or copy the files by hand - which is all the manager does anyway:

```sh
su -c '
  D=/data/adb/modules/coresight_etm4x
  mkdir -p $D
  cp coresight-etm4x.ko module.prop post-fs-data.sh service.sh $D/
  touch $D/update
  chmod 755 $D/post-fs-data.sh $D/service.sh
'
```

Then reboot and check:

```sh
ls /sys/bus/coresight/devices/ | grep ete        # expect coresight-ete0 .. coresight-ete7
cat /sys/bus/event_source/devices/cs_etm/cpus    # expect 0-7
cat /data/adb/coresight/last-report.txt
dmesg | grep -iE 'ete|ETM arch init'
```

If the driver binds, the APSS ETE hardware accepted it and instruction-level
tracing via `perf record -e cs_etm/@tmc_etr/` becomes usable.

If `coresight-ete0` never appears, look for `ETM arch init failed` in `dmesg`:
that would mean the ETE registers are not reachable — a TrustZone / DBGEN gate
rather than a missing driver.

## Rollback

Nothing outside `/data` is modified, so there is no path to a hard brick.

```sh
touch /data/adb/coresight/DISABLE      # stop loading, leave everything else alone
```

or delete `/data/adb/modules/coresight_etm4x`. Safe mode (hold Volume Down while
booting) skips all module scripts entirely. The boot-attempt watchdog in
`post-fs-data.sh` already disables the module by itself after three boots that
never reach `service.sh`.

