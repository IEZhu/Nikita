# -*- coding: utf-8 -*-
import  threading
import  time
import  re
import  requests
import  datetime
import  traceback
from    typing              import  List, Dict, Any, Optional
from    clickhouse_driver   import  Client                  as  ch

from    src.tools           import  tools                   as  t
from    src                 import  globals                 as  g
from    src.redis_manager   import  queue

COLUMN_NAMES                                                 =   (
                                                                    "date",
                                                                    "date_idx",
                                                                    "t_status",
                                                                    "t_id",
                                                                    "t_pos",
                                                                    "user_name",
                                                                    "user_guid",
                                                                    "computer",
                                                                    "app",
                                                                    "connect",
                                                                    "event",
                                                                    "severity",
                                                                    "comment",
                                                                    "meta_name",
                                                                    "meta_uuid",
                                                                    "data",
                                                                    "data_pres",
                                                                    "server",
                                                                    "port",
                                                                    "port_sec",
                                                                    "session",
                                                                    "area",
                                                                    "area_sec",
                                                                    "file_name",
                                                                    "file_pos",
                                                                )
FILE_NAME_INDEX                                             =   COLUMN_NAMES.index("file_name")
FILE_POS_INDEX                                              =   COLUMN_NAMES.index("file_pos")
BASE_NAME_RE                                                =   re.compile(r'^[a-zA-Z0-9_а-яА-ЯёЁ]+$')

# Поля строгой схемы ядра Solr (g.conf.solr.schema), которые project_solr_doc
# копирует в Solr-документ как есть. Запись parser.add_to_json_data двухцелевая:
# вложенные/расшифрованные поля нужны ClickHouse, но в Solr нельзя слать unknown
# field или вложенный Map - иначе HTTP 400, поэтому перед POST запись проецируется.
# NB: финальный документ = эти поля ПЛЮС добавляемые в project_solr_doc поля
# comment/data/data_pres (из *_raw) и расплющенные user_guid/meta_uuid; они тоже
# объявлены в схеме (globals.py) - при изменении схемы синхронизируй оба места.
SOLR_DOC_FIELDS                                            =   (
                                                                    "id",
                                                                    "file_name",
                                                                    "pos",
                                                                    "len",
                                                                    "date",
                                                                    "date_idx",
                                                                    "t_status",
                                                                    "t_id",
                                                                    "t_pos",
                                                                    "user_id",
                                                                    "comp_id",
                                                                    "app_id",
                                                                    "conn_id",
                                                                    "event_id",
                                                                    "severity",
                                                                    "meta_id",
                                                                    "server_id",
                                                                    "port_id",
                                                                    "port_sec_id",
                                                                    "session_id",
                                                                    "area_id",
                                                                    "area_sec_id",
                                                                )
RETRIABLE_SENDER_STATUSES                                  =   {404, 409, 429}
CLICKHOUSE_DEDUPE_LOOKUP_CHUNK_SIZE                        =   100
CLICKHOUSE_DEDUPE_LOOKUP_MAX_LITERAL_CHARS                  =   60000

def is_non_retriable_solr_status(status):
    return (
        status is not None
        and 400 <= status < 500
        and status not in RETRIABLE_SENDER_STATUSES
    )

# ======================================================================================================================
# Утилиты для отправки данных
# ======================================================================================================================

# ----------------------------------------------------------------------------------------------------------------------
# Экранирование спецсимволов для ClickHouse
# ----------------------------------------------------------------------------------------------------------------------
def escape_clickhouse(s: str) -> str:
    return (
        s
        .replace("\\", r"\\")     # обратный слеш
        .replace("'", r"\'")      # одинарная кавычка
        .replace("\n", r"\n")     # переход строки
        .replace("\r", r"\r")     # возврат каретки
    )

def row_identity(row):
    return (str(row[FILE_NAME_INDEX]), int(row[FILE_POS_INDEX]))

