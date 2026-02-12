"""
USB Camera module for live preview and video recording.

Uses OpenCV to capture frames from a USB camera and provides:
- MJPEG streaming for live preview in the browser
- Video recording to file with start/stop control
"""

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    cv2 = None
    CV2_AVAILABLE = False
import glob
import os
import queue
import re
import shutil
import threading
import time
from datetime import datetime
from typing import Optional


MAX_RECORDINGS_GB = 35  # FIFO quota for recordings folder


class USBCamera:
    """USB camera handler with streaming and recording capabilities."""

    def __init__(self, device_index: int = -1, recordings_dir: str = "recordings"):
        """
        Args:
            device_index: Video device index. -1 = auto-detect first working camera.
            recordings_dir: Directory to store video recordings.
        """
        self._device_index = device_index
        self._active_index = None  # Actually opened device index
        self._active_name = None   # Name of the active device
        self._recordings_dir = recordings_dir
        self._cap = None
        self._lock = threading.Lock()
        self._frame: Optional[bytes] = None  # Latest JPEG frame
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_error: Optional[str] = None

        # Recording state
        self._recording_allowed = True  # Set to False if no USB storage
        self._recording = False
        self._recorder = None
        self._recording_file: Optional[str] = None
        self._recording_start: Optional[float] = None
        self._rec_lock = threading.Lock()
        self._rec_queue: queue.Queue = queue.Queue(maxsize=90)  # 3s buffer
        self._rec_thread: Optional[threading.Thread] = None

        # Camera properties
        self._width = 1920
        self._height = 1080
        self._fps = 30
        self._actual_fps = 30.0  # Measured real capture FPS

        os.makedirs(self._recordings_dir, exist_ok=True)

    def start(self):
        """Start camera capture thread."""
        if not CV2_AVAILABLE:
            self._last_error = "OpenCV (cv2) не встановлено. Виконайте: pip3 install opencv-python-headless"
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop camera and release resources."""
        self._running = False
        if self._recording:
            self.stop_recording()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        with self._lock:
            if self._cap and self._cap.isOpened():
                self._cap.release()
                self._cap = None

    @staticmethod
    def _find_video_devices():
        """Find available /dev/video* device indices with device names."""
        devices = sorted(glob.glob('/dev/video*'))
        result = []
        for dev in devices:
            try:
                idx = int(dev.replace('/dev/video', ''))
            except ValueError:
                continue
            # Read device name from sysfs
            name_path = f'/sys/class/video4linux/video{idx}/name'
            name = ''
            try:
                with open(name_path, 'r') as f:
                    name = f.read().strip()
            except (IOError, OSError):
                pass
            result.append({'index': idx, 'path': dev, 'name': name})
        return result

    @staticmethod
    def _filter_usb_cameras(devices):
        """Filter device list to likely USB cameras (not RPi internal codecs)."""
        # USB cameras usually have recognizable names; RPi internal ones have bcm2835/unicam/etc
        rpi_internal = ('bcm2835', 'unicam', 'isp', 'fe801000', 'rpivid', 'rpi-')
        usb_candidates = []
        for dev in devices:
            name_lower = dev['name'].lower()
            # Skip RPi internal video devices
            if any(kw in name_lower for kw in rpi_internal):
                continue
            # Skip devices with no name (likely metadata nodes)
            if not dev['name']:
                continue
            usb_candidates.append(dev)
        # If no candidates found by name, try first 2 indices as fallback
        if not usb_candidates and devices:
            usb_candidates = [d for d in devices if d['index'] <= 1]
        return usb_candidates

    def _open_camera(self) -> bool:
        """Try to open the camera device. Auto-detect if device_index is -1."""
        all_devices = self._find_video_devices()

        if self._device_index >= 0:
            candidates = [d for d in all_devices if d['index'] == self._device_index]
            if not candidates:
                candidates = [{'index': self._device_index, 'path': f'/dev/video{self._device_index}', 'name': ''}]
        else:
            candidates = self._filter_usb_cameras(all_devices)
            if not candidates:
                dev_names = '; '.join(f"video{d['index']}={d['name']}" for d in all_devices[:5])
                self._last_error = f"USB камеру не знайдено серед {len(all_devices)} пристроїв ({dev_names}...)"
                return False

        tried = []
        for dev in candidates:
            idx = dev['index']
            dev_path = dev['path']  # e.g. /dev/video0
            label = f"video{idx}({dev['name']})"

            # Check read permission
            if not os.access(dev_path, os.R_OK | os.W_OK):
                tried.append(f"{label}:немає прав")
                continue

            # Try multiple open methods
            cap = None
            for attempt_desc, open_args in [
                ("path+V4L2", (dev_path, cv2.CAP_V4L2)),
                ("idx+V4L2", (idx, cv2.CAP_V4L2)),
                ("path", (dev_path,)),
                ("idx", (idx,)),
            ]:
                try:
                    c = cv2.VideoCapture(*open_args)
                    if c.isOpened():
                        cap = c
                        break
                    c.release()
                except Exception:
                    pass

            if cap is None:
                tried.append(f"{label}:не відкрилось")
                continue

            # Try resolutions from highest to lowest; pick the first that
            # delivers real 24+ fps, or fall back to the best available.
            # MJPG is required (YUYV can't push enough bandwidth over
            # USB 2.0 at HD resolutions).
            read_ok = False
            mjpg_fourcc = cv2.VideoWriter_fourcc(*'MJPG')
            TARGET_FPS = 30
            MIN_REAL_FPS = 24  # preferred threshold

            res_candidates = [
                (1920, 1080),
                (1280, 720),
                (640, 480),
            ]

            # Track best fallback in case no resolution meets MIN_REAL_FPS
            best_cap = None
            best_fps = 0

            for res_w, res_h in res_candidates:
                # Need a fresh device handle for each resolution attempt
                if cap is None or not cap.isOpened():
                    cap = cv2.VideoCapture(dev_path, cv2.CAP_V4L2)
                    if not cap.isOpened():
                        cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
                    if not cap.isOpened():
                        continue

                cap.set(cv2.CAP_PROP_FOURCC, mjpg_fourcc)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, res_w)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, res_h)
                cap.set(cv2.CAP_PROP_FPS, TARGET_FPS)

                # Warmup: try reading first frame
                warmup_ok = False
                for _ in range(5):
                    try:
                        ret, _ = cap.read()
                    except Exception:
                        ret = False
                    if ret:
                        warmup_ok = True
                        break
                    time.sleep(0.3)

                if not warmup_ok:
                    cap.release()
                    cap = None
                    continue

                # Measure REAL fps: read 10 frames and time them
                t0 = time.monotonic()
                good = 0
                for _ in range(10):
                    try:
                        ret, _ = cap.read()
                    except Exception:
                        ret = False
                    if ret:
                        good += 1
                elapsed = time.monotonic() - t0
                measured_fps = good / elapsed if elapsed > 0 else 0

                if measured_fps >= MIN_REAL_FPS:
                    # Good enough — use this resolution
                    if best_cap is not None:
                        best_cap.release()
                    read_ok = True
                    break
                else:
                    # Below threshold, but remember if it's the best so far
                    if measured_fps > best_fps:
                        if best_cap is not None:
                            best_cap.release()
                        best_cap = cap
                        best_fps = measured_fps
                        cap = None  # don't release — saved as best_cap
                    else:
                        cap.release()
                        cap = None

            # Fall back to best available resolution if none met MIN_REAL_FPS
            if not read_ok and best_cap is not None:
                cap = best_cap
                best_cap = None
                read_ok = True

            # Clean up unused fallback handle
            if best_cap is not None:
                best_cap.release()

            if not read_ok:
                if cap is not None:
                    cap.release()
                    cap = None
                tried.append(f"{label}:не читає кадр")
                continue

            # Read actual values
            self._width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
            self._height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480
            self._fps = cap.get(cv2.CAP_PROP_FPS) or 20
            self._active_index = idx
            self._active_name = dev['name']
            self._last_error = None

            with self._lock:
                self._cap = cap
            return True

        self._last_error = f"Спробовано: {', '.join(tried)}"
        return False

    def _capture_loop(self):
        """Main capture loop running in background thread."""
        # FPS measurement
        fps_frame_count = 0
        fps_start_time = time.monotonic()
        preview_counter = 0

        while self._running:
            # Try to open camera if not open
            with self._lock:
                cap = self._cap

            if cap is None or not cap.isOpened():
                if not self._open_camera():
                    time.sleep(3)
                    continue
                with self._lock:
                    cap = self._cap
                fps_frame_count = 0
                fps_start_time = time.monotonic()

            ret, frame = cap.read()
            if not ret:
                with self._lock:
                    if self._cap:
                        self._cap.release()
                        self._cap = None
                self._last_error = "Втрачено з'єднання з камерою"
                time.sleep(1)
                continue

            # Measure actual FPS every 30 frames
            fps_frame_count += 1
            if fps_frame_count >= 30:
                elapsed = time.monotonic() - fps_start_time
                if elapsed > 0:
                    self._actual_fps = round(fps_frame_count / elapsed, 1)
                fps_frame_count = 0
                fps_start_time = time.monotonic()

            # Encode JPEG for preview every 3rd frame (~10fps preview)
            # Browser polls at 200ms (5fps) so this is more than enough
            preview_counter += 1
            if preview_counter >= 3:
                preview_counter = 0
                ret_enc, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ret_enc:
                    with self._lock:
                        self._frame = jpeg.tobytes()

            # Queue frame for recording thread (non-blocking)
            if self._recording:
                try:
                    self._rec_queue.put_nowait(frame)
                except queue.Full:
                    pass  # Drop frame if recorder can't keep up

    def _recording_loop(self):
        """Recording thread: writes frames from queue to VideoWriter."""
        while self._recording or not self._rec_queue.empty():
            try:
                frame = self._rec_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            with self._rec_lock:
                if self._recorder is not None:
                    self._recorder.write(frame)

    def get_frame(self) -> Optional[bytes]:
        """Get the latest JPEG frame."""
        with self._lock:
            return self._frame

    def generate_mjpeg(self):
        """Generator that yields MJPEG frames for HTTP streaming."""
        while True:
            frame = self.get_frame()
            if frame is not None:
                yield (
                    b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'
                )
                time.sleep(0.033)  # ~30fps
            else:
                time.sleep(0.1)

    def start_recording(self, prefix: str = None) -> dict:
        """Start recording video to file.

        *prefix* — optional prefix for filename (e.g. device name).
        """
        if not CV2_AVAILABLE:
            return {'status': 'error', 'error': 'OpenCV not installed'}
        if not self._recording_allowed:
            return {'status': 'error', 'error': 'USB-накопичувач не підключено. Запис заборонено.'}
        with self._rec_lock:
            if self._recording:
                return {
                    'status': 'already_recording',
                    'file': self._recording_file,
                    'started': self._recording_start
                }

            now = datetime.now()
            date_dir = now.strftime('%Y-%m-%d')
            day_path = os.path.join(self._recordings_dir, date_dir)
            os.makedirs(day_path, exist_ok=True)
            timestamp = now.strftime('%Y%m%d_%H%M%S')
            if prefix:
                # Sanitize prefix: replace spaces/slashes with underscores
                safe_prefix = re.sub(r'[^\w\-]', '_', prefix)
                filename = f"{safe_prefix}_{timestamp}.avi"
            else:
                filename = f"rec_{timestamp}.avi"
            # Store relative path: "2026-02-11/rec_20260211_143025.avi"
            rel_path = os.path.join(date_dir, filename)
            filepath = os.path.join(self._recordings_dir, rel_path)

            fourcc = cv2.VideoWriter_fourcc(*'XVID')
            rec_fps = self._actual_fps if self._actual_fps > 0 else self._fps
            writer = cv2.VideoWriter(filepath, fourcc, rec_fps,
                                     (self._width, self._height))

            if not writer.isOpened():
                return {'status': 'error', 'error': 'Failed to create video writer'}

            # Clear queue before starting
            while not self._rec_queue.empty():
                try:
                    self._rec_queue.get_nowait()
                except queue.Empty:
                    break

            self._recorder = writer
            self._recording_file = rel_path
            self._recording_start = time.time()
            self._recording = True

            # Start recording thread
            self._rec_thread = threading.Thread(target=self._recording_loop, daemon=True)
            self._rec_thread.start()

            return {
                'status': 'recording',
                'file': filename,
                'started': self._recording_start
            }

    def stop_recording(self) -> dict:
        """Stop recording and finalize the file."""
        with self._rec_lock:
            if not self._recording:
                return {'status': 'not_recording'}

            self._recording = False

        # Wait for recording thread to drain the queue and finish
        if self._rec_thread:
            self._rec_thread.join(timeout=5)
            self._rec_thread = None

        with self._rec_lock:
            if self._recorder:
                self._recorder.release()
                self._recorder = None

            result = {
                'status': 'stopped',
                'file': self._recording_file,
                'duration': time.time() - (self._recording_start or 0)
            }
            self._recording_file = None
            self._recording_start = None

        # FIFO cleanup after recording saved
        self._cleanup_old_recordings()

        return result

    def rename_recording(self, old_rel_path: str, new_name: str) -> dict:
        """Rename a recording file (e.g. to add status suffix).

        *old_rel_path* — relative path returned by stop_recording (e.g. "2026-02-11/DevA_20260211_143025.avi")
        *new_name* — new base filename without extension (e.g. "DevA_20260211_143025_OK")
        """
        if not old_rel_path:
            return {'status': 'error', 'error': 'No file path provided'}

        old_abs = os.path.join(self._recordings_dir, old_rel_path)
        if not os.path.isfile(old_abs):
            return {'status': 'error', 'error': f'File not found: {old_rel_path}'}

        dir_part = os.path.dirname(old_rel_path)
        safe_name = re.sub(r'[^\w\-]', '_', new_name)
        new_filename = f"{safe_name}.avi"
        new_rel = os.path.join(dir_part, new_filename) if dir_part else new_filename
        new_abs = os.path.join(self._recordings_dir, new_rel)

        try:
            os.rename(old_abs, new_abs)
            return {'status': 'ok', 'old_file': old_rel_path, 'new_file': new_rel}
        except OSError as e:
            return {'status': 'error', 'error': str(e)}

    def get_status(self) -> dict:
        """Get camera status."""
        if not CV2_AVAILABLE:
            return {
                'connected': False,
                'has_frame': False,
                'cv2_available': False,
                'error': 'OpenCV (cv2) не встановлено. Виконайте: pip3 install opencv-python-headless',
                'video_devices': [],
                'width': 0, 'height': 0, 'fps': 0,
                'device_index': self._device_index,
                'active_device': None,
                'recording': False,
                'recording_file': None,
                'recording_duration': 0
            }

        with self._lock:
            connected = self._cap is not None and self._cap.isOpened()
            has_frame = self._frame is not None

        with self._rec_lock:
            recording = self._recording
            rec_file = self._recording_file
            rec_duration = (time.time() - self._recording_start) if self._recording_start and recording else 0

        all_devices = self._find_video_devices()
        usb_devices = self._filter_usb_cameras(all_devices)

        active_label = None
        if self._active_index is not None:
            active_label = f'/dev/video{self._active_index}'
            if self._active_name:
                active_label += f' ({self._active_name})'

        result = {
            'connected': connected,
            'has_frame': has_frame,
            'cv2_available': True,
            'video_devices': [f"video{d['index']}: {d['name']}" for d in usb_devices],
            'all_devices_count': len(all_devices),
            'width': self._width,
            'height': self._height,
            'fps': self._actual_fps,
            'device_index': self._device_index,
            'active_device': active_label,
            'recording': recording,
            'recording_file': rec_file,
            'recording_duration': round(rec_duration, 1),
            'recording_allowed': self._recording_allowed,
        }
        if self._last_error and not connected:
            result['error'] = self._last_error
        return result

    def list_recordings(self) -> list:
        """List all recorded video files with metadata, scanning date subfolders."""
        recordings = []
        if not os.path.isdir(self._recordings_dir):
            return recordings

        # Collect all video files: root level and date subfolders
        video_files = []
        for entry in os.listdir(self._recordings_dir):
            entry_path = os.path.join(self._recordings_dir, entry)
            if os.path.isfile(entry_path) and entry.endswith(('.avi', '.mp4')):
                # Legacy file in root
                video_files.append((entry, entry_path))
            elif os.path.isdir(entry_path):
                # Date subfolder
                for f in os.listdir(entry_path):
                    fpath = os.path.join(entry_path, f)
                    if os.path.isfile(fpath) and f.endswith(('.avi', '.mp4')):
                        rel = os.path.join(entry, f)  # "2026-02-11/rec_xxx.avi"
                        video_files.append((rel, fpath))

        # Sort by modification time, newest first
        video_files.sort(key=lambda x: os.path.getmtime(x[1]), reverse=True)

        for rel_name, filepath in video_files:
            size = os.path.getsize(filepath)
            mtime = os.path.getmtime(filepath)
            rec = {
                'filename': rel_name,
                'display_name': os.path.basename(rel_name),
                'folder': os.path.dirname(rel_name) or '/',
                'path': filepath,
                'size': size,
                'size_mb': round(size / (1024 * 1024), 2),
                'created': datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S'),
                'width': 0,
                'height': 0,
                'fps': 0,
                'duration': 0,
                'duration_str': '--',
                'codec': '--',
                'frames': 0,
            }
            # Extract video metadata via OpenCV
            if CV2_AVAILABLE:
                try:
                    vc = cv2.VideoCapture(filepath)
                    if vc.isOpened():
                        rec['width'] = int(vc.get(cv2.CAP_PROP_FRAME_WIDTH))
                        rec['height'] = int(vc.get(cv2.CAP_PROP_FRAME_HEIGHT))
                        rec['fps'] = round(vc.get(cv2.CAP_PROP_FPS), 1)
                        rec['frames'] = int(vc.get(cv2.CAP_PROP_FRAME_COUNT))
                        fourcc_int = int(vc.get(cv2.CAP_PROP_FOURCC))
                        if fourcc_int > 0:
                            rec['codec'] = ''.join(chr((fourcc_int >> 8 * i) & 0xFF) for i in range(4))
                        if rec['fps'] > 0 and rec['frames'] > 0:
                            dur = rec['frames'] / rec['fps']
                            rec['duration'] = round(dur, 1)
                            mins = int(dur) // 60
                            secs = int(dur) % 60
                            rec['duration_str'] = f"{mins}:{secs:02d}"
                        vc.release()
                except Exception:
                    pass
            recordings.append(rec)
        return recordings

    def _cleanup_old_recordings(self):
        """Delete oldest recordings (FIFO) when disk space is low.

        Uses 95% of partition capacity as quota, so on a dedicated USB
        drive nearly all space is usable (FIFO deletes oldest when full).
        Falls back to MAX_RECORDINGS_GB for the local filesystem.
        """
        try:
            usage = shutil.disk_usage(self._recordings_dir)
            max_bytes = int(usage.total * 0.95)
        except Exception:
            max_bytes = MAX_RECORDINGS_GB * 1024 ** 3
        if not os.path.isdir(self._recordings_dir):
            return

        # Collect all video files with size and mtime
        files = []
        for dirpath, _dirnames, filenames in os.walk(self._recordings_dir):
            for f in filenames:
                if f.endswith(('.avi', '.mp4')):
                    fpath = os.path.join(dirpath, f)
                    try:
                        stat = os.stat(fpath)
                        files.append((fpath, stat.st_size, stat.st_mtime))
                    except OSError:
                        pass

        total_size = sum(s for _, s, _ in files)
        if total_size <= max_bytes:
            return

        # Sort oldest first
        files.sort(key=lambda x: x[2])

        for fpath, fsize, _ in files:
            if total_size <= max_bytes:
                break
            try:
                parent = os.path.dirname(fpath)
                os.remove(fpath)
                total_size -= fsize
                # Remove empty date folder
                if parent != os.path.abspath(self._recordings_dir):
                    try:
                        if not os.listdir(parent):
                            os.rmdir(parent)
                    except OSError:
                        pass
            except OSError:
                pass

    def get_storage_info(self) -> dict:
        """Get disk usage info: total disk, recordings folder size, quota.

        Quota = 95% of partition capacity (allows using the full USB drive).
        """
        # Disk usage for the partition where recordings_dir lives
        try:
            usage = shutil.disk_usage(self._recordings_dir)
            disk_total = usage.total
            disk_used = usage.used
            disk_free = usage.free
        except Exception:
            disk_total = disk_used = disk_free = 0

        max_bytes = int(disk_total * 0.95) if disk_total else MAX_RECORDINGS_GB * 1024 ** 3

        # Recordings folder total size
        rec_size = 0
        rec_count = 0
        if os.path.isdir(self._recordings_dir):
            for dirpath, _dirnames, filenames in os.walk(self._recordings_dir):
                for f in filenames:
                    if f.endswith(('.avi', '.mp4')):
                        try:
                            rec_size += os.path.getsize(os.path.join(dirpath, f))
                            rec_count += 1
                        except OSError:
                            pass

        def fmt(b):
            if b >= 1024 ** 3:
                return f"{b / (1024 ** 3):.1f} ГБ"
            return f"{b / (1024 ** 2):.1f} МБ"

        disk_pct = round(disk_used / disk_total * 100, 1) if disk_total else 0
        quota_pct = round(rec_size / max_bytes * 100, 1) if max_bytes else 0

        return {
            'disk_total': disk_total,
            'disk_used': disk_used,
            'disk_free': disk_free,
            'disk_total_str': fmt(disk_total),
            'disk_used_str': fmt(disk_used),
            'disk_free_str': fmt(disk_free),
            'disk_used_pct': disk_pct,
            'recordings_size': rec_size,
            'recordings_size_str': fmt(rec_size),
            'recordings_count': rec_count,
            'recordings_dir': os.path.abspath(self._recordings_dir),
            'quota_max': max_bytes,
            'quota_max_str': fmt(max_bytes),
            'quota_used_pct': quota_pct,
        }

    def set_recordings_dir(self, new_dir: str, allow_recording: bool = True) -> dict:
        """Switch recordings directory (e.g. to USB drive).

        Cannot switch while actively recording.
        *allow_recording* controls whether recording is permitted on this dir.
        """
        with self._rec_lock:
            if self._recording:
                return {'status': 'error', 'error': 'Зупиніть запис перед зміною директорії'}
        os.makedirs(new_dir, exist_ok=True)
        old_dir = self._recordings_dir
        self._recordings_dir = new_dir
        self._recording_allowed = allow_recording
        return {
            'status': 'ok',
            'old_dir': old_dir,
            'new_dir': new_dir,
        }

    def delete_recording(self, filename: str) -> bool:
        """Delete a recording file. filename can be 'date/file.avi' or 'file.avi'."""
        filepath = os.path.join(self._recordings_dir, filename)
        # Prevent path traversal
        if not os.path.abspath(filepath).startswith(os.path.abspath(self._recordings_dir)):
            return False
        if os.path.exists(filepath):
            parent = os.path.dirname(filepath)
            os.remove(filepath)
            # Remove empty date folder
            if parent != os.path.abspath(self._recordings_dir):
                try:
                    if not os.listdir(parent):
                        os.rmdir(parent)
                except OSError:
                    pass
            return True
        return False
