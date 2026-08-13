import os
import subprocess
import tempfile
import logging
from typing import Dict, Any, Optional, Tuple
from core.db_session import session_scope
from models import TaskLog, Settings
from core import ssh_keys
import re

logger = logging.getLogger(__name__)

def clean_cpu_info(cpu: str) -> str:
    if not cpu:
        return "Generic CPU"
    # Remove trademarks and brackets
    cpu = cpu.replace("(R)", "").replace("(TM)", "").replace("®", "").replace("™", "")
    cpu = cpu.replace("IntelR", "Intel").replace("CoreTM", "Core")
    # Remove redundant fillers
    cpu = cpu.replace("NONE CPU", "").replace("NONE", "")
    # Strip redundant spaces
    cpu = " ".join(cpu.split())
    return cpu

def clean_memory_info(mem: str) -> str:
    if not mem:
        return "Unknown RAM"
    match = re.match(r"^([0-9.]+)([a-zA-Z]+)(.*)", mem.strip())
    if not match:
        return mem
    try:
        val = float(match.group(1))
        unit = match.group(2)
        rest = match.group(3).strip()
        
        std_unit = "Gi"
        if "M" in unit.upper():
            std_unit = "Mi"
            
        if std_unit == "Gi":
            standards = [1, 2, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 256, 512, 1024]
            closest = min(standards, key=lambda x: abs(x - val))
            rounded_str = f"{closest}Gi"
        else:
            standards = [256, 512, 768]
            closest = min(standards, key=lambda x: abs(x - val))
            rounded_str = f"{closest}Mi"
            
        if rest:
            return f"{rounded_str} {rest}"
        return rounded_str
    except Exception:
        return mem

# Play names, as written in playbooks/*.yml, mapped to the progress percentage
# and translation key to show while that play runs. This is an undocumented
# contract with the playbooks: renaming a play's `name:` silently stops its
# progress step from ever firing, with no test failure and no log line.
BOOTSTRAP_TASKS = {
    "Verify OS type and version compatibility": (10, "verifying_os"),
    "Ensure python3/pip are installed": (25, "installing_python"),
    "Install dependencies": (50, "installing_deps"),
    "Create borg system user": (65, "creating_user"),
    "Generate SSH key for borg user": (70, "generating_ssh"),
    "Authorize orchestrator SSH public key on edge node": (75, "authorizing_keys"),
    "Configure SSH to allow root login with keys": (80, "configuring_ssh"),
    "Gather partition and system details": (90, "gathering_system"),
    "Restore proxy configurations and clean up orchestrator proxy": (95, "cleaning_up"),
}

PREPARE_TASKS = {
    "Backup remote fstab": (10, "backup_fstab"),
    "Gather partition and system details": (20, "gather_details"),
    "Label root filesystem": (50, "labeling_fs"),
    "Rewrite target /etc/fstab with 5-line template": (70, "writing_fstab"),
    "Verify mount configuration live": (85, "verifying_mount"),
    "Update GRUB bootloader configuration": (90, "updating_grub"),
}

MONITORING_TASKS = {
    "Ensure the telemetry buffer directory exists": (10, "buffer_dir"),
    "Install the collector script": (30, "collector_script"),
    "Install the systemd service unit": (40, "service_unit"),
    "Install the systemd timer unit": (50, "timer_unit"),
    "Expose SATA drive temperature via the drivetemp module": (60, "drivetemp"),
    "Enable and start the collection timer": (75, "enable_timer"),
    "Restart the timer if the collector or its units changed": (80, "restart_timer"),
    "Take one sample immediately": (85, "immediate_sample"),
    "Report what the node can actually measure": (90, "capability_report"),
    "Show the capability report": (95, "show_capability"),
}


PROGRESS_TASKS = {
    "bootstrap": BOOTSTRAP_TASKS,
    "prepare": PREPARE_TASKS,
    "monitoring": MONITORING_TASKS,
}

