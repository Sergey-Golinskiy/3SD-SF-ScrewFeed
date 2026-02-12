# Автозапуск TouchDesk UI

## Встановлення залежностей

```bash
sudo apt-get update
sudo apt-get install -y python3-pyqt5 python3-requests
```

## Встановлення systemd сервісу

1. **Скопіюйте файл сервісу:**
```bash
sudo cp /home/user/3SD-SF-ScrewFeed/screwdrive/ui/touchdesk.service /etc/systemd/system/
```

2. **Перезавантажте systemd:**
```bash
sudo systemctl daemon-reload
```

3. **Увімкніть автозапуск:**
```bash
sudo systemctl enable touchdesk.service
```

4. **Запустіть сервіс:**
```bash
sudo systemctl start touchdesk.service
```

## Керування сервісом

```bash
# Перевірити статус
sudo systemctl status touchdesk.service

# Зупинити
sudo systemctl stop touchdesk.service

# Перезапустити
sudo systemctl restart touchdesk.service

# Переглянути логи
sudo journalctl -u touchdesk.service -f

# Вимкнути автозапуск
sudo systemctl disable touchdesk.service
```

## Порядок запуску

Сервіс `touchdesk.service` автоматично запускається **після** `screwdrive-api.service`:

```
screwdrive-api.service → touchdesk.service
```

## Налаштування дисплею

Якщо використовується інший розмір дисплею, змініть параметри в файлі сервісу:

```ini
Environment=QT_QPA_EGLFS_PHYSICAL_WIDTH=800
Environment=QT_QPA_EGLFS_PHYSICAL_HEIGHT=480
Environment=QT_QPA_EGLFS_WIDTH=800
Environment=QT_QPA_EGLFS_HEIGHT=480
```

## Альтернативний запуск (X11)

Якщо використовується X11 замість framebuffer:

1. Змініть `QT_QPA_PLATFORM`:
```ini
Environment=QT_QPA_PLATFORM=xcb
```

2. Або видаліть всі `QT_QPA_EGLFS_*` змінні

## Ручний запуск для тестування

```bash
# Framebuffer (EGLFS)
sudo QT_QPA_PLATFORM=eglfs python3 /home/user/3SD-SF-ScrewFeed/screwdrive/ui/touchdesk.py

# X11
DISPLAY=:0 python3 /home/user/3SD-SF-ScrewFeed/screwdrive/ui/touchdesk.py
```

## Усунення несправностей

### TouchDesk не запускається
```bash
# Перевірте чи працює API сервер
sudo systemctl status screwdrive-api.service

# Перевірте логи
sudo journalctl -u touchdesk.service --no-pager -n 50
```

### Помилка "Could not connect to display"
- Перевірте налаштування `DISPLAY` та `QT_QPA_PLATFORM`
- Для framebuffer: `QT_QPA_PLATFORM=eglfs`
- Для X11: `QT_QPA_PLATFORM=xcb` та `DISPLAY=:0`

### Помилка підключення до API
- Переконайтесь що `screwdrive-api.service` працює
- Перевірте URL API в touchdesk.py (за замовчуванням: `http://localhost:5000`)
