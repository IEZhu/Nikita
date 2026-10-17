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

import  json
import  base64
import  binascii
import  math
import  traceback
# ======================================================================================================================
from    src                 import  globals                 as  g
from    src                 import  mcp_query               as  q
from    src.tools           import  tools                   as  t
# ======================================================================================================================
# MCP-эндпоинт (Streamable HTTP) без состояния: ответ всегда один JSON, без SSE и без сессий.
# Две эпохи протокола на одном эндпоинте:
#   modern 2026-07-28 — метаданные в params._meta каждого запроса, заголовки сверяются с телом;
#   legacy 2025-03-26 … 2025-11-25 — рукопожатие initialize.
# Авторизации здесь нет: /mcp публикуется только за аутентифицирующим прокси.
# ======================================================================================================================
MODERN_VERSION                                          =   "2026-07-28"
LEGACY_VERSIONS                                         =   ("2025-11-25", "2025-06-18", "2025-03-26")
SUPPORTED_VERSIONS                                      =   [MODERN_VERSION] + list(LEGACY_VERSIONS)
HEADERLESS_LEGACY_VERSION                               =   "2025-03-26"                                        # клиенты до 2025-06-18 не шлют MCP-Protocol-Version

META_PROTOCOL_VERSION                                   =   "io.modelcontextprotocol/protocolVersion"
META_SERVER_INFO                                        =   "io.modelcontextprotocol/serverInfo"

ERROR_PARSE                                             =   -32700
ERROR_INVALID_REQUEST                                   =   -32600
ERROR_METHOD_NOT_FOUND                                  =   -32601
ERROR_INVALID_PARAMS                                    =   -32602
ERROR_HEADER_MISMATCH                                   =   -32020
ERROR_UNSUPPORTED_VERSION                               =   -32022
ERROR_INTERNAL                                          =   -32603

DEFAULT_MAX_ROWS                                        =   200
LOGGER_NAME                                             =   "mcp"

INSTRUCTIONS                                            =   (
    "Nikita хранит журнал регистрации 1С в ClickHouse: база zhr1c, по таблице на каждую информационную базу. "
    "Инструмент query выполняет любой читающий SQL ClickHouse. Начните с SHOW TABLES FROM zhr1c "
    "и DESCRIBE TABLE zhr1c.`<таблица>`. Ограничивайте выборку по времени и используйте LIMIT."
)
# ======================================================================================================================
# формирование HTTP-ответов
# ======================================================================================================================
def json_response(status: int, message: dict) -> tuple:
    body                                                =   json.dumps(message, ensure_ascii=False, default=str, allow_nan=False).encode("utf-8")
    return status, {"Content-Type": "application/json; charset=utf-8"}, body


def error_response(status: int, request_id, code: int, message: str, data=None) -> tuple:
    error                                               =   {"code": code, "message": message}
    if data is not None:
        error["data"]                                   =   data
    return json_response(status, {"jsonrpc": "2.0", "id": request_id, "error": error})


def result_response(request_id, result: dict, modern: bool) -> tuple:
    if modern:
        result                                          =   dict(result, resultType="complete")
    return json_response(200, {"jsonrpc": "2.0", "id": request_id, "result": result})
# ======================================================================================================================
# описание сервера и инструмента
# ======================================================================================================================
def server_info() -> dict:
    return {"name": "Nikita", "version": g.service.build_version}


