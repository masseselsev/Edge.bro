#!/bin/sh
# Read-only reconnaissance of a node's monitoring capabilities.
#
# Answers the questions the datasheets cannot, before any monitoring work is
# built on assumptions:
#
#   * Does RAPL report package energy, and does it move?
#   * Does the CPU load vary enough to identify a thermal model? (the single
#     assumption the whole passive approach rests on)
#   * Which temperature sensors exist, and is there a board sensor separate
#     from the CPU?
#   * Is the SSD temperature readable without spinning up smartctl?
#   * Does gpsd answer?
#
# Safe to run on a live roadside unit: reads only, installs nothing, loads no
# kernel modules, writes only to a temp file it removes. Takes ~3 minutes,
# almost all of it idle sampling.
#
#   ssh root@node 'sh -s' < edge-bro-recon.sh
#
# POSIX sh — no bashisms, no Python assumed.

set -u

# Debian's non-interactive PATH for a plain user omits /usr/sbin and /sbin,
# where smartctl and dmidecode actually live. Confirmed on a live node: the
# binaries were present and this script still reported them absent.
PATH="/usr/sbin:/sbin:$PATH"
export PATH

SAMPLES=${SAMPLES:-60}      # 60 samples at 2 s = 2 minutes of load observation
INTERVAL=${INTERVAL:-2}

say() { printf '%s\n' "$*"; }
hdr() { printf '\n===== %s =====\n' "$*"; }

hdr "IDENTITY"
say "hostname:    $(hostname 2>/dev/null || echo '?')"
say "kernel:      $(uname -r 2>/dev/null || echo '?')"
say "os:          $( . /etc/os-release 2>/dev/null && echo "${NAME:-?} ${VERSION_ID:-}" )"
say "uptime_s:    $(cut -d' ' -f1 /proc/uptime 2>/dev/null || echo '?')"
say "cpu:         $(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ *//')"
say "cores:       $(grep -c ^processor /proc/cpuinfo 2>/dev/null || echo '?')"
if command -v dmidecode >/dev/null 2>&1; then
    say "board:       $(dmidecode -s baseboard-manufacturer 2>/dev/null) $(dmidecode -s baseboard-product-name 2>/dev/null)"
    say "product:     $(dmidecode -s system-product-name 2>/dev/null)"
    say "bios:        $(dmidecode -s bios-version 2>/dev/null)"
else
    say "board:       dmidecode absent"
fi

hdr "RAPL (package power)"
# Without RAPL there is no power measurement and no thermal resistance.
# energy_uj is root-only since CVE-2020-8694, so a readability test run as a
# plain user reports absent even when the file is right there — confirmed on
# a live node, where this used to print "NO RAPL" for a file `sudo cat`
# opened without complaint. Existence and readability are checked separately
# so that case reads as what it is: fine for the collector, since it runs as
# root over the same channel the backup path already uses.
RAPL=""
RAPL_EXISTS_NOT_READABLE=""
for d in /sys/class/powercap/intel-rapl:0 /sys/class/powercap/intel-rapl/intel-rapl:0; do
    if [ -r "$d/energy_uj" ]; then
        RAPL="$d"
        break
    elif [ -e "$d/energy_uj" ]; then
        RAPL_EXISTS_NOT_READABLE="$d"
    fi
done
if [ -n "$RAPL" ]; then
    say "path:        $RAPL"
    say "name:        $(cat "$RAPL/name" 2>/dev/null)"
    say "max_range:   $(cat "$RAPL/max_energy_range_uj" 2>/dev/null)"
    E1=$(cat "$RAPL/energy_uj" 2>/dev/null)
    sleep 2
    E2=$(cat "$RAPL/energy_uj" 2>/dev/null)
    if [ -n "$E1" ] && [ -n "$E2" ] && [ "$E2" -gt "$E1" ] 2>/dev/null; then
        say "power_now_W: $(awk -v a="$E1" -v b="$E2" 'BEGIN{printf "%.2f",(b-a)/2000000}')"
        say "VERDICT:     RAPL OK"
    else
        say "VERDICT:     RAPL present but counter did not advance (E1=$E1 E2=$E2)"
    fi
elif [ -n "$RAPL_EXISTS_NOT_READABLE" ]; then
    say "path:        $RAPL_EXISTS_NOT_READABLE (exists, not readable by $(id -un 2>/dev/null))"
    if [ "$(id -u 2>/dev/null)" = "0" ]; then
        say "VERDICT:     UNEXPECTED — already root and still denied; check kernel config"
    else
        say "VERDICT:     present, permission denied for this user — expected, root-only since CVE-2020-8694."
        say "             The collector runs as root over the same channel backups already use, so this is fine."
        say "             Re-run as root (or via sudo) for a real reading: sudo sh -s < edge-bro-recon.sh"
    fi
else
    say "VERDICT:     NO RAPL — package power unavailable, thermal model degraded"
    say "             (check: modprobe intel_rapl_common intel_rapl_msr)"
fi

hdr "TEMPERATURE SENSORS"
# hwmon is the cheap path; anything here needs no extra tooling.
found_hwmon=0
for h in /sys/class/hwmon/hwmon*; do
    [ -d "$h" ] || continue
    name=$(cat "$h/name" 2>/dev/null || echo '?')
    for t in "$h"/temp*_input; do
        [ -r "$t" ] || continue
        found_hwmon=1
        label_file=$(echo "$t" | sed 's/_input$/_label/')
        label=$(cat "$label_file" 2>/dev/null || basename "$t" _input)
        val=$(awk '{printf "%.1f", $1/1000}' "$t" 2>/dev/null)
        say "  $name / $label = ${val} C   ($t)"
    done
