# Налаштування Splash Screen для ScrewDrive

## Опис

Ця інструкція описує як налаштувати Raspberry Pi 5 для відображення splash-екрану при завантаженні без показу робочого столу.

**Результат:**
- При включенні одразу показується splash.png
- Немає логотипу Raspberry Pi (веселковий splash)
- Немає повідомлень завантаження консолі
- Splash залишається поки не запуститься TouchDesk UI
- Робочий стіл не завантажується взагалі

## Автоматичне налаштування

Найпростіший спосіб - використати скрипт автоматичного налаштування:

```bash
cd /home/user/3SD-SF-ScrewFeed
sudo ./scripts/setup_splash.sh
sudo reboot
```

## Ручне налаштування

### Крок 1: Встановлення необхідних пакетів

```bash
sudo apt update
sudo apt install -y fbi imagemagick python3-pyqt5 python3-serial python3-flask python3-yaml
```

### Крок 2: Видалення Plymouth

**ВАЖЛИВО:** Plymouth треба повністю видалити, інакше він буде конфліктувати з fbi:

```bash
sudo apt purge -y plymouth plymouth-themes
sudo rm -rf /usr/share/plymouth
```

### Крок 3: Копіювання splash-зображення

```bash
sudo mkdir -p /opt/splash
sudo mkdir -p /opt/screwdrive
sudo cp screwdrive/resources/splash.png /opt/splash/splash.png
sudo cp screwdrive/services/clear-splash.sh /opt/splash/clear-splash.sh
sudo cp screwdrive/resources/kms.json /opt/screwdrive/kms.json
sudo chmod 644 /opt/splash/splash.png
sudo chmod +x /opt/splash/clear-splash.sh
```

### Крок 4: Вимкнення Rainbow Splash

Відредагуйте файл `/boot/firmware/config.txt`:

```bash
sudo nano /boot/firmware/config.txt
```

Видаліть старий рядок `disable_splash` (якщо є) та додайте в кінець:

```
disable_splash=1
```

### Крок 5: Перенаправлення консолі та приховування повідомлень

Відредагуйте файл `/boot/firmware/cmdline.txt`:

```bash
sudo nano /boot/firmware/cmdline.txt
```

1. Замініть `console=tty1` на `console=tty3`:
   ```
   # Було:
   console=tty1
   # Стало:
   console=tty3
   ```

2. Додайте в кінець рядка (не на новий рядок!):
   ```
   quiet loglevel=3 vt.global_cursor_default=0
   ```

**Приклад повного рядка:**
```
console=serial0,115200 console=tty3 root=PARTUUID=xxx rootfstype=ext4 fsck.repair=yes rootwait quiet loglevel=3 vt.global_cursor_default=0
```

### Крок 6: Налаштування boot behaviour

```bash
# Console Autologin (B2)
sudo raspi-config nonint do_boot_behaviour B2

# Встановити multi-user target (без GUI)
sudo systemctl set-default multi-user.target
```

### Крок 7: Вимкнення getty на tty1

**ВАЖЛИВО:** Якщо не вимкнути getty@tty1, консоль буде "перекривати" TouchDesk UI:

```bash
# Вимкнути autologin консоль на tty1
sudo systemctl disable getty@tty1.service

# Додати користувача в групи video та render для доступу до DRM
sudo usermod -aG video,render user
```

### Крок 8: Створення служби splashscreen

Створіть файл `/etc/systemd/system/splashscreen.service`:

```bash
sudo nano /etc/systemd/system/splashscreen.service
```

Вміст:

```ini
[Unit]
Description=Framebuffer Splash Screen
DefaultDependencies=no
After=local-fs.target systemd-udev-settle.service
Before=graphical.target

[Service]
Type=simple
RemainAfterExit=yes
Environment=FRAMEBUFFER=/dev/fb0
ExecStart=/usr/bin/fbi -a -T 1 -d /dev/fb0 --noverbose /opt/splash/splash.png

[Install]
WantedBy=multi-user.target
```

Увімкніть службу:

```bash
sudo systemctl daemon-reload
sudo systemctl enable splashscreen.service
```

### Крок 9: Створення служби TouchDesk

**Примітка:** API сервер вже має бути налаштований як `screwdrive-api.service`.

Створіть файл `/etc/systemd/system/touchdesk.service`:

```bash
sudo nano /etc/systemd/system/touchdesk.service
```

Вміст:

