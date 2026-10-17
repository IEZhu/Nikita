# -*- coding: utf-8 -*-
# Copyright (C) 2025 Nikita Development Team
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import  re
import os
import threading
import platform
from dotenv import load_dotenv

# ======================================================================================================================
# инициализация окружения и платформы
# ======================================================================================================================
# Явная загрузка .env из корня проекта (на уровень выше src), чтобы не зависеть от CWD
project_root                                            =   os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path                                                =   os.path.join(project_root, '.env')
load_dotenv(env_path)

is_windows                                              =   platform.system() == "Windows"      # Определение платформы
ibases_lock                                             =   threading.Lock()                    # Глобальная блокировка для безопасного доступа к g.parser.ibases

# Функция для вывода конфигурации (вызывается после инициализации tools)
def print_config():
    """Выводит текущую конфигурацию в лог"""
    from src.tools import tools as t
    t.debug_print("=" * 80)
    t.debug_print("ЗАГРУЗКА КОНФИГУРАЦИИ NIKITA")
    t.debug_print("=" * 80)
    # Показываем реальные значения после применения логики
    t.debug_print(f"CLICKHOUSE_ENABLED: {conf.clickhouse.enabled}")
    t.debug_print(f"CLICKHOUSE_HOST: {conf.clickhouse.host}")
    t.debug_print(f"CLICKHOUSE_PORT: {conf.clickhouse.port}")
    t.debug_print(f"CLICKHOUSE_DATABASE: {conf.clickhouse.database}")
    t.debug_print(f"CLICKHOUSE_USER: {conf.clickhouse.user}")
    t.debug_print(f"REDIS_ENABLED: {conf.redis.enabled}")
    t.debug_print(f"SOLR_ENABLED: {conf.solr.enabled}")
    t.debug_print(f"HTTP_INTERFACE: {conf.http.listen_interface}")
    t.debug_print(f"HTTP_PORT: {conf.http.listen_port}")
    t.debug_print(f"DEBUG_ENABLED: {os.getenv('DEBUG_ENABLED', 'True')}")
    t.debug_print("=" * 80)

# ======================================================================================================================
# в блоке переменных выход за длину разрешён
# ======================================================================================================================
# ======================================================================================================================
# ======================================================================================================================
# ======================================================================================================================
# класс описания службы Windows
# ======================================================================================================================
class service:
    name                                                        =   "Nikita"
    display_name                                                =   "Nikita - быстрый поиск по журналам регистрации 1С"
    description                                                 =   "Обеспечивает индексацию и быстрый поиск по " \
                                                                    "журналам регистрации"
    build_version                                               =   "2026.10.17-mcp"                                    # ручной тег версии сборки; бампать при изменениях кода
# ======================================================================================================================
# переменные времени исполнения программы или потоков
# ======================================================================================================================
class execution:
    running_in_console                                          =   False                                               # признак работы в консоли
    self_name                                                   =   ""                                                  # имя исполняемого файла или скрипта
    self_dir                                                    =   ""                                                  # каталог исполняемого файла или скрипта
    # ==================================================================================================================
    class solr:
        pid                                                     =   0                                                   # Process identifier для java, в котором крутится Solr
        url_main                                                =   ""                                                  # url сервера Solr
        #pysorl                                                  =   None                                                # объект pysolr
        started                                                 =   False                                               # признак того, что Solr проснулся
    # ==================================================================================================================
    class c1_dicts:
        dict_actual_filesizes                                   =   {}                                                  # отдельный словарь для размеров файлов словарей. если размер файла изменился, то словарь перечитываем
        users                                                   =   {}                                                  # словарь пользователей ИБ из словаря старого формата ЖР для конкретной базы
        computers                                               =   {}                                                  # словарь компьютеров из словаря старого формата ЖР для конкретной базы
        applications                                            =   {}                                                  # словарь приложенией из словаря старого формата ЖР для конкретной базы
        actions                                                 =   {}                                                  # словарь событий из словаря старого формата ЖР для конкретной базы
        metadata                                                =   {}                                                  # словарь метаданных из словаря старого формата ЖР для конкретной базы
        servers                                                 =   {}                                                  # словарь серверов из словаря старого формата ЖР для конкретной базы
        ports_main                                              =   {}                                                  # словарь главных портов из словаря старого формата ЖР для конкретной базы
        ports_add                                               =   {}                                                  # словарь дополнительных портов из словаря старого формата ЖР для конкретной базы
        tran_fix_list                                           =   {}                                                  # список событий, для который нужно менять тип транзакции на старом ЖР
        ext_main_id                                             =   {}                                                  # словарь ID разделителя основных данных
        ext_add_id                                              =   {}                                                  # словарь ID разделителя вспомогательных данных
        ext_area_main_ids                                       =   {}                                                  # словарь соответствий областей основных данных и указателей на них
        ext_area_add_ids                                        =   {}                                                  # словарь соответствий областей вспомогательных данных и указателей на них