done
[ "$found_hwmon" = 1 ] || say "  no hwmon temperature inputs at all"

say ""
say "thermal_zone:"
for z in /sys/class/thermal/thermal_zone*; do
    [ -r "$z/temp" ] || continue
    say "  $(cat "$z/type" 2>/dev/null) = $(awk '{printf "%.1f", $1/1000}' "$z/temp" 2>/dev/null) C"
done

say ""
say "drivetemp module (cheap SSD temperature, no smartctl):"
if grep -q '^drivetemp ' /proc/modules 2>/dev/null; then
    say "  loaded"
elif [ -e /lib/modules/"$(uname -r)"/kernel/drivers/hwmon/drivetemp.ko ] \
  || [ -e /lib/modules/"$(uname -r)"/kernel/drivers/hwmon/drivetemp.ko.xz ]; then
    say "  AVAILABLE but not loaded — 'modprobe drivetemp' would expose SATA temps"
else
    say "  not available in this kernel"
fi

say ""
say "it87 / Super-I/O (board sensor — the EMBC-5000 carries an ITE IT8786E):"
if grep -q '^it87 ' /proc/modules 2>/dev/null; then
    say "  it87 loaded"
else
    say "  it87 NOT loaded."
    say "  Expect an ACPI resource conflict; confirm with: modprobe it87 2>&1"
    say "  If it says 'Failed to request region', the fix is the kernel parameter"
    say "  acpi_enforce_resources=lax — which costs a reboot."
fi
say "  acpi_enforce_resources: $(cat /sys/module/acpi/parameters/acpi_enforce_resources 2>/dev/null || echo '?')"
say "  cmdline: $(cat /proc/cmdline 2>/dev/null)"

hdr "THROTTLING"
for c in /sys/devices/system/cpu/cpu0/thermal_throttle/*; do
    [ -r "$c" ] && say "  $(basename "$c") = $(cat "$c" 2>/dev/null)"
done

hdr "STORAGE"
lsblk -d -o NAME,ROTA,SIZE,MODEL 2>/dev/null || say "  lsblk absent"
say ""
if command -v smartctl >/dev/null 2>&1; then
    say "smartctl: $(smartctl --version 2>/dev/null | head -1)"
    say "  --json supported: $(smartctl -j -i /dev/sda >/dev/null 2>&1 && echo yes || echo 'no / no such device')"
else
    say "smartctl: ABSENT — smartmontools would need installing"
fi

hdr "GPSD"
if command -v gpspipe >/dev/null 2>&1; then
    say "gpspipe present; sampling 5 records (5 s timeout)..."
    timeout 5 gpspipe -w -n 5 2>/dev/null | grep -o '"lat":[-0-9.]*\|"lon":[-0-9.]*\|"mode":[0-9]*' | head -12 \
        || say "  no fix / no output"
else
    say "gpspipe absent"
    command -v gpsd >/dev/null 2>&1 && say "  (gpsd binary exists; gpsd-clients not installed)"
fi

hdr "LOAD EXCITATION over $((SAMPLES * INTERVAL))s"
# THE decisive measurement. Identifying theta from passive data needs the CPU
# power to vary. If it is flat, the passive approach cannot work and the design
# must fall back to cohort comparison alone.
if [ -n "$RAPL" ]; then
    TMP=$(mktemp) || TMP=/tmp/edge-bro-recon.$$
    PREV=$(cat "$RAPL/energy_uj" 2>/dev/null)
    i=0
    while [ "$i" -lt "$SAMPLES" ]; do
        sleep "$INTERVAL"
        CUR=$(cat "$RAPL/energy_uj" 2>/dev/null)
        if [ -n "$PREV" ] && [ -n "$CUR" ]; then
            awk -v a="$PREV" -v b="$CUR" -v dt="$INTERVAL" \
                'BEGIN{d=b-a; if(d>=0) printf "%.4f\n", d/(dt*1000000)}' >> "$TMP"
        fi
        PREV=$CUR
        i=$((i + 1))
    done

    say "watts samples: $(wc -l < "$TMP")"
    sort -n "$TMP" | awk '
        {v[NR]=$1; s+=$1}
        END{
            if(NR<4){print "  too few samples"; exit}
            med=(NR%2)?v[int(NR/2)+1]:(v[NR/2]+v[NR/2+1])/2
            q1=v[int(NR*0.25)+1]; q3=v[int(NR*0.75)+1]
            iqr=q3-q1
            exc=(med>0)?iqr/med:0
            printf "  min=%.2f W  q1=%.2f  median=%.2f  q3=%.2f  max=%.2f  mean=%.2f\n", v[1],q1,med,q3,v[NR],s/NR
            printf "  IQR=%.3f W   EXCITATION=%.3f  (IQR/median)\n", iqr, exc
            print  ""
            if(exc>=0.15)
                print "  VERDICT: sufficient natural excitation — passive theta identification viable"
            else if(exc>=0.05)
                print "  VERDICT: MARGINAL — theta will be noisy; backup windows become essential"
            else
                print "  VERDICT: LOAD IS FLAT — passive identification will NOT work."
                print "           Fall back to cohort comparison, and lean entirely on backup windows."
        }'
    rm -f "$TMP"
else
    say "  skipped, no RAPL"
fi

hdr "DONE"
say "Two minutes of idle observation is a small sample. If the workload has a"
say "daily rhythm (traffic volume), re-run this at rush hour and at 03:00 —"
say "the excitation figures will differ and both matter."
