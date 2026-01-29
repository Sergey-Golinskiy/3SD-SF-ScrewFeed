#!/usr/bin/env bash
# Clear splash screen when TouchDesk UI starts
printf "\033c" > /dev/tty1 2>/dev/null || true
if [ -e /dev/fb0 ]; then
  if [ ! -f /opt/splash/black.png ]; then
    convert -size 1920x1080 xc:black /opt/splash/black.png 2>/dev/null || true
  fi
  FRAMEBUFFER=/dev/fb0 /usr/bin/fbi -a -T 1 -d /dev/fb0 --noverbose /opt/splash/black.png </dev/tty1 >/dev/tty1 2>/dev/null || true
fi
# Kill any remaining fbi processes
pkill -9 fbi 2>/dev/null || true