def chunk_clickhouse_key_literals(keys):
    chunk                                                   =   []
    literal_chars                                           =   0
    for file_name, file_pos in keys:
        literal                                             =   f"('{escape_clickhouse(file_name)}', {file_pos})"
        next_size                                           =   literal_chars + len(literal) + (1 if chunk else 0)
        if chunk and (len(chunk) >= CLICKHOUSE_DEDUPE_LOOKUP_CHUNK_SIZE or next_size > CLICKHOUSE_DEDUPE_LOOKUP_MAX_LITERAL_CHARS):
            yield chunk
            chunk                                           =   []
            literal_chars                                   =   0
            next_size                                       =   len(literal)
        chunk.append(literal)
        literal_chars                                       =   next_size
    if chunk:
        yield chunk

def get_existing_clickhouse_keys(chclient: Any, rows: List[tuple], base_name: str) -> set:
    keys                                                    =   []
    seen                                                    =   set()
    for row in rows:
        key                                                 =   row_identity(row)
        if key not in seen:
            keys.append(key)
            seen.add(key)

    existing                                                =   set()
    if not keys:
        return existing

    for key_literals_chunk in chunk_clickhouse_key_literals(keys):
        key_literals                                        =   ",".join(key_literals_chunk)
        query                                               =   f"""
            SELECT file_name, file_pos
            FROM {g.conf.clickhouse.database}.`{base_name}`
            WHERE (file_name, file_pos) IN ({key_literals})
        """
        for file_name, file_pos in chclient.execute(query):
            existing.add((str(file_name), int(file_pos)))
    return existing

# ----------------------------------------------------------------------------------------------------------------------
# Учёт ошибки ClickHouse в статистике панели; та же ошибка подряд не вытесняет другие из last_errors
# ----------------------------------------------------------------------------------------------------------------------
def note_clickhouse_error(error_msg: str) -> None:
    now                                                     =   datetime.datetime.now()
    g.stats.clickhouse_total_errors                         +=  1
    g.stats.clickhouse_last_error_time                      =   now
    g.stats.clickhouse_last_error_msg                       =   error_msg
    g.stats.clickhouse_connection_ok                        =   False
    if g.stats.last_errors and g.stats.last_errors[-1][1:] == ("ClickHouse", error_msg):
        return
    g.stats.last_errors.append((now, "ClickHouse", error_msg))
    if len(g.stats.last_errors)                             >   10:
        g.stats.last_errors.pop(0)

# ----------------------------------------------------------------------------------------------------------------------
# Подключение к ClickHouse, проверенное SELECT 1; None, если сервер недоступен
# ----------------------------------------------------------------------------------------------------------------------
def connect_clickhouse(logger_name: str, database: Optional[str] = None) -> Any:
    params                                                  =   {
                                                                    "host"      :   g.conf.clickhouse.host,
                                                                    "port"      :   g.conf.clickhouse.port,
                                                                    "user"      :   g.conf.clickhouse.user,
                                                                    "password"  :   g.conf.clickhouse.password,
                                                                }
    if database:
        params["database"]                                  =   database
    try:
        client                                              =   ch(**params)
        client.execute('SELECT 1')
    except Exception as e:
        t.debug_print(f"✗ ClickHouse: Ошибка подключения: {str(e)}", logger_name)
        note_clickhouse_error(f"Подключение: {str(e)}")
        return None
    t.debug_print(f"✓ ClickHouse: Подключение успешно установлено", logger_name)
    return client

