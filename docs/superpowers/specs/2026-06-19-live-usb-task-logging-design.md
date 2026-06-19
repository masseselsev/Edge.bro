# Live-USB Subprocess Task Logging and Memorable Kiosk ID Design

Design for capturing/streaming stdout/stderr of subprocesses in real-time during Live-USB ISO generation and changing the Kiosk ID format to a memorable random pattern (`XX1234`).

## Problem Statement
1. **Empty console logs**: The Live-USB ISO generation logs console displays "No output logs generated yet..." because the frontend filters out progress indicators (like `[PROGRESS] 10:Unpacking base ISO...`), and the actual CLI operations (`xorriso`, `cp`, `cpio`, `mkisofs`, etc.) are executed using standard `subprocess.check_call` without capturing or redirecting their output stream into the task logging framework.
2. **Hard-to-copy Kiosk UUIDs**: Since kiosks run on physical nodes from a Live-CD, users find copying a standard 36-character UUID from the screen very inconvenient. A shorter, memorable random format (`XX1234`) is preferred.

## Proposed Solution
1. **Subprocess Logging**: We will introduce a helper function `run_command_with_logging` inside `backend/tasks.py` and import it into `backend/iso_tasks.py`. This helper will use `subprocess.Popen` to redirect stdout/stderr and read line-by-line in real-time, calling `log_to_task` to store the lines in the database.
2. **Memorable Kiosk ID**: We will replace `uuid.uuid4()` with a memorable random identifier matching the pattern `XX1234` (two uppercase letters followed by four digits).

## Detailed Changes

### 1. Kiosk ID Generator Logic
We will introduce a helper function `generate_kiosk_id()` to generate IDs like `KB4821`:
```python
import random
import string

def generate_kiosk_id() -> str:
    """Generates a memorable kiosk identifier in XX1234 pattern (2 letters + 4 digits)."""
    letters = "".join(random.choices(string.ascii_uppercase, k=2))
    digits = "".join(random.choices(string.digits, k=4))
    return f"{letters}{digits}"
```

### 2. ISO Generation Task (`backend/iso_tasks.py`)
1. Import `run_command_with_logging` from `tasks`.
2. Replace all `subprocess.check_call` and `subprocess.check_output` calls within `generate_client_iso_task` with calls to `run_command_with_logging`.
3. Add verbose flags where appropriate:
   - For `cp -r` -> use `cp -v -r` or similar.
   - For `cpio` -> use `cpio -v` or similar.
4. Replace `uuid.uuid4()` generator in configuration injection with `generate_kiosk_id()`.

### 3. Kiosk Payload Backend (`payload_client/backend/main.py`)
Replace `uuid.uuid4()` with `generate_kiosk_id()` when initializing the fallback client UUID if not provided by the configuration.

### 4. Tests (`backend/tests/test_kiosks.py`)
Update tests to use a mock/fixed kiosk ID conforming to the `XX1234` pattern or matching the format.

### 5. Backend Utilities (`backend/tasks.py`)
Add `run_command_with_logging` helper function:
```python
from typing import List, Union

def run_command_with_logging(
    task_id: str,
    cmd: Union[str, List[str]],
    shell: bool = False
) -> None:
    """
    Runs a subprocess command and streams its stdout/stderr line-by-line
    to the TaskLog record via log_to_task.
    """
    import subprocess
    
    cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
    log_to_task(task_id, f"[EXEC] {cmd_str}")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        shell=shell,
        bufsize=1
    )

    if process.stdout:
        for line in iter(process.stdout.readline, ""):
            log_to_task(task_id, line.rstrip("\r\n"))
        process.stdout.close()

    return_code = process.wait()
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, cmd)
```

## Verification Plan

### Automated Verification
- Run tests (`pytest`) to ensure kiosk pairing still functions correctly.
- Verify `backend/iso_tasks.py` remains under the 500-line limit.

### Manual Verification
1. Trigger Live-USB generation from the UI.
2. Open the console log modal.
3. Verify that the unpack, pack, configuration copy, and xorriso logs stream in real-time.
4. Verify the generated ISO config has a memorable ID like `KB4821`.
