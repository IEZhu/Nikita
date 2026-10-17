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

import  time
from    clickhouse_driver   import  Client                  as  ch
from    clickhouse_driver   import  errors                  as  ch_errors
# ======================================================================================================================
from    src                 import  globals                 as  g
# ======================================================================================================================
# выполнение произвольного запроса MCP в ClickHouse: потоковое чтение, обрезка по max_rows
# права (только чтение) обеспечивает пользователь ClickHouse MCP_CLICKHOUSE_USER (readonly=1), не этот код
# ======================================================================================================================
SOCKET_TIMEOUT_SECONDS                                  =   90                                                  # больше max_execution_time профиля ClickHouse
CONNECT_TIMEOUT_SECONDS                                 =   5
# ======================================================================================================================
class QueryError(Exception):
    """Ошибка запроса, текст которой можно отдать модели для исправления запроса."""
# ======================================================================================================================
def new_client() -> ch:
    """Отдельное подключение на каждый вызов: Client не потокобезопасен, а CherryPy многопоточный."""
    return ch(
        host                =   g.conf.clickhouse.host,
        port                =   g.conf.clickhouse.port,
        user                =   g.conf.mcp.clickhouse_user,
        password            =   g.conf.mcp.clickhouse_password,
        database            =   g.conf.clickhouse.database,
        connect_timeout     =   CONNECT_TIMEOUT_SECONDS,
        send_receive_timeout=   SOCKET_TIMEOUT_SECONDS,
    )
# ======================================================================================================================
def run_query(sql: str, max_rows: int, client_factory=new_client) -> dict:
    """Выполняет sql, читает не больше max_rows строк и возвращает колонки, строки и признак обрезки."""
    started                                             =   time.monotonic()
    client                                              =   client_factory()
    columns                                             =   []
    rows                                                =   []
    truncated                                           =   False
    try:
        result_iterator                                 =   client.execute_iter(sql, with_column_types=True)
        for index, item in enumerate(result_iterator):
            if index == 0:
                columns                                 =   [{"name": name, "type": type_name} for name, type_name in item]
                continue
            if len(rows) >= max_rows:
                truncated                               =   True
                break
            rows.append(list(item))
    except ch_errors.ServerException as e:
        raise QueryError(f"ClickHouse error {e.code}: {e.message}") from e
    except (ch_errors.Error, OSError, EOFError) as e:
        raise QueryError(f"ClickHouse недоступен: {e}") from e
    finally:
        client.disconnect()                                                                                     # обрывает недочитанный остаток результата
    return {
        "columns"       :   columns,
        "rows"          :   rows,
        "row_count"     :   len(rows),
        "truncated"     :   truncated,
        "elapsed_ms"    :   int((time.monotonic() - started) * 1000),
    }
