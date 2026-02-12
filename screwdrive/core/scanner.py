"""
USB Barcode Scanner reader module.

Reads input events from a USB barcode scanner that acts as a keyboard device.
Uses time-gap detection (GAP_SEC pause between keystrokes = end of scan)
instead of waiting for Enter key, matching real scanner behavior.

Uses EVIOCGRAB ioctl to grab exclusive access to the device, preventing
scanner keystrokes from being sent to the browser/system.

Based on proven barcode_reader_timeout.py approach.
"""

import os
import fcntl
import select
import struct
import threading
import time
from typing import Optional, List


# EVIOCGRAB ioctl - grabs exclusive access to the input device
EVIOCGRAB = 0x40044590

# Event types
EV_KEY = 0x01

# Key value: 1 = press, 0 = release
KEY_DOWN = 1

# Gap in seconds after which we consider scan complete
GAP_SEC = 0.12

# Shift keycodes
KEY_LEFTSHIFT = 42
KEY_RIGHTSHIFT = 54

# Keycode to character mapping (matches ecodes.KEY_*)
KEYCODE_MAP = {
    11: '0', 2: '1', 3: '2', 4: '3', 5: '4',
    6: '5', 7: '6', 8: '7', 9: '8', 10: '9',
    30: 'a', 48: 'b', 46: 'c', 32: 'd', 18: 'e',
    33: 'f', 34: 'g', 35: 'h', 23: 'i', 36: 'j',
    37: 'k', 38: 'l', 50: 'm', 49: 'n', 24: 'o',
    25: 'p', 16: 'q', 19: 'r', 31: 's', 20: 't',
    22: 'u', 47: 'v', 17: 'w', 45: 'x', 21: 'y',
    44: 'z',
    12: '-', 13: '=', 26: '[', 27: ']',
    43: '\\', 39: ';', 40: "'", 41: '`',
    51: ',', 52: '.', 53: '/', 57: ' ',
}

# Shift + key mapping for special characters
SHIFT_KEYMAP = {
    2: '!', 3: '@', 4: '#', 5: '$', 6: '%',
    7: '^', 8: '&', 9: '*', 10: '(', 11: ')',
    12: '_', 13: '+', 26: '{', 27: '}',
    43: '|', 39: ':', 40: '"', 41: '~',
    51: '<', 52: '>', 53: '?',
}


def _detect_event_size():
    """Detect the correct input_event struct size for this platform."""
    fmt64 = 'llHHi'
    fmt32 = 'iiHHi'
    if struct.calcsize('l') == 8:
        return fmt64, struct.calcsize(fmt64)
    else:
        return fmt32, struct.calcsize(fmt32)


EVENT_FORMAT, EVENT_SIZE = _detect_event_size()