# ======================================================================================================================
# ======= настройки программы ==========================================================================================
# ======= чтобы не было исключений, всё, внутри conf в строковом виде хранится. ПОМНИМ ОБ ЭТОМ =========================
# ======================================================================================================================
class conf:
    filename                                                    =   ""                                                  # имя файла конфигурации
    # ======= настройки специфики 1с ===================================================================================
    class c1:
        section_name                                            =   "1C settings"                                       # имя секции для файла конфигурации
        srvinfo                                                 =   os.getenv('C1_SRVINFO_PATH', '')                    # корневой путь к базам 1С для автодетекта
        cluster_file                                            =   "1CV8Clst.lst"                                      # имя файла кластера, предопределено
        cluster_file_o                                          =   "1CV8Clsto.lst"                                     # предпочтительно - его
        jr_dir                                                  =   "1Cv8Log"                                           # имя каталога журнала регистрации, предопределено
        jr_new_fname                                            =   "1Cv8.lgd"                                          # имя файла с новым ЖР в формате Sqlite3, предопределено
        jr_old_dict_fname                                       =   "1Cv8.lgf"                                          # имя файла словаря старого формата ЖР, предопределено

    # ======= настройки cherrypy =======================================================================================
    class http:
        section_name                                            =   "HTTP settings"                                     # имя секции для файла конфигурации
        listen_interface                                        =   os.getenv('HTTP_LISTEN_INTERFACE', '0.0.0.0')
        listen_port                                             =   os.getenv('HTTP_LISTEN_PORT', '8984')

    # ======= настройки ClickHouse =====================================================================================
    class clickhouse:
        section_name                                            =   "ClickHouse settings"                               # имя секции для файла конфигурации
        # Под Windows отключаем ClickHouse, включаем только Solr
        enabled                                                 =   os.getenv('CLICKHOUSE_ENABLED', 'False').lower() in ('true', '1', 't', 'y', 'yes') and not is_windows
        host                                                    =   os.getenv('CLICKHOUSE_HOST', 'localhost')
        port                                                    =   int(os.getenv('CLICKHOUSE_PORT', 9000))
        user                                                    =   os.getenv('CLICKHOUSE_USER', 'default')
        password                                                =   os.getenv('CLICKHOUSE_PASSWORD', '')
        database                                                =   os.getenv('CLICKHOUSE_DATABASE', 'zhr1c')

    # ======= настройки MCP (эндпоинт /mcp веб-сервера) ================================================================
    # авторизации в Nikita нет: публикуйте /mcp только за аутентифицирующим прокси и с HTTP_LISTEN_INTERFACE=127.0.0.1
    class mcp:
        section_name                                            =   "MCP settings"
        clickhouse_user                                         =   os.getenv('MCP_CLICKHOUSE_USER', '')                # отдельный пользователь readonly, без отката на CLICKHOUSE_USER
        clickhouse_password                                     =   os.getenv('MCP_CLICKHOUSE_PASSWORD', '')
        enabled                                                 =   os.getenv('MCP_ENABLED', 'False').lower() in ('true', '1', 't', 'y', 'yes') and clickhouse_user != ''
        max_rows                                                =   max(1, int(os.getenv('MCP_MAX_ROWS', 10000)))       # потолок max_rows инструмента query
        allowed_origins                                         =   [origin.strip() for origin in os.getenv('MCP_ALLOWED_ORIGINS', '').split(',') if origin.strip()]
        max_body                                                =   65536                                               # байт в теле одного запроса

    # ======= настройки Redis ==========================================================================================
    class redis:
        section_name                                            =   "Redis settings"
        # Под Windows отключаем Redis, включаем только Solr
        enabled                                                 =   os.getenv('REDIS_ENABLED', 'False').lower() in ('true', '1', 't', 'y', 'yes') and not is_windows
        server_path                                             =   ""                                                  # путь к redis-server (если нужно запускать)
        host                                                    =   os.getenv('REDIS_HOST', '127.0.0.1')
        port                                                    =   int(os.getenv('REDIS_PORT', 6379))
        db                                                      =   int(os.getenv('REDIS_DB', 0))
        dir                                                     =   ""                                                  # каталог для БД redis (для persistence)

    # ======= настройки solr ===========================================================================================
    class solr:
        section_name                                            =   "Solr settings"                                     # имя секции для файла конфигурации
        # Под Windows автоматически включаем Solr
        enabled                                                 =   os.getenv('SOLR_ENABLED', 'True' if is_windows else 'False').lower() in ('true', '1', 't', 'y', 'yes')
        mem_min                                                 =   os.getenv('SOLR_MEM_MIN', '2g')
        mem_max                                                 =   os.getenv('SOLR_MEM_MAX', '32g')
        dir                                                     =   os.getenv('SOLR_DIR', '')
        listen_interface                                        =   os.getenv('SOLR_LISTEN_INTERFACE', '127.0.0.1')
        listen_port                                             =   os.getenv('SOLR_LISTEN_PORT', '8983')
        solr_host                                               =   os.getenv('SOLR_HOST', '127.0.0.1')
        solr_port                                               =   os.getenv('SOLR_PORT', '8983')
        threads                                                 =   os.getenv('SOLR_THREADS', '12')
        wait_after_start                                        =   3                                                   # сколько сек подождать после старта
        ping                                                    =   "/admin/ping"                                       # указатель на страничку с пингом
        schema                                                  =   '<?xml version="1.0" encoding="UTF-8" ?>\r\n\r\n<schema name="Nikita" version="1.0">\r\n\r\n  <field name="id"        type="string"      indexed="true" stored="true"  multiValued="false"/>\r\n  <uniqueKey>id</uniqueKey>\r\n  <field name="pos"       type="plong"       indexed="true" stored="true"  multiValued="false" omitNorms="true"/>\r\n  <field name="len"       type="pint"        indexed="true" stored="true"  multiValued="false" omitNorms="true"/>\r\n  <field name="file_name" type="string"      indexed="false" stored="true"  multiValued="false"/>\r\n\r\n  <field name="date"        type="plong"       indexed="true" stored="false" multiValued="false" omitNorms="true" docValues="true"/>\r\n  <field name="date_idx"     type="pint"        indexed="true" stored="false" multiValued="false" omitNorms="true" docValues="true"/>\r\n  <field name="t_status"        type="string"      indexed="true" stored="false" multiValued="false" omitNorms="true"/>\r\n\r\n  <field name="t_id"        type="plong"       indexed="true" stored="false" multiValued="false" omitNorms="true"/>\r\n  <field name="t_pos"       type="plong"       indexed="true" stored="false" multiValued="false" omitNorms="true"/>  \r\n  <field name="user_id"        type="pint"        indexed="true" stored="false" multiValued="false" omitNorms="true"/>\r\n  <field name="comp_id"        type="pint"        indexed="true" stored="false" multiValued="false" omitNorms="true"/>\r\n\r\n  <field name="app_id"        type="pint"        indexed="true" stored="false" multiValued="false" omitNorms="true"/>\r\n  <field name="conn_id"        type="pint"        indexed="true" stored="false" multiValued="false" omitNorms="true"/>\r\n  <field name="event_id"        type="pint"        indexed="true" stored="false" multiValued="false" omitNorms="true"/>\r\n\r\n  <field name="severity"        type="string"      indexed="true" stored="false" multiValued="false" omitNorms="true"/>\r\n  <field name="comment"       type="text_simple" indexed="true" stored="false" multiValued="false"/>\r\n  <field name="meta_id"       type="pint"        indexed="true" stored="false" multiValued="false" omitNorms="true"/>\r\n\r\n  <field name="data"       type="text_simple" indexed="true" stored="false" multiValued="false"/>\r\n  <field name="data_pres"       type="text_simple" indexed="true" stored="false" multiValued="false" omitNorms="true"/>\r\n  <field name="server_id"       type="pint"        indexed="true" stored="false" multiValued="false" omitNorms="true"/>\r\n\r\n  <field name="port_id"       type="pint"        indexed="true" stored="false" multiValued="false" omitNorms="true"/>\r\n  <field name="port_sec_id"       type="pint"        indexed="true" stored="false" multiValued="false" omitNorms="true"/>\r\n  <field name="session_id"       type="pint"        indexed="true" stored="false" multiValued="false" omitNorms="true"/>\r\n  <field name="area_id"       type="pint"        indexed="true" stored="false" multiValued="false" omitNorms="true"/>\r\n  <field name="area_sec_id"       type="pint"        indexed="true" stored="false" multiValued="false" omitNorms="true"/>\r\n\r\n  <field name="user_guid"       type="string"        indexed="true" stored="false" multiValued="false" omitNorms="true"/>\r\n  <field name="meta_uuid"       type="string"        indexed="true" stored="false" multiValued="false" omitNorms="true"/>\r\n\r\n  <fieldType name="string"       class="solr.StrField"       docValues="false"	sortMissingLast="true"/>\r\n  <fieldType name="plong"        class="solr.LongPointField" docValues="false"/>\r\n  <fieldType name="pint"         class="solr.IntPointField"  docValues="false"/>\r\n  <fieldType name="puuid"        class="solr.UUIDField"      docValues="false"/>\r\n\r\n  <fieldType name="text_simple"  class="solr.TextField"      docValues="false" positionIncrementGap="0">\r\n    <analyzer>\r\n      <tokenizer class="solr.StandardTokenizerFactory"/>\r\n      <filter class="solr.LowerCaseFilterFactory"/>\r\n    </analyzer>\r\n  </fieldType>\r\n\r\n</schema>'  # файл schema для создания нового core\r\n
        config                                                  =   '<?xml version="1.0" encoding="UTF-8" ?>\r\n<config>\r\n  <luceneMatchVersion>8.0.0</luceneMatchVersion>\r\n\r\n  <updateHandler class="solr.DirectUpdateHandler2">\r\n    <autoCommit>\r\n      <maxTime>15000</maxTime>\r\n      <maxDocs>5000</maxDocs>\r\n      <openSearcher>false</openSearcher>\r\n    </autoCommit>\r\n    <autoSoftCommit>\r\n      <maxTime>5000</maxTime>\r\n    </autoSoftCommit>\r\n  </updateHandler>\r\n\r\n  <!-- Load Data Import Handler and Apache Tika (extraction) libraries -->\r\n  <lib dir="${solr.install.dir:../../../..}/dist/" regex="solr-dataimporthandler-.*\\.jar"/>\r\n  <lib dir="${solr.install.dir:../../../..}/contrib/extraction/lib" regex=".*\\.jar"/>\r\n\r\n  <requestHandler name="/select" class="solr.SearchHandler">\r\n    <lst name="defaults">\r\n      <str name="echoParams">explicit</str>\r\n      <str name="df">text</str>\r\n    </lst>\r\n  </requestHandler>\r\n\r\n  <updateRequestProcessorChain name="dedupe">\r\n    <processor class="solr.LogUpdateProcessorFactory" />\r\n    <processor class="solr.RunUpdateProcessorFactory" />\r\n  </updateRequestProcessorChain>\r\n</config>\r\n' # файл конфигурации ядра solr для создания нового\r\n
        ##  эти записи ниже - отладочный. Они - с сохранением значений при парсинге. потом их закомментировать
        #schema                                                  =   '<?xml version="1.0" encoding="UTF-8" ?>\r\n\r\n<schema name="Nikita" version="1.0">\r\n\r\n  <field name="id"        type="string"      indexed="true" stored="true"  multiValued="false"/>\r\n  <field name="pos"       type="plong"       indexed="true" stored="true"  multiValued="false" omitNorms="true"/>\r\n  <field name="len"       type="pint"        indexed="true" stored="true"  multiValued="false" omitNorms="true"/>\r\n\r\n  <field name="r1"        type="plong"       indexed="true" stored="true" multiValued="false" omitNorms="true"/>\r\n  <field name="r1nmb"     type="pint"        indexed="true" stored="true" multiValued="false" omitNorms="true"/>\r\n  <field name="r2"        type="string"      indexed="true" stored="true" multiValued="false" omitNorms="true"/>\r\n\r\n  <field name="r3"        type="plong"       indexed="true" stored="true" multiValued="false" omitNorms="true"/>\r\n  <field name="r3a"       type="plong"       indexed="true" stored="true" multiValued="false" omitNorms="true"/>  \r\n  <field name="r4"        type="pint"        indexed="true" stored="true" multiValued="false" omitNorms="true"/>\r\n  <field name="r5"        type="pint"        indexed="true" stored="true" multiValued="false" omitNorms="true"/>\r\n\r\n  <field name="r6"        type="pint"        indexed="true" stored="true" multiValued="false" omitNorms="true"/>\r\n  <field name="r7"        type="pint"        indexed="true" stored="true" multiValued="false" omitNorms="true"/>\r\n  <field name="r8"        type="pint"        indexed="true" stored="true" multiValued="false" omitNorms="true"/>\r\n\r\n  <field name="r9"        type="string"      indexed="true" stored="true" multiValued="false" omitNorms="true"/>\r\n  <field name="r10"       type="text_simple" indexed="true" stored="true" multiValued="false"/>\r\n  <field name="r11"       type="pint"        indexed="true" stored="true" multiValued="false" omitNorms="true"/>\r\n\r\n  <field name="r12"       type="text_simple" indexed="true" stored="true" multiValued="false"/>\r\n  <field name="r13"       type="text_simple" indexed="true" stored="true" multiValued="false" omitNorms="true"/>\r\n  <field name="r14"       type="pint"        indexed="true" stored="true" multiValued="false" omitNorms="true"/>\r\n\r\n  <field name="r15"       type="pint"        indexed="true" stored="true" multiValued="false" omitNorms="true"/>\r\n  <field name="r16"       type="pint"        indexed="true" stored="true" multiValued="false" omitNorms="true"/>\r\n  <field name="r17"       type="pint"        indexed="true" stored="true" multiValued="false" omitNorms="true"/>\r\n\r\n\r\n<!--  <field name="text"      type="text_simple" indexed="true" stored="true" multiValued="true" />-->\r\n\r\n  <fieldType name="string"       class="solr.StrField"       docValues="false"	sortMissingLast="true"/>\r\n  <fieldType name="plong"        class="solr.LongPointField" docValues="false"/>\r\n  <fieldType name="pint"         class="solr.IntPointField"  docValues="false"/>\r\n  <fieldType name="puuid"        class="solr.UUIDField"      docValues="false"/>\r\n\r\n  <fieldType name="text_simple"  class="solr.TextField"      docValues="false" positionIncrementGap="0">\r\n    <analyzer>\r\n      <tokenizer class="solr.StandardTokenizerFactory"/>\r\n      <filter class="solr.LowerCaseFilterFactory"/>\r\n    </analyzer>\r\n  </fieldType>\r\n\r\n</schema>'  # файл schema для создания нового core\r\n
        #config                                                  =   '<?xml version="1.0" encoding="UTF-8" ?>\r\n<config>\r\n  <luceneMatchVersion>7.2.1</luceneMatchVersion>\r\n\r\n  <!-- Load Data Import Handler and Apache Tika (extraction) libraries -->\r\n  <lib dir="${solr.install.dir:../../../..}/dist/" regex="solr-dataimporthandler-.*\\.jar"/>\r\n  <lib dir="${solr.install.dir:../../../..}/contrib/extraction/lib" regex=".*\\.jar"/>\r\n\r\n  <requestHandler name="/select" class="solr.SearchHandler">\r\n    <lst name="defaults">\r\n      <str name="echoParams">explicit</str>\r\n      <str name="df">text</str>\r\n    </lst>\r\n  </requestHandler>\r\n\r\n  <updateRequestProcessorChain name="dedupe">\r\n    <processor class="solr.LogUpdateProcessorFactory" />\r\n    <processor class="solr.RunUpdateProcessorFactory" />\r\n  </updateRequestProcessorChain>\r\n</config>\r\n' # файл конфигурации ядра solr для создания нового\r\n
