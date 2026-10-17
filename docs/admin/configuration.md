# Конфигурация Nikita

Полное руководство по настройке службы Nikita через переменные окружения (файл `.env`).

---

## Содержание

- [Общие принципы](#общие-принципы)
- [Настройка баз данных 1С](#настройка-баз-данных-1с)
- [Настройка хранилищ данных](#настройка-хранилищ-данных)
- [Настройка веб-сервера](#настройка-веб-сервера)
- [MCP-эндпоинт](#mcp-эндпоинт)
- [Настройка парсера](#настройка-парсера)
- [Отладка](#отладка)
- [Примеры конфигураций](#примеры-конфигураций)
- [Таблица всех параметров](#таблица-всех-параметров)

---

## Общие принципы

### Расположение файла конфигурации

- **Linux:** `/opt/Nikita/.env`
- **Windows:** `C:\Program Files\Nikita\.env` (или каталог установки)

### Формат файла

```ini
# Комментарии начинаются с #
ПАРАМЕТР=значение

# Строковые значения можно указывать без кавычек
PATH=/home/user/data

# Или с кавычками (если содержат пробелы)
PATH="/home/user/my data"

# Логические значения: true/false (регистр не важен)
ENABLED=true
ENABLED=True
ENABLED=TRUE
```

### Применение изменений

После редактирования `.env` необходимо перезапустить службу:

**Linux:**
```bash
sudo systemctl restart Nikita
```

**Windows:**
```powershell
net stop Nikita && net start Nikita
```

---

## Настройка баз данных 1С

### Автоматическое обнаружение (рекомендуется)

Укажите корневой путь к базам 1С — служба автоматически найдёт все базы в кластерах:

```ini
# Linux
C1_SRVINFO_PATH=/home/usr1cv8/.1cv8/1C/1cv8

# Windows
C1_SRVINFO_PATH=C:\Users\usr1cv8\AppData\Roaming\1C\1cv8
```

**Как работает:**
- Nikita сканирует подкаталоги `reg_*` в указанном пути
- Автоматически определяет UUID баз и пути к журналам
- Обновляет список баз при добавлении/удалении
- Определяет формат журнала (`.lgf` или `.lgd`) автоматически

### Ручное указание баз

Если автодетект не подходит, укажите базы вручную:

```ini
# База #0
IBASE_0=PROD_ZUP_LOCAL         # Имя базы (произвольное)
IBASE_0_JR=/path/to/1Cv8Log    # Путь к каталогу журнала
IBASE_0_FORMAT=lgd             # Формат: lgf (старый) или lgd (SQLite)

# База #1
IBASE_1=TEST_UT11
IBASE_1_JR=/path/to/another/1Cv8Log
IBASE_1_FORMAT=lgf

# База #2
IBASE_2=...
```

**Правила:**
- Нумерация начинается с `0` и идёт последовательно (0, 1, 2, ...)
- Для каждой базы обязательны 3 параметра: `IBASE_N`, `IBASE_N_JR`, `IBASE_N_FORMAT`
- Формат `lgf` — старый текстовый формат журналов
- Формат `lgd` — новый формат на базе SQLite (1С:Предприятие 8.3.12+)

### Комбинированный режим

Можно использовать автодетект + ручные базы одновременно:

```ini
# Автодетект для стандартных баз
C1_SRVINFO_PATH=/home/usr1cv8/.1cv8/1C/1cv8

# Дополнительная база вне стандартного пути
IBASE_0=EXTERNAL_BASE
IBASE_0_JR=/mnt/external/bases/base1/1Cv8Log
IBASE_0_FORMAT=lgf
```

---

## Настройка хранилищ данных

### ClickHouse (рекомендуется)

Колоночное хранилище для аналитики и длительного хранения.

```ini
# Включение (true/false)
CLICKHOUSE_ENABLED=true

# Параметры подключения
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=9000
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=

# База данных (создаётся автоматически)
CLICKHOUSE_DATABASE=zhr1c
```

**Особенности:**
- База данных и таблицы создаются автоматически при первом запуске
- Для каждой базы 1С создаётся отдельная таблица
- Применяется максимальное сжатие (кодек ZSTD)
- TTL по умолчанию: 365 дней (можно изменить в коде)

**Пример имени таблицы:** `zhr1c.PROD_ZUP`

### Solr (опционально)

Полнотекстовый поиск по содержимому журналов.

```ini
# Включение
SOLR_ENABLED=false

# Параметры подключения
SOLR_HOST=127.0.0.1
SOLR_PORT=8983

# Настройки Solr-сервера (если запускается Nikita)
SOLR_DIR=/path/to/solr
SOLR_LISTEN_INTERFACE=127.0.0.1
SOLR_LISTEN_PORT=8983

# Память для Solr JVM
SOLR_MEM_MIN=2g
SOLR_MEM_MAX=32g

# Java 17 встроена в дистрибутив (<каталог установки>/java) — отдельная переменная не требуется

# Потоки для индексации
SOLR_THREADS=12
```

**Примечание:** Если Solr уже запущен отдельно, достаточно указать `SOLR_HOST`, `SOLR_PORT` и `SOLR_ENABLED=true`.

### Redis (опционально)

Буферизация данных при недоступности основных хранилищ.

```ini
# Включение
REDIS_ENABLED=false

# Параметры подключения
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
```

**Как работает:**
- Если ClickHouse/Solr недоступны, данные сохраняются в очередь Redis
- При восстановлении подключения данные автоматически отправляются
- Гарантирует отсутствие потери данных при сбоях

**Примечание:** Redis не является обязательным. Без Redis служба работает в режиме прямой отправки.

---

## Настройка веб-сервера

Встроенный HTTP-сервер для мониторинга и API.

```ini
# Интерфейс для прослушивания
HTTP_LISTEN_INTERFACE=0.0.0.0  # 0.0.0.0 = все интерфейсы
                                # 127.0.0.1 = только localhost

# Порт
HTTP_LISTEN_PORT=8984
```

**Адреса после запуска:**
- Веб-панель: `http://localhost:8984/`
- JSON API: `http://localhost:8984/stats_api`

**Безопасность:**
- По умолчанию HTTP-сервер доступен со всех интерфейсов (`0.0.0.0`)
- Для доступа только с локального компьютера: `HTTP_LISTEN_INTERFACE=127.0.0.1`
- Для защиты в продакшн рекомендуется использовать reverse proxy (nginx, Apache)

---

## MCP-эндпоинт

`POST /mcp` — сервер [Model Context Protocol](https://modelcontextprotocol.io/) внутри Nikita с одним
инструментом `query`: произвольный читающий SQL к ClickHouse. Nikita сам отправляет запрос, читает результат
потоком (не больше `max_rows` строк) и возвращает колонки и строки. Поддерживаются ревизии протокола
`2026-07-28` и `2025-03-26`…`2025-11-25`; ответ всегда один JSON (без SSE и без сессий).

```ini
MCP_ENABLED=true
# отдельный пользователь ClickHouse только на чтение; без него MCP не включается
MCP_CLICKHOUSE_USER=nikita_mcp
MCP_CLICKHOUSE_PASSWORD=секрет
# потолок max_rows инструмента query
MCP_MAX_ROWS=10000
# разрешённые заголовки Origin через запятую; запрос с другим Origin получает 403
MCP_ALLOWED_ORIGINS=
```

**Безопасность — обязательно:**
- В Nikita нет авторизации. Публикуйте `/mcp` только за reverse proxy с аутентификацией и TLS,
  а Nikita слушайте на `HTTP_LISTEN_INTERFACE=127.0.0.1`.
- Запрет записи обеспечивает ClickHouse, а не Nikita: пользователь `MCP_CLICKHOUSE_USER` должен
  иметь `readonly=1` и `SELECT` только на базу журнала.
- Ограничивайте размер тела запроса на прокси (`LimitRequestBody 65536` в примере ниже): лимит Nikita
  в 64 KiB работает только для `application/json`, тела других типов CherryPy разбирает раньше.
- Нефинитные числа из ClickHouse (`nan`, `inf`) возвращаются как `null` — в JSON их нет.

Пример пользователя ClickHouse:

```xml
<clickhouse>
    <profiles>
        <nikita_mcp>
            <readonly>1</readonly>
            <max_execution_time>60</max_execution_time>
            <max_result_rows>100000</max_result_rows>
            <result_overflow_mode>break</result_overflow_mode>
        </nikita_mcp>
    </profiles>
    <users>
        <nikita_mcp>
            <password_sha256_hex>...</password_sha256_hex>
            <networks><ip>10.0.0.10/32</ip></networks>
            <profile>nikita_mcp</profile>
            <grants><query>GRANT SELECT ON zhr1c.*</query></grants>
        </nikita_mcp>
    </users>
</clickhouse>
```

**Пример Apache (Basic auth):**

```apache
<Location /nikita/mcp>
    AuthType Basic
    AuthName "Nikita MCP"
    AuthBasicProvider file
    AuthUserFile /etc/apache2/nikita.htpasswd
    Require valid-user
    LimitRequestBody 65536
    ProxyPass http://127.0.0.1:8984/mcp timeout=120
</Location>
```

**Подключение из Claude Code:**

```bash
claude mcp add --transport http nikita https://host.example/nikita/mcp \
  --header "Authorization: Basic $(printf '%s' 'user:password' | base64)"
```

---

## Настройка парсера

```ini
# Количество потоков парсера
PARSER_THREADS=0  # 0 = автоматически (по числу ядер CPU)
                  # 1-N = точное количество потоков
```

**Рекомендации:**
- Оставьте `0` для автоматического определения (рекомендуется)
- Для мощных серверов можно указать явно (например, `4` или `8`)
- Не рекомендуется устанавливать больше количества баз 1С

---

## Отладка

```ini
# Глобальная отладка
DEBUG_ENABLED=false  # Включается/отключается через веб-интерфейс

# Отладка парсера (подробные логи)
DEBUG_PARSER=false
```

**Логи сохраняются в:**
- **Linux:** `/opt/Nikita/debug/Nikita.<PID>.log`
- **Windows:** `C:\Program Files\Nikita\debug\Nikita.<PID>.log`

**Просмотр логов:**

**Linux:**
```bash
# Все логи
tail -f /opt/Nikita/debug/Nikita.*.log

# Только ошибки
tail -f /opt/Nikita/debug/Nikita.*.log | grep "✗"

# Только ClickHouse
tail -f /opt/Nikita/debug/Nikita.*.log | grep "CLICKHOUSE"
```

**Windows:**
```powershell
Get-Content "C:\Program Files\Nikita\debug\Nikita.*.log" -Tail 50 -Wait
```

**Примечание:** `DEBUG_ENABLED` управляется галочкой "🐛 Отладка" на веб-панели.

---

## Примеры конфигураций

### Минимальная конфигурация (только ClickHouse)

```ini
# Базы 1С
C1_SRVINFO_PATH=/home/usr1cv8/.1cv8/1C/1cv8

# ClickHouse
CLICKHOUSE_ENABLED=true
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=9000
CLICKHOUSE_DATABASE=zhr1c

# Веб-сервер
HTTP_LISTEN_PORT=8984

# Отладка
DEBUG_ENABLED=false
DEBUG_PARSER=false
```

### Полная конфигурация (все компоненты)

```ini
# ============================================================
# Настройка баз данных 1С
# ============================================================

# Автодетект
C1_SRVINFO_PATH=/home/usr1cv8/.1cv8/1C/1cv8

# Дополнительная база
IBASE_0=EXTERNAL_BASE
IBASE_0_JR=/mnt/external/1Cv8Log
IBASE_0_FORMAT=lgf

# ============================================================
# ClickHouse
# ============================================================
CLICKHOUSE_ENABLED=true
CLICKHOUSE_HOST=clickhouse-server.local
CLICKHOUSE_PORT=9000
CLICKHOUSE_USER=nikita_user
CLICKHOUSE_PASSWORD=SecurePassword123
CLICKHOUSE_DATABASE=zhr1c

# ============================================================
# Solr (полнотекстовый поиск)
# ============================================================
SOLR_ENABLED=true
SOLR_HOST=solr-server.local
SOLR_PORT=8983

# Если Solr управляется Nikita
SOLR_DIR=/opt/solr
SOLR_MEM_MIN=4g
SOLR_MEM_MAX=16g
SOLR_THREADS=8

# ============================================================
# Redis (буферизация)
# ============================================================
REDIS_ENABLED=true
REDIS_HOST=redis-server.local
REDIS_PORT=6379
REDIS_DB=0

# ============================================================
# HTTP-сервер мониторинга
# ============================================================
HTTP_LISTEN_INTERFACE=0.0.0.0
HTTP_LISTEN_PORT=8984

# ============================================================
# Парсер
# ============================================================
PARSER_THREADS=0  # автоматически

# ============================================================
# Отладка
# ============================================================
DEBUG_ENABLED=false
DEBUG_PARSER=false
```

### Конфигурация для разработки

```ini
# Базы 1С
C1_SRVINFO_PATH=/home/user/.1cv8/1C/1cv8

# ClickHouse (локальный)
CLICKHOUSE_ENABLED=true
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=9000
CLICKHOUSE_DATABASE=zhr1c_dev

# Без Solr и Redis
SOLR_ENABLED=false
REDIS_ENABLED=false

# Веб-сервер (только localhost)
HTTP_LISTEN_INTERFACE=127.0.0.1
HTTP_LISTEN_PORT=8984

# Отладка включена
DEBUG_ENABLED=true
DEBUG_PARSER=true

# Один поток для удобства отладки
PARSER_THREADS=1
```

---

## Таблица всех параметров

### Базы данных 1С

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `C1_SRVINFO_PATH` | string | - | Путь к корневому каталогу баз 1С для автодетекта |
| `IBASE_N` | string | - | Имя N-ной базы (N = 0, 1, 2, ...) |
| `IBASE_N_JR` | string | - | Путь к каталогу журнала N-ной базы |
| `IBASE_N_FORMAT` | enum | - | Формат журнала: `lgf` или `lgd` |

### ClickHouse

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `CLICKHOUSE_ENABLED` | boolean | `false` | Включить отправку в ClickHouse |
| `CLICKHOUSE_HOST` | string | `localhost` | Хост сервера ClickHouse |
| `CLICKHOUSE_PORT` | integer | `9000` | Порт сервера ClickHouse |
| `CLICKHOUSE_USER` | string | `default` | Пользователь БД |
| `CLICKHOUSE_PASSWORD` | string | ` ` | Пароль пользователя |
| `CLICKHOUSE_DATABASE` | string | `zhr1c` | Имя базы данных |

### Solr

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `SOLR_ENABLED` | boolean | `false` | Включить отправку в Solr |
| `SOLR_HOST` | string | `127.0.0.1` | Хост сервера Solr |
| `SOLR_PORT` | integer | `8983` | Порт сервера Solr |
| `SOLR_DIR` | string | - | Каталог установки Solr (если управляется Nikita) |
| `SOLR_LISTEN_INTERFACE` | string | `127.0.0.1` | Интерфейс для прослушивания Solr |
| `SOLR_LISTEN_PORT` | integer | `8983` | Порт для прослушивания Solr |
| `SOLR_MEM_MIN` | string | `2g` | Минимальная память JVM |
| `SOLR_MEM_MAX` | string | `32g` | Максимальная память JVM |
| `SOLR_THREADS` | integer | `12` | Потоки для индексации |

### Redis

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `REDIS_ENABLED` | boolean | `false` | Включить буферизацию через Redis |
| `REDIS_HOST` | string | `127.0.0.1` | Хост сервера Redis |
| `REDIS_PORT` | integer | `6379` | Порт сервера Redis |
| `REDIS_DB` | integer | `0` | Номер базы данных Redis |

### HTTP-сервер

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `HTTP_LISTEN_INTERFACE` | string | `0.0.0.0` | Интерфейс для прослушивания (0.0.0.0 = все) |
| `HTTP_LISTEN_PORT` | integer | `8984` | Порт веб-сервера |

### MCP

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `MCP_ENABLED` | boolean | `false` | Включает эндпоинт `/mcp` (нужен и `MCP_CLICKHOUSE_USER`) |
| `MCP_CLICKHOUSE_USER` | string | — | Пользователь ClickHouse для запросов MCP, только `readonly=1` |
| `MCP_CLICKHOUSE_PASSWORD` | string | — | Его пароль |
| `MCP_MAX_ROWS` | integer | `10000` | Потолок `max_rows` инструмента `query` |
| `MCP_ALLOWED_ORIGINS` | string | — | Разрешённые `Origin` через запятую |

### Парсер

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `PARSER_THREADS` | integer | `0` | Количество потоков парсера (0 = автоматически) |

### Отладка

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `DEBUG_ENABLED` | boolean | `false` | Глобальная отладка (управляется через веб-интерфейс) |
| `DEBUG_PARSER` | boolean | `false` | Подробное логирование парсера |

---

## Проверка конфигурации

После изменения конфигурации запустите службу в консольном режиме для проверки:

**Linux:**
```bash
/opt/Nikita/venv/bin/python /opt/Nikita/Nikita.py console
```

**Windows:**
```powershell
python Nikita.py console
```

**Что проверить:**
1. Базы 1С обнаружены (✓ Найдено баз: N)
2. ClickHouse подключен (🟢 или ✓)
3. Нет критических ошибок (✗)

Для выхода из консольного режима: `Ctrl+C`

---

## Помощь

Если возникли проблемы с конфигурацией:

1. Проверьте синтаксис `.env` (нет лишних пробелов, кавычек)
2. Проверьте права доступа к файлу `.env`
3. См. [Устранение неполадок](troubleshooting.md)

---

**Обновлено:** 2025-12-11  
**Версия:** 2.0.0

