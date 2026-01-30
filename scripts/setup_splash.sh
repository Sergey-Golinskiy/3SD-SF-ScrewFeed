#!/bin/bash
# =============================================================================
# ScrewDrive Splash Screen Setup Script
# Based on working configuration from old project
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Function to print green bold section headers
header() {
  echo -e "\n\033[1;32m=== $1 ===\033[0m\n"
}

echo "========================================"
echo "ScrewDrive Splash Screen Setup"
echo "========================================"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Please run as root (sudo)"
    exit 1
fi

# 1. System update
header "System update"
apt update

# 2. Install required packages
header "Installing required packages"
apt install -y fbi imagemagick python3-pyqt5 python3-serial python3-flask python3-yaml

# 3. Purge plymouth completely
header "Removing Plymouth (boot splash)"
apt purge -y plymouth plymouth-themes 2>/dev/null || true
rm -rf /usr/share/plymouth 2>/dev/null || true

# 4. Create splash directory and copy files
header "Setting up splash files"
mkdir -p /opt/splash
mkdir -p /opt/screwdrive

cp "$PROJECT_DIR/screwdrive/resources/splash.png" /opt/splash/splash.png
cp "$PROJECT_DIR/screwdrive/services/clear-splash.sh" /opt/splash/clear-splash.sh
cp "$PROJECT_DIR/screwdrive/resources/kms.json" /opt/screwdrive/kms.json

chmod 644 /opt/splash/splash.png
chmod +x /opt/splash/clear-splash.sh
chmod 644 /opt/screwdrive/kms.json

# 5. Configure boot config.txt
header "Configuring /boot/firmware/config.txt"
CONFIG_FILE="/boot/firmware/config.txt"

# Backup original config
if [ ! -f "${CONFIG_FILE}.backup" ]; then
    cp "$CONFIG_FILE" "${CONFIG_FILE}.backup"
    echo "Backed up original config.txt"
fi

# Remove existing disable_splash if present and add new one
sed -i '/^disable_splash=/d' "$CONFIG_FILE"
echo "disable_splash=1" >> "$CONFIG_FILE"
echo "Added: disable_splash=1"

# 6. Configure kernel command line
header "Configuring /boot/firmware/cmdline.txt"
CMDLINE_FILE="/boot/firmware/cmdline.txt"

# Backup original cmdline
if [ ! -f "${CMDLINE_FILE}.backup" ]; then
    cp "$CMDLINE_FILE" "${CMDLINE_FILE}.backup"
    echo "Backed up original cmdline.txt"
fi

# Replace console=tty1 with console=tty3 (hide boot messages)
sed -i 's/console=tty1/console=tty3/g' "$CMDLINE_FILE"

# Add quiet boot parameters if not present
CMDLINE=$(cat "$CMDLINE_FILE")
if [[ ! "$CMDLINE" =~ "quiet" ]]; then
    sed -i 's/$/ quiet loglevel=3 vt.global_cursor_default=0/' "$CMDLINE_FILE"
    echo "Added: quiet loglevel=3 vt.global_cursor_default=0"
fi

# 7. Set boot behaviour to Console Autologin
header "Configuring boot behaviour"
raspi-config nonint do_boot_behaviour B2
echo "Set boot to: Console Autologin (B2)"

# Set default target to multi-user (no GUI)
systemctl set-default multi-user.target
echo "Set default target: multi-user.target"

# 8. Install splash screen service
header "Installing splashscreen.service"
cp "$PROJECT_DIR/screwdrive/services/splashscreen.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable splashscreen.service
echo "Enabled: splashscreen.service"

# 9. Check screwdrive-api.service exists
header "Checking screwdrive-api.service"
if systemctl list-unit-files | grep -q "screwdrive-api.service"; then
    echo "Found: screwdrive-api.service"
    systemctl enable screwdrive-api.service
else
    echo "WARNING: screwdrive-api.service not found!"
    echo "TouchDesk requires screwdrive-api.service to be installed."
fi

# 10. Install touchdesk service
header "Installing touchdesk.service"
cp "$PROJECT_DIR/screwdrive/services/touchdesk.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable touchdesk.service
echo "Enabled: touchdesk.service"

# 11. Copy project files to /opt/screwdrive
header "Deploying project files"
cp -r "$PROJECT_DIR/screwdrive"/* /opt/screwdrive/ 2>/dev/null || true
cp "$PROJECT_DIR"/*.py /opt/screwdrive/ 2>/dev/null || true

# Create necessary temp files
touch /tmp/selected_device.json
touch /tmp/screw_events.jsonl

# 12. Setup environment variables for eglfs
header "Setting up environment variables"
PROFILE="/etc/profile.d/screwdrive.sh"
cat > "$PROFILE" << 'EOF'
# ScrewDrive TouchDesk PyQt eglfs KMS setup
export QT_QPA_PLATFORM=eglfs
export QT_QPA_EGLFS_INTEGRATION=eglfs_kms
export QT_QPA_EGLFS_KMS_CONFIG=/opt/screwdrive/kms.json
EOF
chmod 644 "$PROFILE"
echo "Created: $PROFILE"

echo ""
echo "========================================"
echo "Setup complete!"
echo "========================================"
echo ""
echo "Changes made:"
echo "  [✓] Installed fbi, imagemagick"
echo "  [✓] Removed Plymouth completely"
echo "  [✓] Copied splash.png to /opt/splash/"
echo "  [✓] Disabled Raspberry Pi rainbow splash"
echo "  [✓] Redirected console from tty1 to tty3"
echo "  [✓] Added quiet boot parameters"
echo "  [✓] Set boot to Console Autologin (B2)"
echo "  [✓] Enabled splashscreen.service"
echo "  [✓] Enabled touchdesk.service (depends on screwdrive-api.service)"
echo "  [✓] Deployed project to /opt/screwdrive/"
echo ""
echo "Services status:"
systemctl is-enabled splashscreen.service || true
systemctl is-enabled screwdrive-api.service || true
systemctl is-enabled touchdesk.service || true
echo ""
echo -e "\033[1;32mIMPORTANT: Reboot your Raspberry Pi to apply changes!\033[0m"
echo -e "\033[1;32m  sudo reboot\033[0m"
echo ""