# ======================================================================================================================
# всё, касающееся потоков парсинга
# ======================================================================================================================
class parser:
    section_name                                                =   "Parser settings"                                   # имя секции для файла конфигурации
    threads                                                     =   os.getenv('PARSER_THREADS', '')
    maxrecsize                                                  =   512                                                 # сколько записей отправляем в ClickHouse
    ibases                                                      =   []                                                  # полный каталог др базы, имя базы, формат жр базы
    state_file                                                  =   ""                                                  # Deprecated: файл с информацией об статусе обработки отдельных файлов
    #state_file_lock                                             =   False                                               # Deprecated: однопоточная запись или чтение в файл статусов
    blocksize                                                   =   16384                                               # размер блока для чтения из файла
    bytes_2_commit                                              =   200000                                              # по сколько байт коммитить в solr
    maxblocksize                                                =   8388608                                             # больше такого размера блоки не читать - 8МБ
    lgd_parse_records_limit                                     =   50000                                               # величина обрабатываемых записей за 1 проход
    lgd_select_last_id                                          =   ''' select rowID from EventLog 
                                                                        order by rowID desc limit 1 '''                 # выбор ID последней записи LGD
    lgd_select_first_id                                         =   ''' select rowID from EventLog 
                                                                        order by rowID asc limit 1 '''                  # выбор ID первой записи. У обрезанных ЖР она будет >1
