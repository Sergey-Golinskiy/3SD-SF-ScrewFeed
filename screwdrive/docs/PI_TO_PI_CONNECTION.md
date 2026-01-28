# Подключение Pi 5 (Master) к Pi 5 (XY Table)

Данное руководство описывает как подключить две Raspberry Pi 5 через UART для управления координатным столом.

## Схема подключения

```
┌─────────────────────┐                    ┌─────────────────────┐
│   Pi 5 (MASTER)     │                    │   Pi 5 (XY TABLE)   │
│                     │                    │                     │
│  GPIO14 (TXD) ──────┼────────────────────┼──► GPIO15 (RXD)     │
│  GPIO15 (RXD) ◄─────┼────────────────────┼──── GPIO14 (TXD)    │
│  GND ───────────────┼────────────────────┼──── GND             │
│                     │                    │                     │
└─────────────────────┘                    └─────────────────────┘
```

**ВАЖНО:** TX одной Pi подключается к RX другой и наоборот!

## Распиновка GPIO для UART

| Pin # | Название | Функция |
|-------|----------|---------|
| 8 | GPIO14 | TXD (передача) |
| 10 | GPIO15 | RXD (приём) |
| 6 | GND | Земля |

```
                    Raspberry Pi 5 GPIO Header

                    3V3  (1) (2)  5V
                  GPIO2  (3) (4)  5V
                  GPIO3  (5) (6)  GND  ◄── Соединить
                  GPIO4  (7) (8)  GPIO14 (TXD) ◄── TX
                    GND  (9) (10) GPIO15 (RXD) ◄── RX
                  GPIO17 (11) (12) GPIO18
                  ...
```

## Физическое подключение

### Вариант 1: Прямые провода (для близкого расположения)

Используйте 3 провода (dupont female-female):

| Master Pi 5 | XY Table Pi 5 |
|-------------|---------------|
| Pin 8 (TXD) | Pin 10 (RXD) |
| Pin 10 (RXD) | Pin 8 (TXD) |
| Pin 6 (GND) | Pin 6 (GND) |

### Вариант 2: Длинный кабель (>1 метр)

Для расстояний больше 1 метра рекомендуется использовать:
- Экранированный кабель
- USB-UART адаптеры (CP2102 или FT232)

**С USB-UART адаптерами:**

```
┌─────────────┐     ┌──────────┐         ┌──────────┐     ┌─────────────┐
│ Master Pi 5 │     │ USB-UART │         │ USB-UART │     │ XY Table    │
│             │     │ Adapter  │         │ Adapter  │     │ Pi 5        │
│ USB ────────┼─────┤          │◄─USB───►│          ├─────┼──── USB     │
│             │     │ TX ──────┼─────────┼── RX     │     │             │
│             │     │ RX ◄─────┼─────────┼── TX     │     │             │
│             │     │ GND ─────┼─────────┼── GND    │     │             │
└─────────────┘     └──────────┘         └──────────┘     └─────────────┘
```

При использовании USB адаптеров порты будут `/dev/ttyUSB0` вместо `/dev/ttyAMA0`.

## Настройка UART на обеих Pi

### На обеих Raspberry Pi выполните:

```bash
# 1. Запустите raspi-config
sudo raspi-config

# 2. Выберите: Interface Options -> Serial Port
#    - Login shell accessible over serial: NO
#    - Serial port hardware enabled: YES

# 3. Перезагрузите
sudo reboot
```

### Альтернативно через config.txt:

```bash
sudo nano /boot/firmware/config.txt
```

Добавьте в конец:
```ini
# UART Configuration
enable_uart=1
dtoverlay=uart0
```

```bash
sudo reboot
```

## Проверка подключения

### 1. Проверьте наличие UART устройства

```bash
# На обеих Pi
ls -la /dev/ttyAMA0
# Должно показать: crw-rw---- 1 root dialout ... /dev/ttyAMA0
```

### 2. Добавьте пользователя в группу dialout

```bash
sudo usermod -aG dialout $USER
# Перелогиньтесь или перезагрузите
```

### 3. Тест с помощью minicom

**На XY Table Pi:**
```bash
# Запустите xy_cli в serial режиме
python3 xy_cli.py --serial /dev/ttyAMA0 --baud 115200
```