def query_tool() -> dict:
    return {
        "name": "query",
        "title": "SQL к журналу регистрации 1С",
        "description": (
            "Выполняет произвольный читающий SQL-запрос ClickHouse к журналу регистрации 1С (база zhr1c, "
            "таблица на каждую информационную базу; список — SHOW TABLES FROM zhr1c, колонки — DESCRIBE TABLE). "
            "Запись запрещена на стороне ClickHouse. Возвращает колонки, строки (не больше max_rows) и признак truncated."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "Один SQL-запрос ClickHouse без завершающей точки с запятой"},
                "max_rows": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": g.conf.mcp.max_rows,
                    "default": DEFAULT_MAX_ROWS,
                    "description": "Сколько строк вернуть, не больше " + str(g.conf.mcp.max_rows),
                },
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    }
# ======================================================================================================================
# инструмент query
# ======================================================================================================================
def tool_error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def json_safe(value):
    """NaN и ±Inf из ClickHouse заменяет на None: в JSON их нет; остальное оставляет для default=str."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    return value


def call_query_tool(arguments: dict) -> dict:
    sql                                                 =   arguments.get("sql")
    max_rows                                            =   arguments.get("max_rows", DEFAULT_MAX_ROWS)
    if not isinstance(sql, str) or not sql.strip():
        return tool_error("Параметр sql обязателен и должен быть непустой строкой")
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
        return tool_error("Параметр max_rows должен быть целым числом от 1")
    max_rows                                            =   min(max_rows, g.conf.mcp.max_rows)
    try:
        result                                          =   q.run_query(sql, max_rows)
    except q.QueryError as e:
        t.debug_print(f"query failed: {str(e)[:300]} | sql: {sql[:200]}", LOGGER_NAME)
        return tool_error(str(e))
    t.debug_print(f"query ok: {result['row_count']} rows, truncated={result['truncated']}, {result['elapsed_ms']} ms | sql: {sql[:200]}", LOGGER_NAME)
    structured                                          =   json_safe(result)
    text                                                =   json.dumps(structured, ensure_ascii=False, default=str, allow_nan=False)
    return {"content": [{"type": "text", "text": text}], "structuredContent": structured, "isError": False}
# ======================================================================================================================
# разбор методов, общий для обеих эпох
# ======================================================================================================================
def dispatch(request_id, method: str, params: dict, modern: bool) -> tuple:
    if method == "ping":
        return result_response(request_id, {}, modern)
    if method == "tools/list":
        return result_response(request_id, {"tools": [query_tool()]}, modern)
    if method == "tools/call":
        if params.get("name") != "query":
            return error_response(200, request_id, ERROR_INVALID_PARAMS, f"Unknown tool: {params.get('name')}")
        arguments                                       =   params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return error_response(200, request_id, ERROR_INVALID_PARAMS, "arguments must be an object")
        return result_response(request_id, call_query_tool(arguments), modern)
    if modern and method == "server/discover":
        return result_response(request_id, {
            "supportedVersions": SUPPORTED_VERSIONS,
            "capabilities": {"tools": {}},
            "_meta": {META_SERVER_INFO: server_info()},
            "instructions": INSTRUCTIONS,
        }, modern)
    return error_response(404 if modern else 200, request_id, ERROR_METHOD_NOT_FOUND, f"Method not found: {method}")
# ======================================================================================================================
# modern-эпоха: проверка заголовков против тела
# ======================================================================================================================
def decode_header_value(value):
    """Снимает обёртку =?base64?…?= у Mcp-Name и Mcp-Param-*; None, если base64 битый."""
    if value is None or not (value.startswith("=?base64?") and value.endswith("?=")):
        return value
    try:
        return base64.b64decode(value[len("=?base64?"):-len("?=")], validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None


def handle_modern(request_id, method: str, params: dict, version, headers: dict) -> tuple:
    header_version                                      =   headers.get("mcp-protocol-version")
    if header_version != version:
        return error_response(400, request_id, ERROR_HEADER_MISMATCH, f"Header mismatch: MCP-Protocol-Version {header_version!r} does not match body {version!r}")
    if version != MODERN_VERSION:
        return error_response(400, request_id, ERROR_UNSUPPORTED_VERSION, "Unsupported protocol version", {"supported": SUPPORTED_VERSIONS, "requested": version})
    if headers.get("mcp-method") != method:
        return error_response(400, request_id, ERROR_HEADER_MISMATCH, f"Header mismatch: Mcp-Method {headers.get('mcp-method')!r} does not match body {method!r}")
    if method == "tools/call" and decode_header_value(headers.get("mcp-name")) != params.get("name"):
        return error_response(400, request_id, ERROR_HEADER_MISMATCH, f"Header mismatch: Mcp-Name does not match body {params.get('name')!r}")
    return dispatch(request_id, method, params, modern=True)
# ======================================================================================================================
# legacy-эпоха: initialize и запросы после него
# ======================================================================================================================
def handle_legacy(request_id, method: str, params: dict, headers: dict) -> tuple:
    if method == "initialize":
        requested                                       =   params.get("protocolVersion")
        version                                         =   requested if requested in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        return result_response(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": server_info(),
            "instructions": INSTRUCTIONS,
        }, modern=False)
    header_version                                      =   headers.get("mcp-protocol-version", HEADERLESS_LEGACY_VERSION)
    if header_version == MODERN_VERSION:
        return error_response(400, request_id, ERROR_HEADER_MISMATCH, "Header mismatch: MCP-Protocol-Version 2026-07-28 requires params._meta")
    if header_version not in LEGACY_VERSIONS:
        return error_response(400, request_id, ERROR_UNSUPPORTED_VERSION, "Unsupported protocol version", {"supported": SUPPORTED_VERSIONS, "requested": header_version})
    return dispatch(request_id, method, params, modern=False)
# ======================================================================================================================
# точка входа из cherry.py
# ======================================================================================================================
def handle(http_method: str, headers: dict, body: bytes) -> tuple:
    """Обрабатывает один HTTP-запрос к /mcp. headers — с именами в нижнем регистре. Возвращает (status, headers, body)."""
    if not g.conf.mcp.enabled:
        return 404, {}, b""
    origin                                              =   headers.get("origin")
    if origin and origin not in g.conf.mcp.allowed_origins:
        return error_response(403, None, ERROR_INVALID_REQUEST, "Origin not allowed")
    if http_method != "POST":
        return 405, {"Allow": "POST"}, b""
    if len(body) > g.conf.mcp.max_body:
        return error_response(413, None, ERROR_INVALID_REQUEST, "Request body too large")
    try:
        message                                         =   json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return error_response(400, None, ERROR_PARSE, "Parse error")
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return error_response(400, message.get("id") if isinstance(message, dict) else None, ERROR_INVALID_REQUEST, "Invalid Request")
    if "id" not in message:                                                                                     # нотификация: принять и ничего не отвечать
        return 202, {}, b""
    request_id                                          =   message["id"]
    method                                              =   message["method"]
    params                                              =   message.get("params") or {}
    if not isinstance(params, dict):
        return error_response(400, request_id, ERROR_INVALID_PARAMS, "params must be an object")
    try:
        meta                                            =   params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        if META_PROTOCOL_VERSION in meta:
            return handle_modern(request_id, method, params, meta[META_PROTOCOL_VERSION], headers)
        return handle_legacy(request_id, method, params, headers)
    except Exception:
        t.debug_print(f"internal error: {traceback.format_exc()}", LOGGER_NAME)
        return error_response(500, request_id, ERROR_INTERNAL, "Internal error")