```ini
[Unit]
Description=ScrewDrive TouchDesk UI (PyQt5 EGLFS)
After=screwdrive-api.service
Requires=screwdrive-api.service

[Service]
User=user
Group=video
WorkingDirectory=/home/user/3SD-SF-ScrewFeed/screwdrive
Environment=QT_QPA_PLATFORM=eglfs
Environment=QT_QPA_EGLFS_KMS_CONFIG=/home/user/3SD-SF-ScrewFeed/screwdrive/resources/kms.json
ExecStart=/usr/bin/python3 /home/user/3SD-SF-ScrewFeed/screwdrive/ui/touchdesk.py
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**ВАЖЛИВО:** На Raspberry Pi 5 дисплей підключено до `/dev/dri/card1`, тому `kms.json` повинен містити:

```json
{
  "device": "/dev/dri/card1",
  "outputs": [
    {
      "name": "HDMI-A-1",
      "mode": "1280x800@60",
      "format": "ARGB8888",
      "transform": "normal"
    }
  ]
}
```

Увімкніть службу:

```bash
sudo systemctl daemon-reload
sudo systemctl enable touchdesk.service
```

### Крок 10: Налаштування змінних середовища

Створіть файл `/etc/profile.d/screwdrive.sh`:

```bash
sudo nano /etc/profile.d/screwdrive.sh
```

Вміст:

```bash
# ScrewDrive TouchDesk PyQt eglfs KMS setup
export QT_QPA_PLATFORM=eglfs
export QT_QPA_EGLFS_INTEGRATION=eglfs_kms
export QT_QPA_EGLFS_KMS_CONFIG=/opt/screwdrive/kms.json
```

### Крок 11: Перезавантаження

```bash
sudo reboot
```

## Перевірка

Після перезавантаження:

1. **Перевірити статус splash:**
   ```bash
   sudo systemctl status splashscreen.service
   ```

2. **Перевірити статус screwdrive-api:**
   ```bash
   sudo systemctl status screwdrive-api.service
   ```

3. **Перевірити статус TouchDesk:**
   ```bash
   sudo systemctl status touchdesk.service
   ```

4. **Переглянути логи:**
   ```bash
   journalctl -u splashscreen.service
   journalctl -u screwdrive-api.service
   journalctl -u touchdesk.service
   ```

## Відновлення робочого столу

Якщо потрібно повернути робочий стіл:

```bash
# Увімкнути графічний target
sudo systemctl set-default graphical.target

# Змінити boot behaviour на Desktop
sudo raspi-config nonint do_boot_behaviour B4

# Відновити cmdline.txt з бекапу
sudo cp /boot/firmware/cmdline.txt.backup /boot/firmware/cmdline.txt

# Перезавантажити
sudo reboot
```

## Зміна splash-зображення

1. Підготуйте зображення PNG з роздільністю екрану (рекомендовано 1280x800)
2. Замініть файл:
   ```bash
   sudo cp your_new_splash.png /opt/splash/splash.png
   ```
3. Перезавантажте систему

## Налаштування kms.json

Файл `/opt/screwdrive/kms.json` визначає параметри екрану:

```json
{
  "device": "/dev/dri/card1",
  "outputs": [
    {
      "name": "HDMI-A-1",
      "mode": "1280x800@60",
      "format": "ARGB8888",
      "transform": "normal"
    }
  ]
}
```

**ВАЖЛИВО:** На Raspberry Pi 5 дисплей підключено до `/dev/dri/card1` (card0 - це тільки GPU v3d без виходів на дисплей).

Змініть `mode` відповідно до вашого екрану (наприклад, `1920x1080@60`).

## Вирішення проблем

### Splash не показується

1. Перевірте чи існує файл:
   ```bash
   ls -la /opt/splash/splash.png
   ```

2. Перевірте чи встановлено fbi:
   ```bash
   which fbi
   ```

3. Перевірте чи видалено plymouth:
   ```bash
   dpkg -l | grep plymouth
   ```

4. Спробуйте запустити вручну:
   ```bash
   sudo fbi -a -T 1 -d /dev/fb0 --noverbose /opt/splash/splash.png
   ```

### Видно текст завантаження

Перевірте cmdline.txt:
- Консоль має бути `console=tty3` (не tty1)
- Повинні бути параметри: `quiet loglevel=3 vt.global_cursor_default=0`

### TouchDesk не запускається

1. Перевірте логи:
   ```bash
   journalctl -u touchdesk.service -f
   ```

2. Перевірте чи існує kms.json:
   ```bash
   ls -la /opt/screwdrive/kms.json
   ```

3. Перевірте змінні середовища:
   ```bash
   echo $QT_QPA_PLATFORM
   ```

4. Спробуйте запустити вручну:
   ```bash
   export QT_QPA_PLATFORM=eglfs
   export QT_QPA_EGLFS_INTEGRATION=eglfs_kms
   export QT_QPA_EGLFS_KMS_CONFIG=/opt/screwdrive/kms.json
   sudo python3 /opt/screwdrive/touchdesk.py
   ```

### Помилка "Could not open framebuffer device"

Перевірте права доступу:
```bash
ls -la /dev/fb0
sudo chmod 666 /dev/fb0
```
