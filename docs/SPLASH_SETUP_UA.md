# Налаштування Splash Screen для ScrewDrive

## Опис

Ця інструкція описує як налаштувати Raspberry Pi 5 для відображення splash-екрану при завантаженні без показу робочого столу.

**Результат:**
- При включенні одразу показується splash.png
- Немає логотипу Raspberry Pi
- Немає повідомлень завантаження
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
sudo apt-get update
sudo apt-get install -y fbi
```

`fbi` - це програма для відображення зображень на фреймбуфері без X-сервера.

### Крок 2: Копіювання splash-зображення

```bash
sudo mkdir -p /opt/screwdrive
sudo cp screwdrive/resources/splash.png /opt/screwdrive/
sudo chmod 644 /opt/screwdrive/splash.png
```

### Крок 3: Вимкнення Rainbow Splash

Відредагуйте файл `/boot/firmware/config.txt`:

```bash
sudo nano /boot/firmware/config.txt
```

Додайте в кінець файлу:

```
# Вимкнути веселковий splash
disable_splash=1
```

### Крок 4: Приховування повідомлень завантаження

Відредагуйте файл `/boot/firmware/cmdline.txt`:

```bash
sudo nano /boot/firmware/cmdline.txt
```

Додайте в кінець рядка (не на новий рядок!):

```
quiet splash loglevel=0 logo.nologo vt.global_cursor_default=0
```

**Приклад повного рядка:**
```
console=serial0,115200 console=tty1 root=PARTUUID=xxx rootfstype=ext4 fsck.repair=yes rootwait quiet splash loglevel=0 logo.nologo vt.global_cursor_default=0
```

### Крок 5: Створення служби splash screen

Створіть файл `/etc/systemd/system/splashscreen.service`:

```bash
sudo nano /etc/systemd/system/splashscreen.service
```

Вміст:

```ini
[Unit]
Description=Splash Screen for ScrewDrive System
DefaultDependencies=no
After=local-fs.target systemd-udev-settle.service
Before=sysinit.target
ConditionPathExists=/opt/screwdrive/splash.png

[Service]
Type=oneshot
RemainAfterExit=yes
Environment=FRAMEBUFFER=/dev/fb0
ExecStartPre=/bin/sleep 0.5
ExecStart=/usr/bin/fbi -a -T 1 -d /dev/fb0 --noverbose /opt/screwdrive/splash.png
StandardInput=tty
StandardOutput=tty

[Install]
WantedBy=sysinit.target
```

Увімкніть службу:

```bash
sudo systemctl daemon-reload
sudo systemctl enable splashscreen.service
```

### Крок 6: Вимкнення робочого столу

```bash
# Встановити multi-user target (без GUI)
sudo systemctl set-default multi-user.target

# Вимкнути display manager
sudo systemctl disable lightdm.service
sudo systemctl disable gdm.service
```

### Крок 7: Створення служби TouchDesk

Створіть файл `/etc/systemd/system/touchdesk.service`:

```bash
sudo nano /etc/systemd/system/touchdesk.service
```

Вміст:

```ini
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
```

Увімкніть службу:

```bash
sudo systemctl daemon-reload
sudo systemctl enable touchdesk.service
```

### Крок 8: Перезавантаження

```bash
sudo reboot
```

## Перевірка

Після перезавантаження:

1. **Перевірити статус splash:**
   ```bash
   sudo systemctl status splashscreen.service
   ```

2. **Перевірити статус TouchDesk:**
   ```bash
   sudo systemctl status touchdesk.service
   ```

3. **Переглянути логи:**
   ```bash
   journalctl -u splashscreen.service
   journalctl -u touchdesk.service
   ```

## Відновлення робочого столу

Якщо потрібно повернути робочий стіл:

```bash
# Увімкнути графічний target
sudo systemctl set-default graphical.target

# Увімкнути display manager
sudo systemctl enable lightdm.service

# Перезавантажити
sudo reboot
```

## Зміна splash-зображення

1. Підготуйте зображення PNG з роздільністю екрану (рекомендовано 1920x1080 або 1024x600)
2. Замініть файл:
   ```bash
   sudo cp your_new_splash.png /opt/screwdrive/splash.png
   ```
3. Перезавантажте систему

## Вирішення проблем

### Splash не показується

1. Перевірте чи існує файл:
   ```bash
   ls -la /opt/screwdrive/splash.png
   ```

2. Перевірте чи встановлено fbi:
   ```bash
   which fbi
   ```

3. Спробуйте запустити вручну:
   ```bash
   sudo fbi -a -T 1 -d /dev/fb0 --noverbose /opt/screwdrive/splash.png
   ```

### Видно текст завантаження

Перевірте що cmdline.txt містить всі параметри:
- `quiet`
- `splash`
- `loglevel=0`
- `logo.nologo`
- `vt.global_cursor_default=0`

### TouchDesk не запускається

1. Перевірте логи:
   ```bash
   journalctl -u touchdesk.service -f
   ```

2. Перевірте чи існує скрипт:
   ```bash
   ls -la /opt/screwdrive/touchdesk.py
   ```

3. Спробуйте запустити вручну:
   ```bash
   sudo python3 /opt/screwdrive/touchdesk.py
   ```