# ----------------------------------------------------------------------------------------------------------------------
# Отправка пакета данных в ClickHouse
# ----------------------------------------------------------------------------------------------------------------------
def send_to_clickhouse(chclient: Any, data: List[Dict[str, Any]], base_name: str, logger_name: str) -> bool:
    if not chclient:
        if g.conf.clickhouse.enabled:
            t.debug_print(f"✗ CLICKHOUSE: Нет подключения, пачка из {len(data)} записей для базы {base_name} не отправлена", logger_name)
            g.stats.clickhouse_connection_ok                =   False
            return False                                                                                                # без связи пачка не считается отправленной: state не двигается
        t.debug_print(f"ClickHouse не настроен, пропускаем отправку {len(data)} записей для базы {base_name}", logger_name)
        return True                                                                                                     # Если CH не настроен, считаем отправку успешной
    
    # Валидация имени базы (таблицы) для защиты от инъекций в F-строке
    # Разрешаем только латиницу, кириллицу, цифры и подчеркивание
    if not isinstance(base_name, str) or not BASE_NAME_RE.match(base_name):
        t.debug_print(f"✗ CLICKHOUSE: Недопустимое имя базы/таблицы: '{base_name}'. Пропуск отправки.", logger_name)
        return False

    start_time                                              =   time.time()
    
    try:
        t.debug_print(f"→ CLICKHOUSE: Начинаем отправку пакета для базы '{base_name}' (записей: {len(data)})", logger_name)
        
        rows                                                =   []
        for rec in data:
            date_str                                        =   f"{rec['date'][0:4]}-{rec['date'][4:6]}-{rec['date'][6:8]} {rec['date'][8:10]}:{rec['date'][10:12]}:{rec['date'][12:14]}"
            dt                                              =   datetime.datetime.fromisoformat(date_str)
            
            row                                             =   (
                                                                    dt,                                                 # date DateTime
                                                                    dt,                                                 # date_idx DateTime (дублируем, как в оригинале)
                                                                    rec['t_status'],                                    # t_status
                                                                    rec['t_id'],                                        # t_id
                                                                    rec['t_pos'],                                       # t_pos
                                                                    rec['user']['name'],                                # user_name
                                                                    rec['user']['uuid'],                                # user_guid
                                                                    rec['computer'],                                    # computer
                                                                    rec['app'],                                         # app
                                                                    int(rec['connect']),                                # connect (теперь Int64)
                                                                    rec['event'],                                       # event
                                                                    rec['severity_val'],                                # severity
                                                                    rec['comment'],                                     # comment
                                                                    rec['metadata']['name'],                            # meta_name
                                                                    rec['metadata']['uuid'],                            # meta_uuid
                                                                    str(rec['data']),                                   # data
                                                                    str(rec['data_pres']),                              # data_pres
                                                                    str(rec['server']),                                 # server
                                                                    int(rec['port']),                                   # port
                                                                    int(rec['port_sec']),                               # port_sec
                                                                    int(rec['session']),                                # session
                                                                    int(rec['area']),                                   # area
                                                                    int(rec['area_sec']),                               # area_sec
                                                                    str(rec['file_name']),                              # file_name
                                                                    int(rec['pos'])                                     # file_pos
                                                                )
            rows.append(row)
        
        if rows:
            unique_rows                                     =   []
            seen_in_batch                                   =   set()
            for row in rows:
                key                                         =   row_identity(row)
                if key in seen_in_batch:
                    continue
                unique_rows.append(row)
                seen_in_batch.add(key)

            existing_keys                                   =   get_existing_clickhouse_keys(chclient, unique_rows, base_name)
            rows_to_insert                                  =   [
                                                                    row
                                                                    for row in unique_rows
                                                                    if row_identity(row) not in existing_keys
                                                                ]
            duplicate_count                                 =   len(rows) - len(unique_rows)
            already_sent_count                              =   len(unique_rows) - len(rows_to_insert)
            if duplicate_count:
                t.debug_print(f"CLICKHOUSE: Пропущено {duplicate_count} дублей внутри batch для {base_name}", logger_name)
            if already_sent_count:
                t.debug_print(f"CLICKHOUSE: Пропущено {already_sent_count} уже отправленных записей для {base_name}", logger_name)

            if rows_to_insert:
                column_list                                 =   ", ".join(COLUMN_NAMES)
                query                                       =   f"INSERT INTO {g.conf.clickhouse.database}.`{base_name}` ({column_list}) VALUES"
                chclient.execute(query, rows_to_insert)
            else:
                t.debug_print(f"CLICKHOUSE: Новых записей для {base_name} нет", logger_name)

            elapsed_time                                    =   time.time() - start_time
            
            # Обновляем статистику успешной отправки
            g.stats.clickhouse_total_sent                   +=  len(rows_to_insert)
            g.stats.clickhouse_last_success_time            =   datetime.datetime.now()
            g.stats.clickhouse_connection_ok                =   True
            
            rate                                            =   len(rows_to_insert) / elapsed_time if elapsed_time else 0
            t.debug_print(f"✓ CLICKHOUSE: Успешно отправлено {len(rows_to_insert)} записей в таблицу {g.conf.clickhouse.database}.{base_name}", logger_name)
            t.debug_print(f"✓ CLICKHOUSE: Время выполнения: {elapsed_time:.3f} сек ({rate:.1f} записей/сек)", logger_name)
            t.debug_print(f"✓ CLICKHOUSE: Всего отправлено за сессию: {g.stats.clickhouse_total_sent} записей", logger_name)
        return True

    except Exception as e:
        elapsed_time                                        =   time.time() - start_time
        error_msg                                           =   str(e)
        
        # Обновляем статистику ошибок
        g.stats.clickhouse_total_errors                     +=  1
        g.stats.clickhouse_last_error_time                  =   datetime.datetime.now()
        g.stats.clickhouse_last_error_msg                   =   error_msg
        g.stats.clickhouse_connection_ok                    =   False
        
        # Добавляем в список последних ошибок
        error_entry                                         =   (datetime.datetime.now(), "ClickHouse", f"{base_name}: {error_msg}")
        g.stats.last_errors.append(error_entry)
        if len(g.stats.last_errors)                         >   10:
            g.stats.last_errors.pop(0)
        
        t.debug_print(f"✗ CLICKHOUSE: Ошибка отправки в {g.conf.clickhouse.database}.{base_name}: {error_msg}", logger_name)
        t.debug_print(f"✗ CLICKHOUSE: Время до ошибки: {elapsed_time:.3f} сек", logger_name)
        t.debug_print(f"✗ CLICKHOUSE: Всего ошибок за сессию: {g.stats.clickhouse_total_errors}", logger_name)
        t.debug_print(f"✗ CLICKHOUSE: Traceback:\n{traceback.format_exc()}", logger_name)
        return False

