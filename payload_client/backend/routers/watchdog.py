import serial
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any

router = APIRouter(prefix="/kiosk/watchdog", tags=["Watchdog"])

class WatchdogStatus(BaseModel):
    detected: bool
    port: str | None = None
    seconds_left: int | None = None
    frozen: bool = False

def calculate_crc(data: bytes) -> bytes:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])

def scan_watchdog() -> Dict[str, Any]:
    ports_to_scan = ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyUSB2"]
    # pc_wdt read command: 30 03 00 00 00 01 + CRC (80 2B)
    read_cmd = bytes.fromhex("300300000001802B")
    
    # read coils starting at 0x0000 to get REG_VSM_FROZEN_REQ state
    read_coils_cmd = bytes.fromhex("3001000000083E0C")

    for port in ports_to_scan:
        try:
            ser = serial.Serial(port, 19200, timeout=0.15)
            ser.write(read_cmd)
            res = ser.read(7)
            if len(res) == 7 and res[0] == 0x30 and res[1] == 0x03 and res[2] == 0x02:
                # Validate CRC
                expected_crc = calculate_crc(res[:-2])
                if res[-2:] == expected_crc:
                    seconds = (res[3] << 8) | res[4]
                    
                    # Now check if frozen
                    frozen = False
                    ser.write(read_coils_cmd)
                    c_res = ser.read(6)
                    if len(c_res) == 6 and c_res[0] == 0x30 and c_res[1] == 0x01:
                        expected_c_crc = calculate_crc(c_res[:-2])
                        if c_res[-2:] == expected_c_crc:
                            # Bit 7 is Frozen Request
                            frozen = bool((c_res[3] >> 7) & 1)

                    ser.close()
                    return {
                        "detected": True,
                        "port": port,
                        "seconds_left": seconds,
                        "frozen": frozen
                    }
            ser.close()
        except Exception:
            pass
    return {"detected": False, "port": None, "seconds_left": None, "frozen": False}

@router.get("/status", response_model=WatchdogStatus)
def get_watchdog_status():
    try:
        status = scan_watchdog()
        return WatchdogStatus(**status)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/freeze")
def freeze_watchdog():
    status = scan_watchdog()
    if not status["detected"]:
        raise HTTPException(status_code=404, detail="Watchdog controller not found")
    
    port = status["port"]
    # Commands
    freeze_cmd = bytes.fromhex("30050007FF0039DA")
    reset_cmd = bytes.fromhex("3006000000008DEB")
    
    try:
        ser = serial.Serial(port, 19200, timeout=0.5)
        
        # Send freeze command
        ser.write(freeze_cmd)
        f_res = ser.read(8)
        if f_res != freeze_cmd:
            ser.close()
            raise Exception("Watchdog failed to confirm freeze request")
            
        # Send reset command
        ser.write(reset_cmd)
        r_res = ser.read(8)
        ser.close()
        return {"status": "SUCCESS", "message": "Watchdog frozen successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/unfreeze")
def unfreeze_watchdog():
    status = scan_watchdog()
    if not status["detected"]:
        raise HTTPException(status_code=404, detail="Watchdog controller not found")
    
    port = status["port"]
    unfreeze_cmd = bytes.fromhex("300500070000782A")
    
    try:
        ser = serial.Serial(port, 19200, timeout=0.5)
        ser.write(unfreeze_cmd)
        res = ser.read(8)
        ser.close()
        if res != unfreeze_cmd:
            raise Exception("Watchdog failed to confirm unfreeze request")
        return {"status": "SUCCESS", "message": "Watchdog unfrozen successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