# TODO(phase-2): these belong in frontend/src/i18n/translations.ts, keyed by
# the trans_key above and resolved at render time. Keeping them here means the
# language is baked into the stored log at write time, so switching languages
# does not re-render past logs, and it costs a Settings read per playbook run.
PROGRESS_TRANSLATIONS = {
    "bootstrap": {
        "en": {
            "verifying_os": "Connecting to node via SSH & verifying OS compatibility (please wait)...",
            "installing_python": "Ensuring Python3/Pip are installed (Downloading/Installing - this may take several minutes)...",
            "installing_deps": "Installing system dependencies (parted, borgbackup, udev... this may take a moment)...",
            "creating_user": "Creating borg system user...",
            "generating_ssh": "Generating SSH keys for borg...",
            "authorizing_keys": "Authorizing orchestrator SSH key...",
            "configuring_ssh": "Configuring SSH server settings...",
            "gathering_system": "Gathering partition and system details...",
            "cleaning_up": "Restoring proxy configurations & cleaning up...",
            "complete": "Bootstrap completed successfully!"
        },
        "ru": {
            "verifying_os": "Подключение к узлу по SSH и проверка совместимости ОС (пожалуйста, подождите)...",
            "installing_python": "Установка Python3/Pip (скачивание и установка пакетов, может занять несколько минут)...",
            "installing_deps": "Установка системных зависимостей (parted, borgbackup, udev... это может занять некоторое время)...",
            "creating_user": "Создание системного пользователя borg...",
            "generating_ssh": "Генерация SSH-ключей для borg...",
            "authorizing_keys": "Авторизация SSH-ключа оркестратора...",
            "configuring_ssh": "Настройка SSH-сервера...",
            "gathering_system": "Сбор сведений о разделах и системе...",
            "cleaning_up": "Восстановление настроек прокси и очистка...",
            "complete": "Начальная настройка успешно завершена!"
        },
        "uk": {
            "verifying_os": "Підключення до вузла по SSH та перевірка сумісності ОС (будь ласка, зачекайте)...",
            "installing_python": "Встановлення Python3/Pip (завантаження та встановлення пакетів, може зайняти кілька хвилин)...",
            "installing_deps": "Встановлення системних залежностей (parted, borgbackup, udev... це може зайняти деякий час)...",
            "creating_user": "Створення системного користувача borg...",
            "generating_ssh": "Генерація SSH-ключів для borg...",
            "authorizing_keys": "Авторизація SSH-ключа оркестратора...",
            "configuring_ssh": "Налаштування SSH-сервера...",
            "gathering_system": "Збір відомостей про розділи та систему...",
            "cleaning_up": "Відновлення налаштувань проксі та очищення...",
            "complete": "Початкове налаштування успішно завершено!"
        }
    },
    "prepare": {
        "en": {
            "backup_fstab": "Backing up fstab...",
            "gather_details": "Gathering partition and system details...",
            "labeling_fs": "Labeling filesystems (root, boot, log, storage)...",
            "writing_fstab": "Writing standardized fstab configuration...",
            "verifying_mount": "Verifying new mount configuration...",
            "updating_grub": "Updating GRUB bootloader and initramfs...",
            "complete": "Auto-prepare completed successfully!"
        },
        "ru": {
            "backup_fstab": "Резервное копирование fstab...",
            "gather_details": "Сбор сведений о разделах и системе...",
            "labeling_fs": "Маркировка файловых систем...",
            "writing_fstab": "Запись стандартизированной конфигурации fstab...",
            "verifying_mount": "Проверка новой конфигурации монтирования...",
            "updating_grub": "Обновление загрузчика GRUB и initramfs...",
            "complete": "Автоподготовка успешно завершена!"
        },
        "uk": {
            "backup_fstab": "Резервне копіювання fstab...",
            "gather_details": "Збір відомостей про розділи та систему...",
            "labeling_fs": "Маркування файлових систем...",
            "writing_fstab": "Запис стандартизованої конфігурації fstab...",
            "verifying_mount": "Перевірка нової конфігурації монтування...",
            "updating_grub": "Оновлення завантажувача GRUB та initramfs...",
            "complete": "Автопідготовка успішно завершена!"
        }
    },
    "monitoring": {
        "en": {
            "buffer_dir": "Ensuring telemetry buffer directory...",
            "collector_script": "Installing telemetry collector script...",
            "service_unit": "Installing systemd service unit...",
            "timer_unit": "Installing systemd timer unit...",
            "drivetemp": "Configuring drivetemp kernel module for SSD monitoring...",
            "enable_timer": "Enabling telemetry collection timer...",
            "restart_timer": "Restarting telemetry collection timer...",
            "immediate_sample": "Taking initial telemetry sample...",
            "capability_report": "Checking hardware sensors & capability report...",
            "show_capability": "Displaying telemetry capability report...",
            "complete": "Telemetry collector installed successfully!"
        },
        "ru": {
            "buffer_dir": "Проверка директории буфера телеметрии...",
            "collector_script": "Установка скрипта сборщика телеметрии...",
            "service_unit": "Установка службы systemd...",
            "timer_unit": "Установка таймера systemd...",
            "drivetemp": "Настройка модуля ядра drivetemp для мониторинга SSD...",
            "enable_timer": "Включение таймера сбора телеметрии...",
            "restart_timer": "Перезапуск таймера сбора телеметрии...",
            "immediate_sample": "Снятие первого образца телеметрии...",
            "capability_report": "Проверка аппаратных датчиков и возможностей...",
            "show_capability": "Отображение отчета о возможностях телеметрии...",
            "complete": "Сборщик телеметрии успешно установлен!"
        },
        "uk": {
            "buffer_dir": "Перевірка директорії буфера телеметрії...",
            "collector_script": "Встановлення скрипта збирача телеметрії...",
            "service_unit": "Встановлення служби systemd...",
            "timer_unit": "Встановлення таймера systemd...",
            "drivetemp": "Налаштування модуля ядра drivetemp для моніторингу SSD...",
            "enable_timer": "Увімкнення таймера збору телеметрії...",
            "restart_timer": "Перезапуск таймера збору телеметрії...",
            "immediate_sample": "Зняття першого зразка телеметрії...",
            "capability_report": "Перевірка апаратних датчиків та можливостей...",
            "show_capability": "Відображення звіту про можливості телеметрії...",
            "complete": "Збирач телеметрії успішно встановлено!"
        }
    }
}


