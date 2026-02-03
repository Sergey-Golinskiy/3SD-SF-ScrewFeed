"""
Comprehensive logging system for ScrewDrive.
Provides structured logging with categories, levels, and real-time access.
"""

import os
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from collections import deque
from logging.handlers import RotatingFileHandler
from enum import Enum
from typing import Optional, Dict, Any, List


# === Log Categories ===
class LogCategory(str, Enum):
    SYSTEM = "SYSTEM"       # System startup, shutdown, configuration
    AUTH = "AUTH"           # Authentication, login, logout
    XY = "XY"               # XY table movements, homing, positions
    CYCLE = "CYCLE"         # Cycle execution, progress, completion
    RELAY = "RELAY"         # Relay activations, states
    SENSOR = "SENSOR"       # Sensor readings, state changes
    API = "API"             # API requests, responses
    DEVICE = "DEVICE"       # Device management, configuration
    GCODE = "GCODE"         # G-code commands sent/received
    COMM = "COMM"           # Communication with hardware (serial, etc.)
    ERROR = "ERROR"         # Errors and exceptions


# === Log Levels ===
class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# Level to numeric mapping
LEVEL_MAP = {
    LogLevel.DEBUG: logging.DEBUG,
    LogLevel.INFO: logging.INFO,
    LogLevel.WARNING: logging.WARNING,
    LogLevel.ERROR: logging.ERROR,
    LogLevel.CRITICAL: logging.CRITICAL,
}

LEVEL_PRIORITY = {
    LogLevel.DEBUG: 0,
    LogLevel.INFO: 1,
    LogLevel.WARNING: 2,
    LogLevel.ERROR: 3,
    LogLevel.CRITICAL: 4,
}


# === Log Entry ===
class LogEntry:
    """Represents a single log entry."""

    _counter = 0
    _counter_lock = threading.Lock()

    def __init__(
        self,
        level: LogLevel,
        category: LogCategory,
        message: str,
        source: str = "",
        details: Optional[Dict[str, Any]] = None
    ):
        with LogEntry._counter_lock:
            LogEntry._counter += 1
            self.id = LogEntry._counter

        self.timestamp = datetime.now()
        self.level = level
        self.category = category
        self.message = message
        self.source = source
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "timestamp_display": self.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "level": self.level.value,
            "category": self.category.value,
            "message": self.message,
            "source": self.source,
            "details": self.details
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def __str__(self) -> str:
        ts = self.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        src = f"[{self.source}]" if self.source else ""
        return f"{ts} [{self.level.value}] [{self.category.value}] {src} {self.message}"


# === Ring Buffer for Real-time Logs ===
class LogBuffer:
    """Thread-safe ring buffer for storing recent logs."""

    def __init__(self, max_size: int = 5000):
        self.max_size = max_size
        self.buffer: deque[LogEntry] = deque(maxlen=max_size)
        self.lock = threading.Lock()
        self.listeners: List[callable] = []

    def add(self, entry: LogEntry) -> None:
        """Add a log entry to the buffer."""
        with self.lock:
            self.buffer.append(entry)

        # Notify listeners
        for listener in self.listeners:
            try:
                listener(entry)
            except Exception:
                pass

    def get_all(self) -> List[Dict[str, Any]]:
        """Get all logs as list of dicts."""
        with self.lock:
            return [e.to_dict() for e in self.buffer]

    def get_filtered(
        self,
        level: Optional[LogLevel] = None,
        category: Optional[LogCategory] = None,
        since_id: Optional[int] = None,
        search: Optional[str] = None,
        limit: int = 500
    ) -> List[Dict[str, Any]]:
        """Get filtered logs."""
        with self.lock:
            result = []
            search_lower = search.lower() if search else None

            for entry in reversed(self.buffer):
                # Filter by ID
                if since_id is not None and entry.id <= since_id:
                    continue

                # Filter by level (show this level and higher)
                if level is not None:
                    if LEVEL_PRIORITY.get(entry.level, 0) < LEVEL_PRIORITY.get(level, 0):
                        continue

                # Filter by category
                if category is not None and entry.category != category:
                    continue

                # Filter by search
                if search_lower:
                    if (search_lower not in entry.message.lower() and
                        search_lower not in entry.source.lower() and
                        search_lower not in str(entry.details).lower()):
                        continue

                result.append(entry.to_dict())

                if len(result) >= limit:
                    break

            return list(reversed(result))

    def get_since(self, since_id: int) -> List[Dict[str, Any]]:
        """Get logs since a specific ID."""
        with self.lock:
            return [e.to_dict() for e in self.buffer if e.id > since_id]

    def clear(self) -> None:
        """Clear all logs from buffer."""
        with self.lock:
            self.buffer.clear()

    def add_listener(self, callback: callable) -> None:
        """Add a listener for new log entries."""
        self.listeners.append(callback)

    def remove_listener(self, callback: callable) -> None:
        """Remove a listener."""
        if callback in self.listeners:
            self.listeners.remove(callback)


