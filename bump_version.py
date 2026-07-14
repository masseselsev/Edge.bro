#!/usr/bin/env python3
import sys
import os
import re

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 bump_version.py <new_version>")
        print("Example: python3 bump_version.py 1.0.1")
        sys.exit(1)

    new_ver = sys.argv[1].strip()
    # Normalize version: remove leading 'v' for package.json, ensure it exists for python modules
    clean_ver = new_ver.lstrip('v')
    v_ver = f"v{clean_ver}"

    root_dir = os.path.dirname(os.path.abspath(__file__))

    # 1. Update backend/version.py
    version_py_path = os.path.join(root_dir, "backend", "version.py")
    if os.path.exists(version_py_path):
        with open(version_py_path, "r") as f:
            content = f.read()
        new_content = re.sub(r'VERSION\s*=\s*["\']v?[0-9.]+["\']', f'VERSION = "{v_ver}"', content)
        with open(version_py_path, "w") as f:
            f.write(new_content)
        print(f"Updated backend/version.py to VERSION = \"{v_ver}\"")
    else:
        print("Warning: backend/version.py not found")

    # 2. Update payload_client/backend/main.py fallback version
    payload_main_path = os.path.join(root_dir, "payload_client", "backend", "main.py")
    if os.path.exists(payload_main_path):
        with open(payload_main_path, "r") as f:
            content = f.read()
        new_content = re.sub(r'VERSION\s*=\s*["\']v?[0-9.]+["\']', f'VERSION = "{v_ver}"', content)
        with open(payload_main_path, "w") as f:
            f.write(new_content)
        print(f"Updated payload_client/backend/main.py to VERSION = \"{v_ver}\"")
    else:
        print("Warning: payload_client/backend/main.py not found")

    # 3. Update frontend/package.json
    pkg_json_path = os.path.join(root_dir, "frontend", "package.json")
    if os.path.exists(pkg_json_path):
        with open(pkg_json_path, "r") as f:
            content = f.read()
        new_content = re.sub(r'"version"\s*:\s*"[0-9.]+"', f'"version": "{clean_ver}"', content)
        with open(pkg_json_path, "w") as f:
            f.write(new_content)
        print(f"Updated frontend/package.json to version: \"{clean_ver}\"")
    else:
        print("Warning: frontend/package.json not found")

    # 4. Update frontend/package-lock.json
    pkg_lock_path = os.path.join(root_dir, "frontend", "package-lock.json")
    if os.path.exists(pkg_lock_path):
        with open(pkg_lock_path, "r") as f:
            content = f.read()
        # Update root package version
        new_content = re.sub(r'"version"\s*:\s*"[0-9.]+"', f'"version": "{clean_ver}"', content, count=2)
        with open(pkg_lock_path, "w") as f:
            f.write(new_content)
        print(f"Updated frontend/package-lock.json versions to \"{clean_ver}\"")
    else:
        print("Warning: frontend/package-lock.json not found")

    print(f"\nSuccessfully bumped version across the project to {v_ver} ({clean_ver})!")

if __name__ == "__main__":
    main()
