#!/usr/bin/env python3

# комплексный мониторинг даст полную картину о состоянии сервера.
# ОЗУ (RAM)
# Диска
# Сетевой активности (входящий/исходящий трафик)
# MySQL

# Поддерживать персистентное состояние, чтобы не слал уведомления слишком часто?

# Поведение:
# Если, например, CPU превысит 50% — будет 1 сообщение.
# Пока он остаётся >50% — ничего не присылается.
# Как только нагрузка спадает ниже — Telegram-бот напишет, что всё в порядке.

# При запуске считывает state.json, чтобы "помнить", о чём уже уведомлял.
# После каждой проверки сохраняет файл заново.

# Добавим сохранение и восстановление состояния state в JSON-файл, который будет лежать рядом со скриптом.
# Создаётся файл state.json в той же директории, где находится cpu_monitor_bot.py.
# Состояние (cpu, ram, disk, net, mysql) сохраняется при каждом цикле.
# При запуске — загружается из файла, если он существует.

# добавить ограничение частоты уведомлений, чтобы бот отправлял алерты не чаще 1 раза в час, даже если превышения происходят чаще.
# Добавим файл last_alert_time.json, в котором хранится время последней отправки.
# При каждом цикле проверяем: прошло ли 60 минут с последнего уведомления.
# Если прошло — отправляем алерты.
# Алерты о превышении должны быть не чаще одного раза в час, но уведомления о возвращении в норму — приходят сразу.

import psutil
import requests
import time
import logging
import subprocess
import json
import os
import datetime
from datetime import time as dtime
from dotenv import load_dotenv

# === Загрузка переменных из .env ===
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# === Настройки ===
CHECK_INTERVAL = 10  # минут
CPU_THRESHOLD = 90
RAM_THRESHOLD = 90
DISK_THRESHOLD = 90
NET_THRESHOLD_MB = 2000
MYSQL_CONN_THRESHOLD = 60

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, 'monitor.log')
STATE_FILE = os.path.join(SCRIPT_DIR, 'state.json')
ALERT_STATE_FILE = os.path.join(SCRIPT_DIR, 'last_alert_time.json')
ACCUMULATOR_FILE = os.path.join(SCRIPT_DIR, 'accumulator.json')

ALERT_INTERVAL_MINUTES = 60
ACCUMULATE_DURATION = 60
ACCUMULATE_LIMIT = int(ACCUMULATE_DURATION / CHECK_INTERVAL)
ACCUMULATE_THRESHOLD = int(ACCUMULATE_LIMIT * 0.9)

DEFAULT_STATE = {'cpu': False, 'ram': False, 'disk': False, 'net': False, 'mysql': False}

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def is_quiet_time():
    now = datetime.datetime.now().time()
    return dtime(23, 0) <= now or now <= dtime(7, 0)

def load_last_alert_time():
    if os.path.exists(ALERT_STATE_FILE):
        try:
            with open(ALERT_STATE_FILE, 'r') as f:
                ts = json.load(f).get('last_alert_time')
                if not ts:
                    return datetime.datetime.min
                try:
                    return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%f")
                except ValueError:
                    return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
        except:
            return datetime.datetime.min
    return datetime.datetime.min

def save_last_alert_time(dt=None):
    if dt is None:
        dt = datetime.datetime.now()
    with open(ALERT_STATE_FILE, 'w') as f:
        json.dump({'last_alert_time': dt.isoformat()}, f)

def send_telegram_message(message):
    if is_quiet_time():
        logging.info("⏰ Тихий режим: уведомление не отправлено.")
        return
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message}
    try:
        response = requests.post(url, data=payload, timeout=10)
        if response.ok:
            logging.info("✅ Уведомление отправлено")
        else:
            logging.error(f"❌ Ошибка Telegram API: {response.text}")
    except Exception as e:
        logging.error(f"❌ Ошибка при отправке уведомления: {e}")