# ----------------------------------------------------------------------------------------------------------------------
# Проекция записи в документ Solr (только поля схемы; вложенные dict-и расплющиваются)
# ----------------------------------------------------------------------------------------------------------------------
def project_solr_doc(rec: Dict[str, Any]) -> Dict[str, Any]:
    doc                                                     =   {f: rec[f] for f in SOLR_DOC_FIELDS if f in rec}
    # Текстовые поля индексируем по сырым значениям (без ClickHouse-экранирования кавычек/слешей)
    doc["comment"]                                          =   rec.get("comment_raw", rec.get("comment", ""))
    doc["data"]                                             =   rec.get("data_raw", rec.get("data", ""))
    doc["data_pres"]                                        =   rec.get("data_pres_raw", rec.get("data_pres", ""))
    # Расплющиваем вложенные {"uuid","name"} в строковые поля схемы
    user                                                    =   rec.get("user")
    if isinstance(user, dict):
        doc["user_guid"]                                    =   user.get("uuid", "")
    meta                                                    =   rec.get("metadata")
    if isinstance(meta, dict):
        doc["meta_uuid"]                                    =   meta.get("uuid", "")
    return doc

# ----------------------------------------------------------------------------------------------------------------------
# Отправка пакета данных в Solr
# ----------------------------------------------------------------------------------------------------------------------
def send_to_solr(url: str, data: List[Dict[str, Any]], logger_name: str) -> int:
    start_time                                              =   time.time()

    try:
        t.debug_print(f"→ SOLR: Отправка пакета на {url} (записей: {len(data)})", logger_name)
        solr_docs                                           =   [project_solr_doc(rec) for rec in data]                 # только поля схемы → нет HTTP 400 на unknown field
        response                                            =   requests.post(url=url, json=solr_docs, timeout=30)
        status_code                                         =   response.status_code
        elapsed_time                                        =   time.time() - start_time
        
        if status_code                                      ==  200:
            # Обновляем статистику успешной отправки
            g.stats.solr_total_sent                         +=  len(data)
            g.stats.solr_last_success_time                  =   datetime.datetime.now()
            g.stats.solr_connection_ok                      =   True
            
            t.debug_print(f"✓ SOLR: Успешно отправлено {len(data)} записей на {url}", logger_name)
            t.debug_print(f"✓ SOLR: Время выполнения: {elapsed_time:.3f} сек ({len(data)/elapsed_time:.1f} записей/сек)", logger_name)
            t.debug_print(f"✓ SOLR: Всего отправлено за сессию: {g.stats.solr_total_sent} записей", logger_name)
        else:
            # Тело ответа читаем один раз: оно нужно и для детекта "ядро ещё грузится",
            # и ниже для лога ошибки.
            try:
                response_text                               =   response.text
            except Exception:
                response_text                               =   ""
            # Solr ещё поднимается: ядро ещё не создано -> /update отдаёт 404 (или 503).
            # Пока Solr не помечен запущенным (g.execution.solr.started), это ожидаемо:
            # не засоряем last_errors/solr_total_errors; статус retriable - пачка
            # повторится и уйдёт, когда ядро будет создано.
            #
            # Отдельный случай гонки: 503 с телом "SolrCore is loading". Признак
            # g.execution.solr.started выставляется по ответу 200 от /admin/ping, но
            # обработчик /update у ещё грузящегося ядра продолжает отдавать 503. Ответ
            # на уже отправленный POST может прийти ПОСЛЕ started=True (видно в debug-логе
            # старта), и тогда проверка "not started" его пропускала и засчитывала как
            # ошибку сервиса. Такой 503 всегда транзиентный - трактуем как "ещё не готов".
            core_is_loading                                 =   status_code == 503 \
                                                                and "solrcore is loading" in response_text.lower()
            if core_is_loading \
               or (status_code in (404, 503) and not getattr(g.execution.solr, "started", False)):
                t.debug_print(f"SOLR: ядро ещё не готово (HTTP {status_code}), повтор позже: {url}", logger_name)
                # Наблюдаемость застрявшего ядра: solr_total_errors/last_errors не трогаем
                # (иначе вернётся шум на каждый рестарт), но текущее здоровье помечаем not-ok.
                # На штатной гонке старта ближайший успешный POST вернёт флаг в True; у реально
                # залипшего "SolrCore is loading" ядра он останется False и будет виден на дашборде.
                if core_is_loading:
                    g.stats.solr_connection_ok                  =   False
                return status_code
            # Обновляем статистику ошибок
            g.stats.solr_total_errors                       +=  1
            g.stats.solr_last_error_time                    =   datetime.datetime.now()
            g.stats.solr_last_error_msg                     =   f"HTTP статус: {status_code}"
            g.stats.solr_connection_ok                      =   False
            
            # Добавляем в список последних ошибок
            error_entry                                     =   (datetime.datetime.now(), "Solr", f"HTTP {status_code}: {url}")
            g.stats.last_errors.append(error_entry)
            if len(g.stats.last_errors)                     >   10:
                g.stats.last_errors.pop(0)
            
            t.debug_print(f"✗ SOLR: Ошибка отправки, статус: {status_code}", logger_name)
            if response_text:
                t.debug_print(f"✗ SOLR: ответ сервера: {response_text[:2000]}", logger_name)                            # тело ответа Solr - точная формулировка ошибки
            t.debug_print(f"✗ SOLR: Всего ошибок за сессию: {g.stats.solr_total_errors}", logger_name)
        
        return status_code
        
    except Exception as e:
        elapsed_time                                        =   time.time() - start_time
        error_msg                                           =   str(e)
        
        # Solr ещё поднимается (или перезапускается): отказ в соединении на этом этапе -
        # ожидаемое состояние, а не ошибка. Пока Solr не помечен запущенным
        # (g.execution.solr.started), не засоряем last_errors/solr_total_errors и
        # возвращаем retriable 503 - пачка повторится и уйдёт, когда Solr поднимется.
        if isinstance(e, requests.exceptions.ConnectionError) and not getattr(g.execution.solr, "started", False):
            t.debug_print(f"SOLR: сервер ещё не готов, повтор позже: {error_msg[:200]}", logger_name)
            return 503

        # Обновляем статистику ошибок
        g.stats.solr_total_errors                           +=  1
        g.stats.solr_last_error_time                        =   datetime.datetime.now()
        g.stats.solr_last_error_msg                         =   error_msg
        g.stats.solr_connection_ok                          =   False
        
        # Добавляем в список последних ошибок
        error_entry                                         =   (datetime.datetime.now(), "Solr", f"{url}: {error_msg}")
        g.stats.last_errors.append(error_entry)
        if len(g.stats.last_errors)                         >   10:
            g.stats.last_errors.pop(0)
        
        t.debug_print(f"✗ SOLR: Исключение при отправке: {error_msg}", logger_name)
        t.debug_print(f"✗ SOLR: Время до ошибки: {elapsed_time:.3f} сек", logger_name)
        t.debug_print(f"✗ SOLR: Всего ошибок за сессию: {g.stats.solr_total_errors}", logger_name)
        return 500