def playbook_kind(playbook_name: str) -> Optional[str]:
    """Which progress table, if any, applies to this playbook.

    Matched on the filename rather than declared per call, because the same
    three playbooks are launched from several places and none of them should
    have to remember to pass a kind.
    """
    for kind in ("bootstrap", "prepare", "monitoring"):
        if kind in playbook_name:
            return kind
    return None


def _load_log_prefix_and_language(task_id: str) -> Tuple[str, str]:
    """What this run needs from the database before it starts — and nothing more.

    A single task_id can drive more than one playbook run in sequence
    (bootstrap.yml, then deploy_monitoring.yml). `log_accumulator` only holds
    what *this* run has printed, so every write is layered on top of whatever
    this task_id already logged — never a replacement, or the earlier
    playbook's output would vanish the moment this one starts writing.
    """
    log_prefix = ""
    lang = "en"
    try:
        with session_scope() as db:
            existing_log = db.query(TaskLog).filter(TaskLog.id == task_id).first()
            if existing_log and existing_log.log_output:
                log_prefix = existing_log.log_output
            settings = db.query(Settings).first()
            if settings and settings.language in ("en", "ru", "uk"):
                lang = settings.language
    except Exception as e:
        logger.warning("Could not read task log prefix or language for %s: %s", task_id, e)
    return log_prefix, lang


def _write_task_log(task_id: str, log_output: str, status: str) -> None:
    """Persist the log so far. One short session per write, deliberately.

    The playbook runs for minutes; holding a connection open across it is the
    failure core.db_session exists to prevent. Writes are frequent but cheap —
    the connection comes straight back out of the pool.
    """
    with session_scope() as db:
        db.query(TaskLog).filter(TaskLog.id == task_id).update({
            "log_output": log_output,
            "status": status,
        })


