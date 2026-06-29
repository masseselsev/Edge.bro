from unittest.mock import patch, mock_open
import pytest
from routers.network import (
    get_vpn_status,
    save_vpn_config,
    connect_vpn,
    disconnect_vpn,
    delete_vpn,
    VpnConfigRequest
)

@patch("subprocess.run")
@patch("os.path.exists")
def test_vpn_status_not_configured(mock_exists, mock_run):
    mock_exists.return_value = False
    response = get_vpn_status()
    assert response is None

@patch("subprocess.run")
@patch("os.path.exists")
@patch("builtins.open", new_callable=mock_open, read_data="[Interface]\nPrivateKey = privkey\nAddress = 10.0.0.5/24\n")
def test_vpn_status_configured_disconnected(mock_file, mock_exists, mock_run):
    def exists_side_effect(path):
        if path == "/media/usb-data/wg0.conf" or path == "/etc/wireguard/wg0.conf":
            return True
        return False
    mock_exists.side_effect = exists_side_effect
    
    # Mock wg show wg0 dump output raising exception (disconnected/interface down)
    import subprocess
    mock_run.return_value.returncode = 1
    mock_run.return_value.stderr = "Device wg0 does not exist"
    
    data = get_vpn_status()
    assert data is not None
    assert data.connected is False
    assert data.ip == "10.0.0.5"

@patch("subprocess.run")
@patch("os.path.exists")
@patch("builtins.open", new_callable=mock_open, read_data="[Interface]\nPrivateKey = privkey\nAddress = 10.0.0.5/24\n")
def test_vpn_status_configured_connected(mock_file, mock_exists, mock_run):
    def exists_side_effect(path):
        if path == "/media/usb-data/wg0.conf" or path == "/etc/wireguard/wg0.conf":
            return True
        return False
    mock_exists.side_effect = exists_side_effect
    
    # Mock wg show wg0 dump output for connected state
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = (
        "privatekey\tpublickey\t51820\toff\n"
        "peerpublickey\t(none)\t192.168.1.100:51820\t0.0.0.0/0\t1782768390\t149422080\t19293798\toff\n"
    )
    
    data = get_vpn_status()
    assert data is not None
    assert data.connected is True
    assert data.ip == "10.0.0.5"
    assert data.endpoint == "192.168.1.100:51820"
    assert data.allowed_ips == "0.0.0.0/0"
    assert data.received_bytes == 149422080
    assert data.sent_bytes == 19293798

@patch("subprocess.run")
@patch("os.path.exists")
@patch("os.makedirs")
@patch("builtins.open", new_callable=mock_open)
def test_save_vpn_config(mock_file, mock_makedirs, mock_exists, mock_run):
    mock_exists.return_value = False
    mock_run.return_value.returncode = 0
    
    response = save_vpn_config(VpnConfigRequest(config_text="[Interface]\nAddress = 10.0.0.5/24\n"))
    assert response.status == "SUCCESS"
    
    # Verify wg-quick up was executed
    mock_run.assert_any_call(["wg-quick", "down", "wg0"], capture_output=True)
    mock_run.assert_any_call(["wg-quick", "up", "wg0"], capture_output=True, text=True)

@patch("subprocess.run")
@patch("os.path.exists")
def test_connect_disconnect_vpn(mock_exists, mock_run):
    mock_exists.return_value = True
    mock_run.return_value.returncode = 0
    
    res1 = connect_vpn()
    assert res1.status == "SUCCESS"
    mock_run.assert_any_call(["wg-quick", "up", "wg0"], capture_output=True, text=True)
    
    res2 = disconnect_vpn()
    assert res2.status == "SUCCESS"
    mock_run.assert_any_call(["wg-quick", "down", "wg0"], capture_output=True, text=True)

@patch("subprocess.run")
@patch("os.path.exists")
@patch("os.remove")
def test_delete_vpn(mock_remove, mock_exists, mock_run):
    mock_exists.return_value = True
    mock_run.return_value.returncode = 0
    
    response = delete_vpn()
    assert response.status == "SUCCESS"
    
    mock_run.assert_any_call(["wg-quick", "down", "wg0"], capture_output=True)
    assert mock_remove.call_count >= 1