class BarcodeScanner:
    """
    Background reader for USB barcode scanner input events.

    Uses time-gap detection: when there's a pause > GAP_SEC (120ms) between
    keystrokes, the accumulated buffer is treated as a complete barcode scan.
    This matches how real barcode scanners work — they send characters rapidly
    and then stop, without necessarily sending Enter.
    """

    def __init__(self, device_path: str):
        self._device_path = device_path
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Scanner state
        self._connected = False
        self._last_scan: Optional[str] = None
        self._last_scan_time: Optional[float] = None
        self._scan_history: List[dict] = []
        self._max_history = 20
        self._current_buffer: list = []
        self._error: Optional[str] = None
        self._scan_count = 0
        self._shift_down = False
        self._last_key_ts: Optional[float] = None

    @property
    def device_path(self) -> str:
        return self._device_path

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    @property
    def last_scan(self) -> Optional[str]:
        with self._lock:
            return self._last_scan

    @property
    def last_scan_time(self) -> Optional[float]:
        with self._lock:
            return self._last_scan_time

    @property
    def scan_count(self) -> int:
        with self._lock:
            return self._scan_count

    @property
    def error(self) -> Optional[str]:
        with self._lock:
            return self._error

    def get_status(self) -> dict:
        """Get scanner status as dict."""
        with self._lock:
            return {
                'device_path': self._device_path,
                'connected': self._connected,
                'last_scan': self._last_scan,
                'last_scan_time': self._last_scan_time,
                'scan_count': self._scan_count,
                'error': self._error,
                'history': list(self._scan_history),
            }

    def reset_scan_count(self):
        """Reset scan count and last scan data. Used before QR verification."""
        with self._lock:
            self._last_scan = None
            self._last_scan_time = None
            self._scan_count = 0
            self._scan_history.clear()

    def start(self) -> None:
        """Start the scanner reading thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        print(f"Barcode scanner started: {self._device_path}")
        print(f"  Event format: {EVENT_FORMAT}, size: {EVENT_SIZE} bytes, gap: {GAP_SEC}s")

    def stop(self) -> None:
        """Stop the scanner reading thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        with self._lock:
            self._connected = False
        print("Barcode scanner stopped")

    def _flush_buffer(self):
        """Flush accumulated buffer as a completed scan."""
        if not self._current_buffer:
            return
        scanned = ''.join(self._current_buffer).strip()
        self._current_buffer.clear()
        if not scanned:
            return

        now = time.time()
        with self._lock:
            self._last_scan = scanned
            self._last_scan_time = now
            self._scan_count += 1
            self._scan_history.append({
                'value': scanned,
                'time': now,
            })
            if len(self._scan_history) > self._max_history:
                self._scan_history = self._scan_history[-self._max_history:]

        print(f"Scanner read: {scanned}")

    def _read_loop(self) -> None:
        """Main reading loop with auto-reconnect and gap-based scan detection."""
        while self._running:
            fd = None
            try:
                if not os.path.exists(self._device_path):
                    with self._lock:
                        self._connected = False
                        self._error = 'Пристрій не знайдено'
                    time.sleep(2.0)
                    continue

                fd = os.open(self._device_path, os.O_RDONLY | os.O_NONBLOCK)

                # Grab exclusive access
                try:
                    fcntl.ioctl(fd, EVIOCGRAB, 1)
                    print("Scanner EVIOCGRAB: exclusive access granted")
                except OSError as e:
                    print(f"Scanner EVIOCGRAB failed (non-critical): {e}")

                with self._lock:
                    self._connected = True
                    self._error = None
                print(f"Scanner device opened: {self._device_path}")

                self._shift_down = False
                self._last_key_ts = None
                self._current_buffer.clear()

                while self._running:
                    # Use select with timeout to detect scan gaps
                    timeout = GAP_SEC if self._current_buffer else 0.5
                    ready, _, _ = select.select([fd], [], [], timeout)

                    if not ready:
                        # Timeout — if we have buffered chars, flush as complete scan
                        if self._current_buffer and self._last_key_ts is not None:
                            now = time.monotonic()
                            if (now - self._last_key_ts) >= GAP_SEC:
                                self._flush_buffer()
                                self._last_key_ts = None
                        continue

                    # Read available events
                    try:
                        data = os.read(fd, EVENT_SIZE * 64)
                    except BlockingIOError:
                        continue
                    except OSError:
                        break

                    if not data:
                        break

                    # Process all events in the read buffer
                    offset = 0
                    while offset + EVENT_SIZE <= len(data):
                        fields = struct.unpack_from(EVENT_FORMAT, data, offset)
                        offset += EVENT_SIZE
                        ev_type = fields[2]
                        ev_code = fields[3]
                        ev_value = fields[4]

                        if ev_type != EV_KEY:
                            continue

                        # Track shift state on both press and release
                        if ev_code in (KEY_LEFTSHIFT, KEY_RIGHTSHIFT):
                            self._shift_down = (ev_value != 0)
                            if ev_value == KEY_DOWN:
                                continue
                            continue

                        # Only process key-down events for character keys
                        if ev_value != KEY_DOWN:
                            continue

                        now = time.monotonic()

                        # Check gap — if pause exceeded, flush previous scan
                        if (self._last_key_ts is not None and
                                (now - self._last_key_ts) > GAP_SEC and
                                self._current_buffer):
                            self._flush_buffer()

                        self._last_key_ts = now

                        # Map keycode to character
                        if self._shift_down and ev_code in SHIFT_KEYMAP:
                            self._current_buffer.append(SHIFT_KEYMAP[ev_code])
                        elif ev_code in KEYCODE_MAP:
                            ch = KEYCODE_MAP[ev_code]
                            if self._shift_down and 'a' <= ch <= 'z':
                                ch = ch.upper()
                            self._current_buffer.append(ch)

            except PermissionError:
                with self._lock:
                    self._connected = False
                    self._error = 'Немає доступу до пристрою (потрібен root)'
                time.sleep(5.0)
            except OSError as e:
                with self._lock:
                    self._connected = False
                    self._error = f'Помилка пристрою: {e}'
                time.sleep(2.0)
            except Exception as e:
                with self._lock:
                    self._connected = False
                    self._error = f'Помилка: {e}'
                print(f"Scanner error: {e}")
                time.sleep(2.0)
            finally:
                if fd is not None:
                    try:
                        fcntl.ioctl(fd, EVIOCGRAB, 0)
                    except Exception:
                        pass
                    try:
                        os.close(fd)
                    except Exception:
                        pass