# ----------------------------------------------------------------------------------------------------------------------
# Универсальная функция отправки (Диспетчер)
# ----------------------------------------------------------------------------------------------------------------------
def post_query(chclient, solr_url, data, base_name, logger_name, bypass_redis=False):
    ret_ok                                                  =   200
    ret_err                                                 =   500
    non_solr_failure                                        =   False
    solr_status                                             =   None
    
    # 1. Если Redis включен и мы не обходим его (т.е. мы не Sender thread)
    if g.conf.redis.enabled and not bypass_redis:
        t.debug_print("→ REDIS: Отправка в очередь Redis...", logger_name)
        
        try:
            if queue.push(data, base_name):
                # Обновляем статистику Redis
                g.stats.redis_total_queued                  +=  len(data)
                g.stats.redis_last_success_time             =   datetime.datetime.now()
                g.stats.redis_connection_ok                 =   True
                
                t.debug_print("✓ REDIS: Успешно помещено в очередь", logger_name)
                return ret_ok
            else:
                # Обновляем статистику ошибок Redis
                g.stats.redis_total_errors                  +=  1
                g.stats.redis_last_error_time               =   datetime.datetime.now()
                g.stats.redis_last_error_msg                =   "Ошибка добавления в очередь"
                g.stats.redis_connection_ok                 =   False
                
                error_entry                                 =   (datetime.datetime.now(), "Redis", "Ошибка добавления в очередь")
                g.stats.last_errors.append(error_entry)
                if len(g.stats.last_errors)                 >   10:
                    g.stats.last_errors.pop(0)
                
                t.debug_print("✗ REDIS: Ошибка, переключение на прямую отправку", logger_name)
        except Exception as e:
            # Обновляем статистику ошибок Redis
            g.stats.redis_total_errors                      +=  1
            g.stats.redis_last_error_time                   =   datetime.datetime.now()
            g.stats.redis_last_error_msg                    =   str(e)
            g.stats.redis_connection_ok                     =   False
            
            error_entry                                     =   (datetime.datetime.now(), "Redis", str(e))
            g.stats.last_errors.append(error_entry)
            if len(g.stats.last_errors)                     >   10:
                g.stats.last_errors.pop(0)
            
            t.debug_print(f"✗ REDIS: Исключение: {str(e)}, переключение на прямую отправку", logger_name)
    
    # 2. Прямая отправка (или если Redis недоступен/обойден)
    success                                                 =   True
    sent_to_any                                             =   False
    
    # ClickHouse
    if g.conf.clickhouse.enabled:
        if send_to_clickhouse(chclient, data, base_name, logger_name):
            sent_to_any                                     =   True
        else:
            success                                         =   False
            non_solr_failure                                =   True
    
    # Solr (только если включен и URL задан)
    if solr_url and g.conf.solr.enabled:
        solr_status                                         =   send_to_solr(solr_url, data, logger_name)
        if solr_status                                      ==  200:
            sent_to_any                                     =   True
        else:
            success                                         =   False
            t.debug_print(f"✗ Solr: Отправка не удалась (статус: {solr_status})", logger_name)
    
    if not sent_to_any and not g.conf.clickhouse.enabled and not g.conf.solr.enabled:
        success                                             =   False
        t.debug_print("⚠ ВНИМАНИЕ: Ни ClickHouse, ни Solr не настроены! Данные никуда не отправлены.", logger_name)
    
    if success:
        return ret_ok
    if non_solr_failure:
        if is_non_retriable_solr_status(solr_status):
            t.debug_print(
                f"Non-Solr failure keeps payload retriable before dead-lettering Solr status {solr_status}",
                logger_name
            )
        return ret_err
    if is_non_retriable_solr_status(solr_status):
        return solr_status
    if solr_status and solr_status != 200:
        return solr_status
    return ret_err

