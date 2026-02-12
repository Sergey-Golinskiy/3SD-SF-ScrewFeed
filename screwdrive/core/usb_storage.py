"""
USB Storage manager for external recording drive.

Detects removable USB block devices, mounts/unmounts them,
formats to ext4, and provides storage status information.
"""

import json
import logging
import os
import shutil
import subprocess
import threading

logger = logging.getLogger(__name__)

# Default mount point for the recording USB drive
USB_MOUNT_POINT = '/mnt/rec_usb'

# System mount points that must NEVER be touched
_SYSTEM_MOUNTS = {'/', '/boot', '/boot/firmware'}


class USBStorage:
    """Manage a dedicated USB flash drive for video recordings."""

    def __init__(self, mount_point: str = USB_MOUNT_POINT):
        self._mount_point = mount_point
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def list_usb_block_devices(self) -> list:
        """Return list of removable USB block devices (not partitions)."""
        devices = []
        try:
            out = subprocess.check_output(
                ['lsblk', '-J', '-o', 'NAME,SIZE,TYPE,MOUNTPOINT,RM,TRAN,FSTYPE,LABEL,MODEL'],
                text=True, timeout=5
            )
        except Exception as e:
            logger.error("lsblk failed: %s", e)
            return devices

        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return devices

        for dev in data.get('blockdevices', []):
            # Only USB transport
            if dev.get('tran') != 'usb':
                continue

            # Skip if any partition of this disk is a system mount
            all_parts = dev.get('children', [])
            if self._has_system_mount(dev, all_parts):
                logger.info("Skipping %s — contains system partition", dev.get('name'))
                continue

            for p in all_parts:
                mp = p.get('mountpoint') or ''
                if mp in _SYSTEM_MOUNTS:
                    continue  # extra safety: skip individual system partitions
                devices.append({
                    'device': '/dev/' + p['name'],
                    'size': p.get('size', '--'),
                    'fstype': p.get('fstype') or '',
                    'label': p.get('label') or '',
                    'mountpoint': mp,
                    'model': dev.get('model') or '',
                    'parent': '/dev/' + dev['name'],
                })
            if not all_parts:
                mp = dev.get('mountpoint') or ''
                if mp in _SYSTEM_MOUNTS:
                    continue
                devices.append({
                    'device': '/dev/' + dev['name'],
                    'size': dev.get('size', '--'),
                    'fstype': dev.get('fstype') or '',
                    'label': dev.get('label') or '',
                    'mountpoint': mp,
                    'model': dev.get('model') or '',
                    'parent': '/dev/' + dev['name'],
                })
        return devices

    @staticmethod
    def _has_system_mount(dev: dict, parts: list) -> bool:
        """Return True if this disk or any of its partitions is mounted at a system path."""
        for entry in [dev] + parts:
            mp = entry.get('mountpoint') or ''
            if mp in _SYSTEM_MOUNTS:
                return True
        return False

    def detect(self) -> dict | None:
        """Find first USB partition suitable for recordings."""
        devs = self.list_usb_block_devices()
        if not devs:
            return None
        return devs[0]

    # ------------------------------------------------------------------
    # Mount / Unmount
    # ------------------------------------------------------------------

    def is_mounted(self) -> bool:
        """Check if the USB drive is mounted at our mount point."""
        return os.path.ismount(self._mount_point)

    def mount(self, device: str | None = None) -> dict:
        """Mount a USB partition to the recordings mount point.

        If *device* is None, auto-detect the first USB partition.
        """
        with self._lock:
            if self.is_mounted():
                return {'status': 'already_mounted', 'mount_point': self._mount_point}

            if device is None:
                info = self.detect()
                if info is None:
                    return {'status': 'error', 'error': 'USB-накопичувач не знайдено'}
                device = info['device']

            # Validate device path
            if not device.startswith('/dev/'):
                return {'status': 'error', 'error': 'Невірний шлях пристрою'}

            # Safety: only allow mounting devices that appear in our USB list
            usb_devs = self.list_usb_block_devices()
            allowed = {d['device'] for d in usb_devs}
            if device not in allowed:
                return {'status': 'error', 'error': 'Пристрій не є USB-накопичувачем або містить системний розділ'}

            os.makedirs(self._mount_point, exist_ok=True)

            try:
                subprocess.check_call(
                    ['mount', device, self._mount_point],
                    timeout=15
                )
            except subprocess.CalledProcessError as e:
                return {'status': 'error', 'error': f'Помилка монтування: {e}'}

            # Ensure recordings sub-dir exists
            rec_dir = os.path.join(self._mount_point, 'recordings')
            os.makedirs(rec_dir, exist_ok=True)

            return {
                'status': 'mounted',
                'device': device,
                'mount_point': self._mount_point,
                'recordings_dir': rec_dir,
            }

    def unmount(self) -> dict:
        """Unmount the USB drive."""
        with self._lock:
            if not self.is_mounted():
                return {'status': 'not_mounted'}
            try:
                subprocess.check_call(['umount', self._mount_point], timeout=15)
            except subprocess.CalledProcessError as e:
                # Try lazy unmount as fallback
                try:
                    subprocess.check_call(['umount', '-l', self._mount_point], timeout=15)
                except subprocess.CalledProcessError:
                    return {'status': 'error', 'error': f'Помилка відмонтування: {e}'}
            return {'status': 'unmounted'}

    # ------------------------------------------------------------------
    # Format
    # ------------------------------------------------------------------

    def format_device(self, device: str, label: str = 'REC_USB') -> dict:
        """Format a USB partition as ext4.

        WARNING: This destroys all data on the partition.
        The device must NOT be mounted.
        """
        with self._lock:
            if not device.startswith('/dev/'):
                return {'status': 'error', 'error': 'Невірний шлях пристрою'}

            # Safety: refuse to format if mounted
            if self.is_mounted():
                return {'status': 'error', 'error': 'Спочатку відмонтуйте пристрій'}

            # Check it's actually a USB device
            devs = self.list_usb_block_devices()
            dev_paths = [d['device'] for d in devs]
            if device not in dev_paths:
                return {'status': 'error', 'error': 'Пристрій не є USB-накопичувачем'}

            try:
                subprocess.check_call(
                    ['mkfs.ext4', '-F', '-L', label, device],
                    timeout=120
                )
            except subprocess.CalledProcessError as e:
                return {'status': 'error', 'error': f'Помилка форматування: {e}'}

            return {'status': 'formatted', 'device': device, 'fstype': 'ext4', 'label': label}

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return full USB storage status."""
        detected = self.detect()
        mounted = self.is_mounted()
        recordings_dir = os.path.join(self._mount_point, 'recordings') if mounted else None

        result = {
            'detected': detected is not None,
            'device_info': detected,
            'mounted': mounted,
            'mount_point': self._mount_point,
            'recordings_dir': recordings_dir,
        }

        if mounted:
            try:
                usage = shutil.disk_usage(self._mount_point)
                result['disk_total'] = usage.total
                result['disk_used'] = usage.used
                result['disk_free'] = usage.free
                result['disk_total_str'] = self._fmt(usage.total)
                result['disk_used_str'] = self._fmt(usage.used)
                result['disk_free_str'] = self._fmt(usage.free)
                result['disk_used_pct'] = round(usage.used / usage.total * 100, 1) if usage.total else 0
            except Exception:
                pass

            # Count recordings
            if recordings_dir and os.path.isdir(recordings_dir):
                rec_count = 0
                rec_size = 0
                for dp, _, fns in os.walk(recordings_dir):
                    for f in fns:
                        if f.endswith(('.avi', '.mp4')):
                            try:
                                rec_size += os.path.getsize(os.path.join(dp, f))
                                rec_count += 1
                            except OSError:
                                pass
                result['recordings_count'] = rec_count
                result['recordings_size'] = rec_size
                result['recordings_size_str'] = self._fmt(rec_size)
        return result

    @property
    def mount_point(self) -> str:
        return self._mount_point

    @property
    def recordings_dir(self) -> str | None:
        """Return recordings dir path if USB is mounted, else None."""
        if self.is_mounted():
            return os.path.join(self._mount_point, 'recordings')
        return None

    @staticmethod
    def _fmt(b: int) -> str:
        if b >= 1024 ** 3:
            return f"{b / (1024 ** 3):.1f} ГБ"
        return f"{b / (1024 ** 2):.1f} МБ"
