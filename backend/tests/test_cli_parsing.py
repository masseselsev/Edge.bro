import re
import pytest

def parse_wifi_line(line: str):
    # This is the exact parsing logic used in backend/routers/network.py
    parts = re.split(r"(?<!\\):", line)
    if len(parts) >= 4:
        ssid = parts[0].replace("\\:", ":").strip()
        signal = int(parts[1]) if parts[1].isdigit() else 0
        security = parts[2].strip()
        active = parts[3].strip().lower() == "yes"
        return {
            "ssid": ssid,
            "signal": signal,
            "security": security if security else "Open",
            "active": active
        }
    return None

def test_parse_wifi_line_normal():
    line = "MyNetwork:80:WPA2:no"
    parsed = parse_wifi_line(line)
    assert parsed is not None
    assert parsed["ssid"] == "MyNetwork"
    assert parsed["signal"] == 80
    assert parsed["security"] == "WPA2"
    assert parsed["active"] is False

def test_parse_wifi_line_escaped_colon():
    # SSID is "Office:5G" represented in terse format as "Office\:5G"
    line = "Office\\:5G:95:WPA2:no"
    parsed = parse_wifi_line(line)
    assert parsed is not None
    assert parsed["ssid"] == "Office:5G"
    assert parsed["signal"] == 95
    assert parsed["security"] == "WPA2"
    assert parsed["active"] is False

def test_parse_wifi_line_multiple_escaped_colons():
    line = "A\\:B\\:C:50:WPA1:yes"
    parsed = parse_wifi_line(line)
    assert parsed is not None
    assert parsed["ssid"] == "A:B:C"
    assert parsed["signal"] == 50
    assert parsed["security"] == "WPA1"
    assert parsed["active"] is True

def test_parse_wifi_line_invalid_format():
    line = "short_line"
    assert parse_wifi_line(line) is None