# ======================================================================================================================
# всё, касающееся отладки
# ======================================================================================================================
class debug:
    on                                                          =   os.getenv('DEBUG_ENABLED', 'True').lower() in ('true', '1', 't', 'y', 'yes')
    on_parser                                                   =   os.getenv('DEBUG_PARSER', 'False').lower() in ('true', '1', 't', 'y', 'yes')
    filehandle                                                  =   None                                                # указатель на файл лога отладки
    filename                                                    =   ""                                                  # имя файла лога для отладки
    dir                                                         =   ""
# ======================================================================================================================
# здесь описаны все используемые регулярки
# ======================================================================================================================
class rexp:
    service_is_1c                                               =   re.compile(r'\\ragent.*\-d' , flags=re.IGNORECASE)  # имя службы 1с
    daemon_1c                                                   =   re.compile(r'srv1cv8.*'     , flags=re.IGNORECASE)  # имя службы 1с
    enviroments                                                 =   re.compile(r'(\w+)=(".*?"|\'.*?\'|\S*)')            # получение переменных окружения
    service_1c_workdir                                          =   re.compile(r'\-d\s*\$*[\"\{](.+?)[\"\}]')           # получение рабочего каталога из пути к службе 1с
    any_filename                                                =   re.compile(r'[^\\/]*?$')                            # любое имя файла - последние символы после \ или /
    any_file_ext                                                =   re.compile(r'[^\.]*?$')                             # любое расширение файла - последние символы после точки
    uuid_start                                                  =   re.compile(r'^[\w]{8}-[\w]{4}-[\w]{4}-[\w]{4}-[\w]{12}')# UUID c самого начала строки!
    is_1c_cluster                                               =   re.compile(r'reg_\d+',flags=re.IGNORECASE)          # проверка имени каталога на шаблон кластера 1с
    clst_1c_base_rec                                            =   re.compile(r'\{([а-яА-ЯёЁйЙ\(\)\s\w\-]+),"(.*?)",".*?",".*?",".*?",".*?",".*?",".*?",".*?Srvr=',flags=re.IGNORECASE)# шаблон для получения идентификатора базы из файла 1CV8Clst.lst #2019.01.28 https://github.com/WonderMr/Journal2Ct/issues/42
    is_lgP_file_re                                              =   re.compile(r'\.lgp$')
    is_lgD_file_re                                              =   re.compile(r'\.lgd$')
    bad_chunk_pos                                               =   re.compile(r'position\s(\d+)')                      # смещение плохого чанка при побайтовом чтении
    del_quotes                                                  =   re.compile(r'^"|"$')                                # для удаления кавычек в начале и конце строки
    find_1c_link                                                =   re.compile(r'([\w\d]+\:[\w\d]+)')                   # ссылка для внутренних ссылок 1С
    data_p_item                                                 =   re.compile(r'\{"([SBNDU])"(?:,("(?:[^"]|"")*"|[^{}]*))?\}') # элемент структуры {"P",{...}} поля «данные» R12: тип + значение
    # универсальная регулярка записи ЖР c отбором для выбора
    sel_re                                                      =   r'\,*\r*\n*\{(\d{14}),(\w),\r*\n\{([0-9a-f]+),([0-9a-f]+)\},(\d+),(\d+),(\d+),(\d+),(\d+),(\w),"([^ꡏ]*?)(?=",\d+,\r*\n)",(\d+),\r*\n\{([^ꡏ]*?)(?=\},")\},"([^ꡏ]*?)(?=",\d+)",(\d+),(\d+),(\d+),(\d+),\d+[,\d+]*,\r*\n\{((\d+)|\d+,(\d+),(\d+),(\d+),(\d+))\}\r*\n\},*\r*\n*' #case 2020.05.21
    sel_re_ext_nmb                                              =   22                                                  # если есть дополнительные области, то length(sel_re)>sel_re_ext #case 2020.05.21
    my_sel_re                                                   =   re.compile(sel_re);
    # а это старая, без выбора
    #my_re                                                       =   re.compile(r'\,*\r*\n*\{\d{14},\w,\r*\n\{[0-9a-f]+,[0-9a-f]+\},\d+,\d+,\d+,\d+,\d+,\w,"[А-ЯA-Zа-яa-z\Ё\ё\d\n\r\s\.\,\:\;\(\)\{\}\<\>\[\]\«\»\=\/\~\'\"\_\\\#\!\&\%\$\@\#\^\*\+\-\?\№\¶]*?(?=",\d+,\r*\n)",\d+,\r*\n\{[А-ЯA-Zа-яa-z\Ё\ё\d\n\r\s\.\,\:\;\(\)\{\}\<\>\[\]\«\»\=\/\~\'\"\_\\\#\!\&\%\$\@\#\^\*\+\-\?\№\¶]*?(?=\},")\},"[А-ЯA-Zа-яa-z\Ё\ё\d\n\r\s\.\,\:\;\(\)\{\}\<\>\[\]\«\»\=\/\~\'\"\_\\\#\!\&\%\$\@\#\^\*\+\-\?\№\¶]*?(?=",\d+)",\d+,\d+,\d+,\d+,\d+[,\d+]*,\r*\n\{[\d,*]+\}\r*\n\},*\r*\n*');
    # универсальная регулярка записи ЖР с отбором полей для парсинга
    my_parse_re                                                 =   re.compile("("+sel_re+")");
    # отбор данных их словарей файловых ЖР ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    class dict:
        users_re                                                =   re.compile(r'\{1,([\w\d]{8}\-[\w\d]{4}\-[\w\d]{4}\-[\w\d]{4}\-[\w\d]{12}),"(.*?)",(\d+)}')# выбор всех пользователей из словаря старого формата ЖР
        computers_re                                            =   re.compile(r'\{2,"(.*?)",(\d+)}')                   # выбор всех рабочих станций из словаря старого формата ЖР
        applications_re                                         =   re.compile(r'\{3,"(.*?)",(\d+)}')                   # выбор всех приложений из словаря старого формата ЖР
        actions_re                                              =   re.compile(r'\{4,"(.*?)",(\d+)}')                   # выбор всех непредопределённых событий из словаря старого формата ЖР
        metadata_re                                             =   re.compile(r'\{5,([\w\d]{8}\-[\w\d]{4}\-[\w\d]{4}\-[\w\d]{4}\-[\w\d]{12}),"(.*?)",(\d+)}')# выбор всех метаданных из словаря старого формата ЖР
        servers_re                                              =   re.compile(r'\{6,"(.*?)",(\d+)}')                   # выбор всех серверов из словаря старого формата ЖР
        ports_main_re                                           =   re.compile(r'\{7,(\d+),(\d+)}')                     # выбор всех главных портов из словаря старого формата ЖР
        ports_add_re                                            =   re.compile(r'\{8,(\d+),(\d+)}')                     # выбор всех дополнительных портов из словаря старого формата ЖР
        ext_id                                                  =   re.compile(r'\{9,([\w\d]{8}\-[\w\d]{4}\-[\w\d]{4}\-[\w\d]{4}\-[\w\d]{12}),"(.*)",(\d+)}') # выбор ID для обозначения области данных
        #ext_add_id                                              =   re.compile(r'^\{9,([\w\d]{8}\-[\w\d]{4}\-[\w\d]{4}\-[\w\d]{4}\-[\w\d]{12}),"(ОбластьДанныхВспомогательныеДанные|РћР±Р»Р°СЃС‚СЊР”Р°РЅРЅС‹С…Р’СЃРїРѕРјРѕРіР°С‚РµР»СЊРЅС‹РµР”Р°РЅРЅС‹Рµ)",(\d+)}') # выбор ID для обозначения ОбластьДанныхВспомогательныеДанные
        ext_area_id                                             =   re.compile(r'{"N",(\d+)},(\d+),(\d+)}')             # собственно, это и есть сслыка на область данных, а так же обозначение, основная оно или вспомогательная, ;$1 = номер области, $2 - признак основные или вспомогательные, $3 - закондированый идентификатор
    # разбор данных запроса от обработки ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    class q:
        ref_re                                                  =   re.compile(r'ref=(.+?)(?=&)')                       # имя информационной базы
        date_start                                              =   re.compile(r'ДатаНачала=(.+)')                      # date начало периода отбора
        date_end                                                =   re.compile(r'ДатаОкончания=(.+)')                   # date конец периода отбора
        trans_status_re                                         =   re.compile(r'СтатусТранзакции=(.+)')                # t_status отбор по статусу транзакции
        user_re                                                 =   re.compile(r'Пользователь=(.+)')                    # user_id имя пользователя информационной базы
        user_guid_re                                            =   re.compile(r'ПользовательGUID=(.+)')                # user_guid
        computer_re                                             =   re.compile(r'Компьютер=(.+)')                       # comp_id имя компьютера пользователя информационной базы
        application_re                                          =   re.compile(r'ИмяПриложения=(.+)')                   # app_id имя приложения пользователя информационной базы
        connections_re                                          =   re.compile(r'Соединение=(.+)')                      # conn_id
        action_re                                               =   re.compile(r'Событие=(.+)')                         # event_id имя отбираемого события
        level_re                                                =   re.compile(r'Уровень=(.+)')                         # severity уровень отбора - может принимать значения Информация,Ошибка,Предупреждение,Примечание
        comments_re                                             =   re.compile(r'Комментарий="(.+?)(?=")')              # comment отбор по комментарию
        metadata_re                                             =   re.compile(r'Метаданные=(.+)')                      # meta_id метаданные отбираемого события
        data_re                                                 =   re.compile(r'^Данные=(.+)$')                        # data данные отбираемого события
        data_presentation_re                                    =   re.compile(r'ПредставлениеДанных="(.+)(?=")')       # data_pres отбор по представлению данных
        server_re                                               =   re.compile(r'РабочийСервер=(.+)')                   # server_id имя сервера из запроса отбора событий
        port_main_re                                            =   re.compile(r'ОсновнойIPПорт=(.+)')                  # port_id основной порт из отбора событий
        port_add_re                                             =   re.compile(r'ВспомогательныйIPПорт=(.+)')           # port_sec_id дополнительный порт порт из отбора событий
        seans_re                                                =   re.compile(r'Сеанс=(.+)')                           # session_id ID сеанса, передаётся без словаря
        ext_main                                                =   re.compile(r'РазделениеОсновныеДанные=(.+)')        # area_id(21) разделение данных основные данные #case 2020.05.21
        ext_add                                                 =   re.compile(r'РазделениеВспомогательныеДанные=(.+)') # area_sec_id(22) разделение данных основные данные #case 2020.05.21
        amount_re                                               =   re.compile(r'КоличествоСобытий=(.+)')               # количество событий из отбора. число