def get_mysql_total_connections():
    try:
        cmd = '/usr/bin/mysql -e "SHOW STATUS LIKE \'Threads_connected\';"'
        output = subprocess.check_output(cmd, shell=True, universal_newlines=True)
        lines = output.strip().split('\n')
        if len(lines) >= 2:
            return int(lines[1].split('\t')[1])
    except Exception as e:
        logging.error(f"MySQL ошибка: {e}")
    return -1

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return {**DEFAULT_STATE, **json.load(f)}
        except:
            pass
    return DEFAULT_STATE.copy()

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def load_accumulator():
    if os.path.exists(ACCUMULATOR_FILE):
        try:
            with open(ACCUMULATOR_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {k: [] for k in DEFAULT_STATE}

def save_accumulator(data):
    with open(ACCUMULATOR_FILE, 'w') as f:
        json.dump(data, f)

def should_alert(acc):
    result = []
    if len([x for x in acc['cpu'] if x > CPU_THRESHOLD]) >= ACCUMULATE_THRESHOLD:
        result.append('cpu')
    if len([x for x in acc['ram'] if x > RAM_THRESHOLD]) >= ACCUMULATE_THRESHOLD:
        result.append('ram')
    if len([x for x in acc['disk'] if x > DISK_THRESHOLD]) >= ACCUMULATE_THRESHOLD:
        result.append('disk')
    if len([x for x in acc['mysql'] if x > MYSQL_CONN_THRESHOLD]) >= ACCUMULATE_THRESHOLD:
        result.append('mysql')
    if len([1 for sent, recv in acc['net'] if sent > NET_THRESHOLD_MB or recv > NET_THRESHOLD_MB]) >= ACCUMULATE_THRESHOLD:
        result.append('net')
    return result

def get_exceeded_details(acc, exceeded_keys):
    details = []
    if 'cpu' in exceeded_keys and acc['cpu']:
        avg = sum(acc['cpu']) / len(acc['cpu'])
        details.append(f"- cpu (среднее: {avg:.2f}%)")
    if 'ram' in exceeded_keys and acc['ram']:
        avg = sum(acc['ram']) / len(acc['ram'])
        details.append(f"- ram (среднее: {avg:.2f}%)")
    if 'disk' in exceeded_keys and acc['disk']:
        avg = sum(acc['disk']) / len(acc['disk'])
        details.append(f"- disk (среднее: {avg:.2f}%)")
    if 'mysql' in exceeded_keys and acc['mysql']:
        avg = sum(acc['mysql']) / len(acc['mysql'])
        details.append(f"- mysql (среднее подключений: {avg:.2f})")
    if 'net' in exceeded_keys and acc['net']:
        sent_avg = sum([x[0] for x in acc['net']]) / len(acc['net'])
        recv_avg = sum([x[1] for x in acc['net']]) / len(acc['net'])
        details.append(f"- net (↑ {sent_avg:.2f} MB, ↓ {recv_avg:.2f} MB)")
    return details

def monitor():
    net_io_prev = psutil.net_io_counters()
    state = load_state()
    accumulator = load_accumulator()
    send_telegram_message("🔍 Серверный мониторинг запущен")

    while True:
        cpu = psutil.cpu_percent(interval=1)
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage('/').percent
        net_io = psutil.net_io_counters()
        sent_mb = (net_io.bytes_sent - net_io_prev.bytes_sent) / (1024 * 1024)
        recv_mb = (net_io.bytes_recv - net_io_prev.bytes_recv) / (1024 * 1024)
        net_io_prev = net_io
        mysql_conn = get_mysql_total_connections()

        # Сохраняем реальные значения
        accumulator['cpu'].append(cpu)
        accumulator['ram'].append(ram)
        accumulator['disk'].append(disk)
        accumulator['mysql'].append(mysql_conn if mysql_conn >= 0 else 0)
        accumulator['net'].append((sent_mb, recv_mb))

        for k in ['cpu', 'ram', 'disk', 'mysql']:
            accumulator[k] = accumulator[k][-ACCUMULATE_LIMIT:]
        accumulator['net'] = accumulator['net'][-ACCUMULATE_LIMIT:]

        # Проверка накопленных данных
        exceeded = should_alert(accumulator)
        now = datetime.datetime.now()
        minutes_since_last = (now - load_last_alert_time()).total_seconds() / 60

        if exceeded and minutes_since_last >= ALERT_INTERVAL_MINUTES:
            message = "⚠️ Превышения за последний час:\n" + "\n".join(get_exceeded_details(accumulator, exceeded))
            send_telegram_message(message)
            save_last_alert_time()
        elif exceeded:
            logging.info("Превышения найдены, но уведомление было недавно.")

        save_accumulator(accumulator)
        save_state(state)
        time.sleep(CHECK_INTERVAL * 60)

if __name__ == '__main__':
    logging.info("Мониторинг запущен.")
    monitor()
