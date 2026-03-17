#!/bin/bash
# =============================================================================
# Slave Pi GPIO Safety Configuration
# =============================================================================
# This script configures Raspberry Pi boot-time GPIO pull states to prevent
# unintended motor movement during the time between power-on and xy_cli.py start.
#
# PROBLEM: Between power-on and xy_cli.py startup (30-60 seconds), GPIO pins
# are in their default pull-up/pull-down state. Some pins (like GPIO 8 = Y_ENA)
# have internal pull-ups, which can ENABLE motor drivers before software control.
# Combined with noise on STEP/DIR pins from SPI probing during boot, this can
# cause random high-speed motor movement.
#
# SOLUTION: Use gpio-no-irq overlay in config.txt to set safe pull states at boot:
# - ENA pins: pull-down (LOW) = drivers DISABLED
# - STEP pins: pull-up (HIGH) = idle level for active-low pulse config
# - DIR pins: pull-down (LOW) = safe direction (towards MIN endstops)
# =============================================================================

set -euo pipefail

CONFIG_FILE="/boot/firmware/config.txt"

# Fallback for older Pi OS
if [ ! -f "$CONFIG_FILE" ]; then
    CONFIG_FILE="/boot/config.txt"
fi

if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: Cannot find config.txt"
    exit 1
fi

echo "========================================"
echo "Slave Pi GPIO Safety Setup"
echo "========================================"
echo "Config file: $CONFIG_FILE"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Please run as root (sudo)"
    exit 1
fi

# GPIO pin assignments (from xy_cli.py)
# X axis: STEP=9, DIR=10, ENA=11
# Y axis: STEP=21, DIR=7, ENA=8

MARKER="# === ScrewFeed Slave GPIO Safety ==="

# Check if already configured
if grep -q "$MARKER" "$CONFIG_FILE" 2>/dev/null; then
    echo "GPIO safety configuration already present in $CONFIG_FILE"
    echo "To reconfigure, remove the section between the markers and run again."
    exit 0
fi

# Backup config
cp "$CONFIG_FILE" "${CONFIG_FILE}.bak.$(date +%Y%m%d_%H%M%S)"
echo "Backup created: ${CONFIG_FILE}.bak.*"

# Add GPIO pull configuration
cat >> "$CONFIG_FILE" << 'EOF'

# === ScrewFeed Slave GPIO Safety ===
# Boot-time GPIO pull states to prevent unintended motor movement
# before xy_cli.py starts and takes control of GPIO pins.
#
# ENA pins: pull-down = drivers DISABLED at boot
gpio=8,11=pd
# STEP pins: pull-up = idle level (STEP_IDLE_LEVEL=1, active-low pulses)
gpio=9,21=pu
# DIR pins: pull-down = safe direction (towards MIN endstops)
gpio=7,10=pd
# Disable SPI to prevent probe signals on GPIO 7-11 during boot
dtparam=spi=off
# === End ScrewFeed Slave GPIO Safety ===
EOF

echo ""
echo "GPIO safety configuration added to $CONFIG_FILE:"
echo "  GPIO 8  (Y_ENA):  pull-down (driver disabled)"
echo "  GPIO 11 (X_ENA):  pull-down (driver disabled)"
echo "  GPIO 9  (X_STEP): pull-up   (idle level)"
echo "  GPIO 21 (Y_STEP): pull-up   (idle level)"
echo "  GPIO 7  (Y_DIR):  pull-down (safe direction)"
echo "  GPIO 10 (X_DIR):  pull-down (safe direction)"
echo "  SPI:              disabled   (prevents GPIO toggling during boot)"
echo ""
echo "REBOOT REQUIRED for changes to take effect."
echo "Run: sudo reboot"
