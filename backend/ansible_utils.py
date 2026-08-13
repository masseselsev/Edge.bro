import os
import subprocess
import tempfile
import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from database import SessionLocal
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
    db: Session = SessionLocal()
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

        # A single task_id can drive more than one playbook run in sequence
        # (bootstrap.yml, then deploy_monitoring.yml). log_accumulator only
        # holds what *this* run has printed so far, so every write below is
        # layered on top of whatever this task_id already logged — never a
        # replacement, or the earlier playbook's output would vanish the
        # moment this one starts writing.
        existing_log = db.query(TaskLog).filter(TaskLog.id == task_id).first()
        log_prefix = existing_log.log_output if existing_log and existing_log.log_output else ""

        is_bootstrap = "bootstrap" in playbook_name
        is_prepare = "prepare" in playbook_name
        is_monitoring = "monitoring" in playbook_name
        
        lang = "en"
        try:
            settings = db.query(Settings).first()
            if settings and settings.language in ("en", "ru", "uk"):
                lang = settings.language
        except Exception:
            pass

        BOOTSTRAP_TASKS = {
            "Verify OS type and version compatibility": (10, "verifying_os"),
            "Ensure python3/pip are installed": (25, "installing_python"),
            "Install dependencies": (50, "installing_deps"),
            "Create borg system user": (65, "creating_user"),
            "Generate SSH key for borg user": (70, "generating_ssh"),
            "Authorize orchestrator SSH public key on edge node": (75, "authorizing_keys"),
            "Configure SSH to allow root login with keys": (80, "configuring_ssh"),
            "Gather partition and system details": (90, "gathering_system"),
            "Restore proxy configurations and clean up orchestrator proxy": (95, "cleaning_up")
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
            "Show the capability report": (95, "show_capability")
        }

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
                    "service_unit": "Встановлення службы systemd...",
                    "timer_unit": "Встановлення таймера systemd...",
                    "drivetemp": "Налаштування модуля ядра drivetemp для моніторингу SSD...",
                    "enable_timer": "Увімкнення таймера збору телеметрії...",
                    "restart_timer": "Перезапуск таймера збору телеметрії...",
                    "immediate_sample": "Зняття першого зразка телеметрії...",
                    "capability_report": "Перевірка апаратних датчиків та возможностей...",
                    "show_capability": "Відображення звіту про можливості телеметрії...",
                    "complete": "Збирач телеметрії успішно встановлено!"
                }
            }
        }

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
                        if is_bootstrap:
                            for key, (pct, trans_key) in BOOTSTRAP_TASKS.items():
                                if key in task_title:
                                    percent = pct
                                    desc = PROGRESS_TRANSLATIONS["bootstrap"][lang].get(trans_key)
                                    break
                        elif is_prepare:
                            for key, (pct, trans_key) in PREPARE_TASKS.items():
                                if key in task_title:
                                    percent = pct
                                    desc = PROGRESS_TRANSLATIONS["prepare"][lang].get(trans_key)
                                    break
                        elif is_monitoring:
                            for key, (pct, trans_key) in MONITORING_TASKS.items():
                                if key in task_title:
                                    percent = pct
                                    desc = PROGRESS_TRANSLATIONS["monitoring"][lang].get(trans_key)
                                    break
                    except Exception:
                        pass
                elif "PLAY RECAP" in line:
                    percent = 100
                    p_type = "bootstrap" if is_bootstrap else ("prepare" if is_prepare else ("monitoring" if is_monitoring else None))
                    if p_type:
                        desc = PROGRESS_TRANSLATIONS[p_type][lang].get("complete")

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

                # Periodic write to DB to avoid overloading database connections
                if len(log_accumulator) % 5 == 0:
                    current_log = log_prefix + "".join(log_accumulator)
                    db.query(TaskLog).filter(TaskLog.id == task_id).update({
                        "log_output": current_log,
                        "status": "RUNNING"
                    })
                    db.commit()

        # Final write to DB
        return_code = process.wait()

        # Transform partition_layout if present
        if "partition_layout_raw" in parsed_data:
            try:
                import json
                import re
                raw_json = parsed_data["partition_layout_raw"].strip()
                
                # Extract the JSON block between the first '{' and the last '}'
                start_idx = raw_json.find('{')
                end_idx = raw_json.rfind('}')
                if start_idx != -1 and end_idx != -1:
                    raw_json = raw_json[start_idx:end_idx+1]
                
                # Replace escaped quotes back to normal quotes
                raw_json = raw_json.replace('\\"', '"')
                
                lsblk_data = json.loads(raw_json)
                devices = lsblk_data.get("blockdevices", [])
                
                root_disk_name = None
                all_parts = []
                
                def traverse_devices(devs):
                    for dev in devs:
                        dev_type = dev.get("type", "")
                        mount = dev.get("mountpoint") or ""
                        name = dev.get("name", "")
                        
                        if dev_type == "part" and mount:
                            all_parts.append(dev)
                        
                        children = dev.get("children", [])
                        if children:
                            traverse_devices(children)
                
                traverse_devices(devices)
                
                root_part = None
                for p in all_parts:
                    if p.get("mountpoint") == "/":
                        root_part = p
                        break
                
                if root_part:
                    root_part_name = root_part.get("name", "")
                    if "nvme" in root_part_name:
                        match = re.match(r'(nvme\d+n\d+)', root_part_name)
                        if match:
                            root_disk_name = match.group(1)
                    else:
                        match = re.match(r'([a-zA-Z]+)', root_part_name)
                        if match:
                            root_disk_name = match.group(1)
                
                filtered_layout = []
                if root_disk_name:
                    for p in all_parts:
                        p_name = p.get("name", "")
                        if root_disk_name in p_name:
                            filtered_layout.append({
                                "name": p_name,
                                "mount": p.get("mountpoint"),
                                "fstype": p.get("fstype", "ext4"),
                                "label": p.get("label"),
                                "uuid": p.get("uuid"),
                                "partuuid": p.get("partuuid"),
                                "size_bytes": int(p.get("size", 0))
                            })
                
                def get_partition_index(part_dict):
                    name = part_dict["name"]
                    match = re.search(r'p?(\d+)$', name)
                    return int(match.group(1)) if match else 99
                
                filtered_layout.sort(key=get_partition_index)
                
                if filtered_layout:
                    parsed_data["partition_layout"] = filtered_layout
            except Exception as e:
                import traceback
                print(f"Error parsing partition layout: {str(e)}")
                traceback.print_exc()

        current_log_obj = db.query(TaskLog).filter(TaskLog.id == task_id).first()
        current_text = current_log_obj.log_output if current_log_obj and current_log_obj.log_output else log_prefix
        accumulated_text = "".join(log_accumulator)
        if accumulated_text and not current_text.endswith(accumulated_text):
            if current_text and not current_text.endswith("\n"):
                current_text += "\n"
            final_log = current_text + accumulated_text
        else:
            final_log = current_text

        status = "SUCCESS" if return_code == 0 else "FAILED"

        db.query(TaskLog).filter(TaskLog.id == task_id).update({
            "log_output": final_log,
            "status": status
        })
        db.commit()

        # Cleanup temporary files
        try:
            os.remove(inv_path)
        except OSError:
            pass

        return {
            "return_code": return_code,
            "status": status,
            "parsed_data": parsed_data
        }

    except Exception as e:
        error_msg = f"Exception occurred during execution: {str(e)}"
        prior_log = db.query(TaskLog).filter(TaskLog.id == task_id).first()
        prior_text = prior_log.log_output if prior_log and prior_log.log_output else ""
        db.query(TaskLog).filter(TaskLog.id == task_id).update({
            "log_output": prior_text + error_msg,
            "status": "FAILED"
        })
        db.commit()
        return {
            "return_code": -1,
            "status": "FAILED",
            "error": error_msg,
            "parsed_data": {}
        }
    finally:
        db.close()