def run_ansible_playbook(
    task_id: str,
    playbook_name: str,
    host_ip: str,
    ssh_port: int,
    extra_vars: Dict[str, Any],
    ssh_password: Optional[str] = None,
    ssh_key_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Executes an Ansible playbook via subprocess.Popen, streams the stdout logs
    line-by-line to the Database (TaskLog table), and parses outputs.

    Args:
        task_id: The UUID of the TaskLog tracking this task.
        playbook_name: The filename of the playbook to execute (e.g. bootstrap.yml).
        host_ip: IP address of the target edge node.
        ssh_port: SSH port of the target edge node.
        extra_vars: Dictionary of variables to pass into the playbook.
        ssh_password: Temporary password for password-based authentication.
        ssh_key_path: Path to the private SSH key for passwordless authentication.

    Returns:
        A dictionary containing the return code, parsed outputs, and status.
    """
    log_prefix, lang = _load_log_prefix_and_language(task_id)
    kind = playbook_kind(playbook_name)
    progress_tasks = PROGRESS_TASKS.get(kind, {})
    translations = PROGRESS_TRANSLATIONS.get(kind, {}).get(lang, {})

    inv_path = None
    try:
        # Resolve playbook path
        base_dir = os.path.dirname(os.path.abspath(__file__))
        playbook_path = os.path.join(base_dir, "playbooks", playbook_name)

        # Create temporary inventory
        user = extra_vars.get('bootstrap_user', 'root') if ssh_password else 'root'
        inv_vars = [f"ansible_host={host_ip}", f"ansible_port={ssh_port}", f"ansible_user={user}"]
        if ssh_password:
            inv_vars.extend([
                f"ansible_password={ssh_password}",
                "ansible_become=yes",
                "ansible_become_method=sudo",
                f"ansible_become_password={ssh_password}"
            ])
        if ssh_key_path and os.path.exists(ssh_key_path):
            inv_vars.append(f"ansible_ssh_private_key_file={ssh_key_path}")

        inventory_content = f"{host_ip} " + " ".join(inv_vars)

        # Write temporary files for safety
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as inv_file:
            inv_file.write(inventory_content)
            inv_path = inv_file.name

        # Construct ansible-playbook command
        cmd = [
            "ansible-playbook",
            "-i", inv_path,
            playbook_path
        ]

        # Add extra variables
        if extra_vars:
            filtered_vars = {k: v for k, v in extra_vars.items() if k != 'bootstrap_user'}
            if filtered_vars:
                import json
                cmd.extend(["--extra-vars", json.dumps(filtered_vars)])

        # Execute playbook and stream stdout
        env = os.environ.copy()
        # Prevent SSH strict host checking prompts
        env["ANSIBLE_HOST_KEY_CHECKING"] = "False"

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env
        )

        parsed_data: Dict[str, Any] = {}
        log_accumulator = []

        # Read line by line and update DB TaskLog
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line:
                log_accumulator.append(line)

                # Check for progress updates
                percent = None
                desc = None
                if "TASK [" in line:
                    try:
                        task_title = line.split("TASK [")[1].split("]")[0]
                        for key, (pct, trans_key) in progress_tasks.items():
                            if key in task_title:
                                percent = pct
                                desc = translations.get(trans_key)
                                break
                    except Exception:
                        pass
                elif "PLAY RECAP" in line and kind:
                    percent = 100
                    desc = translations.get("complete")

                if percent is not None and desc is not None:
                    log_accumulator.append(f"[PROGRESS] {percent}:{desc}\n")
                # Parse custom output lines
                if "NODE_AUTHKEY:" in line:
                    entry_line = line.split("NODE_AUTHKEY:", 1)[1].strip().strip('"').rstrip(",")
                    parsed_data.setdefault("node_authorized_keys", []).append(entry_line)
                    continue
                if "SSH_KEY:" in line:
                    raw_key = line.split("SSH_KEY:")[1].strip().strip('"').rstrip(",").strip()
                    parsed_key = ssh_keys.parse_line(raw_key)
                    if parsed_key is not None:
                        # Store the key without its comment; we set our own tag
                        # when writing it into authorized_keys.
                        parsed_data["ssh_pub_key"] = f"{parsed_key.keytype} {parsed_key.blob}"
                    else:
                        logger.warning("Unparseable SSH_KEY line from node: %r", raw_key)
                if "DISK_TYPE:" in line:
                    parsed_data["disk_type"] = line.split("DISK_TYPE:")[1].strip().replace('"', '').replace(',', '').replace(')', '').replace('(', '')
                if "EFI_UUID:" in line:
                    parsed_data["efi_uuid"] = line.split("EFI_UUID:")[1].strip().replace('"', '').replace(',', '').replace(')', '').replace('(', '')
                if "INTERFACE:" in line:
                    parsed_data["network_iface"] = line.split("INTERFACE:")[1].strip().replace('"', '').replace(',', '').replace(')', '').replace('(', '')
                if "PREPARED:" in line:
                    parsed_data["prepared"] = line.split("PREPARED:")[1].strip().replace('"', '').replace(',', '').replace(')', '').replace('(', '')
                if "HOSTNAME:" in line:
                    parsed_data["hostname"] = line.split("HOSTNAME:")[1].strip().replace('"', '').replace(',', '').replace(')', '').replace('(', '')
                if "OS_VERSION:" in line:
                    parsed_data["os_version"] = line.split("OS_VERSION:")[1].strip().replace('"', '').replace(',', '').replace(')', '').replace('(', '')
                if "CPU_INFO:" in line:
                    cpu_raw = line.split("CPU_INFO:")[1].strip().replace('"', '').replace(',', '').replace(')', '').replace('(', '')
                    parsed_data["cpu_info"] = clean_cpu_info(cpu_raw)
                if "MEM_INFO:" in line:
                    mem_raw = line.split("MEM_INFO:")[1].strip().replace('"', '').replace(',', '').replace(')', '').replace('(', '')
                    parsed_data["memory_info"] = clean_memory_info(mem_raw)
                if "EDGE_VERSION:" in line:
                    parsed_data["edge_version"] = line.split("EDGE_VERSION:")[1].strip().replace('"', '').replace(',', '').replace(')', '').replace('(', '')
                if "HASP_VERSION:" in line:
                    parsed_data["hasp_runtime_version"] = line.split("HASP_VERSION:")[1].strip().replace('"', '').replace(',', '').replace(')', '').replace('(', '')
                if "PARTITION_LAYOUT_JSON:" in line:
                    parsed_data["partition_layout_raw"] = line.split("PARTITION_LAYOUT_JSON:")[1].strip()

                # Periodic write to DB so the console has something to show
                # while the playbook runs. Every write is its own short
                # session — see _write_task_log.
                if len(log_accumulator) % 5 == 0:
                    _write_task_log(task_id, log_prefix + "".join(log_accumulator), "RUNNING")

        return_code = process.wait()

        if "partition_layout_raw" in parsed_data:
            layout = _parse_partition_layout(parsed_data["partition_layout_raw"])
            if layout:
                parsed_data["partition_layout"] = layout

        status = "SUCCESS" if return_code == 0 else "FAILED"
        _finalise_task_log(task_id, log_prefix, "".join(log_accumulator), status)

        return {
            "return_code": return_code,
            "status": status,
            "parsed_data": parsed_data
        }

    except Exception as e:
        error_msg = f"Exception occurred during execution: {str(e)}"
        try:
            _append_task_log(task_id, error_msg, "FAILED")
        except Exception:
            logger.exception("Could not record playbook failure for task %s", task_id)
        return {
            "return_code": -1,
            "status": "FAILED",
            "error": error_msg,
            "parsed_data": {}
        }
    finally:
        # In a finally now: a playbook that raised used to leave its inventory
        # file — which contains the bootstrap password — behind in /tmp.
        if inv_path:
            try:
                os.remove(inv_path)
            except OSError:
                pass


def _parse_partition_layout(raw_json: str):
    """Reduce the node's lsblk dump to the partitions on its root disk.

    Everything else — USB sticks, the installer medium, a second data disk —
    is deliberately dropped: this layout is what a bare-metal restore
    reconstructs, and restoring onto anything but the root disk would be a
    mistake rather than a feature.
    """
    try:
        import json
        raw_json = raw_json.strip()

        # Extract the JSON block between the first '{' and the last '}'
        start_idx = raw_json.find('{')
        end_idx = raw_json.rfind('}')
        if start_idx != -1 and end_idx != -1:
            raw_json = raw_json[start_idx:end_idx + 1]

        # Replace escaped quotes back to normal quotes
        raw_json = raw_json.replace('\\"', '"')

        lsblk_data = json.loads(raw_json)
        devices = lsblk_data.get("blockdevices", [])

        all_parts = []

        def traverse_devices(devs):
            for dev in devs:
                mount = dev.get("mountpoint") or ""
                if dev.get("type", "") == "part" and mount:
                    all_parts.append(dev)
                children = dev.get("children", [])
                if children:
                    traverse_devices(children)

        traverse_devices(devices)

        root_part = next((p for p in all_parts if p.get("mountpoint") == "/"), None)
        if not root_part:
            return None

        # nvme0n1p2 -> nvme0n1, sda2 -> sda. The two naming schemes need
        # different patterns; matching letters alone would truncate an NVMe
        # name to "nvme".
        root_part_name = root_part.get("name", "")
        if "nvme" in root_part_name:
            match = re.match(r'(nvme\d+n\d+)', root_part_name)
        else:
            match = re.match(r'([a-zA-Z]+)', root_part_name)
        if not match:
            return None
        root_disk_name = match.group(1)

        filtered_layout = [
            {
                "name": p.get("name", ""),
                "mount": p.get("mountpoint"),
                "fstype": p.get("fstype", "ext4"),
                "label": p.get("label"),
                "uuid": p.get("uuid"),
                "partuuid": p.get("partuuid"),
                "size_bytes": int(p.get("size", 0)),
            }
            for p in all_parts
            if root_disk_name in p.get("name", "")
        ]

        def get_partition_index(part_dict):
            match = re.search(r'p?(\d+)$', part_dict["name"])
            return int(match.group(1)) if match else 99

        filtered_layout.sort(key=get_partition_index)
        return filtered_layout or None
    except Exception:
        logger.exception("Error parsing partition layout")
        return None


def _finalise_task_log(task_id: str, log_prefix: str, accumulated_text: str, status: str) -> None:
    """Write the run's complete output, without duplicating what is already there.

    The periodic writes during the run may already have persisted everything,
    so this appends only if the stored text does not already end with it.
    """
    with session_scope() as db:
        current_log_obj = db.query(TaskLog).filter(TaskLog.id == task_id).first()
        current_text = current_log_obj.log_output if current_log_obj and current_log_obj.log_output else log_prefix
        if accumulated_text and not current_text.endswith(accumulated_text):
            if current_text and not current_text.endswith("\n"):
                current_text += "\n"
            final_log = current_text + accumulated_text
        else:
            final_log = current_text

        db.query(TaskLog).filter(TaskLog.id == task_id).update({
            "log_output": final_log,
            "status": status,
        })


def _append_task_log(task_id: str, message: str, status: str) -> None:
    """Append one message to a task's log, preserving what is already stored."""
    with session_scope() as db:
        prior_log = db.query(TaskLog).filter(TaskLog.id == task_id).first()
        prior_text = prior_log.log_output if prior_log and prior_log.log_output else ""
        db.query(TaskLog).filter(TaskLog.id == task_id).update({
            "log_output": prior_text + message,
            "status": status,
        })
