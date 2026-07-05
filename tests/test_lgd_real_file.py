# -*- coding: utf-8 -*-
"""
Регресс-тест разбора LGD на настоящем parser.parse_lgd_file.

Гонит реальный parse_lgd_file поверх SQLite-LGD (заглушки только на персистентность —
Solr/SQLite-состояние) и проверяет, что каждая запись доходит до коммита без KeyError
и без вызова graceful_shutdown.

Регресс для бага "Exception while add_to_json 1" -> graceful_shutdown(111):
parse_lgd_file отдавал в add_to_json_data сырые int-коды из SQLite, а словари ЖР
ключуются строками -> KeyError(1) -> str(e)=="1" -> вся служба падала на старте.
Исправлено приведением кодов к str() (коммит 8b8ccd2).

Регресс для бага "Exception while add_to_json '97'" (ЖР ZP, 2026-07-02):
1С дописывает новые коды (пользователь/компьютер/событие/метаданные) в словарь
1Cv8.lgd на ходу, а LGD-ветка парсера словарь после старта не перечитывала ->
KeyError('97') -> graceful_shutdown(111) ронял всю службу. Исправлено перечитыванием
словаря перед каждым блоком + self-heal (перечитать и повторить) в add_to_json_data.

Тест-классы:
- TestParseSyntheticLgd            — всегда выполняется в CI: строит синтетический мини-LGD
  во временном файле (без реальных, потенциально чувствительных данных журнала).
- TestLgdDictRefreshMidParse       — регресс '97': код дописан в словарь после старта.
- TestAddToJsonSelfHealOnKeyError  — self-heal retry в add_to_json_data и сохранение
  семантики graceful_shutdown для реально отсутствующих кодов.
- TestParseRealLgd                 — опциональный полный прогон на test_data/1cv8.lgd, если файл
  есть локально. Объём строк: NIKITA_LGD_TEST_LIMIT (0 -> весь файл).
"""
import os
import sys
import sqlite3
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import tests.mock_env
tests.mock_env.setup_mocks()

from src import parser as P
from src import globals as g
from src.tools import tools as t
from src.dictionaries import dictionary as d
from src import sender as snd

REAL_LGD_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'test_data', '1cv8.lgd'))
BASE = 'UU'
ROW_LIMIT = int(os.getenv('NIKITA_LGD_TEST_LIMIT', '20000'))    # 0 -> весь файл

# 1С-таймстамп (0.0001 c с 0001 года): соответствует ~2025-01-01, валиден для fromtimestamp
_TS_2025 = 638719344000000

# Словари c1_dicts, которые read_new_ib_dictionary заполняет под ключом базы
_C1_DICT_ATTRS = (
    "users", "computers", "applications", "actions", "metadata",
    "servers", "ports_main", "ports_add",
    "ext_main_id", "ext_add_id", "ext_area_main_ids", "ext_area_add_ids",
)


def _restore_globals(prev_debug_on):
    """Возвращает процессные глобалы в исходное состояние после класса тестов:
    g.debug.on и записи словарей под ключом BASE (их добавляет read_new_ib_dictionary)."""
    g.debug.on = prev_debug_on
    for attr in _C1_DICT_ATTRS:
        getattr(g.execution.c1_dicts, attr).pop(BASE, None)


def _register_ibase(jr_dir):
    """Регистрирует базу BASE в g.parser.ibases: с фиксом '97' парсер зовёт
    d.read_ib_dictionary(pf_base) перед каждым блоком, а тот резолвит путь к словарю
    через ibases (jr_dir + '/1Cv8.lgd'). Возвращает запись для последующего remove."""
    entry = {
        g.nms.ib.name:        BASE,
        g.nms.ib.jr_dir:      jr_dir,
        g.nms.ib.jr_format:   'lgd',
        g.nms.ib.total_size:  0,
        g.nms.ib.parsed_size: 0,
    }
    g.parser.ibases.append(entry)
    return entry


def _make_parser_instance():
    """parser без запуска потоков/ClickHouse: только база Thread + нужные поля.
    parser наследует threading.Thread, поэтому .name — property и требует Thread.__init__."""
    inst = P.parser.__new__(P.parser)
    threading.Thread.__init__(inst)
    inst.name = 'lgd parser (test)'
    inst.json_data = {inst.name: []}
    inst.stopMe = False
    inst.chclient = None
    return inst


