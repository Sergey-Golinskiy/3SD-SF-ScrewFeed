#!/bin/bash
# Screw Drive Control System - Service Installation Script
# This script installs systemd services for auto-start at boot

set -e

SCREWDRIVE_DIR="/home/user/3SD-SF-ScrewFeed/screwdrive"
PYTHON_BIN="/usr/bin/python3"

echo "=== Screw Drive Control System - Service Installer ==="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo ./install_services.sh"
    exit 1
fi

# Check if screwdrive directory exists
if [ ! -d "$SCREWDRIVE_DIR" ]; then
    echo "ERROR: Directory $SCREWDRIVE_DIR not found!"
    exit 1
fi

# Check Python
if [ ! -f "$PYTHON_BIN" ]; then
    echo "ERROR: Python3 not found at $PYTHON_BIN"
    exit 1
fi

echo "Installing systemd services..."
echo ""

# Create API service
echo "Creating screwdrive-api.service..."
cat << EOF > /etc/systemd/system/screwdrive-api.service
[Unit]
Description=Screw Drive Control API Server
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$SCREWDRIVE_DIR
ExecStart=$PYTHON_BIN $SCREWDRIVE_DIR/main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

echo "  Created: /etc/systemd/system/screwdrive-api.service"

# Create TouchDesk service
echo "Creating screwdrive-touchdesk.service..."
cat << EOF > /etc/systemd/system/screwdrive-touchdesk.service
[Unit]
Description=Screw Drive TouchDesk UI
After=screwdrive-api.service
Requires=screwdrive-api.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$SCREWDRIVE_DIR
ExecStartPre=/bin/sleep 3
ExecStart=$PYTHON_BIN $SCREWDRIVE_DIR/ui/touchdesk.py
Environment=QT_QPA_PLATFORM=eglfs
Environment=QT_QPA_EGLFS_ALWAYS_SET_MODE=1
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "  Created: /etc/systemd/system/screwdrive-touchdesk.service"

# Create management script
echo "Creating management script..."
cat << 'SCRIPT' > /usr/local/bin/screwdrive
#!/bin/bash

case "$1" in
    start)
        echo "Starting Screw Drive services..."
        sudo systemctl start screwdrive-api.service
        sleep 2
        sudo systemctl start screwdrive-touchdesk.service
        echo "Services started."
        ;;
    stop)
        echo "Stopping Screw Drive services..."
        sudo systemctl stop screwdrive-touchdesk.service
        sudo systemctl stop screwdrive-api.service
        echo "Services stopped."
        ;;
    restart)
        echo "Restarting Screw Drive services..."
        sudo systemctl restart screwdrive-api.service
        sleep 2
        sudo systemctl restart screwdrive-touchdesk.service
        echo "Services restarted."
        ;;
    status)
        echo "=== API Server ==="
        sudo systemctl status screwdrive-api.service --no-pager -l
        echo ""
        echo "=== TouchDesk UI ==="
        sudo systemctl status screwdrive-touchdesk.service --no-pager -l
        ;;
    logs)
        sudo journalctl -u screwdrive-api.service -u screwdrive-touchdesk.service -f
        ;;
    logs-api)
        sudo journalctl -u screwdrive-api.service -f
        ;;
    logs-ui)
        sudo journalctl -u screwdrive-touchdesk.service -f
        ;;
    enable)
        echo "Enabling auto-start..."
        sudo systemctl enable screwdrive-api.service
        sudo systemctl enable screwdrive-touchdesk.service
        echo "Auto-start enabled."
        ;;
    disable)
        echo "Disabling auto-start..."
        sudo systemctl disable screwdrive-touchdesk.service
        sudo systemctl disable screwdrive-api.service
        echo "Auto-start disabled."
        ;;
    api-only)
        echo "Starting API server only..."
        sudo systemctl start screwdrive-api.service
        echo "API server started. Web UI: http://localhost:5000/"
        ;;
    *)
        echo "Screw Drive Control System Manager"
        echo ""
        echo "Usage: screwdrive {command}"
        echo ""
        echo "Commands:"
        echo "  start      - Start all services"
        echo "  stop       - Stop all services"
        echo "  restart    - Restart all services"
        echo "  status     - Show service status"
        echo "  logs       - Follow logs (all services)"
        echo "  logs-api   - Follow API server logs"
        echo "  logs-ui    - Follow TouchDesk UI logs"
        echo "  enable     - Enable auto-start at boot"
        echo "  disable    - Disable auto-start"
        echo "  api-only   - Start API server only (no TouchDesk)"
        echo ""
        exit 1
        ;;
esac
SCRIPT

chmod +x /usr/local/bin/screwdrive
echo "  Created: /usr/local/bin/screwdrive"

# Reload systemd
echo ""
echo "Reloading systemd daemon..."
systemctl daemon-reload

# Enable services
echo "Enabling services for auto-start..."
systemctl enable screwdrive-api.service
systemctl enable screwdrive-touchdesk.service

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Services installed and enabled for auto-start."
echo ""
echo "Usage:"
echo "  screwdrive start    - Start all services"
echo "  screwdrive stop     - Stop all services"
echo "  screwdrive status   - Check service status"
echo "  screwdrive logs     - View logs"
echo ""
echo "To start services now:"
echo "  sudo screwdrive start"
echo ""
echo "Or reboot the system:"
echo "  sudo reboot"
echo ""
echo "Web UI will be available at: http://<IP>:5000/"
