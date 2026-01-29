#!/bin/bash
# =============================================================================
# ScrewDrive Splash Screen Setup Script
# Configures Raspberry Pi 5 to show splash screen on boot without desktop
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "========================================"
echo "ScrewDrive Splash Screen Setup"
echo "========================================"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Please run as root (sudo)"
    exit 1
fi

# 1. Install required packages
echo ""
echo "[1/7] Installing required packages..."
apt-get update
apt-get install -y fbi plymouth plymouth-themes

# 2. Create splash directory and copy image
echo ""
echo "[2/7] Setting up splash image..."
mkdir -p /opt/screwdrive
cp "$PROJECT_DIR/screwdrive/resources/splash.png" /opt/screwdrive/
chmod 644 /opt/screwdrive/splash.png

# 3. Configure boot config to disable rainbow splash and logo
echo ""
echo "[3/7] Configuring boot settings..."
CONFIG_FILE="/boot/firmware/config.txt"

# Backup original config
if [ ! -f "${CONFIG_FILE}.backup" ]; then
    cp "$CONFIG_FILE" "${CONFIG_FILE}.backup"
    echo "Backed up original config.txt"
fi

# Add/update settings in config.txt
if ! grep -q "disable_splash=1" "$CONFIG_FILE"; then
    echo "" >> "$CONFIG_FILE"
    echo "# ScrewDrive: Disable rainbow splash" >> "$CONFIG_FILE"
    echo "disable_splash=1" >> "$CONFIG_FILE"
fi

# 4. Configure kernel command line to hide boot messages
echo ""
echo "[4/7] Configuring kernel command line..."
CMDLINE_FILE="/boot/firmware/cmdline.txt"

# Backup original cmdline
if [ ! -f "${CMDLINE_FILE}.backup" ]; then
    cp "$CMDLINE_FILE" "${CMDLINE_FILE}.backup"
    echo "Backed up original cmdline.txt"
fi

# Read current cmdline
CMDLINE=$(cat "$CMDLINE_FILE")

# Add quiet and splash options if not present
NEEDS_UPDATE=false
if [[ ! "$CMDLINE" =~ "quiet" ]]; then
    CMDLINE="$CMDLINE quiet"
    NEEDS_UPDATE=true
fi
if [[ ! "$CMDLINE" =~ "splash" ]]; then
    CMDLINE="$CMDLINE splash"
    NEEDS_UPDATE=true
fi
if [[ ! "$CMDLINE" =~ "loglevel=0" ]]; then
    CMDLINE="$CMDLINE loglevel=0"
    NEEDS_UPDATE=true
fi
if [[ ! "$CMDLINE" =~ "logo.nologo" ]]; then
    CMDLINE="$CMDLINE logo.nologo"
    NEEDS_UPDATE=true
fi
if [[ ! "$CMDLINE" =~ "vt.global_cursor_default=0" ]]; then
    CMDLINE="$CMDLINE vt.global_cursor_default=0"
    NEEDS_UPDATE=true
fi

if [ "$NEEDS_UPDATE" = true ]; then
    echo "$CMDLINE" > "$CMDLINE_FILE"
    echo "Updated cmdline.txt"
fi

# 5. Install splash screen service
echo ""
echo "[5/7] Installing splash screen service..."
cp "$PROJECT_DIR/screwdrive/services/splashscreen.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable splashscreen.service

# 6. Disable desktop environment (optional - comment out if you need desktop sometimes)
echo ""
echo "[6/7] Configuring boot target..."
# Set default to multi-user (no GUI)
systemctl set-default multi-user.target

# Disable lightdm/gdm if present
systemctl disable lightdm.service 2>/dev/null || true
systemctl disable gdm.service 2>/dev/null || true

# 7. Create touchdesk startup service that kills splash
echo ""
echo "[7/7] Creating TouchDesk service with splash killer..."
cat > /etc/systemd/system/touchdesk.service << 'EOF'
[Unit]
Description=ScrewDrive TouchDesk UI
After=network.target splashscreen.service
Wants=splashscreen.service

[Service]
Type=simple
User=root
Environment=DISPLAY=:0
Environment=QT_QPA_PLATFORM=linuxfb
Environment=QT_QPA_FB_DRM=1
WorkingDirectory=/opt/screwdrive
ExecStartPre=/bin/bash -c 'pkill -9 fbi || true'
ExecStart=/usr/bin/python3 /opt/screwdrive/touchdesk.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable touchdesk.service

echo ""
echo "========================================"
echo "Setup complete!"
echo "========================================"
echo ""
echo "Changes made:"
echo "  - Installed fbi for framebuffer display"
echo "  - Copied splash.png to /opt/screwdrive/"
echo "  - Disabled Raspberry Pi rainbow splash"
echo "  - Hidden boot messages (quiet boot)"
echo "  - Hidden kernel logo"
echo "  - Enabled splash screen service"
echo "  - Disabled desktop environment"
echo "  - Created TouchDesk service"
echo ""
echo "IMPORTANT: You need to reboot for changes to take effect!"
echo "Run: sudo reboot"
echo ""
