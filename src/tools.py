# -*- coding: utf-8 -*-
import platform
is_windows                                                  =   platform.system() == "Windows"
if is_windows:
    import  win32event
else:
    import  signal
import  os
import  datetime
try:
    # servicemanager нужен только под Windows (pywin32); на Linux его может не быть.
    if is_windows:
        import  servicemanager
    else:
        servicemanager                                       =   None
except Exception:
    servicemanager                                           =   None
import  sqlite3
import  json
import  urllib
import  time
import  threading
from    typing              import  Any, Optional, Tuple, List
# ======================================================================================================================
from    src                 import  globals                 as  g
# ======================================================================================================================
# ----------------------------------------------------------------------------------------------------------------------
# разные используемый функции
# ----------------------------------------------------------------------------------------------------------------------
class tools():
    log_lock                                                =   threading.Lock()
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # преобразование строки в булево значение
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def strtobool(val: Any) -> bool:
        """Конвертирует строку в булево значение"""
        val                                                 =   str(val).lower()
        if val in ('y', 'yes', 't', 'true', 'on', '1'):
            return True
        elif val in ('n', 'no', 'f', 'false', 'off', '0'):
            return False
        else:
            return False
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Корректное завершение программы с остановкой всех потоков
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def graceful_shutdown(exit_code: int = 0) -> None:
        """
        Корректное завершение программы:
        1. Останавливает все потоки парсеров
        2. Закрывает соединения с БД
        3. Останавливает сервисы (Solr, Redis, CherryPy)
        4. Закрывает файлы логов
        5. Завершает процесс с указанным кодом
        """
        tools.debug_print(f"═══ НАЧАЛО КОРРЕКТНОГО ЗАВЕРШЕНИЯ (код: {exit_code}) ═══")
        
        try:
            # Останавливаем все потоки через stop_all
            tools.debug_print("Останавливаем все потоки...")
            from Nikita import stop_all
            stop_all()
            tools.debug_print("✓ Все потоки остановлены")
        except Exception as e:
            tools.debug_print(f"⚠ Ошибка при остановке потоков: {str(e)}")
        
        try:
            # Закрываем файл лога
            if g.debug.filehandle:
                tools.debug_print("Закрываем файл лога...")
                g.debug.filehandle.close()
                g.debug.filehandle                          =   None
                tools.debug_print("✓ Файл лога закрыт")
        except Exception as e:
            tools.debug_print(f"⚠ Ошибка при закрытии лога: {str(e)}")
        
        tools.debug_print(f"═══ ЗАВЕРШЕНИЕ ПРОГРАММЫ ═══")

        # os._exit, а не sys.exit: sys.exit из рабочего потока (парсер, solr) завершает только сам поток,
        # главный поток службы (SvcDoRun) продолжает крутиться по self.Stop - служба висит "зомби":
        # SCM видит Running, recovery не срабатывает, а все рабочие потоки уже остановлены.
        # Ресурсы закрыты выше (stop_all + лог), поэтому жёсткий выход здесь безопасен.
        os._exit(exit_code)
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # печать системного сообщения (всегда выводится в консоль)
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def status_print(msg: str) -> None:
        if g.execution.running_in_console:
            dt = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            print(f"[{dt}] [STATUS] {msg}", flush=True)

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # печать отладочного сообщения, работает только на взведённом глобально флаге отладки
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def debug_print(dp_msg: str, dp_thread: str = "") -> None:
        if (g.debug.on):
            dp_dt                                           =   datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
            dp_msg                                          =   dp_dt + ":::" + dp_thread + ":::" + dp_msg
            # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
            if g.execution.running_in_console:
                print(dp_msg, flush=True)
            #else:
            #    servicemanager.LogErrorMsg(dp_msg)
            # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
            with tools.log_lock:
                if(g.debug.filehandle):                                                                                 # если уже открыт
                    g.debug.filehandle.write(dp_msg + "\n")
                    g.debug.filehandle.flush()
                else:                                                                                                   # если ещё не открыт
                    if not g.debug.dir or not g.debug.filename:
                        return
                    if(not os.path.exists(g.debug.dir)):
                        os.makedirs(g.debug.dir)
                    g.debug.filehandle                      =   open(g.debug.filename, 'a', encoding='UTF8')
                    g.debug.filehandle.write(dp_msg + "\n")
                    g.debug.filehandle.flush()
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # чтение файла и возврат содержимого
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def readfile(r_filename: str, encoding: str = 'UTF8') -> str:
        if(os.path.exists(r_filename)):
            rf_handle                                       =   open(r_filename, 'r', encoding=encoding)
            rf_content                                      =   rf_handle.read()
            rf_handle.close()
            return rf_content
        else:
            return ""
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # выполняет командру SQlite3 и возвращает результат (с поддержкой параметров)
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def sqlite3_exec(sql3_file: str, sql3_cmd: str, params: Optional[Tuple[Any, ...]] = None) -> Optional[List[Tuple[Any, ...]]]:
        ret                                                 =   None
        conn                                                =   None
        if(os.path.exists(sql3_file)):
            try:
                conn                                        =   sqlite3.connect(sql3_file)
                sql                                         =   conn.cursor()
                sql.execute('''PRAGMA encoding = "UTF-8";''')
                if params:
                    ret                                     =   sql.execute(sql3_cmd, params).fetchall()
                else:
                    ret                                     =   sql.execute(sql3_cmd).fetchall()
            except Exception as e:
                tools.debug_print("Exception while sqlexec "+sql3_cmd + " on file "+sql3_file + " error is " + str(e))
            finally:
                if conn:
                    conn.close()
        return ret
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # смещение часового пояса
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def get_time_zone(self: Any = None) -> str:
        d                                                   =   datetime.datetime
        t                                                   =   int(d.timestamp(d.now()))                               # фиксирую время в epoch
        u                                                   =   int(d.utcfromtimestamp(t).timestamp())
        tz                                                  =   t - u
        return str(tz // 60)
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # 2019.01.11 17:00 всё таки надо имя править
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def normalize_ib_name(n_ib_name: str) -> str:
        n_ib_name                                           =   urllib.parse.quote(n_ib_name)
        n_ib_name                                           =   n_ib_name.replace("%","aAa_")
        return n_ib_name
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # 2019.01.28 16:30 всё таки надо имя править
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def denormalize_ib_name(dn_ib_name: str) -> str:
        dn_ib_name                                          =   dn_ib_name.replace("aAa_","%")
        return urllib.parse.unquote(dn_ib_name)
    # ------------------------------------------------------------------------------------------------------------------
    # получение количества записей lgd-файла
    # ------------------------------------------------------------------------------------------------------------------
    def get_lgd_evens_count(dlr_name: str) -> List[int]:
        dlr_ret                                             =   [0,0]
        try:
            max_row_q                                       =   tools.sqlite3_exec(
                                                                    dlr_name,
                                                                    g.parser.lgd_select_last_id
                                                                )
            min_row_q                                       =   tools.sqlite3_exec(
                                                                    dlr_name,
                                                                    g.parser.lgd_select_first_id
                                                                )
            if (isinstance(max_row_q, list) and isinstance(min_row_q, list)):                                           # если всё норм
                dlr_ret                                     =   [max_row_q[0][0],min_row_q[0][0]]
        except Exception as e:  # здесь споткнулся на битых файлах
            tools.debug_print("Exception on query lgd " + str(e))
        return dlr_ret
    # ------------------------------------------------------------------------------------------------------------------
    # запись сообщения об ошибке авторизации
    # ------------------------------------------------------------------------------------------------------------------
    def log_msg(dp_msg: str) -> None:
        g.notify.filename                                   =   os.path.join(g.execution.self_dir, "log_msg.txt")
        dp_dt                                               =   datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
        dp_msg                                              =   dp_dt + ":::" + dp_msg
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        with tools.log_lock:
            if(g.notify.filehandle):                                                                                    # если уже открыт
                g.notify.filehandle.write(dp_msg + "\n")
                g.notify.filehandle.flush()
            else:                                                                                                       # если ещё не открыт
                g.notify.filehandle                         =   open(g.notify.filename,'a',encoding='UTF8')
                g.notify.filehandle.write(dp_msg + "\n")
                g.notify.filehandle.flush()
# ======================================================================================================================