def _run_parse(path, base, row_limit, collected):
    """Запускает настоящий parse_lgd_file; persistence замокана, graceful_shutdown
    перехвачен. Возвращает (gs_called, gs_calls)."""
    inst = _make_parser_instance()

    def fake_commit(pf_name, pf_base, file_state, batch_start_offset, records_to_log):
        # Solr и SQLite-состояние в тесте не нужны: фиксируем, что запись дошла до commit
        collected.extend(records_to_log)
        del inst.json_data[inst.name][:]
        return True
    inst.commit_json_data = fake_commit

    real_count = P.t.get_lgd_evens_count                       # оригинал до патча
    def capped_count(p):
        max_row, min_row = real_count(p)
        if row_limit and (max_row - min_row + 1) > row_limit:
            max_row = min_row + row_limit - 1
        return (max_row, min_row)

    with patch.object(t, 'graceful_shutdown') as gs, \
         patch.object(P.state_manager, 'get_file_state', return_value=None), \
         patch.object(P.t, 'get_lgd_evens_count', side_effect=capped_count):
        inst.parse_lgd_file(path, base)
        return gs.called, list(gs.call_args_list)


def _build_synthetic_lgd(path):
    """Создаёт минимальный, но валидный LGD (SQLite): словарные таблицы + EventLog.
    Чтобы тест выполнялся в CI без реального (бинарного и потенциально чувствительного)
    журнала. Строки покрывают: разрешимые коды, нулевые/NULL-поля и отсутствующий
    код приложения (мягкая ветка 'Not Found'), а также вторичный порт."""
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE UserCodes          (code INTEGER, name TEXT, uuid TEXT);
        CREATE TABLE ComputerCodes      (code INTEGER, name TEXT);
        CREATE TABLE AppCodes           (code INTEGER, name TEXT);
        CREATE TABLE EventCodes         (code INTEGER, name TEXT);
        CREATE TABLE MetadataCodes      (code INTEGER, name TEXT, uuid TEXT);
        CREATE TABLE WorkServerCodes    (code INTEGER, name TEXT);
        CREATE TABLE PrimaryPortCodes   (code INTEGER, port INTEGER);
        CREATE TABLE SecondaryPortCodes (code INTEGER, port INTEGER);
        CREATE TABLE EventLog (
            date INTEGER, transactionStatus INTEGER, transactionDate INTEGER,
            transactionID INTEGER, userCode INTEGER, computerCode INTEGER,
            appCode INTEGER, connectID INTEGER, eventCode INTEGER, severity INTEGER,
            comment TEXT, metadataCodes INTEGER, data TEXT, dataPresentation TEXT,
            workServerCode INTEGER, primaryPortCode INTEGER, secondaryPortCode INTEGER,
            session INTEGER, rowID INTEGER
        );
        """
    )
    cur.execute("INSERT INTO UserCodes VALUES (1,'TestUser','11111111-1111-1111-1111-111111111111')")
    cur.execute("INSERT INTO ComputerCodes VALUES (1,'TestPC')")
    cur.execute("INSERT INTO AppCodes VALUES (1,'1CV8')")
    cur.execute("INSERT INTO EventCodes VALUES (1,'_$Session$_.Start')")
    cur.execute("INSERT INTO MetadataCodes VALUES (1,'TestMeta','22222222-2222-2222-2222-222222222222')")
    cur.execute("INSERT INTO WorkServerCodes VALUES (1,'TestSrv')")
    cur.execute("INSERT INTO PrimaryPortCodes VALUES (1,1540)")
    cur.execute("INSERT INTO SecondaryPortCodes VALUES (1,1541)")
    rows = [
        # все коды разрешимы, вторичный порт тоже (code=1 есть)
        (_TS_2025,         2, 12345, 67890, 1, 1, 1, 42, 1, 1, 'login ok', 1, 'somedata', 'pres', 1, 1, 1, 777, 1),
        # нулевые коды (ветки-заглушки) + NULL comment/data/dataPresentation + meta=0
        (_TS_2025 + 600000, 0, 0, 0, 0, 0, 1, 0, 1, 0, None, 0, None, None, 0, 0, 0, 0, 2),
        # отсутствующий код приложения 999 -> мягкая ветка "Not Found in Dictionary" (без падения)
        (_TS_2025 + 1200000, 1, 1, 2, 1, 1, 999, 5, 1, 3, 'warn', 1, 'x', 'y', 1, 1, 0, 9, 3),
    ]
    cur.executemany(
        "INSERT INTO EventLog VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()
    return len(rows)


class TestParseSyntheticLgd(unittest.TestCase):
    """Всегда-выполняемый CI-регресс: настоящий parse_lgd_file поверх синтетического LGD."""
    @classmethod
    def setUpClass(cls):
        cls._prev_debug_on = g.debug.on
        g.debug.on = False
        cls.tmp = tempfile.mkdtemp(prefix='nikita_lgd_test_')
        cls.path = os.path.join(cls.tmp, '1Cv8.lgd')           # имя как в проде: read_ib_dictionary ищет jr_dir + '/1Cv8.lgd'
        cls.expected_rows = _build_synthetic_lgd(cls.path)
        cls._ib_entry = _register_ibase(cls.tmp)
        d.read_new_ib_dictionary(BASE, cls.path)               # реальные словари из синтетического файла
        cls.collected = []
        cls.gs_called, cls.gs_calls = _run_parse(cls.path, BASE, 0, cls.collected)

    @classmethod
    def tearDownClass(cls):
        g.parser.ibases.remove(cls._ib_entry)
        _restore_globals(cls._prev_debug_on)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_parses_without_graceful_shutdown(self):
        """Все записи разбираются без KeyError -> add_to_json_data не зовёт graceful_shutdown."""
        self.assertFalse(
            self.gs_called,
            f"add_to_json_data упал и вызвал graceful_shutdown{self.gs_calls} — "
            f"повтор бага разбора LGD (int-коды vs строковые ключи словарей)"
        )

    def test_all_rows_reached_commit(self):
        """Парсер разобрал все строки (не «тихо» пропустил)."""
        self.assertEqual(len(self.collected), self.expected_rows)

    def test_records_project_to_valid_solr_docs(self):
        """Обязательные поля проверяем на РЕЗУЛЬТАТЕ project_solr_doc (doc), а не на
        исходной записи rec: иначе тест не поймает, если проекция отбросит/переименует поле."""
        self.assertTrue(self.collected)
        for rec in self.collected:
            doc = snd.project_solr_doc(rec)
            for field in ('id', 'date', 't_status', 'event_id', 'user_id'):
                self.assertIn(field, doc, f"поле '{field}' пропало из Solr-документа")
            self.assertTrue(doc['id'], "у Solr-документа пустой id (uniqueKey схемы)")


class TestLgdDictRefreshMidParse(unittest.TestCase):
    """Регресс "Exception while add_to_json '97'": 1С дописала новый код в словарь
    1Cv8.lgd ПОСЛЕ загрузки словарей при старте службы; запись ЖР с этим кодом роняла
    весь сервис через graceful_shutdown(111). Проверяем, что парсер перечитывает словарь
    и запись разрешается по-настоящему (не через мягкую ветку 'Not Found')."""
    @classmethod
    def setUpClass(cls):
        cls._prev_debug_on = g.debug.on
        g.debug.on = False
        cls.tmp = tempfile.mkdtemp(prefix='nikita_lgd_test_')
        cls.path = os.path.join(cls.tmp, '1Cv8.lgd')
        cls.expected_rows = _build_synthetic_lgd(cls.path)
        cls._ib_entry = _register_ibase(cls.tmp)
        d.read_new_ib_dictionary(BASE, cls.path)               # снапшот словарей "на старте службы"
        # 1С дописывает нового пользователя (код 97) и запись ЖР с ним — ПОСЛЕ снапшота
        conn = sqlite3.connect(cls.path)
        cur = conn.cursor()
        cur.execute("INSERT INTO UserCodes VALUES (97,'LateUser','33333333-3333-3333-3333-333333333333')")
        cur.execute(
            "INSERT INTO EventLog VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_TS_2025 + 1800000, 2, 1, 2, 97, 1, 1, 7, 1, 1, 'late user login', 1, 'd', 'p', 1, 1, 0, 10, 4),
        )
        conn.commit()
        conn.close()
        cls.expected_rows += 1
        assert '97' not in g.execution.c1_dicts.users[BASE], "предусловие: снапшот словаря должен быть протухшим"
        cls.collected = []
        cls.gs_called, cls.gs_calls = _run_parse(cls.path, BASE, 0, cls.collected)

    @classmethod
    def tearDownClass(cls):
        g.parser.ibases.remove(cls._ib_entry)
        _restore_globals(cls._prev_debug_on)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_no_graceful_shutdown_on_late_dict_code(self):
        self.assertFalse(
            self.gs_called,
            f"graceful_shutdown{self.gs_calls} — запись с кодом, дописанным в словарь "
            f"после старта службы, уронила парсер (регресс '97')"
        )

    def test_all_rows_reached_commit(self):
        self.assertEqual(len(self.collected), self.expected_rows)

    def test_late_code_resolved_from_refreshed_dict(self):
        late = [r for r in self.collected if r.get('user_id') == '97']
        self.assertEqual(len(late), 1, "запись с поздним кодом 97 не дошла до коммита")
        self.assertEqual(late[0]['user']['name'], 'LateUser',
                         "код 97 должен разрешиться из перечитанного словаря")


class TestAddToJsonSelfHealOnKeyError(unittest.TestCase):
    """add_to_json_data: KeyError по протухшему словарю -> перечитать словарь и повторить
    (закрывает гонку внутри одного блока, когда код появился после блочного перечитывания).
    Для кода, которого нет и в файле, семантика graceful_shutdown(111) сохраняется."""
    @classmethod
    def setUpClass(cls):
        cls._prev_debug_on = g.debug.on
        g.debug.on = False
        cls.tmp = tempfile.mkdtemp(prefix='nikita_lgd_test_')
        cls.path = os.path.join(cls.tmp, '1Cv8.lgd')
        _build_synthetic_lgd(cls.path)
        cls._ib_entry = _register_ibase(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        g.parser.ibases.remove(cls._ib_entry)
        _restore_globals(cls._prev_debug_on)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def _make_rec(user_code):
        # минимально валидная распарсенная запись ЖР (формат rslt из parse_lgd_file)
        return {0: '20250101120000', 1: 'C', 2: '1a', 3: '2b', 4: user_code, 5: '1',
                6: '1', 7: '42', 8: '1', 9: '1', 10: '', 11: '0', 12: '', 13: '',
                14: '1', 15: '1', 16: '1', 17: '7', 18: '0', 19: '0'}

    def test_keyerror_triggers_dict_reload_and_retry(self):
        d.read_new_ib_dictionary(BASE, self.path)
        del g.execution.c1_dicts.users[BASE]['1']              # имитируем протухший снапшот
        inst = _make_parser_instance()
        with patch.object(t, 'graceful_shutdown') as gs:
            ok = inst.add_to_json_data(self._make_rec('1'), 0, 'f', 1, 0, BASE)
        self.assertTrue(ok, "self-heal должен перечитать словарь и добавить запись")
        self.assertFalse(gs.called, f"self-heal не сработал: graceful_shutdown{gs.call_args_list}")
        doc = inst.json_data[inst.name][-1]
        self.assertEqual(doc['user']['name'], 'TestUser', "пользователь должен разрешиться после перечитывания")

    def test_keyerror_after_retry_still_shuts_down(self):
        d.read_new_ib_dictionary(BASE, self.path)
        inst = _make_parser_instance()
        with patch.object(t, 'graceful_shutdown') as gs:
            ok = inst.add_to_json_data(self._make_rec('424242'), 0, 'f', 1, 0, BASE)   # кода 424242 нет и в файле
        self.assertFalse(ok)
        gs.assert_called_once_with(111)                        # семантика для реально битых данных сохранена


@unittest.skipUnless(os.path.exists(REAL_LGD_PATH), f"опц. полный прогон: нет {REAL_LGD_PATH}")
class TestParseRealLgd(unittest.TestCase):
    """Опциональный полный прогон на реальном журнале, если он есть локально."""
    @classmethod
    def setUpClass(cls):
        cls._prev_debug_on = g.debug.on
        g.debug.on = False
        conn = sqlite3.connect(REAL_LGD_PATH)                  # ожидание выводим из самого файла
        try:                                                   # __exit__ sqlite3 делает только commit/rollback, не close
            cls.total_rows = conn.execute("SELECT COUNT(*) FROM EventLog").fetchone()[0]
        finally:
            conn.close()
        cls._ib_entry = _register_ibase(os.path.dirname(REAL_LGD_PATH))
        d.read_new_ib_dictionary(BASE, REAL_LGD_PATH)
        cls.collected = []
        cls.gs_called, cls.gs_calls = _run_parse(REAL_LGD_PATH, BASE, ROW_LIMIT, cls.collected)

    @classmethod
    def tearDownClass(cls):
        g.parser.ibases.remove(cls._ib_entry)
        _restore_globals(cls._prev_debug_on)

    def test_parses_without_graceful_shutdown(self):
        self.assertFalse(
            self.gs_called,
            f"add_to_json_data упал и вызвал graceful_shutdown{self.gs_calls} на реальном LGD"
        )

    def test_expected_row_count(self):
        # фикстура не коммитится и может отличаться -> сверяемся с самим файлом,
        # с тем же кэпом, что применяет _run_parse
        expected = min(ROW_LIMIT, self.total_rows) if ROW_LIMIT else self.total_rows
        self.assertEqual(len(self.collected), expected)


if __name__ == '__main__':
    unittest.main()