**На Master Pi:**
```bash
# Установите minicom
sudo apt install minicom

# Подключитесь к UART
minicom -D /dev/ttyAMA0 -b 115200

# Введите команду (нажмите Enter после)
PING
# Должен ответить: PONG

# Для выхода: Ctrl+A, затем X
```

### 4. Тест через Python

**На Master Pi:**
```python
import serial
import time

# Открываем порт
ser = serial.Serial('/dev/ttyAMA0', 115200, timeout=1)
time.sleep(0.5)

# Очищаем буфер
ser.reset_input_buffer()

# Отправляем команду
ser.write(b'PING\n')
ser.flush()

# Читаем ответ
time.sleep(0.1)
response = ser.readline().decode().strip()
print(f"Response: {response}")  # Должно быть: PONG

ser.close()
```

## Конфигурация системы

### На XY Table Pi

Создайте сервис автозапуска:

```bash
sudo nano /etc/systemd/system/xy_table.service
```

```ini
[Unit]
Description=XY Table Serial Controller
After=multi-user.target

[Service]
Type=simple
User=pi
ExecStart=/usr/bin/python3 /home/pi/3SD-SF-ScrewFeed/xy_cli.py --serial /dev/ttyAMA0 --baud 115200
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable xy_table.service
sudo systemctl start xy_table.service
```

### На Master Pi

Отредактируйте настройки:

```bash
sudo nano /etc/screwdrive/settings.yaml
```

```yaml
xy_table:
  mode: "serial"
  serial_port: "/dev/ttyAMA0"  # или /dev/ttyUSB0 для USB адаптера
  serial_baud: 115200
  command_timeout_s: 30.0
```

## Проверка после настройки

### 1. Статус сервиса XY стола

```bash
# На XY Table Pi
sudo systemctl status xy_table.service

# Должно показать: Active: active (running)
```

### 2. Проверка через API

```bash
# На Master Pi
curl http://localhost:5000/api/xy/status

# Ответ должен быть:
# {"connected": true, "state": "READY", "ready": true, "position": {"x": 0, "y": 0}}
```

### 3. Тест движения

```bash
# Хоминг
curl -X POST http://localhost:5000/api/xy/home

# Движение в позицию
curl -X POST http://localhost:5000/api/xy/move \
     -H "Content-Type: application/json" \
     -d '{"x": 100, "y": 200, "feed": 10000}'
```

## Устранение неполадок

### Нет связи между Pi

1. **Проверьте провода:** TX↔RX перекрещены правильно?
2. **Проверьте GND:** Земля соединена?
3. **Проверьте UART:** `ls /dev/ttyAMA0` работает на обеих Pi?

### Ошибка "Permission denied"

```bash
# Добавьте пользователя в группу
sudo usermod -aG dialout $USER

# Или временно измените права
sudo chmod 666 /dev/ttyAMA0
```

### Мусор в данных

1. Убедитесь что скорость (baud rate) одинаковая на обеих Pi
2. Проверьте длину кабеля - для длинных кабелей снизьте скорость до 9600

### XY Table Pi не отвечает

```bash
# Проверьте логи
sudo journalctl -u xy_table.service -f

# Перезапустите сервис
sudo systemctl restart xy_table.service
```

## Диагностика

### Просмотр Serial трафика

```bash
# На Master Pi - просмотр отправляемых данных
stty -F /dev/ttyAMA0 115200 raw -echo
cat /dev/ttyAMA0 &

# В другом терминале отправьте команду
echo "PING" > /dev/ttyAMA0
```

### Тест loopback (замыкание TX-RX)

Для проверки что UART работает, замкните TX и RX на одной Pi:

```bash
# Замкните pin 8 и pin 10 на одной Pi
minicom -D /dev/ttyAMA0 -b 115200
# Всё что вводите должно отображаться
```

## Дополнительные настройки

### Отключение Bluetooth для освобождения UART

По умолчанию на Pi 5 UART может быть занят Bluetooth:

```bash
sudo nano /boot/firmware/config.txt
```

Добавьте:
```ini
dtoverlay=disable-bt
```

```bash
sudo systemctl disable hciuart
sudo reboot
```

### Увеличение буфера UART

Для больших объёмов данных:

```bash
# Увеличьте размер буфера приёма
stty -F /dev/ttyAMA0 115200 raw -echo min 0 time 5
```