# === Main Logger Class ===
class ScrewDriveLogger:
    """Main logger class for the ScrewDrive system."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self.buffer = LogBuffer(max_size=5000)
        self._setup_file_logging()

    def _setup_file_logging(self):
        """Setup file-based logging with rotation."""
        # Create logs directory
        log_dir = Path(__file__).parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)

        self.log_file = log_dir / "screwdrive.log"

        # Setup rotating file handler
        self.file_handler = RotatingFileHandler(
            self.log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding='utf-8'
        )

        # Also setup a JSON log file for structured logs
        self.json_log_file = log_dir / "screwdrive.jsonl"
        self.json_file_handler = RotatingFileHandler(
            self.json_log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding='utf-8'
        )

    def log(
        self,
        level: LogLevel,
        category: LogCategory,
        message: str,
        source: str = "",
        details: Optional[Dict[str, Any]] = None
    ) -> LogEntry:
        """Log a message."""
        entry = LogEntry(level, category, message, source, details)

        # Add to buffer
        self.buffer.add(entry)

        # Write to file
        try:
            self.file_handler.stream.write(str(entry) + "\n")
            self.file_handler.stream.flush()
        except Exception:
            pass

        # Write to JSON log
        try:
            self.json_file_handler.stream.write(entry.to_json() + "\n")
            self.json_file_handler.stream.flush()
        except Exception:
            pass

        return entry

    # Convenience methods for each level
    def debug(self, category: LogCategory, message: str, source: str = "", details: Dict = None):
        return self.log(LogLevel.DEBUG, category, message, source, details)

    def info(self, category: LogCategory, message: str, source: str = "", details: Dict = None):
        return self.log(LogLevel.INFO, category, message, source, details)

    def warning(self, category: LogCategory, message: str, source: str = "", details: Dict = None):
        return self.log(LogLevel.WARNING, category, message, source, details)

    def error(self, category: LogCategory, message: str, source: str = "", details: Dict = None):
        return self.log(LogLevel.ERROR, category, message, source, details)

    def critical(self, category: LogCategory, message: str, source: str = "", details: Dict = None):
        return self.log(LogLevel.CRITICAL, category, message, source, details)

    # Category-specific convenience methods
    def system(self, message: str, level: LogLevel = LogLevel.INFO, source: str = "", details: Dict = None):
        return self.log(level, LogCategory.SYSTEM, message, source, details)

    def auth(self, message: str, level: LogLevel = LogLevel.INFO, source: str = "", details: Dict = None):
        return self.log(level, LogCategory.AUTH, message, source, details)

    def xy(self, message: str, level: LogLevel = LogLevel.INFO, source: str = "", details: Dict = None):
        return self.log(level, LogCategory.XY, message, source, details)

    def cycle(self, message: str, level: LogLevel = LogLevel.INFO, source: str = "", details: Dict = None):
        return self.log(level, LogCategory.CYCLE, message, source, details)

    def relay(self, message: str, level: LogLevel = LogLevel.INFO, source: str = "", details: Dict = None):
        return self.log(level, LogCategory.RELAY, message, source, details)

    def sensor(self, message: str, level: LogLevel = LogLevel.INFO, source: str = "", details: Dict = None):
        return self.log(level, LogCategory.SENSOR, message, source, details)

    def api(self, message: str, level: LogLevel = LogLevel.INFO, source: str = "", details: Dict = None):
        return self.log(level, LogCategory.API, message, source, details)

    def device(self, message: str, level: LogLevel = LogLevel.INFO, source: str = "", details: Dict = None):
        return self.log(level, LogCategory.DEVICE, message, source, details)

    def gcode(self, message: str, level: LogLevel = LogLevel.DEBUG, source: str = "", details: Dict = None):
        return self.log(level, LogCategory.GCODE, message, source, details)

    def comm(self, message: str, level: LogLevel = LogLevel.DEBUG, source: str = "", details: Dict = None):
        return self.log(level, LogCategory.COMM, message, source, details)

    def get_logs(
        self,
        level: Optional[str] = None,
        category: Optional[str] = None,
        since_id: Optional[int] = None,
        search: Optional[str] = None,
        limit: int = 500
    ) -> List[Dict[str, Any]]:
        """Get logs with optional filters."""
        lvl = LogLevel(level) if level else None
        cat = LogCategory(category) if category else None
        return self.buffer.get_filtered(lvl, cat, since_id, search, limit)

    def get_stats(self) -> Dict[str, Any]:
        """Get logging statistics."""
        logs = self.buffer.get_all()

        stats = {
            "total": len(logs),
            "by_level": {},
            "by_category": {},
            "recent_errors": []
        }

        for log in logs:
            level = log["level"]
            category = log["category"]

            stats["by_level"][level] = stats["by_level"].get(level, 0) + 1
            stats["by_category"][category] = stats["by_category"].get(category, 0) + 1

            if level in ("ERROR", "CRITICAL"):
                if len(stats["recent_errors"]) < 10:
                    stats["recent_errors"].append(log)

        return stats

    def clear(self):
        """Clear log buffer (file logs remain)."""
        self.buffer.clear()


# === Global Logger Instance ===
logger = ScrewDriveLogger()


# === Helper Functions ===
def get_logger() -> ScrewDriveLogger:
    """Get the global logger instance."""
    return logger


def log_exception(category: LogCategory, message: str, exception: Exception, source: str = ""):
    """Log an exception with traceback."""
    import traceback
    details = {
        "exception_type": type(exception).__name__,
        "exception_message": str(exception),
        "traceback": traceback.format_exc()
    }
    logger.error(category, f"{message}: {exception}", source, details)


# === Categories and Levels for API ===
def get_log_categories() -> List[str]:
    """Get list of all log categories."""
    return [c.value for c in LogCategory]


def get_log_levels() -> List[str]:
    """Get list of all log levels."""
    return [l.value for l in LogLevel]
