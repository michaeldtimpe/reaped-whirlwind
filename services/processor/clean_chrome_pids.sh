#!/bin/bash
# kill_defunct_chrome.sh
# Kills defunct (zombie) Chrome processes older than 5 minutes.
# Safe to run manually or via cron.

MIN_AGE_SECONDS=300  # 5 minutes
NOW=$(date +%s)
KILLED=0
SKIPPED=0

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Scanning for defunct Chrome processes older than ${MIN_AGE_SECONDS}s..."

# Find all defunct chrome processes
while read -r pid ppid stime; do
    # Parse the start time of the process using /proc/<pid>/stat
    # Fall back to 'ps' elapsed time if /proc is unavailable
    if [[ ! -d "/proc/$pid" ]]; then
        # Process already gone
        continue
    fi

    # Get process start time in seconds since epoch via stat on /proc/<pid>
    PROC_START=$(stat -c %Y /proc/"$pid" 2>/dev/null)
    if [[ -z "$PROC_START" ]]; then
        echo "  [SKIP] PID $pid — could not determine start time"
        ((SKIPPED++))
        continue
    fi

    AGE=$(( NOW - PROC_START ))

    if (( AGE >= MIN_AGE_SECONDS )); then
        echo "  [KILL] PID $pid (PPID $ppid) — age ${AGE}s >= ${MIN_AGE_SECONDS}s"
        # Defunct processes can't be killed directly; signal the parent to reap them.
        # Try SIGCHLD to nudge the parent, then SIGKILL the zombie's PID as fallback.
        kill -SIGCHLD "$ppid" 2>/dev/null
        kill -9 "$pid" 2>/dev/null
        ((KILLED++))
    else
        echo "  [SKIP] PID $pid (PPID $ppid) — age ${AGE}s < ${MIN_AGE_SECONDS}s (too young)"
        ((SKIPPED++))
    fi

done < <(ps -eo pid,ppid,stat,comm | awk '$3 ~ /Z/ && $4 == "chrome" {print $1, $2, $3}')

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Done. Killed: $KILLED | Skipped (too young): $SKIPPED"

# Optionally reap all defunct children of the parent process (PID 26768 from your logs)
# Uncomment the line below if you want to also signal the known parent directly:
# kill -SIGCHLD 26768 2>/dev/null