# ======================================================================================================================
# все потоки хранятся в этих переменных
# ======================================================================================================================
class threads:
    solr                                                        =   None                                                # в этом потоке запускается Solr
    cherry                                                      =   None                                                # вишенка живёт в этом потоке
    parser                                                      =   None                                                # поток для парсера старого формата ЖР
    parser_new                                                  =   None                                                # поток для парсера нового формата ЖР
    config_updater                                              =   None                                                # поток для обновления списка баз
    warming_cache                                               =   None                                                # поток для прогревания кэша
    parser2                                                     =   None                                                # поток для парсера старого формата ЖР
    parser_new2                                                 =   None                                                # поток для парсера нового формата ЖР
    redis                                                       =   None                                                # поток управления процессом Redis
    sender                                                      =   None                                                # поток отправки данных из очереди
# ======================================================================================================================
# все ожидания опеределены здесь
# ======================================================================================================================
class waits:
    parser_sleep_on_update_filelist                             =   30                                                  # пауза между обновлениями списка файлов
    warming_cache_wait                                          =   300                                                 # интервал между програваниями кэша
    sleep_on_conf_detection                                     =   15                                                  # добавление или удаление баз будет происходить в этом интервале
    read_state_exception                                        =   1                                                   # пауза на ошибке при чтении или записи в файл состояния
    in_cycle_we_trust                                           =   0.01
    solr_on_bad_send_to                                         =   0.1
    solr_wait_start                                             =   5
    solr_cycles                                                 =   10                                                  # сколько раз пробуем отправить запрос к Solr
    clickhouse_reconnect                                        =   10                                                  # пауза перед новой попыткой разбора, пока ClickHouse недоступен
    stop_check                                                  =   0.5                                                 # шаг пауз, на котором парсер проверяет запрос на остановку