# ======================================================================================================================
# Поток отправки данных из очереди Redis
# ======================================================================================================================
class sender_thread(threading.Thread):
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Инициализация
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def __init__(self, name):
        threading.Thread.__init__(self)
        self.name                                           =   name
        self.stop_signal                                    =   False
        self.chclient                                       =   None
        
        if g.conf.clickhouse.enabled:
            t.debug_print(f"Sender: Подключение к ClickHouse...", self.name)
            self.chclient                                   =   connect_clickhouse(self.name, database=g.conf.clickhouse.database)

        t.debug_print("Thread initialized", self.name)

    def stop_on_queue_error(self, action):
        t.debug_print(f"Redis {action} failed. Sender stops to avoid unknown queue state.", self.name)
        self.stop_signal                                    =   True

    def move_payload_to_dead(self, payload, reason):
        t.debug_print(f"Redis payload {reason}; moving to dead letter", self.name)
        if not queue.move_to_dead(payload):
            self.stop_on_queue_error("dead-letter move")
            return False
        return True

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Основной цикл
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def run(self):
        t.debug_print("Sender thread started", self.name)
        while not self.stop_signal:
            if not g.conf.redis.enabled:
                time.sleep(5)
                continue

            payload                                             =   None
            try:
                # Читаем из очереди
                base_name, data, payload                     =   queue.pop(timeout=2)
                
                if not payload:
                    continue
                if not isinstance(base_name, str) or not base_name or not isinstance(data, list):
                    self.move_payload_to_dead(payload, "has invalid base or data")
                    continue
                if not BASE_NAME_RE.match(base_name):
                    self.move_payload_to_dead(payload, f"has invalid target '{base_name}'")
                    continue
                if not data:
                    t.debug_print(f"Empty Redis payload for {base_name}; acking", self.name)
                    if not queue.ack(payload):
                        self.stop_on_queue_error("ack")
                    continue

                t.debug_print(f"Got {len(data)} records for {base_name} from Redis", self.name)

                solr_url                                    =   f"{g.execution.solr.url_main}/{base_name}/update?wt=json"
                if g.conf.clickhouse.enabled and not self.chclient:                                                     # подключение могло не удаться при старте Sender
                    self.chclient                           =   connect_clickhouse(self.name, database=g.conf.clickhouse.database)

                ret_code                                    =   post_query(
                                                                    self.chclient,
                                                                    solr_url,
                                                                    data,
                                                                    base_name,
                                                                    self.name,
                                                                    bypass_redis=True
                                                                )

                if ret_code                                 !=  200:
                    t.debug_print(f"Failed to send data (code {ret_code}).", self.name)
                    if is_non_retriable_solr_status(ret_code):
                        self.move_payload_to_dead(payload, f"failed with non-retriable status {ret_code}")
                    elif not queue.requeue(payload):
                        self.stop_on_queue_error("requeue")
                    time.sleep(1)
                else:
                    if not queue.ack(payload):
                        self.stop_on_queue_error("ack")
            except Exception as e:
                t.debug_print(f"Sender loop exception: {str(e)}", self.name)
                if payload:
                    if not queue.requeue(payload):
                        self.stop_on_queue_error("requeue")
                time.sleep(1)

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Остановка потока
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def stop(self):
        self.stop_signal                                    =   True
