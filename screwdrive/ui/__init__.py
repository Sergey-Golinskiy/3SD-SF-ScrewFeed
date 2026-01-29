"""
UI module for Screw Drive Control System.

Provides:
- TouchDesk: PyQt5 fullscreen touch interface for Raspberry Pi
- Web UI is served through the API server (api/server.py)

Usage:
    python -m ui.touchdesk    # Run TouchDesk UI

For headless operation with touchscreen:
    QT_QPA_PLATFORM=eglfs python -m ui.touchdesk
"""

__all__ = ['touchdesk']