# ======================================================================================================================
# класс с именами для полей
# ======================================================================================================================
class nms:
    class ib:
        name                                                    =   "ibase_name"                                        # имя раздела базы в файле конфигурации, предопределено
        jr_dir                                                  =   "ibase_jr_dir"                                      # имя раздела пути к каталогу ЖР в файле конфигурации, предопределено
        jr_format                                               =   "ibase_jr_format"                                   # имя раздела формата ЖР в файле конфигурации, предопределено
        jr_format_new                                           =   "lgd"                                               # расширение файла нового формата ЖР, предопределено
        jr_format_old                                           =   "lgf"                                               # расширение файла старого формата ЖР, предопределено
        total_size                                              =   "total_size"                                        # сколько всего занимает ЖР базы, в байтах или количестве записей
        parsed_size                                             =   "parsed_size"                                       # размер распарсенных данных, в байтах или количестве записей
# ======================================================================================================================
# класс кэшей
# ======================================================================================================================
class cache:
    filesizes                                                   =   []                                                  # кэш размеров файлов для прохода по списку при  чтении
# ======================================================================================================================
# всё, касающееся отправки уведомлений
# ======================================================================================================================
class notify:
    filehandle                                                  =   None                                                # указатель на файл лога
    filename                                                    =   ""                                                  # имя файла лога для
    select_user_re                                              =   re.compile(r',"(.*)?"')                             # выбор имени пользователя из события логина
    failed_logons                                               =   {}                                                  # список айдишников для _$Session$_.AuthenticationError по базам
