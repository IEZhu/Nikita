# Nikita

**English summary.** Nikita is a service for 1C:Enterprise administrators that parses 1C event
logs (the registration log, `.lgf`/`.lgp` text and `.lgd` SQLite formats) and exports them to
ClickHouse for SQL analytics, with optional Solr full-text search and Redis buffering. It runs as a
Windows service or a Linux systemd daemon, auto-discovers infobases in 1C server clusters, resumes
from saved read positions, and offers a web monitoring panel, a JSON API and an optional MCP
endpoint for read-only ClickHouse queries. Python 3.10+, GPLv3. The documentation below is in Russian.

**Высокопроизводительный сервис парсинга и экспорта журналов регистрации 1С:Предприятие в ClickHouse, Solr и Redis.**

Работает как служба Windows или демон Linux (systemd). Автоматически обнаруживает базы, парсит логи (`.lgf`/`.lgd`) и отправляет данные в хранилища.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-lightgrey.svg)](https://github.com/)
[![License](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

---

## 🚀 Быстрый старт

### Linux

```bash
# 1. Клонирование и установка зависимостей
sudo git clone https://github.com/your-org/Nikita.git /opt/Nikita
cd /opt/Nikita && sudo python3 -m venv venv && sudo venv/bin/pip install -r requirements.lin

# 2. Настройка прав (смените пользователя на владельца 1С)
sudo chown -R usr1cv8:grp1cv8 /opt/Nikita

# 3. Конфигурация
sudo cp env.example .env && sudo nano .env  # Настройте C1_SRVINFO_PATH

# 4. Запуск службы
sudo ln -s /opt/Nikita/Nikita.service /etc/systemd/system/Nikita.service
sudo systemctl daemon-reload && sudo systemctl enable --now Nikita
```

**Веб-панель:** `http://localhost:8984/`

### Windows

1. Соберите дистрибутив (см. **[BUILD.md](BUILD.md)**) и используйте инсталлятор из папки `dist/`.
2. Запустите инсталлятор от имени Администратора.
3. Настройте `.env` в папке установки (`C:\Program Files\Nikita\.env`).
4. Запустите службу через `services.msc` или `Nikita.exe start`.

Подробнее: **[docs/admin/installation.md](docs/admin/installation.md)**

---

## 📚 Документация

Вся документация находится в папке `docs/`.

### 🛠️ Для Администраторов

*   **[Установка](docs/admin/installation.md)** — подробный гайд (Linux/Windows).
*   **[Конфигурация](docs/admin/configuration.md)** — описание всех параметров `.env`.
*   **[Эксплуатация](docs/admin/monitoring.md)** — мониторинг, веб-панель, API.
*   **[Решение проблем](docs/admin/troubleshooting.md)** — FAQ и типичные ошибки.
*   **[Скрипты](docs/admin/scripts.md)** — вспомогательные инструменты.

### 💻 Для Разработчиков

*   **[Начало работы](docs/dev/contributing.md)** — как собрать проект и внести вклад.
*   **[Внутреннее устройство](docs/dev/internals.md)** — архитектура и описание модулей.
*   **[Технические детали](docs/dev/clickhouse-schema.md)** — схемы БД, форматы логов.
*   **[Стиль кода](docs/dev/code-style.md)** — стандарты разработки.
*   **[Сборка](BUILD.md)** — создание дистрибутива.

---

## Особенности

*   **Автообнаружение баз 1С**: сканирует кластеры, находит новые базы на лету.
*   **ClickHouse**: сжатие 10-20x, SQL-аналитика, высокая скорость вставки.
*   **Solr / Redis**: опциональная поддержка полнотекстового поиска и буферизации.
*   **Отказоустойчивость**: защита от падений, сохранение позиции чтения (SQLite state).

---

## Лицензия

GNU GPLv3. См. [LICENSE](LICENSE).
