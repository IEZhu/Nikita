# -*- coding: utf-8 -*-
import sys
import  threading
import  os
import  time
import  operator
import  requests
import  json
import  hashlib
import  traceback
from    dotenv              import load_dotenv
load_dotenv() 
# ======================================================================================================================
from    src.tools           import  tools                   as  t
from    src                 import  globals                 as  g
from    src.dictionaries    import  dictionary              as  d
from    src                 import  reader                  as  r
from    src.redis_manager   import  queue
from    src.state_manager   import  state_manager
from    datetime            import  datetime
import  src.messenger                                       as  m
from    src                 import  sender                  as  snd

# ClickHouse драйвер может отсутствовать в окружениях, где ClickHouse выключен (например Windows).
try:
    from    clickhouse_driver   import  Client              as  ch
except Exception:
    ch                                                      =   None
# ======================================================================================================================
# класс, умеющий разбирать записи ЖР 1с и отправлять их в Solr
# ======================================================================================================================
class parser(threading.Thread):
    json_data                                               =   {}                                                      # для хранения записей json внутри класса
    file_list_updater                                       =   None
    stopMe                                                  =   False
    chclient                                                =   None
    # ------------------------------------------------------------------------------------------------------------------
    # инициализация класса и создание дочернего потока filelistupdater'а
    # ------------------------------------------------------------------------------------------------------------------
    def __init__(self, name):
        threading.Thread.__init__(self)
        self.name                                           =   name + " parser"
        self.json_data[self.name]                           =   []
        
        if name != "sender_helper":
            self.files_list_updater                         =   self.files_list_updater_thread_class("list " + name)    # создаём поток в зависиомсти от вызываемого
            self.files_list_updater.start()
        else:
            self.files_list_updater                         =   None

        self.stopMe                                         =   False
        
        if g.conf.clickhouse.enabled:
            try:
                if ch is None:
                    t.debug_print("ClickHouse драйвер (clickhouse_driver) не доступен. ClickHouse будет выключен.", self.name)
                    self.chclient                           =   None
                    g.conf.clickhouse.enabled               =   False
                    # продолжаем работу парсера (например, Solr/Redis могут быть включены в другом окружении)
                t.debug_print(f"Подключение к ClickHouse: {g.conf.clickhouse.host}:{g.conf.clickhouse.port}, БД: {g.conf.clickhouse.database}", self.name)
                
                # Подключаемся без указания базы данных для её создания
                self.chclient                               =   ch(
                                                                    host=g.conf.clickhouse.host, 
                                                                    port=g.conf.clickhouse.port, 
                                                                    user=g.conf.clickhouse.user, 
                                                                    password=g.conf.clickhouse.password
                                                                )
                self.chclient.execute('SELECT 1')
                t.debug_print(f"✓ ClickHouse: Подключение успешно установлено", self.name)
                
                # Проверяем и создаём базу данных с максимальным сжатием
                try:
                    databases                               =   self.chclient.execute("SHOW DATABASES")
                    db_list                                 =   [db[0] for db in databases]
                    
                    if g.conf.clickhouse.database not in db_list:
                        t.debug_print(f"⚠ База данных '{g.conf.clickhouse.database}' не существует, создаём...", self.name)
                        # Создаём базу с движком Atomic и кодеком ZSTD для максимального сжатия
                        create_db_query                     =   f"""
                            CREATE DATABASE IF NOT EXISTS {g.conf.clickhouse.database}
                            ENGINE = Atomic
                            COMMENT 'База данных для журналов регистрации 1С с максимальным сжатием'
                        """
                        self.chclient.execute(create_db_query)
                        t.debug_print(f"✓ База данных '{g.conf.clickhouse.database}' успешно создана", self.name)
                    else:
                        t.debug_print(f"✓ База данных '{g.conf.clickhouse.database}' существует", self.name)
                except Exception as db_check_err:
                    t.debug_print(f"⚠ Ошибка при работе с базой данных: {str(db_check_err)}", self.name)
                
                # Переподключаемся к созданной базе данных
                self.chclient                               =   ch(
                                                                    host=g.conf.clickhouse.host, 
                                                                    port=g.conf.clickhouse.port, 
                                                                    user=g.conf.clickhouse.user, 
                                                                    password=g.conf.clickhouse.password,
                                                                    database=g.conf.clickhouse.database
                                                                )
                    
            except Exception as e:
                t.debug_print(f"✗ ClickHouse: Ошибка подключения: {str(e)}", self.name)
                self.chclient = None
        else:
            t.debug_print(f"ClickHouse отключен в конфигурации", self.name)
            self.chclient                                   =   None

        t.debug_print("Thread initialized", self.name)
    # ------------------------------------------------------------------------------------------------------------------
    # Запуск и работа класса
    # ------------------------------------------------------------------------------------------------------------------
    def run(self):
        if self.files_list_updater is None: return

        while True and not self.stopMe:
            try:
                # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                while (g.parser.ibases)                     ==  None:                                                   # ждём инициализации
                    time.sleep(g.waits.in_cycle_we_trust)
                # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                next_item                                   =   self.files_list_updater.pop_next_file()
                if next_item:                                                                                           # если есть над чем работать
                    self.parse_file(next_item[1] + next_item[2], next_item[0])                                          # обрабатываем очередной файл
                # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                time.sleep(g.waits.in_cycle_we_trust)
                # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
            except Exception as e:
                t.debug_print("Exception6 "+str(e),self.name)
    # ------------------------------------------------------------------------------------------------------------------
    # Остановка класса
    # ------------------------------------------------------------------------------------------------------------------
    def stop(self):
        self.stopMe                                         =   True
        try:
            if self.files_list_updater:
                self.files_list_updater.stop()
                self.files_list_updater.join(timeout=10)
        except Exception as e:
            t.debug_print(f"Exception while stopping filelist updater: {str(e)}", self.name)
    # ------------------------------------------------------------------------------------------------------------------
    # вложенный класс/поток для обновления имен файлов - это происходит асинхронно
    # ------------------------------------------------------------------------------------------------------------------
    # ------------------------------------------------------------------------------------------------------------------
    class files_list_updater_thread_class(threading.Thread):
        ibases_files                                        =   []                                                      # список обрабатываемых файлов
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        def __init__(self, name):
            threading.Thread.__init__(self)
            self.name                                       =   name
            self._stop_event                                =   threading.Event()
            self._lock                                      =   threading.Lock()
            self._queue                                     =   []
            t.debug_print("Thread initialized", self.name)
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        def stop(self):
            self._stop_event.set()
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        def pop_next_file(self):
            """
            Потокобезопасно извлекает следующий файл для обработки.
            Возвращает элемент вида [ibase_name, jr_dir + '/', filename] или None.
            """
            with self._lock:
                if self._queue and len(self._queue) > 0:
                    return self._queue.pop(0)
            return None
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        def run(self):                                                                                                  # обновляет глобальный список обрабатываемых файлов
            if (self.name.upper()).find('LGP')              >   0:                                                      # если старый формат ЖР
                regexp                                      =   g.rexp.is_lgP_file_re                                   # если старый формат ЖР
            if (self.name.upper()).find('LGD')              >   0:                                                      # если новый формат ЖР
                regexp                                      =   g.rexp.is_lgD_file_re                                   # если новый формат ЖР
            while not self._stop_event.is_set():
                if g.debug.on:
                     t.debug_print("Scanning for log files...", self.name)
                g.parser.ibases_lpf_files                   =   []
                local_list                                  =   []
                # Создаем копию списка баз с блокировкой
                with g.ibases_lock:
                    ibases_copy                             =   list(g.parser.ibases)
                
                for ibase in ibases_copy:                                                                               # по всем базам
                    total_files_or_recs_size                =   0
                    total_parsed                            =   0
                    files                                   =   [
                                                                    element for element
                                                                    in os.listdir(ibase[g.nms.ib.jr_dir])
                                                                    if os.path.isfile(os.path.join(ibase[g.nms.ib.jr_dir], element))
                                                                    and regexp.findall(element)
                                                                ]                                                       # получаю список всех файлов ЖР базы
                    for file in files:                                                                                  # по всем файлам
                        try:
                            full_name                       =   os.path.join(ibase[g.nms.ib.jr_dir], file)
                            # для старого формата - размер файла ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                            if (file.upper()).find('.LGP')  >   0:                                                      # если формат старый
                                this_file_size              =   os.stat(full_name).st_size
                            # для нового - количество записей ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                            if (file.upper()).find('.LGD')  >   0:                                                      # если формат новый
                                (max_row, min_row)          =   t.get_lgd_evens_count(full_name)
                                this_file_size              =   max_row + 1 - min_row
                            # прибавляю к общему для базы ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                            total_files_or_recs_size        +=  this_file_size
                            # добавляю файлы в списко для обработки только размер отличается ~~~~~~~~~~~~~~~~~~~~~~~~~~~
                            _state                          =   state_manager.get_file_state(full_name, ibase[g.nms.ib.name])
                            get_saved_size                  =   _state['filesizeread'] if _state else 0
                            if this_file_size               !=  get_saved_size:
                                ibase_file                  =   [
                                                                        ibase[g.nms.ib.name],
                                                                        ibase[g.nms.ib.jr_dir] + "/",
                                                                        file
                                                                ]                                                       # и кладу в общий массив с информацией о базе
                                local_list.append(ibase_file)
                            # общее для всех~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                            total_parsed                    +=  get_saved_size
                        except Exception as e:
                            t.debug_print(str(e),self.name)
                        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                    # обнуляю размер распарсенных файлов ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                    if files:                                                                                           # для многопоточности
                        ibase[g.nms.ib.parsed_size]         =   total_parsed                                            # устанавливаю актуальный размер распарсенного для базы
                        ibase[g.nms.ib.total_size]          =   total_files_or_recs_size                                # устанавливаю актуальный размер базы
                    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                if local_list:                                                                                          # для многопоточности
                    local_list.sort(key=operator.itemgetter(2), reverse=True)                                           # сортирую по убыванию
                    with self._lock:
                        self._queue                         =   local_list                                              # готовая очередь
                # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Безусловное ожидание для https://github.com/WonderMr/Journal2Ct/issues/48
                time.sleep(g.waits.parser_sleep_on_update_filelist)

    # ------------------------------------------------------------------------------------------------------------------
    # ------------------------------------------------------------------------------------------------------------------
    # добавление в parser.json_data данных из разобранной записи
    # ------------------------------------------------------------------------------------------------------------------
    def add_to_json_data(self, fj_rec, fj_dt_sort_add, fj_id, fj_pos, fj_size, fj_base, fj_retry=False):                # первый параметр - распарсенные поля записи ЖР
        cc                                                  =   0                                                       # маркер прогресса по полям: пишется в лог при исключении
        try:
            if(g.debug.on_parser):
                t.debug_print("adding to json "+str(fj_rec),self.name)
            local_json                                      =   {}                                                      # второй - порядок времени для сортировки одинаковых моментов
            cc                                              =   1 
            local_json['id']                                =   hashlib.sha256(f"{fj_base}|{fj_id}|{fj_pos}|{fj_size}".encode("utf-8")).hexdigest()
            local_json['file_name']                         =   fj_id                                                   # третий - имя файла
            local_json['pos']                               =   fj_pos                                                  # четвёртый - смещение
            local_json['len']                               =   fj_size                                                 # пятый - размер записи
            local_json['date']                              =   fj_rec[0]
            local_json['date_idx']                          =   fj_dt_sort_add
            local_json['t_status']                          =   fj_rec[1]                                               # сырая буква 1С (C/U/R/N); ДОЛЖНА совпадать с reader.trans_id() в Solr-запросе и read_lgp_data()
            local_json['t_id_hex']                          =   "0x" + fj_rec[2]
            local_json['t_id']                              =   int(int(fj_rec[2],16)/10000)                            # в десятичном формате
            cc = 2
            local_json['t_pos_hex']                         =   "0x" + fj_rec[3]
            cc = 3
            local_json['t_pos']                             =   int(fj_rec[3],16)
            cc = 4
            local_json['user']                              =   g.execution.c1_dicts.users[fj_base][fj_rec[4]] if int(fj_rec[4])>0\
                                                                else {"uuid":"","name":""}
            cc = 5
            local_json['computer']                          =   g.execution.c1_dicts.computers[fj_base][fj_rec[5]] if int(fj_rec[5])>0\
                                                                else "0"
            cc = 6
            if fj_rec[6] in g.execution.c1_dicts.applications[fj_base]:
                local_json['app']                           =   g.execution.c1_dicts.applications[fj_base][fj_rec[6]]
            elif g.execution.c1_dicts.actions[fj_base][fj_rec[8]] == '_$User$_.AuthenticationLock':                     # в событии блокировки почему-то нет клиента
                local_json['app']                           =   ""
            else:
                vocab                                       =   str(g.execution.c1_dicts.applications[fj_base])
                local_json['app']                           =   f"Not Found in Dictionary code = {fj_rec[6]}, vocab = {snd.escape_clickhouse(vocab)}"
            cc = 7
            local_json['connect']                           =   fj_rec[7]
            cc = 8
            local_json['event']                             =   g.execution.c1_dicts.actions[fj_base][fj_rec[8]]
            cc = 9
            local_json['severity_val']                      =   d.severity_full.get(fj_rec[9], fj_rec[9])
            cc = 10
            local_json['comment']                           =   fj_rec[10].replace("'","''").replace('\\','\\\\')
            cc = 11
            local_json['metadata']                          =   g.execution.c1_dicts.metadata[fj_base][fj_rec[11]] if not fj_rec[11]=="0"\
                                                                else {"uuid":"", "name":""}
            cc = 12
            local_json['data']                              =   fj_rec[12].replace("'","''").replace('\\','\\\\')
            cc = 13
            local_json['data_pres']                         =   fj_rec[13].replace("'","''").replace('\\','\\\\')
            cc = 14
            local_json['server']                            =   g.execution.c1_dicts.servers[fj_base][fj_rec[14]] if int(fj_rec[14])>0\
                                                                else ""
            cc = 15
            local_json['port']                              =   g.execution.c1_dicts.ports_main[fj_base][fj_rec[15]] if int(fj_rec[15])>0\
                                                                else "0"
            cc = 16
            local_json['port_sec']                          =   g.execution.c1_dicts.ports_add[fj_base][fj_rec[16]] if int(fj_rec[16])>0\
                                                                else '0'
            cc = 17
            local_json['session']                           =   fj_rec[17]
            
            # Explicit assignments (formerly loop r4..r17)
            local_json['user_id']                           =   fj_rec[4]
            local_json['comp_id']                           =   fj_rec[5]
            local_json['app_id']                            =   fj_rec[6]
            local_json['conn_id']                           =   fj_rec[7]
            local_json['event_id']                          =   fj_rec[8]
            local_json['severity']                          =   fj_rec[9]
            local_json['comment_raw']                       =   fj_rec[10]
            local_json['meta_id']                           =   fj_rec[11]
            local_json['data_raw']                          =   fj_rec[12]
            local_json['data_pres_raw']                     =   fj_rec[13]
            local_json['server_id']                         =   fj_rec[14]
            local_json['port_id']                           =   fj_rec[15]
            local_json['port_sec_id']                       =   fj_rec[16]
            local_json['session_id']                        =   fj_rec[17]

            if len(fj_rec) - 1                              >   g.rexp.sel_re_ext_nmb:
                local_json['area_id']                       =   d.get_main_area(fj_base, fj_rec[22], fj_rec[23])        # case 2020.05.21
                local_json['area']                          =   local_json['area_id']
                local_json['area_sec_id']                   =   d.get_add_area(fj_base, fj_rec[20], fj_rec[21])         # case 2020.05.21
                local_json['area_sec']                      =   local_json['area_sec_id']
            else:
                local_json['area_id']                       =   '0'                                                     # case 2020.05.21
                local_json['area']                          =   '0'
                local_json['area_sec_id']                   =   '0'                                                     # case 2020.05.21
                local_json['area_sec']                      =   '0'
            self.json_data[self.name].append(local_json)
            if(g.debug.on_parser):
                t.debug_print(json.dumps(local_json, indent=2), self.name)
        except KeyError as e:
            if not fj_retry:                                                                                            # 1С дописывает новые коды (пользователь/компьютер/событие/...) в словарь ЖР на ходу;
                t.debug_print(f"KeyError while add_to_json {str(e)} (cc={cc}): "                                        # снапшот словаря в памяти отстал от файла - перечитываем и повторяем один раз
                              f"перечитываю словарь {fj_base} и повторяю запись", self.name)
                d.read_ib_dictionary(fj_base)
                return self.add_to_json_data(fj_rec, fj_dt_sort_add, fj_id, fj_pos, fj_size, fj_base, fj_retry=True)
            t.debug_print(f"Exception while add_to_json {str(e)} (cc={cc}, rec={str(fj_rec)})\n"                        # ключа нет даже в свежем словаре - это уже не гонка, а битые данные
                          + traceback.format_exc(), self.name)
            t.graceful_shutdown(111)
            return False
        except Exception as e:
            t.debug_print(f"Exception while add_to_json {str(e)} (cc={cc}, rec={str(fj_rec)})\n"
                          + traceback.format_exc(), self.name)
            t.graceful_shutdown(111)
            return False
        return True
    
    # ------------------------------------------------------------------------------------------------------------------
    # отправка parser.json_data в solr и commit. До победного
    # ------------------------------------------------------------------------------------------------------------------
    def solr_post_json_data(self, spjd_base):
        start_time                                          =   time.time()
        attempts                                            =   0
        max_attempts                                        =   int(getattr(g.waits, 'solr_cycles', 10))
        max_attempts                                        =   max_attempts if max_attempts > 0 else 10
        if not self.json_data[self.name]:
            t.debug_print("Post skipped: no data", self.name)
            return True

        while attempts < max_attempts and not self.stopMe:
            attempts                                        +=  1
            try:
                #while not g.execution.solr.started:                                                                     # ждём, пока Solr проснётся
                #    t.debug_print("waiting for solr to start",self.name)
                #    time.sleep(g.waits.solr_wait_start)
                t.debug_print("Posting", self.name)
                # первый запрос ----------------------------------------------------------------------------------------
                spjd_post_url                               =   f"{g.execution.solr.url_main}/{spjd_base}/update?wt=json&commit=true"  # commit=true -> add+commit атомарно: документы durable и видимы ДО пометки state как committed
                spjd_ret_code                               =   snd.post_query(
                                                                    self.chclient,
                                                                    spjd_post_url,
                                                                    data      = self.json_data[self.name],
                                                                    base_name = spjd_base,
                                                                    logger_name = self.name,
                                                                    bypass_redis = True
                                                                )
                if spjd_ret_code                            !=  200:
                    if 400 <= spjd_ret_code < 500 and spjd_ret_code not in snd.RETRIABLE_SENDER_STATUSES:
                        t.debug_print(f"Post data returned non-retriable client error {str(spjd_ret_code)}", self.name)
                        break
                    t.debug_print(f"Post data returned {str(spjd_ret_code)}, retrying", self.name)
                    time.sleep(g.waits.solr_on_bad_send_to)
                    continue

                t.debug_print("Post data was sucesfully sended", self.name)
                del self.json_data[self.name][:]                                                                        # пачку отправили, обнуляем данные
                # попытка коммита --------------------------------------------------------------------------------------
                # spjd_commit_url                             =   g.execution.solr.url_main +  "/" \
                #                                            +   spjd_base + "/update?commit=true&waitSearcher=false"
                # spjd_ret_code                               =   self.get_query(spjd_commit_url)
                # пока всё не зайдёт
                
                # while spjd_ret_code                         !=  200:
                #    t.debug_print(f"Post commit returned {str(spjd_ret_code)}, retrying")
                #    time.sleep(g.waits.solr_on_bad_send_to)
                #    #spjd_ret_code                           =   self.get_query(spjd_commit_url)
                t.debug_print("Solr commit done inline via /update?commit=true", self.name)
                t.debug_print("Post took "+str(time.time()-start_time),self.name)
                return True
            except Exception as ee:
                error_message                               =   f"Ошибка при отправке в SOLR: {str(ee)}"
                t.debug_print(error_message, self.name)
                time.sleep(g.waits.solr_on_bad_send_to)

        t.debug_print(
            f"Post/commit не удались за {attempts} попыток. State не будет обновлён.",
            self.name
        )
        t.debug_print("Post took "+str(time.time()-start_time),self.name)
        return False

    def commit_json_data(self, pf_name, pf_base, file_state, batch_start_offset, records_to_log):
        data_hash                                           =   state_manager._data_hash(records_to_log)
        if state_manager.is_block_committed(
            pf_name,
            batch_start_offset,
            file_state['filesizeread'],
            records_to_log,
            pf_base,
            data_hash=data_hash
        ):
            t.debug_print(f"{pf_base}: block already committed, moving state without resend", self.name)
            del self.json_data[self.name][:]
        elif records_to_log:
            if not self.solr_post_json_data(pf_base):
                return False

        if not state_manager.log_committed_block(
            pf_name,
            batch_start_offset,
            file_state['filesizeread'],
            records_to_log,
            pf_base,
            data_hash=data_hash
        ):
            t.debug_print(f"{pf_base}: committed block was sent, but SQLite block log failed", self.name)
            return False
        if not state_manager.update_file_state(file_state['filename'], file_state['filesize'], file_state['filesizeread'], pf_base):
            t.debug_print(f"{pf_base}: committed block was sent, but SQLite file state update failed", self.name)
            return False
        return True
    # ------------------------------------------------------------------------------------------------------------------
    # проверка корректности записи через её чтение и разбор
    # ------------------------------------------------------------------------------------------------------------------
    def check_rec(cr_name,cr_pos,cr_len):
        f                                                   =   open(cr_name, 'rb')
        f.seek(cr_pos)
        data                                                =   f.read(cr_len).decode(encoding='UTF8')
        f.close()
        if g.rexp.my_re.match(data):
            return True
        else:
            t.debug_print('invalid record '+data)
            return False
    # ------------------------------------------------------------------------------------------------------------------
    # разбор и индексирование одного файла
    # ------------------------------------------------------------------------------------------------------------------
    def parse_file(self, pf_name, pf_base):                                                                             # обработка файла
        try:
            if g.conf.clickhouse.enabled and self.chclient:
                 # Создаем таблицу для базы с оптимальным сжатием и индексацией
                 try:
                     t.debug_print(f"Проверка существования таблицы {g.conf.clickhouse.database}.{pf_base}", self.name)
                     
                     # Используем ReplacingMergeTree с кодеком ZSTD; строгая защита от дублей делается перед INSERT.
                     create_table_query                      =   f"""
                         CREATE TABLE IF NOT EXISTS {g.conf.clickhouse.database}.`{pf_base}` (
                             date DateTime CODEC(DoubleDelta, ZSTD(3)),
                             date_idx DateTime CODEC(DoubleDelta, ZSTD(3)),
                             t_status String CODEC(ZSTD(3)),
                             t_id Int64 CODEC(ZSTD(3)),
                             t_pos Int64 CODEC(ZSTD(3)),
                             user_name String CODEC(ZSTD(3)),
                             user_guid String CODEC(ZSTD(3)),
                             computer String CODEC(ZSTD(3)),
                             app String CODEC(ZSTD(3)),
                             connect Int64 CODEC(ZSTD(3)),
                             event String CODEC(ZSTD(3)),
                             severity String CODEC(ZSTD(3)),
                             comment String CODEC(ZSTD(3)),
                             meta_name String CODEC(ZSTD(3)),
                             meta_uuid String CODEC(ZSTD(3)),
                             data String CODEC(ZSTD(3)),
                             data_pres String CODEC(ZSTD(3)),
                             server String CODEC(ZSTD(3)),
                             port Int32 CODEC(ZSTD(3)),
                             port_sec Int32 CODEC(ZSTD(3)),
                             session Int64 CODEC(ZSTD(3)),
                             area Int32 CODEC(ZSTD(3)),
                             area_sec Int32 CODEC(ZSTD(3)),
                             file_name String CODEC(ZSTD(3)),
                             file_pos UInt64 CODEC(ZSTD(3))
                         ) 
                         ENGINE = ReplacingMergeTree()
                         ORDER BY (file_name, file_pos)
                         PARTITION BY toYYYYMM(date)
                         SETTINGS index_granularity = 8192
                         COMMENT 'Журнал регистрации 1С с максимальным сжатием ZSTD (ReplacingMergeTree)'
                     """
                     self.chclient.execute(create_table_query)
                     t.debug_print(f"✓ Таблица {g.conf.clickhouse.database}.{pf_base} готова (ReplacingMergeTree + ZSTD)", self.name)
                 except Exception as e:
                     t.debug_print(f"✗ Ошибка создания таблицы {pf_base}: {str(e)}", self.name)

            if g.rexp.is_lgD_file_re.findall(pf_name):
                self.parse_lgd_file(pf_name, pf_base)                                                                    # обрабатываю новый формат ЖР
            if g.rexp.is_lgP_file_re.findall(pf_name):                                                                  # или старый формат ЖР
                self.parse_lgp_file(pf_name, pf_base)
        except Exception as e:
            import traceback
            t.debug_print("got parsefile exception on "+pf_base+" - "+pf_name+". Error is:"+str(e), self.name)
            t.debug_print("Traceback:\n"+traceback.format_exc(), self.name)
            return
    # ------------------------------------------------------------------------------------------------------------------
    # разбор и индексирование одного файла нового формата
    # ------------------------------------------------------------------------------------------------------------------
    def parse_lgd_file(self,pf_name,pf_base):                                                                           # обработка файла старого формата
        file_state                                          =   {}
        # Проверка наличия файла ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        if not os.path.exists(pf_name):
            t.debug_print("file not found "+pf_name,self.name)
            return
        # Готовлюсь к чтению ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        try:
            # получаем текущий статус парсинга ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
            (max_row,min_row)                               =   t.get_lgd_evens_count(pf_name)
            pf_size                                         =   max_row+1-min_row
            if(pf_size == 1):
                t.debug_print(pf_base + " has empty zhr.", self.name)
                return
            else:
                t.debug_print(pf_base+":pf_size = " + str(pf_size), self.name)
            file_state['filename']                          =   pf_name                                                 # локальная структура json с именем файла
            file_state['filesize']                          =   pf_size                                                 # локальная структура json с размером файла
            _state                                          =   state_manager.get_file_state(pf_name, pf_base)
            file_state['filesizeread']                      =   _state['filesizeread'] if _state else 0
            batch_start_offset                              =   file_state['filesizeread']
            # сообщим о начале обработки, при необходимости~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
            if int(file_state['filesizeread'])              <   int(file_state['filesize']):
                t.debug_print(pf_base+":processing " + pf_base + "@" + pf_name, self.name)
            # разбираем файл, при необходимости~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
            while int(file_state['filesizeread'])           <   int(file_state['filesize']):                            # пока количеством не дошёл до конца
                if os.path.exists(pf_name):                                                                             # guard: при сокращении ЖР 1С может пересоздать файл; без файла read_ib_dictionary уронит службу через graceful_shutdown(3), а ниже цикл и так мягко выйдет по "no rows was returned"
                    d.read_ib_dictionary(pf_base)                                                                       # перечитываем словарь перед каждым блоком, как в LGP-ветке: 1С дописывает новые коды в 1Cv8.lgd на ходу, со старым снапшотом свежая запись даёт KeyError
                plf_rowID                                   =   file_state['filesizeread'] + min_row - 1                # у нас же смещение может быть в обрезанных файлах!!!
                limit_delta                                 =   int(file_state['filesize'])\
                                                            -   int(file_state['filesizeread'])                         # https://github.com/WonderMr/Journal2Ct/issues/40
                limit_records                               =   g.parser.lgd_parse_records_limit \
                                                                if limit_delta>g.parser.lgd_parse_records_limit \
                                                                else limit_delta                                        # читаю не больше, чем то количество, о котором знаю
                t.debug_print(pf_base + ":plf_rowID = " + str(plf_rowID), self.name)
                # Используем параметризированный запрос для защиты от SQL injection
                plf_query                                   =   '''
                                                                  select 
                                                                    date,
                                                                    transactionStatus, 
                                                                    transactionDate,
                                                                    transactionID,
                                                                    userCode,
                                                                    computerCode,
                                                                    appCode,
                                                                    connectID,
                                                                    eventCode,
                                                                    severity,
                                                                    comment,
                                                                    metadataCodes,
                                                                    data,
                                                                    dataPresentation,
                                                                    workServerCode,
                                                                    primaryPortCode,
                                                                    secondaryPortCode,
                                                                    session,                                                                    
                                                                    rowID 
                                                                    from EventLog where rowID > ?
                                                                    ORDER BY rowID ASC LIMIT ?'''
                if g.debug.on_parser:
                    t.debug_print(pf_base+ ":run Query params: " + str(plf_rowID) + ", " + str(limit_records), self.name)
                ret                                         =   t.sqlite3_exec(pf_name, plf_query, (plf_rowID, limit_records))
                if ret:
                    result                                  =   []
                    rslt                                    =   {}
                    prev_date                               =   ""
                    r1_nmb                                  =   0
                    for rec                                 in ret:
                        rslt[0]                             =   r.reader.int_1c_time_to_old_zhr_time(rec[0],islgd=True) # Дата
                        if rslt[0]                          ==  prev_date:                                              # здесь трюк с сортировкой дат с одинаковым временем
                            r1_nmb                          +=  1
                        else:
                            r1_nmb                          =   0
                        prev_date                           =   rslt[0]
                        rslt[1]                             =   d.trans_state.get(str(rec[1]))                          # Статус Транзакции
                        rslt[2]                             =   format(rec[2], 'x')                                     # время транзакции в hex БЕЗ префикса 0x - add_to_json_data сам припишет "0x" (иначе t_id_hex = "0x0x…")
                        rslt[3]                             =   format(rec[3], 'x')                                     # ID транзакции в hex БЕЗ префикса 0x - по той же причине
                        for l_i in range (4,9):
                            rslt[l_i]                       =   str(rec[l_i])                                           # коды строками: словари ЖР ключуются str(code), add_to_json_data ждёт строки (как из LGP). 4-пользователь 5-компьютер 6-приложение 7-соединение 8-событие
                        rslt[9]                             =   d.severity.get(str(rec[9]))                             # 9 - Severity - Важность
                        rslt[10]                            =   "" if rec[10] is None else str(rec[10])                 # 10- комментарий (NULL-safe: add_to_json_data зовёт .replace())
                        rslt[11]                            =   str(rec[11]) if str(rec[11]).isdigit() else "0"         # 11- номер метаданных строкой ("0", а не int 0 - иначе проверка fj_rec[11]=="0" в add_to_json_data не срабатывает → KeyError)
                        rslt[12]                            =   r.reader.force_decode("" if rec[12] is None else str(rec[12])) # 12- данные (NULL-safe: force_decode упал бы на None)
                        rslt[13]                            =   "" if rec[13] is None else str(rec[13])                 # 13- представление данных (NULL-safe: add_to_json_data зовёт .replace())
                        for l_i in range (14,18):
                            rslt[l_i]                       =   str(rec[l_i])                                           # коды строками: 14-сервер 15-порт 16-доп.порт 17-сеанс (ключи словарей str, как из LGP)
                        r18_nmb                             =   rec[18]                                                 # сохраняю значение row_id #case 2020.05.21
                        rslt[18]                            =   '0'                                                     # заглушка #case 2020.05.21
                        rslt[19]                            =   '0'                                                     # заглушка #case 2020.05.21
                        result.append(rslt)
                        m.check_event(result[0],pf_base)
                        if not self.add_to_json_data(
                                                            result[0],
                                                            r1_nmb,
                                                            pf_name,
                                                            r18_nmb,
                                                            0,
                                                            pf_base
                                                    ):                                                                  # добавляю в json структуры, нумератор записей с одинаковой датой, имя файла, rowID записи и "" как размер #case 2020.05.21
                            t.debug_print("Exception : не удалось обработать запись " + str(result))
                        del result[:]
                    # отправили тысячу записей или все оставшиемся, высылаем в SOLR~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                    t.debug_print(pf_base+ ":commiting with state: \n"  +
                                  "total length     = "     +   str(file_state['filesize'])     + "\n" +
                                  "new curr.length  = "     +   str(
                                                                    file_state['filesizeread']  +
                                                                    len(self.json_data[self.name])
                                                                )                               + "\n" +
                                  "current length   = "     +   str(file_state['filesizeread']) + "\n" +
                                  "commited records = "     +   str(
                                                                    len(self.json_data[self.name])),
                                                                    self.name
                                                                )                                                       # add 2019.02.15 для https://github.com/WonderMr/Journal2Ct/issues/40
                    file_state['filesizeread']              =   int(file_state['filesizeread']) + \
                                                                len(self.json_data[self.name])
                    # сохраняем копию данных для логирования, так как solr_post_json_data очистит список
                    records_to_log                          =   list(self.json_data[self.name])
                    if not self.commit_json_data(
                        pf_name,
                        pf_base,
                        file_state,
                        batch_start_offset,
                        records_to_log
                    ):
                        return
                    batch_start_offset                      =   file_state['filesizeread']
                    parser.set_parsed_size(pf_base,file_state['filesizeread'])                                          # устанавливаю размер на количетво распарсенных данных
                else:                                                                                                   # в ret ничего не вернулось
                    t.debug_print("no rows was returned",self.name)
                    time.sleep(g.waits.read_state_exception)                                                            # чтобы не нагружать в цикле CPU на базах, которых не удалось прочитать файл. Например, отсутствующих на кластере
                    return
            # сохраняем текущий статус парсинга ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        except Exception as e:
            t.debug_print("parse lgd file "+pf_name+" on base "+ pf_base +" got exception:"+str(e),self.name)
    # ------------------------------------------------------------------------------------------------------------------
    # разбор и индексирование одного файла старого формата
    # ------------------------------------------------------------------------------------------------------------------
    def parse_lgp_file(self, pf_name, pf_base):                                                                           # обработка файла старого формата
        t.debug_print("processing " + pf_name,self.name)                                                                # слишком много текстов буде
        file_state                                          =   {}
        # Проверка наличия файла ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        if not os.path.exists(pf_name):
            t.debug_print(f"file not found {pf_name}", self.name)
            return
        # Готовлюсь к чтению ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        pf_size                                             =   os.stat(pf_name).st_size                                # текущий размер файла
        file_state['filename']                              =   pf_name                                                 # локальная структура json с именем файла
        file_state['filesize']                              =   pf_size                                                 # локальная структура json с размером файла
        _state                                              =   state_manager.get_file_state(pf_name, pf_base)
        file_state['filesizeread']                          =   _state['filesizeread'] if _state else 0
        batch_start_offset                                  =   file_state['filesizeread']
        # Немного переменных ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        pf_block_mul                                        =   1                                                       # мультипликатор для блоков.
        #pf_match_no                                         =   0                                                       # номер записи
        pf_bytes_2_commit                                   =   0
        dt_sort_add                                         =   0                                                       # переменная доп. нумерации дополнительного поля сортировки дат
        prev_r1                                             =   ""                                                      # значение для хранения дат из предудущей записи
        block_time_start                                    =   None
        block_commit_start                                  =   time.time()
        try:                                                                                                            # всё в попытке
            # чтение остатка содержимого файла в байтах ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
            while file_state['filesizeread']                <   pf_size:                                                # пока смещением не дошёл до конца
                t.debug_print(
                    f"file={pf_name} base={pf_base} read={str(file_state['filesizeread'])}",
                    self.name
                )
                if g.debug.on_parser and block_time_start:
                        t.debug_print(f"Block tooked {str(time.time()-block_time_start)}",self.name)
                block_time_start                            =   time.time()                                             # фиксация времени начала обработки блока
                # Перечитываем словарь только если файл словаря изменился (оптимизация производительности)
                # Проверка времени модификации словаря перед перечитыванием
                d.read_ib_dictionary(pf_base)                                                                           # словарь должен быть уже проинициализирован
                
                # определяю размер для чтения ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                pf_size_read                                =   g.parser.blocksize * pf_block_mul                       # размер блока для чтения
                # обработка корявых фрагментов ЖР ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                if pf_size_read                             >   g.parser.maxblocksize:                                  # если превышен макс размер блока для чтения
                    pf_size_read                            =   g.parser.maxblocksize                                   # блок оставляем маскимальным
                    file_state['filesizeread']              +=  g.parser.maxblocksize//2                                # и перемещаем маркер чтения на половину максимального блока вперёд
                    t.debug_print(pf_base+':bad block detected, skipping to '+str(file_state['filesizeread']),self.name)
                # вычисляю сколько нужно читать ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~+ https://github.com/WonderMr/Journal2Ct/issues/40
                pf_delta                                    =   pf_size - file_state['filesizeread']
                # сначала проверю, не меньши ли файл блока для чтения ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                pf_rest_bytes                               =   pf_delta                        \
                                                                if pf_delta > 0                 \
                                                                else pf_size
                                                                # вычисляю остаток для чтения
                # теперь - размер блока для чтения. Если он больше остатка байт для чтения, то
                pf_size_read                                =   pf_size_read                    \
                                                                if pf_rest_bytes > pf_size_read \
                                                                else pf_rest_bytes                                      # если остаток для чтения больше размера блока, то считываем размер блока, иначе - остаток файла
                # иду к смещению и читаю блок ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                pf_chunk                                    =   self.read_block(
                                                                    pf_name,
                                                                    file_state['filesizeread'],
                                                                    pf_size_read
                                                                )
                # конвертация байтов в строку ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                if pf_chunk:                                                                                            # если данные есть
                    pf_chunk_converted                      =   False                                                   # получилось ли их перегнать в строку?
                    while not pf_chunk_converted:                                                                       # пока не получится
                        try:                                                                                            # пробую декодировать
                            pf_block_as_str                 =   pf_chunk.decode(encoding='UTF8')                        # пробую декодировать
                            pf_chunk_converted              =   True                                                    # получилось
                        except Exception as e:                                                                          # не получилось,
                            if g.debug.on_parser:
                                t.debug_print(
                                    pf_base+":Bad symbol while decoding chunk in " + pf_name + " " + str(e),
                                    self.name
                                )
                            mesg                            =   str(e)
                            position                        =   g.rexp.bad_chunk_pos.findall(mesg)
                            if position and len(position)>0:                                                            # если есть сообщение об ошибке декодирования с позицией проблемного байте
                                if int(position[0])         >   0:                                                      # и если оно спереди
                                    if g.debug.on_parser:
                                        t.debug_print(pf_base+":chunk decrease to "+position[0],self.name)
                                    pf_chunk                =   pf_chunk[0:int(position[0])]                            # то уменьшаюсь до его размера
                                else:                                                                                   # если не декодируется с начала блока
                                    if g.debug.on_parser:
                                        t.debug_print(pf_base+":chunk -1 going forward", self.name)
                                    file_state['filesizeread']\
                                                            -=  1                                                       # то читаем на байт сзади. Такое авно только для поверждённых кусков бывает
                                    pf_chunk                =   self.read_block(
                                                                    pf_name,
                                                                    file_state['filesizeread'],
                                                                    pf_size_read
                                                                )
                            else:
                                t.debug_print(pf_base+":Exception: всё очень плохо, я не придумал что с этим делать"
                                              ,self.name)
                    # получаю все записи из строки ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                    pf_records                              =   g.rexp.my_parse_re.findall(pf_block_as_str)             # вот это все мои записи ЖР
                    if pf_records:                                                                                      # если они есть
                        last_pos_in_chunk                   =   0
                        for pf_record in pf_records:                                                                    # то пройдёмся по ним
                            #pf_match_no                     +=  1                                                       # номер записи
                            # разбираю каждую запись на составные ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                            # pf_rec_parsed                   =   g.rexp.my_sel_re.findall(pf_record)                     # разбираю запись на составные
                            if(g.debug.on_parser):
                                t.debug_print("processing pf_record "+str(pf_record),self.name)
                            pf_rec_in_bytes                 =   str(pf_record[0]).encode(encoding='UTF8')               # чтобы найти смещение записи кодирую её
                            pf_rec_parsed                   =   pf_record[1:]                                           # копирую в такой же элемент, только без первой строки
                            if(g.debug.on_parser):
                                t.debug_print("processing pf_rec_parsed "+str(pf_rec_parsed),self.name)
                            
                            # Ищем вхождение с учетом предыдущей позиции, чтобы избежать дубликатов при одинаковых записях
                            found_pos                       =   pf_chunk.find(pf_rec_in_bytes, last_pos_in_chunk)
                            if found_pos == -1:
                                # Если не нашли с текущей позиции (странно, но бывает), ищем с начала
                                found_pos                   =   pf_chunk.find(pf_rec_in_bytes)
                            
                            if found_pos != -1:
                                last_pos_in_chunk           =   found_pos + 1                                           # сдвигаем курсор
                                pf_rec_offset               =   file_state['filesizeread'] + found_pos
                            else:
                                t.debug_print(f"CRITICAL: Record bytes not found in chunk for {pf_base}", self.name)
                                continue

                            if (prev_r1                     ==  pf_rec_parsed[0][0]):                                   # если дата совпадает
                                dt_sort_add                 +=  1
                            else:
                                dt_sort_add                 =   0
                            # добавляю в перменную класса json_data данные из очередной записи ~~~~~~~~~~~~~~~~~~~~~~~~~
                            #if(parser.check_rec(pf_name,pf_rec_offset,len(pf_rec_in_bytes))):                          # эта была проверка для каждой добавляемой записи
                            pf_rec_parsed                   =   self.fix_act_tran(pf_rec_parsed,pf_base)                # вот она, знаменитаю функция исправления дурацкого статуса транзакци транзакций
                            m.check_event(pf_rec_parsed, pf_base)
                            if not self.add_to_json_data    (
                                                                pf_rec_parsed,
                                                                dt_sort_add,
                                                                pf_name,
                                                                pf_rec_offset,
                                                                len(pf_rec_in_bytes),
                                                                pf_base
                                                            ):                                                          # добавлю данные текущей записи, моменте времени для сортировки, имени файла и её смщения в глобальный массив
                                t.debug_print("Exception : can't append "+str(pf_rec_parsed))
                            #t.debug_print(r.reader.read_rec(test))                                               test stuff
                            prev_r1                         =   pf_rec_parsed[0][0]
                            # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                        # освежаю переменные после завершения парсинга очередного блока ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                        pf_last_rec                         =   pf_records[len(pf_records) - 1]                         # извлекаю последную запись
                        ps_last_rec_in_bytes                =   str(pf_last_rec[0]).encode(encoding='UTF8')             # заворачиваю её в байты
                        pf_last_pos                         =   pf_chunk.rfind(ps_last_rec_in_bytes)                    # нахожу её позицию
                        pf_last_rec_bytes_len               =   pf_last_pos + len(ps_last_rec_in_bytes)                 # это - смещение после последней обработанной записи
                        file_state['filesizeread']          +=  pf_last_rec_bytes_len                                   # увеличиваю смещение на позицию последней записи и её длину в байтах
                        #t.debug_print('filesizeread = ' + str(file_state['filesizeread']))
                        pf_block_mul                        =   1                                                       # освобождаю мультипликаторв блока
                        pf_bytes_2_commit                   +=  pf_last_rec_bytes_len                                   # размер байт для коммита
                        #t.debug_print('pf_bytes_2_commit = ' + str(pf_bytes_2_commit))
                        # проверяю необходимость коммита и осуществляю его ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                        if  pf_bytes_2_commit               >   g.parser.bytes_2_commit or \
                            file_state['filesizeread']      ==  file_state['filesize']:
                                t.debug_print(  pf_base+":commiting with state: \n" +
                                                "total length     = "        +  str(file_state['filesize'])     + "\n"+
                                                "new curr. length = "        +  str (
                                                                                    file_state['filesizeread']  +
                                                                                    len(self.json_data[self.name])
                                                                                )                               + "\n"+
                                                "current length   = "        +  str(file_state['filesizeread']) + "\n"+
                                                "commited records = "        +  str(
                                                                                    len(self.json_data[self.name])),
                                                                                    self.name
                                                                                )                                       # add 2019.02.15 для https://github.com/WonderMr/Journal2Ct/issues/40
                                if block_time_start:
                                    t.debug_print(
                                        "Block before commit " + str(time.time() - block_commit_start),
                                        self.name
                                    )
                                    block_commit_start      =   time.time()
                                records_to_log              =   list(self.json_data[self.name])                         # сохраняем копию перед отправкой
                                if not self.commit_json_data(
                                    pf_name,
                                    pf_base,
                                    file_state,
                                    batch_start_offset,
                                    records_to_log
                                ):
                                    return
                                batch_start_offset          =   file_state['filesizeread']
                                # увеличиваю размер на количетво распарсенных данных ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                                parser.inc_parsed_size(pf_base, pf_bytes_2_commit)                                      # увеличиваю размер на количетво распарсенных данных
                                # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                                pf_bytes_2_commit           =   0                                                       # обнуляю сумматор байт для отправки
                    else:
                        if (pf_size - pf_size_read)         ==  file_state['filesizeread']:                             # если дочитали файл до конца, но записей ЖР не нашли
                            t.debug_print(  pf_base+":commiting(strange) with state: \n"  +
                                            "total length     = "         + str(file_state['filesize'])     + "\n" +
                                            "new curr. length = "         + str(
                                                                                file_state['filesizeread']  +
                                                                                len(self.json_data[self.name])
                                                                            )                               + "\n" +
                                            "current length   = "         + str(file_state['filesizeread']) + "\n" +
                                            "commited records = "         + str(
                                                                                len(self.json_data[self.name])),
                                                                                self.name
                                                                            )                                           # add 2019.02.15 для https://github.com/WonderMr/Journal2Ct/issues/40
                            file_state['filesizeread']      =   pf_size                                                 # закрываем чтение
                            records_to_log                  =   list(self.json_data[self.name])                         # сохраняем копию
                            if records_to_log:                                                                          # если есть неотправленные данные
                                if block_time_start:
                                    t.debug_print("Block tooked before commit " + str(time.time() - block_commit_start))
                                    block_commit_start      =   time.time()
                                if not self.commit_json_data(
                                    pf_name,
                                    pf_base,
                                    file_state,
                                    batch_start_offset,
                                    records_to_log
                                ):
                                    return
                                # увеличиваю размер на количетво распарсенных данных ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                                parser.inc_parsed_size(pf_base,pf_size_read)                                            # увеличиваю размер на количетво распарсенных данных
                                # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                                pf_bytes_2_commit           =   0
                            else:
                                if not self.commit_json_data(
                                    pf_name,
                                    pf_base,
                                    file_state,
                                    batch_start_offset,
                                    records_to_log
                                ):
                                    return
                            batch_start_offset              =   file_state['filesizeread']
                        else:
                            pf_block_mul                    *=  2                                                       # разобрать блок на записи ЖР не получилось, увеличиваю мультипликатор
                            t.debug_print("Block too large. Current size is " + str(g.parser.blocksize * pf_block_mul))
                else:
                    t.debug_print(pf_base+":got empty chunk on file "+pf_name+" with pos="+file_state['filesizeread'])
        except Exception as e:
            t.debug_print(pf_base+":Exception # parse_file got error " + str(e),self.name)
    # ------------------------------------------------------------------------------------------------------------------
    # read part of file - читаю кусочек файла по смещению
    # ------------------------------------------------------------------------------------------------------------------
    def read_block(self,rb_name,rb_offset,rb_size):
        rb_chunk                                            =   b""
        try:
            with open(rb_name, 'rb') as rb_fh:                                                                        # открываю файл
                rb_fh.seek(rb_offset)                                                                               # иду к смещению
                rb_chunk                                    =   rb_fh.read(rb_size)                                   # читаю его
        except Exception as e:
            t.debug_print("Exception11 while read file "+rb_name+" error="+str(e), self.name)
        return rb_chunk
    # ------------------------------------------------------------------------------------------------------------------
    # Механизм сохранения состояния перенесён в src/state_manager.py
    # Функции read_file_state и write_file_state удалены
    # ------------------------------------------------------------------------------------------------------------------
    #-------------------------------------------------------------------------------------------------------------------
    # увеличиваю размер распарсенных данных для базы (с блокировкой для thread-safety)
    # ------------------------------------------------------------------------------------------------------------------
    def inc_parsed_size(base,count):
        with g.ibases_lock:
            for each in g.parser.ibases:
                if each[g.nms.ib.name]                      ==  base:
                    each[g.nms.ib.parsed_size]              +=  count
                    break
    #-------------------------------------------------------------------------------------------------------------------
    # устанавливаю размер всего для базы (с блокировкой для thread-safety)
    # ------------------------------------------------------------------------------------------------------------------
    def set_total_size(base,sts_size):
        with g.ibases_lock:
            for each in g.parser.ibases:
                if each[g.nms.ib.name]                      ==  base:
                    each[g.nms.ib.total_size]               =   sts_size
                    break
    #-------------------------------------------------------------------------------------------------------------------
    # получаю размер всего для базы (с блокировкой для thread-safety)
    # ------------------------------------------------------------------------------------------------------------------
    def get_total_size(base):
        with g.ibases_lock:
            for each in g.parser.ibases:
                if each[g.nms.ib.name]                      ==  base:
                    return each[g.nms.ib.total_size]
        return 0
    #-------------------------------------------------------------------------------------------------------------------
    # устанавливаю размер распарсенных данных для базы (с блокировкой для thread-safety)
    # ------------------------------------------------------------------------------------------------------------------
    def set_parsed_size(base,count):
        with g.ibases_lock:
            for each in g.parser.ibases:
                if each[g.nms.ib.name]                      ==  base:
                    each[g.nms.ib.parsed_size]              =   count
                    break
    #-------------------------------------------------------------------------------------------------------------------
    # функция исправления статуса транзакции при парсинге (УСТАРЕЛО - больше не используется)
    # Ранее применялась инверсия C↔U, R↔C для исправления бага в старых версиях 1С
    # Но оказалось, что статусы в LGP записаны ПРАВИЛЬНО, fix не нужен
    # ------------------------------------------------------------------------------------------------------------------
    def fix_act_tran(self, pf_rec, pf_base):
        # Просто возвращаем запись как есть, без изменений
        return pf_rec
# ======================================================================================================================