# ======================================================================================================================
# статистика отправок данных
# ======================================================================================================================
class stats:
    # Статистика по ClickHouse
    clickhouse_total_sent                                       =   0                                                   # всего отправлено записей
    clickhouse_total_errors                                     =   0                                                   # всего ошибок отправки
    clickhouse_last_success_time                                =   None                                                # время последней успешной отправки
    clickhouse_last_error_time                                  =   None                                                # время последней ошибки
    clickhouse_last_error_msg                                   =   ""                                                  # текст последней ошибки
    clickhouse_connection_ok                                    =   False                                               # статус подключения
    
    # Статистика по Solr
    solr_total_sent                                             =   0                                                   # всего отправлено записей
    solr_total_errors                                           =   0                                                   # всего ошибок отправки
    solr_last_success_time                                      =   None                                                # время последней успешной отправки
    solr_last_error_time                                        =   None                                                # время последней ошибки
    solr_last_error_msg                                         =   ""                                                  # текст последней ошибки
    solr_connection_ok                                          =   False                                               # статус подключения
    
    # Статистика по Redis
    redis_total_queued                                          =   0                                                   # всего добавлено в очередь
    redis_total_errors                                          =   0                                                   # всего ошибок
    redis_last_success_time                                     =   None                                                # время последнего успешного добавления в очередь
    redis_last_error_time                                       =   None                                                # время последней ошибки
    redis_last_error_msg                                        =   ""                                                  # текст последней ошибки
    redis_connection_ok                                         =   False                                               # статус подключения
    
    # Общая статистика
    start_time                                                  =   None                                                # время запуска службы
    total_records_parsed                                        =   0                                                   # всего распарсено записей
    last_errors                                                 =   []                                                  # последние 10 ошибок (список кортежей: (время, тип, сообщение))
