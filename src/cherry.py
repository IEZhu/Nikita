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

import  cherrypy
import  urllib
import  threading
import  locale
import  json
from    datetime           import  datetime
# ======================================================================================================================
from    src.tools           import  tools                   as  t
from    src                 import  globals                 as  g
from    src                 import  reader                  as  r
from    src                 import  mcp                     as  m
# ======================================================================================================================
# собственно, имплементация веб-сервера
# ======================================================================================================================
class nikita_web(object):
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    @cherrypy.expose
    def index(self):
        locale.setlocale(locale.LC_ALL,"")
        
        # Подготовка данных для статистики
        uptime_str                                          =   "Н/Д"
        if g.stats.start_time:
            uptime                                          =   (datetime.now() - g.stats.start_time).total_seconds()
            uptime_str                                      =   f"{int(uptime // 3600)}ч {int((uptime % 3600) // 60)}м {int(uptime % 60)}с"
            
        # Определяем время последней активности (максимум из всех сервисов)
        last_times                                          =   []
        if g.conf.clickhouse.enabled and g.stats.clickhouse_last_success_time:
            last_times.append(g.stats.clickhouse_last_success_time)
        if g.conf.solr.enabled and g.stats.solr_last_success_time:
            last_times.append(g.stats.solr_last_success_time)
        if g.conf.redis.enabled and g.stats.redis_last_success_time:
            last_times.append(g.stats.redis_last_success_time)
            
        last_activity_str                                   =   "Нет данных"
        last_activity_iso                                   =   ""
        if last_times:
            last_max                                        =   max(last_times)
            last_activity_str                               =   last_max.strftime("%H:%M:%S")
            last_activity_iso                               =   last_max.isoformat()

        # ======= Блок статистики (Таблица) ============================================================================
        stats_block                                         =   ""
        stats_block                                         +=  '<div class="stats-container">'
        stats_block                                         +=  '<h2>Статистика сервисов</h2>'
        stats_block                                         +=  '<div class="table stats-table">'
        
        # Заголовки колонок
        stats_block                                         +=  '<div class="row header">'
        stats_block                                         +=  '<span class="cell">ClickHouse</span>'
        stats_block                                         +=  '<span class="cell">Solr</span>'
        stats_block                                         +=  '<span class="cell">Redis</span>'
        stats_block                                         +=  '<span class="cell">БД</span>'
        stats_block                                         +=  '<span class="cell">Записей<br>(с запуска)</span>'
        stats_block                                         +=  '<span class="cell">Ошибок<br>(с запуска)</span>'
        stats_block                                         +=  '</div>'
        
        # Строка со значениями
        stats_block                                         +=  '<div class="row">'
        
        # ClickHouse Status
        if g.conf.clickhouse.enabled:
            ch_icon                                         =   "🟢" if g.stats.clickhouse_connection_ok else "🔴"
            ch_text                                         =   f"{g.conf.clickhouse.host}:{g.conf.clickhouse.port}" if g.stats.clickhouse_connection_ok else "Ошибка"
            stats_block                                     +=  f'<span class="cell">{ch_icon} {ch_text}</span>'
        else:
            stats_block                                     +=  '<span class="cell disabled">Отключено</span>'
        
        # Solr Status
        if g.conf.solr.enabled:
            solr_icon                                       =   "🟢" if g.stats.solr_connection_ok else "🔴"
            solr_text                                       =   f"{g.conf.solr.solr_host}:{g.conf.solr.solr_port}" if g.stats.solr_connection_ok else "Ошибка"
            stats_block                                     +=  f'<span class="cell">{solr_icon} {solr_text}</span>'
        else:
            stats_block                                     +=  '<span class="cell disabled">Отключено</span>'
        
        # Redis Status
        if g.conf.redis.enabled:
            redis_icon                                      =   "🟢" if g.stats.redis_connection_ok else "🔴"
            redis_text                                      =   f"{g.conf.redis.host}:{g.conf.redis.port}" if g.stats.redis_connection_ok else "Ошибка"
            stats_block                                     +=  f'<span class="cell">{redis_icon} {redis_text}</span>'
        else:
            stats_block                                     +=  '<span class="cell disabled">Отключено</span>'
        
        
        # Базы данных (только для включенных сервисов)
        db_list                                             =   []
        if g.conf.clickhouse.enabled:
            db_list.append(g.conf.clickhouse.database)
        if g.conf.solr.enabled:
            db_list.append('default')
        if g.conf.redis.enabled:
            db_list.append(str(g.conf.redis.db))
        stats_block                                         +=  f'<span class="cell">{("<br>".join(db_list)) if db_list else "-"}</span>'
        
        # Записей (с запуска) - только для включенных сервисов
        sent_list                                           =   []
        if g.conf.clickhouse.enabled:
            sent_list.append(locale.format_string("%d", g.stats.clickhouse_total_sent, grouping=True))
        if g.conf.solr.enabled:
            sent_list.append(locale.format_string("%d", g.stats.solr_total_sent, grouping=True))
        if g.conf.redis.enabled:
            sent_list.append(locale.format_string("%d", g.stats.redis_total_queued, grouping=True))
        stats_block                                         +=  f'<span class="cell">{("<br>".join(sent_list)) if sent_list else "-"}</span>'
        
        # Ошибок (с запуска) - только для включенных сервисов
        errors_list                                         =   []
        if g.conf.clickhouse.enabled:
            errors_list.append(str(g.stats.clickhouse_total_errors))
        if g.conf.solr.enabled:
            errors_list.append(str(g.stats.solr_total_errors))
        if g.conf.redis.enabled:
            errors_list.append(str(g.stats.redis_total_errors))
        stats_block                                         +=  f'<span class="cell">{("<br>".join(errors_list)) if errors_list else "-"}</span>'
        
        stats_block                                         +=  '</div>' # end row
        stats_block                                         +=  '</div>' # end table
        
        # Последние ошибки (глобальные)
        if g.stats.last_errors:
            stats_block                                     +=  '<div class="errors-list" style="margin-top: 15px; border-top: 1px solid #eee; padding-top: 10px;">'
            stats_block                                     +=  '<h3>🚨 Последние ошибки</h3>'
            for error_time, error_type, error_msg in reversed(g.stats.last_errors[-3:]):
                error_iso                                   =   error_time.isoformat()
                stats_block                                 +=  '<div class="stats-row error" style="font-size: 0.9em;">'
                stats_block                                 +=  f'<span class="stats-label">[<span class="time-val" data-ts="{error_iso}">{error_time.strftime("%H:%M:%S")}</span>] {error_type}:</span>'
                stats_block                                 +=  f'<span class="stats-value">{error_msg[:120]}</span>'
                stats_block                                 +=  '</div>'
            stats_block                                     +=  '</div>'
            
        stats_block                                         +=  '</div>' # end stats-container

        
        # ======= Блок обрабатываемых баз ==============================================================================
        # Сначала вычисляем итоги (создаем копию списка с блокировкой для thread-safety)
        from src.state_manager import state_manager
        with g.ibases_lock:
            ibases_snapshot                                 =   list(g.parser.ibases)
        
        total_size_all                                      =   0
        total_parsed_all                                    =   0
        total_sent_all                                      =   0
        
        for base in ibases_snapshot:
            base_total                                      =   base[g.nms.ib.total_size]\
                                                                if base[g.nms.ib.total_size]>=base[g.nms.ib.parsed_size]\
                                                                else base[g.nms.ib.parsed_size]
            total_size_all                                  +=  base_total
            total_parsed_all                                +=  base[g.nms.ib.parsed_size]
            
            base_name                                       =   t.denormalize_ib_name(base[g.nms.ib.name])
            records_sent                                    =   state_manager.get_total_records_sent(base_name)
            # Если не нашли по денормализованному имени, пробуем нормализованное
            if records_sent == 0:
                records_sent                                =   state_manager.get_total_records_sent(base[g.nms.ib.name])
            total_sent_all                                  +=  records_sent
        
        bases                                               =   ""
        bases                                               +=  '<div class="table-container">'
        bases                                               +=  '<h2>Обрабатываемые базы</h2>'
        bases                                               +=  '<div class="table">'
        
        # Строка ИТОГО (зеленоватая)
        bases                                               +=  '<div class="row total">'
        bases                                               +=  '<span class="cell"><b>ИТОГО</b></span>'
        bases                                               +=  '<span class="cell"></span>'  # Путь - пусто
        bases                                               +=  '<span class="cell"></span>'  # Тип - пусто
        bases                                               +=  f'<span class="cell size-value" data-val="{total_size_all}" data-type="lgf">'  \
                                                            +   locale.format_string('%d', total_size_all, grouping=True, monetary=True)   \
                                                            +   ' байт</span>'
        bases                                               +=  f'<span class="cell size-value" data-val="{total_parsed_all}" data-type="lgf">'  \
                                                            +   locale.format_string('%d', total_parsed_all, grouping=True, monetary=True)  \
                                                            +   ' байт</span>'
        bases                                               +=  f'<span class="cell">'  \
                                                            +   locale.format_string('%d', total_sent_all, grouping=True)  \
                                                            +   '</span>'
        bases                                               +=  '<span class="cell"></span>'  # % - пусто
        bases                                               +=  '</div>'
        
        # Заголовки колонок
        bases                                               +=  '<div class="row header">'
        bases                                               +=  '<span class="cell">Название базы</span>'
        bases                                               +=  '<span class="cell">Путь к журналу регистрации</span>'
        bases                                               +=  '<span class="cell">Тип ЖР</span>'
        bases                                               +=  '<span class="cell">Размер ЖР</span>'
        bases                                               +=  '<span class="cell">Обработано</span>'
        bases                                               +=  '<span class="cell">Отправлено<br>(записей)</span>'
        bases                                               +=  '<span class="cell">% Обработано</span>'
        bases                                               +=  '</div>'

        for base in ibases_snapshot:
            base_total                                      =   base[g.nms.ib.total_size]\
                                                                if base[g.nms.ib.total_size]>=base[g.nms.ib.parsed_size]\
                                                                else base[g.nms.ib.parsed_size]
            jr_format                                       =   base[g.nms.ib.jr_format]
            is_lgf                                          =   jr_format == 'lgf'
            
            # Получаем количество отправленных записей из базы состояний
            base_name                                       =   t.denormalize_ib_name(base[g.nms.ib.name])
            records_sent                                    =   state_manager.get_total_records_sent(base_name)
            # Если не нашли по денормализованному имени, пробуем нормализованное
            if records_sent == 0:
                records_sent                                =   state_manager.get_total_records_sent(base[g.nms.ib.name])
            
            bases                                           +=  '<div class="row" onclick="colorize(this)">'
            bases                                           +=  '<span class="cell"">'                          \
                                                            +   base_name                                       \
                                                            +   "</span>"
            bases                                           +=  '<span class="cell">'                           \
                                                            +   base[g.nms.ib.jr_dir]                           \
                                                            +   "</span>"
            bases                                           +=  '<span class="cell">'                           \
                                                            +   jr_format                                       \
                                                            +   "</span>"
            
            # Размер ЖР с data-атрибутами для JS конвертации
            bases                                           +=  '<span class="cell size-value" data-val="' + str(base_total) + '" data-type="' + jr_format + '">' \
                                                            +   locale.format_string(
                                                                    '%d',
                                                                    base_total,
                                                                    grouping        =   True,
                                                                    monetary        =   True
                                                                )                                               \
                                                            +   (' байт' if is_lgf else ' шт.')                 \
                                                            +   "</span>"
            
            # Обработано с data-атрибутами
            bases                                           +=  '<span class="cell size-value" data-val="' + str(base[g.nms.ib.parsed_size]) + '" data-type="' + jr_format + '">' \
                                                            +   locale.format_string(
                                                                    '%d',
                                                                    base[g.nms.ib.parsed_size],
                                                                    grouping        =   True,
                                                                    monetary        =   True
                                                                )                                               \
                                                            +   (' байт' if is_lgf else ' шт.')                 \
                                                            +   "</span>"
            
            # Отправлено записей
            bases                                           +=  '<span class="cell">'                           \
                                                            +   locale.format_string(
                                                                    '%d',
                                                                    records_sent,
                                                                    grouping        =   True
                                                                )                                               \
                                                            +   "</span>"
                                                            
            bases                                           +=  '<span class="cell">'                           \
                                                            +   str(
                                                                    round(
                                                                        (
                                                                            base[g.nms.ib.parsed_size]/
                                                                            (
                                                                                int(base_total)
                                                                                if int(base_total) > 0
                                                                                else 1
                                                                            )*100
                                                                        ),
                                                                        4
                                                                    )
                                                                )                                               \
                                                            +   "</span>"
            bases                                           +=  '</div>'
        
        bases                                               +=  '</div>'
        bases                                               +=  '</div>'
        
        # ======= Верхняя панель (Controls + Info) =====================================================================
        top_bar                                             =   ""
        top_bar                                             +=  '<div class="refresh-controls">'
        
        # Left: Refresh & Units & Timezone
        top_bar                                             +=  '<div style="display: flex; align-items: center; flex-wrap: wrap;">'
        
        # Refresh
        top_bar                                             +=  '<div style="display: flex; align-items: center; margin-right: 20px;">'
        top_bar                                             +=  '<span>🔄 Автообновление:</span>'
        top_bar                                             +=  '<label class="switch">'
        top_bar                                             +=  '<input type="checkbox" id="autoRefresh">'
        top_bar                                             +=  '<span class="slider round"></span>'
        top_bar                                             +=  '</label>'
        top_bar                                             +=  '<input type="number" id="refreshInterval" value="30" min="5" style="width: 50px; margin-left: 5px;">'
        top_bar                                             +=  '<span style="margin-left: 5px;">сек.</span>'
        top_bar                                             +=  '</div>'

        # Timezone
        top_bar                                             +=  '<div style="display: flex; align-items: center; margin-right: 20px;">'
        top_bar                                             +=  '<span>🕒 Пояс: GMT</span>'
        top_bar                                             +=  '<select id="timezoneSelect" style="margin-left: 5px; padding: 2px 5px; border: 1px solid #ccc; border-radius: 4px;">'
        top_bar                                             +=  '<option value="0">+0</option>'
        top_bar                                             +=  '<option value="1">+1</option>'
        top_bar                                             +=  '<option value="2">+2</option>'
        top_bar                                             +=  '<option value="3" selected>+3</option>'
        top_bar                                             +=  '<option value="4">+4</option>'
        top_bar                                             +=  '<option value="5">+5</option>'
        top_bar                                             +=  '<option value="6">+6</option>'
        top_bar                                             +=  '<option value="7">+7</option>'
        top_bar                                             +=  '<option value="8">+8</option>'
        top_bar                                             +=  '</select>'
        top_bar                                             +=  '</div>'

        # Debug toggle
        top_bar                                             +=  '<div style="display: flex; align-items: center; margin-right: 20px;">'
        top_bar                                             +=  '<span>🐛 Отладка:</span>'
        top_bar                                             +=  '<label class="switch">'
        top_bar                                             +=  '<input type="checkbox" id="debugToggle">'
        top_bar                                             +=  '<span class="slider round"></span>'
        top_bar                                             +=  '</label>'
        top_bar                                             +=  '</div>'
        
        # Debug Controls (Filter & Limit) - initially hidden
        top_bar                                             +=  '<div id="debugControls" style="display: none; align-items: center; margin-right: 20px;">'
        top_bar                                             +=  '<div style="display: flex; align-items: center; margin-right: 15px;" title="Фильтрация строк лога (серверная). Поддерживаются регулярные выражения.">'
        top_bar                                             +=  '<span>🔍 Фильтр:</span>'
        top_bar                                             +=  '<input type="text" id="logFilter" placeholder="Regex..." style="margin-left: 5px; padding: 2px 5px; border: 1px solid #ccc; border-radius: 4px; width: 150px;">'
        top_bar                                             +=  '</div>'
        top_bar                                             +=  '<div style="display: flex; align-items: center;" title="Количество последних найденных строк">'
        top_bar                                             +=  '<span>🔢 Строк:</span>'
        top_bar                                             +=  '<input type="number" id="logLimit" value="100" min="10" max="10000" style="margin-left: 5px; padding: 2px 5px; border: 1px solid #ccc; border-radius: 4px; width: 60px;">'
        top_bar                                             +=  '</div>'
        top_bar                                             +=  '</div>'

        # Units
        top_bar                                             +=  '<div class="units-controls" style="display: flex; align-items: center;">'
        top_bar                                             +=  '<span style="margin-right: 10px;">Единицы:</span>'
        top_bar                                             +=  '<div class="btn-group">'
        top_bar                                             +=  '<button class="unit-btn active" data-unit="bytes">Байты</button>'
        top_bar                                             +=  '<button class="unit-btn" data-unit="KB">KB</button>'
        top_bar                                             +=  '<button class="unit-btn" data-unit="MB">MB</button>'
        top_bar                                             +=  '<button class="unit-btn" data-unit="GB">GB</button>'
        top_bar                                             +=  '</div>'
        top_bar                                             +=  '</div>'
        
        top_bar                                             +=  '</div>' # End Left
        
        # Right: Uptime & Last Activity
        top_bar                                             +=  '<div style="display: flex; align-items: center; margin-left: auto; font-size: 0.9em; color: #555;">'
        top_bar                                             +=  f'<span style="margin-right: 20px;">⏱ Время работы: <b>{uptime_str}</b></span>'
        if last_activity_iso:
            top_bar                                         +=  f'<span>🚀 Последняя отправка: <b><span class="time-val" data-ts="{last_activity_iso}">{last_activity_str}</span></b></span>'
        else:
            top_bar                                         +=  f'<span>🚀 Последняя отправка: <b>{last_activity_str}</b></span>'
        top_bar                                             +=  '</div>'
        
        top_bar                                             +=  '</div>'
        
        # ======= Блок отладочных сообщений ============================================================================
        debug_block                                         =   ""
        debug_block                                         +=  '<div id="debugBlock" class="debug-container" style="display: none;">'
        debug_block                                         +=  '<h2>🐛 Отладочные сообщения</h2>'
        debug_block                                         +=  '<div id="debugMessages" style="background: #f8f9fa; padding: 10px; border-radius: 4px; max-height: 300px; overflow-y: auto; font-family: monospace; font-size: 12px;">'
        debug_block                                         +=  '<div style="color: #999;">Загрузка логов...</div>'
        debug_block                                         +=  '</div>'
        debug_block                                         +=  '</div>'

        return \
            """
            <html>
            <head>
                <meta charset="utf-8">
                <title>Панель управления службой индексации журналов регистрации</title>
                <style type="text/css">
                    body {
                        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                        margin: 10px;
                        background-color: #f5f5f5;
                        color: #333;
                    }
                    h1 {
                        color: #00b36b;
                        font-size: 20px;
                        margin-bottom: 15px;
                    }
                    h2 {
                        color: #333;
                        margin-bottom: 10px;
                        border-bottom: 2px solid #00b36b;
                        padding-bottom: 5px;
                        font-size: 18px;
                    }
                    h3 {
                        color: #555;
                        margin: 5px 0;
                        font-size: 16px;
                    }
                    .stats-container, .table-container, .refresh-controls {
                        background: white;
                        padding: 10px 15px;
                        margin-bottom: 15px;
                        border-radius: 8px;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
                    }
                    .refresh-controls {
                        display: flex;
                        align-items: center;
                        justify-content: space-between;
                        padding: 10px 20px;
                        flex-wrap: wrap;
                    }
                    .stats-table .row {
                        display: grid;
                        grid-template-columns: 1fr 1fr 1fr 1.5fr 1.5fr 1fr;
                        border-bottom: 1px solid #eee;
                    }
                    .stats-table .row.header {
                        font-weight: bold;
                        background-color: #00b36b;
                        color: white;
                        border-bottom: 2px solid #00995c;
                    }
                    .stats-table .cell {
                        padding: 8px 10px;
                        display: flex;
                        align-items: center;
                        border-right: 1px solid #eee;
                    }
                    .stats-table .cell:last-child {
                        border-right: none;
                    }
                    .stats-table .cell.disabled {
                        color: #999;
                        font-style: italic;
                    }
                    
                    .stats-row.error {
                        background-color: #fff3cd;
                        padding: 5px 10px;
                        margin: 2px 0;
                        border-radius: 4px;
                        border-left: 3px solid #ff6b6b;
                        display: flex;
                        justify-content: space-between;
                    }
                    
                    .table {
                        display: table;
                        border-collapse: separate;
                        border-spacing: 0;
                        width: 100%;
                        border: 1px solid #eee;
                        border-radius: 4px;
                        overflow: hidden;
                    }
                    .row {
                        display: table-row;
                        cursor: pointer;
                        transition: background-color 0.2s;
                    }
                    .row.header {
                        font-weight: bold;
                        background-color: #00b36b;
                        color: white;
                    }
                    .cell {
                        display: table-cell;
                        padding: 8px 10px;
                        border-bottom: 1px solid #eee;
                        text-align: left;
                        background-color: white;
                    }
                    .row.header .cell {
                        border-bottom: 2px solid #00995c;
                        text-transform: uppercase;
                        font-size: 12px;
                        letter-spacing: 0.5px;
                        background-color: #00b36b;
                    }
                    .row.total {
                        background-color: #d4f4e6;
                        font-weight: bold;
                    }
                    .row.total .cell {
                        background-color: #d4f4e6;
                        border-bottom: 2px solid #00b36b;
                    }
                    .row:not(.header):not(.total):hover .cell {
                        background-color: #f0f9f4;
                    }
                    
                    /* Switch toggle styles */
                    .switch {
                        position: relative;
                        display: inline-block;
                        width: 34px;
                        height: 20px;
                        margin: 0 8px;
                    }
                    .switch input { 
                        opacity: 0;
                        width: 0;
                        height: 0;
                    }
                    .slider {
                        position: absolute;
                        cursor: pointer;
                        top: 0;
                        left: 0;
                        right: 0;
                        bottom: 0;
                        background-color: #ccc;
                        transition: .4s;
                        border-radius: 34px;
                    }
                    .slider:before {
                        position: absolute;
                        content: "";
                        height: 14px;
                        width: 14px;
                        left: 3px;
                        bottom: 3px;
                        background-color: white;
                        transition: .4s;
                        border-radius: 50%;
                    }
                    input:checked + .slider {
                        background-color: #00b36b;
                    }
                    input:checked + .slider:before {
                        transform: translateX(14px);
                    }
                    
                    /* Unit buttons styles */
                    .btn-group {
                        display: flex;
                        border: 1px solid #ccc;
                        border-radius: 4px;
                        overflow: hidden;
                    }
                    .unit-btn {
                        background-color: #f8f9fa;
                        border: none;
                        border-right: 1px solid #ccc;
                        padding: 4px 8px;
                        cursor: pointer;
                        font-size: 13px;
                        transition: background-color 0.2s;
                    }
                    .unit-btn:last-child {
                        border-right: none;
                    }
                    .unit-btn:hover {
                        background-color: #e2e6ea;
                    }
                    .unit-btn.active {
                        background-color: #00b36b;
                        color: white;
                    }
                    
                    .debug-container {
                        background: white;
                        padding: 10px 15px;
                        margin-bottom: 15px;
                        border-radius: 8px;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
                    }
                    
                    #debugMessages .log-entry {
                        padding: 3px 0;
                        border-bottom: 1px solid #eee;
                    }
                    
                    #debugMessages .log-entry:last-child {
                        border-bottom: none;
                    }
                    
                    #debugMessages .log-timestamp {
                        color: #666;
                        margin-right: 8px;
                    }
                    
                    #debugMessages .log-level {
                        font-weight: bold;
                        margin-right: 8px;
                    }
                    
                    #debugMessages .log-level.info {
                        color: #00b36b;
                    }
                    
                    #debugMessages .log-level.error {
                        color: #ff6b6b;
                    }
                </style>   
                <script type="text/javascript">
                    function colorize(Element) {
                        elements = document.querySelectorAll(".table .row:not(.header) .cell");  
                        if(elements.length > 0){
                            for(var i = 0; i < elements.length; i++){
                                if(!elements[i].classList.contains('parameter')) {
                                    elements[i].style.backgroundColor = 'white';
                                    elements[i].style.color = 'inherit';
                                }
                            }
                        }
                        
                        var children = Element.children;
                        for (var i = 0; i < children.length; i++) {
                             if(!children[i].classList.contains('parameter')) {
                                children[i].style.backgroundColor = '#00b36b';
                                children[i].style.color = 'white';
                             }
                        }
                        return false;
                    }
                    
                    // Форматирование размера
                    function formatSize(value, unit) {
                        const val = parseFloat(value);
                        if (isNaN(val)) return value;
                        
                        if (unit === 'bytes') {
                            return val.toLocaleString('ru-RU') + ' байт';
                        }
                        if (unit === 'KB') return (val / 1024).toFixed(2) + ' KB';
                        if (unit === 'MB') return (val / (1024 * 1024)).toFixed(2) + ' MB';
                        if (unit === 'GB') return (val / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
                        
                        return val + ' байт';
                    }

                    // Обновление всех ячеек с размерами
                    function updateSizes(unit) {
                        const cells = document.querySelectorAll('.size-value');
                        cells.forEach(cell => {
                            const type = cell.getAttribute('data-type');
                            const val = cell.getAttribute('data-val');
                            
                            if (type === 'lgf') {
                                cell.textContent = formatSize(val, unit);
                            }
                        });
                    }
                    
                    // Форматирование времени с учетом пояса
                    function updateTimes(offset) {
                        document.querySelectorAll('.time-val').forEach(el => {
                            const ts = el.getAttribute('data-ts');
                            if (!ts) return;
                            
                            // Считаем, что серверное время - это UTC, если в строке нет зоны
                            // Если ISO строка без Z, добавляем Z для корректного парсинга как UTC
                            const timeStr = ts.endsWith('Z') ? ts : ts + 'Z';
                            const date = new Date(timeStr);
                            
                            if (isNaN(date.getTime())) return;
                            
                            // Прибавляем смещение (в часах)
                            // getTime() дает миллисекунды
                            const targetTime = new Date(date.getTime() + (offset * 3600000));
                            
                            const hours = targetTime.getUTCHours().toString().padStart(2, '0');
                            const minutes = targetTime.getUTCMinutes().toString().padStart(2, '0');
                            const seconds = targetTime.getUTCSeconds().toString().padStart(2, '0');
                            
                            el.textContent = hours + ':' + minutes + ':' + seconds;
                        });
                    }

                    document.addEventListener("DOMContentLoaded", function() {
                        // --- Автообновление ---
                        const checkbox = document.getElementById('autoRefresh');
                        const intervalInput = document.getElementById('refreshInterval');
                        const debugToggle = document.getElementById('debugToggle');
                        let timer = null;

                        const savedState = localStorage.getItem('nikita_autoRefresh');
                        if (savedState) {
                            const state = JSON.parse(savedState);
                            checkbox.checked = state.enabled;
                            intervalInput.value = state.interval;
                        } else {
                            checkbox.checked = true;
                            intervalInput.value = 30;
                        }

                        function saveRefreshState() {
                            localStorage.setItem('nikita_autoRefresh', JSON.stringify({
                                enabled: checkbox.checked,
                                interval: intervalInput.value
                            }));
                        }

                        function updateTimer() {
                            if (timer) clearTimeout(timer);
                            saveRefreshState();
                            if (checkbox.checked) {
                                const interval = parseInt(intervalInput.value) || 30;
                                const ms = interval * 1000;
                                if (ms >= 1000) {
                                    timer = setTimeout(() => location.reload(), ms);
                                }
                            }
                        }

                        checkbox.addEventListener('change', function() {
                            // Если включаем автообновление, выключаем отладку
                            if (this.checked && debugToggle.checked) {
                                debugToggle.checked = false;
                                debugToggle.dispatchEvent(new Event('change'));
                            }
                            updateTimer();
                        });
                        intervalInput.addEventListener('change', updateTimer);
                        updateTimer();
                        
                        // --- Единицы измерения ---
                        const unitBtns = document.querySelectorAll('.unit-btn');
                        let currentUnit = localStorage.getItem('nikita_unit') || 'bytes';
                        
                        unitBtns.forEach(btn => {
                            if (btn.getAttribute('data-unit') === currentUnit) {
                                btn.classList.add('active');
                            } else {
                                btn.classList.remove('active');
                            }
                            
                            btn.addEventListener('click', function() {
                                currentUnit = this.getAttribute('data-unit');
                                localStorage.setItem('nikita_unit', currentUnit);
                                
                                unitBtns.forEach(b => b.classList.remove('active'));
                                this.classList.add('active');
                                
                                updateSizes(currentUnit);
                            });
                        });
                        updateSizes(currentUnit);
                        
                        // --- Часовой пояс ---
                        const tzSelect = document.getElementById('timezoneSelect');
                        // По умолчанию GMT+3
                        let currentOffset = localStorage.getItem('nikita_timezone');
                        if (currentOffset === null) {
                            currentOffset = "3";
                        }
                        
                        tzSelect.value = currentOffset;
                        
                        // Функция обновления временных меток в логах и статистике
                        function updateAllTimes() {
                            const offset = parseInt(currentOffset);
                            // Обновляем статистику
                            updateTimes(offset);
                            // Обновляем логи (если есть)
                            document.querySelectorAll('.log-timestamp').forEach(el => {
                                const ts = el.getAttribute('data-ts');
                                if (ts) {
                                     // Для логов логика та же, если есть атрибут
                                     // Но логи приходят готовым текстом, нам нужно парсить их
                                }
                            });
                        }

                        tzSelect.addEventListener('change', function() {
                            currentOffset = parseInt(this.value);
                            localStorage.setItem('nikita_timezone', currentOffset);
                            updateTimes(currentOffset);
                            // Перезагружаем логи, чтобы применился новый пояс (клиентский парсинг сложнее)
                            if (debugToggle.checked) {
                                loadDebugLogs();
                            }
                        });
                        
                        // Инициализация времени
                        updateTimes(parseInt(currentOffset));
                        
                        // --- Отладка ---
                        const debugBlock = document.getElementById('debugBlock');
                        const debugMessages = document.getElementById('debugMessages');
                        const logFilter = document.getElementById('logFilter');
                        const logLimit = document.getElementById('logLimit');
                        const debugControls = document.getElementById('debugControls');
                        
                        function updateFilterUI() {
                            const val = logFilter.value;
                            try {
                                if (val) new RegExp(val, 'i');
                                logFilter.style.borderColor = '#ccc';
                                logFilter.title = "Фильтрация строк лога (серверная).";
                            } catch (e) {
                                logFilter.style.borderColor = '#ff6b6b';
                                logFilter.title = "Ошибка в регулярном выражении: " + e.message;
                            }
                        }

                        // При изменении фильтра или лимита перезагружаем логи
                        let filterTimeout;
                        function debouncedLoad() {
                            clearTimeout(filterTimeout);
                            updateFilterUI();
                            filterTimeout = setTimeout(loadDebugLogs, 500);
                        }

                        logFilter.addEventListener('input', debouncedLoad);
                        logLimit.addEventListener('change', loadDebugLogs);
                        
                        // Загружаем состояние с сервера
                        fetch('/set_debug')
                            .then(response => {
                                if (!response.ok) {
                                    throw new Error('Network response was not ok');
                                }
                                return response.json();
                            })
                            .then(data => {
                                if (data.success) {
                                    debugToggle.checked = data.debug_enabled;
                                    
                                    // Если отладка включена при загрузке, выключаем автообновление
                                    if (data.debug_enabled) {
                                        debugBlock.style.display = 'block';
                                        debugControls.style.display = 'flex';
                                        if (checkbox.checked) {
                                            checkbox.checked = false;
                                            updateTimer();
                                        }
                                        loadDebugLogs();
                                    }
                                }
                            })
                            .catch(err => {
                                console.error('Ошибка загрузки состояния отладки:', err);
                            });
                        
                        debugToggle.addEventListener('change', function() {
                            const enabled = this.checked;
                            debugBlock.style.display = enabled ? 'block' : 'none';
                            debugControls.style.display = enabled ? 'flex' : 'none';
                            
                            // Если включаем отладку, выключаем автообновление
                            if (enabled && checkbox.checked) {
                                checkbox.checked = false;
                                updateTimer();
                            }
                            
                            // Отправляем изменение на сервер
                            fetch('/set_debug?enabled=' + enabled)
                                .then(response => {
                                    if (!response.ok) {
                                        throw new Error('Network response was not ok');
                                    }
                                    return response.json();
                                })
                                .then(data => {
                                    if (data.success) {
                                        console.log(data.message);
                                        if (enabled) {
                                            loadDebugLogs();
                                        }
                                    } else {
                                        console.error('Ошибка изменения режима отладки:', data.error);
                                    }
                                })
                                .catch(err => {
                                    console.error('Ошибка запроса к серверу:', err);
                                });
                        });
                        
                        // Функция загрузки логов
                        function loadDebugLogs() {
                            const filterVal = encodeURIComponent(logFilter.value);
                            const limitVal = logLimit.value;
                            
                            fetch(`/debug_logs?filter_text=${filterVal}&limit=${limitVal}`)
                                .then(response => {
                                    if (!response.ok) {
                                        throw new Error('Network response was not ok');
                                    }
                                    return response.json();
                                })
                                .then(data => {
                                    if (data.success && data.logs && data.logs.length > 0) {
                                        let html = '';
                                        const offset = parseInt(localStorage.getItem('nikita_timezone') || "3");
                                        
                                        data.logs.forEach(log => {
                                            // Пытаемся распарсить дату из строки лога: YYYY-MM-DD HH:MM:SS.mmmmmm:::thread:::msg
                                            let displayLog = log;
                                            // Regex: начало строки, дата, разделитель, остальное
                                            const match = log.match(/^(\\d{4}-\\d{2}-\\d{2}\\s\\d{2}:\\d{2}:\\d{2}\\.\\d+)(:::.*)/);
                                            
                                            if (match) {
                                                const timeStr = match[1];
                                                const content = match[2];
                                                
                                                // Считаем время в логе как локальное время сервера
                                                // Но так как у нас нет инфы о таймзоне сервера, считаем, что это "Server Time"
                                                // И просто сдвигаем его, если пользователь хочет видеть "Server Time + Offset"
                                                // Или более правильно: парсим как UTC (добавляем Z) и сдвигаем на Offset
                                                
                                                try {
                                                    // Предполагаем, что логи пишутся в UTC или мы просто хотим сдвинуть отображаемое время
                                                    // Самый простой вариант: парсим, добавляем смещение и форматируем обратно
                                                    
                                                    // Заменяем пробел на T для ISO
                                                    const isoStr = timeStr.replace(' ', 'T') + 'Z'; 
                                                    const date = new Date(isoStr);
                                                    
                                                    if (!isNaN(date.getTime())) {
                                                        const targetTime = new Date(date.getTime() + (offset * 3600000));
                                                        
                                                        const y = targetTime.getUTCFullYear();
                                                        const m = (targetTime.getUTCMonth() + 1).toString().padStart(2, '0');
                                                        const d = targetTime.getUTCDate().toString().padStart(2, '0');
                                                        const h = targetTime.getUTCHours().toString().padStart(2, '0');
                                                        const min = targetTime.getUTCMinutes().toString().padStart(2, '0');
                                                        const s = targetTime.getUTCSeconds().toString().padStart(2, '0');
                                                        const ms = targetTime.getUTCMilliseconds().toString().padStart(3, '0');
                                                        
                                                        const newTimeStr = `${y}-${m}-${d} ${h}:${min}:${s}.${ms}`;
                                                        
                                                        // Формируем новую строку
                                                        // Мы не меняем исходный текст лога, а рендерим его части
                                                        const level = log.includes('✓') ? 'info' : (log.includes('✗') || log.includes('Ошибка') ? 'error' : 'info');
                                                        html += `<div class="log-entry">
                                                                    <span class="log-timestamp" style="color:#666; margin-right:5px;">${newTimeStr}</span>
                                                                    <span class="log-level ${level}">${level.toUpperCase()}</span>
                                                                    ${content.substring(3)} 
                                                                 </div>`;
                                                        return; // переходим к следующей итерации
                                                    }
                                                } catch(e) {
                                                    console.error("Date parse error", e);
                                                }
                                            }
                                            
                                            // Fallback если не удалось распарсить
                                            const level = log.includes('✓') ? 'info' : (log.includes('✗') || log.includes('Ошибка') ? 'error' : 'info');
                                            html += `<div class="log-entry"><span class="log-level ${level}">${level.toUpperCase()}</span>${log}</div>`;
                                        });
                                        debugMessages.innerHTML = html;
                                        // Фильтрация теперь на сервере, клиентская не нужна
                                    } else {
                                        debugMessages.innerHTML = '<div style="color: #999;">Логов пока нет (или не найдено по фильтру)</div>';
                                    }
                                })
                                .catch(err => {
                                    debugMessages.innerHTML = '<div style="color: #ff6b6b;">Ошибка загрузки логов: ' + err.message + '</div>';
                                });
                        }
                        
                        // Автообновление логов если отладка включена
                        setInterval(() => {
                            if (debugToggle.checked) {
                                loadDebugLogs();
                            }
                        }, 5000); // каждые 5 секунд
                    });
                </script>
            </head>
            <body>
                <h1>📊 Nikita - Панель мониторинга</h1>
                """+top_bar+"""
                """+debug_block+"""
                """+stats_block+"""
                """+bases+"""
            </body>
            </html>                    
            """
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    @cherrypy.expose
    def query(self):
        return "Hello World!"
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    @cherrypy.expose
    def mcp(self, *args, **kwargs):
        """MCP-эндпоинт (src/mcp.py); авторизации нет — только за аутентифицирующим прокси."""
        request_headers                                     =   {name.lower(): value for name, value in cherrypy.request.headers.items()}
        request_body                                        =   b""
        if cherrypy.request.method == "POST":
            request_body                                    =   cherrypy.request.body.read(g.conf.mcp.max_body + 1)
        status, response_headers, response_body             =   m.handle(cherrypy.request.method, request_headers, request_body)
        cherrypy.response.status                            =   status
        for name, value in response_headers.items():
            cherrypy.response.headers[name]                 =   value
        return response_body
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    @cherrypy.expose
    def debug_logs(self, filter_text=None, limit=100):
        """API endpoint для получения отладочных логов"""
        try:
            cherrypy.response.headers['Content-Type']           =   'application/json; charset=utf-8'
            
            # Валидация и санитизация входных данных
            if filter_text and len(filter_text) > 1000:
                filter_text                                 =   filter_text[:1000]  # Ограничиваем длину фильтра
            
            # Параметры запроса
            try:
                limit_int                                   =   int(limit)
                if limit_int < 1: limit_int = 100
                if limit_int > 10000: limit_int = 10000
            except:
                limit_int                                   =   100
            
            debug_logs_list                                     =   []
            
            # Если отладка выключена, возвращаем пустой список
            if not g.debug.on:
                return json.dumps({'logs': ['Отладка выключена'], 'success': True}, ensure_ascii=False).encode('utf-8')
            
            # Пробуем прочитать последние строки из файла отладочного лога
            try:
                import os
                import re
                
                # t.debug_print(f"debug_logs: g.debug.filename = {g.debug.filename}", "cherry")
                
                if not g.debug.filename:
                    debug_logs_list.append("⚠ Файл логов не настроен (g.debug.filename пуст)")
                elif not os.path.exists(g.debug.filename):
                    debug_logs_list.append(f"⚠ Файл логов не найден: {g.debug.filename}")
                else:
                    # Компилируем regex, если передан
                    filter_re = None
                    if filter_text:
                        try:
                            # Таймаут и защита от ReDoS через ограничение сложности
                            filter_re = re.compile(filter_text, re.IGNORECASE)
                        except Exception as regex_err:
                            # Если regex невалидный, будем искать как подстроку
                            t.debug_print(f"Invalid regex filter: {str(regex_err)}", "cherry")
                            pass
                    
                    # Читаем файл
                    # ОПТИМИЗАЦИЯ: используем deque для эффективного чтения последних N строк
                    # без загрузки всего файла в память (важно для больших лог-файлов)
                    from collections import deque
                    
                    read_limit                              =   limit_int if not filter_text else 50000
                    
                    with open(g.debug.filename, 'r', encoding='utf-8', errors='ignore') as f:
                        # Используем deque для эффективного чтения последних строк
                        # Это значительно быстрее и экономнее по памяти для больших файлов
                        if not filter_text:
                            # Без фильтра - просто берем последние limit_int строк
                            all_lines                       =   list(deque(f, maxlen=limit_int))
                        else:
                            # С фильтром - читаем больше строк для поиска совпадений
                            all_lines                       =   list(deque(f, maxlen=read_limit))
                        
                        # Если фильтра нет - берем последние limit
                        if not filter_text:
                             last_lines                     =   all_lines[-limit_int:] if len(all_lines) > limit_int else all_lines
                             # Разворачиваем, чтобы новые были сверху
                             for line in reversed(last_lines):
                                 if line.strip(): debug_logs_list.append(line.strip())
                        
                        else:
                            # Если фильтр есть - идем с конца и ищем совпадения
                            count                           =   0
                            temp_list                       =   []
                            # Перебираем с конца
                            for line in reversed(all_lines):
                                line_clean                  =   line.strip()
                                if not line_clean: continue
                                
                                match                       =   False
                                if filter_re:
                                    if filter_re.search(line_clean): match = True
                                elif filter_text.lower() in line_clean.lower():
                                    match                   =   True
                                    
                                if match:
                                    temp_list.append(line_clean)
                                    count                   +=  1
                                    if count >= limit_int: break
                                    
                                # Защита от слишком долгого поиска (если просмотрели 50000 строк и не нашли)
                                # Для readlines() это уже не важно (все в памяти), но логически верно
                            
                            # temp_list уже содержит записи от новых к старым
                            debug_logs_list                 =   temp_list
                    
                    if not debug_logs_list:
                        if filter_text:
                            debug_logs_list.append(f"📝 По фильтру '{filter_text}' ничего не найдено (в последних строках)")
                        else:
                            debug_logs_list.append("📝 Файл логов пуст")
                        
            except Exception as e:
                import traceback
                error_msg                                       =   f"✗ Ошибка чтения файла логов: {str(e)}"
                t.debug_print(error_msg + "\n" + traceback.format_exc(), "cherry")
                debug_logs_list.append(error_msg)
            
            if not debug_logs_list:
                debug_logs_list.append("📝 Логов пока нет")
            
            t.debug_print(f"debug_logs: returning {len(debug_logs_list)} log entries", "cherry")
            
            result                                              =   {'logs': debug_logs_list, 'success': True}
            
            try:
                return json.dumps(result, ensure_ascii=False).encode('utf-8')
            except Exception as json_err:
                # Если не удалось сериализовать в JSON, возвращаем безопасный ответ
                t.debug_print(f"✗ Ошибка JSON сериализации: {str(json_err)}", "cherry")
                return json.dumps({'logs': [f'Ошибка сериализации: {str(json_err)}'], 'success': False}, ensure_ascii=False).encode('utf-8')
        
        except Exception as top_err:
            # Гарантируем возврат корректного JSON в любом случае
            import traceback
            t.debug_print(f"✗ Критическая ошибка в debug_logs: {str(top_err)}\n{traceback.format_exc()}", "cherry")
            cherrypy.response.headers['Content-Type']           =   'application/json; charset=utf-8'
            return json.dumps({
                'logs'      :   [f'Критическая ошибка: {str(top_err)}'],
                'success'   :   False
            }, ensure_ascii=False).encode('utf-8')
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    @cherrypy.expose
    def stats_api(self):
        """API endpoint для получения статистики в JSON формате"""
        cherrypy.response.headers['Content-Type']           =   'application/json; charset=utf-8'
        
        stats_data                                          =   {}
        
        # Общая информация
        if g.stats.start_time:
            uptime                                          =   (datetime.now() - g.stats.start_time).total_seconds()
            stats_data['uptime_seconds']                    =   uptime
            stats_data['uptime_formatted']                  =   f"{int(uptime // 3600)}ч {int((uptime % 3600) // 60)}м {int(uptime % 60)}с"
        else:
            stats_data['uptime_seconds']                    =   0
            stats_data['uptime_formatted']                  =   "Н/Д"
        
        stats_data['total_records_parsed']                  =   g.stats.total_records_parsed
        
        # ClickHouse
        if g.conf.clickhouse.enabled:
            stats_data['clickhouse']                        =   {
                                                                    'enabled'           :   True,
                                                                    'host'              :   g.conf.clickhouse.host,
                                                                    'port'              :   g.conf.clickhouse.port,
                                                                    'database'          :   g.conf.clickhouse.database,
                                                                    'connection_ok'     :   g.stats.clickhouse_connection_ok,
                                                                    'total_sent'        :   g.stats.clickhouse_total_sent,
                                                                    'total_errors'      :   g.stats.clickhouse_total_errors,
                                                                    'last_success_time' :   g.stats.clickhouse_last_success_time.isoformat() if g.stats.clickhouse_last_success_time else None,
                                                                    'last_error_time'   :   g.stats.clickhouse_last_error_time.isoformat() if g.stats.clickhouse_last_error_time else None,
                                                                    'last_error_msg'    :   g.stats.clickhouse_last_error_msg
                                                                }
        else:
            stats_data['clickhouse']                        =   {'enabled': False}
        
        # Solr
        if g.conf.solr.enabled:
            stats_data['solr']                              =   {
                                                                    'enabled'           :   True,
                                                                    'host'              :   g.conf.solr.solr_host,
                                                                    'port'              :   g.conf.solr.solr_port,
                                                                    'connection_ok'     :   g.stats.solr_connection_ok,
                                                                    'total_sent'        :   g.stats.solr_total_sent,
                                                                    'total_errors'      :   g.stats.solr_total_errors,
                                                                    'last_success_time' :   g.stats.solr_last_success_time.isoformat() if g.stats.solr_last_success_time else None,
                                                                    'last_error_time'   :   g.stats.solr_last_error_time.isoformat() if g.stats.solr_last_error_time else None,
                                                                    'last_error_msg'    :   g.stats.solr_last_error_msg
                                                                }
        else:
            stats_data['solr']                              =   {'enabled': False}
        
        # Redis
        if g.conf.redis.enabled:
            stats_data['redis']                             =   {
                                                                    'enabled'           :   True,
                                                                    'host'              :   g.conf.redis.host,
                                                                    'port'              :   g.conf.redis.port,
                                                                    'connection_ok'     :   g.stats.redis_connection_ok,
                                                                    'total_queued'      :   g.stats.redis_total_queued,
                                                                    'total_errors'      :   g.stats.redis_total_errors,
                                                                    'last_success_time' :   g.stats.redis_last_success_time.isoformat() if g.stats.redis_last_success_time else None,
                                                                    'last_error_time'   :   g.stats.redis_last_error_time.isoformat() if g.stats.redis_last_error_time else None,
                                                                    'last_error_msg'    :   g.stats.redis_last_error_msg
                                                                }
        else:
            stats_data['redis']                             =   {'enabled': False}
        
        # Последние ошибки
        stats_data['last_errors']                           =   []
        for error_time, error_type, error_msg in reversed(g.stats.last_errors[-10:]):
            stats_data['last_errors'].append({
                                                                    'time'      :   error_time.isoformat(),
                                                                    'type'      :   error_type,
                                                                    'message'   :   error_msg
                                                                })
        
        # Информация о базах (с блокировкой для thread-safety)
        with g.ibases_lock:
            ibases_for_stats                                =   list(g.parser.ibases)
        
        stats_data['databases']                             =   []
        for base in ibases_for_stats:
            base_total                                      =   base[g.nms.ib.total_size] if base[g.nms.ib.total_size] >= base[g.nms.ib.parsed_size] else base[g.nms.ib.parsed_size]
            percent                                         =   round((base[g.nms.ib.parsed_size] / (int(base_total) if int(base_total) > 0 else 1)) * 100, 4)
            
            stats_data['databases'].append({
                                                                    'name'          :   t.denormalize_ib_name(base[g.nms.ib.name]),
                                                                    'path'          :   base[g.nms.ib.jr_dir],
                                                                    'format'        :   base[g.nms.ib.jr_format],
                                                                    'total_size'    :   base_total,
                                                                    'parsed_size'   :   base[g.nms.ib.parsed_size],
                                                                    'percent'       :   percent
                                                                })
        
        return json.dumps(stats_data, ensure_ascii=False, indent=2).encode('utf-8')
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    @cherrypy.expose
    def set_debug(self, enabled=None):
        """API endpoint для управления флагом отладки"""
        cherrypy.response.headers['Content-Type']           =   'application/json; charset=utf-8'
        
        try:
            if enabled is not None:
                # Преобразуем строковое значение в boolean
                new_debug_state                             =   str(enabled).lower() in ('true', '1', 't', 'y', 'yes')
                
                # Логируем изменение статуса (ДО изменения g.debug.on, чтобы всегда логировалось)
                old_state                                   =   g.debug.on
                status_msg                                  =   f"🔧 Изменение статуса отладки: {old_state} → {new_debug_state}"
                if old_state:
                    t.debug_print(status_msg, "cherry")
                
                # Обновляем глобальную переменную отладки
                g.debug.on                                  =   new_debug_state
                
                # Логируем после изменения (если отладка была выключена, теперь включена)
                if not old_state and new_debug_state:
                    t.debug_print(status_msg, "cherry")
                
                # Обновляем переменную окружения для будущих потоков
                import os
                os.environ['DEBUG_ENABLED']                 =   'True' if new_debug_state else 'False'
                
                # Сохраняем в .env файл
                env_path                                    =   os.path.join(g.execution.self_dir, '.env')
                try:
                    # Читаем текущий .env файл
                    env_lines                               =   []
                    debug_found                             =   False
                    
                    if os.path.exists(env_path):
                        with open(env_path, 'r', encoding='utf-8') as f:
                            for line in f:
                                if line.strip().startswith('DEBUG_ENABLED='):
                                    env_lines.append(f'DEBUG_ENABLED={"True" if new_debug_state else "False"}\n')
                                    debug_found         =   True
                                else:
                                    env_lines.append(line)
                    
                    # Если переменная не найдена, добавляем её
                    if not debug_found:
                        env_lines.append(f'DEBUG_ENABLED={"True" if new_debug_state else "False"}\n')
                    
                    # Записываем обратно в файл
                    with open(env_path, 'w', encoding='utf-8') as f:
                        f.writelines(env_lines)
                    
                    t.debug_print(f"✓ Отладка {'включена' if new_debug_state else 'выключена'} и сохранена в .env", "cherry")
                except Exception as env_err:
                    t.debug_print(f"⚠ Не удалось сохранить в .env: {str(env_err)}, но отладка {'включена' if new_debug_state else 'выключена'}", "cherry")
                
                return json.dumps({
                    'success'       :   True,
                    'debug_enabled' :   g.debug.on,
                    'message'       :   f"Отладка {'включена' if g.debug.on else 'выключена'}"
                }, ensure_ascii=False).encode('utf-8')
            else:
                # Возвращаем текущее состояние
                return json.dumps({
                    'success'       :   True,
                    'debug_enabled' :   g.debug.on
                }, ensure_ascii=False).encode('utf-8')
        except Exception as e:
            t.debug_print(f"✗ Ошибка изменения режима отладки: {str(e)}", "cherry")
            return json.dumps({
                'success'   :   False,
                'error'     :   str(e)
            }, ensure_ascii=False).encode('utf-8')
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    @cherrypy.expose
    def show(self, length=9):
        fail_on                                            =    "start"
        try:
            t.debug_print("got query","cherry")
            fail_on                                         =   "post_size"
            post_size                                       =   cherrypy.request.headers['Content-Length']              # получаю размер post-запроса
            fail_on                                         =   "post_rawbody"
            post_rawbody                                    =   cherrypy.request.body.read(int(post_size))              # читаю сам запрос
            fail_on                                         =   "post_decoded"
            post_decoded                                    =   urllib.parse.unquote(post_rawbody.decode("utf-8"))      # декодирую текст запроса
            #if(post_decoded):
            fail_on                                         =   "show_return"
            show_return                                     =   r.reader.full_proceess_read(post_decoded)
            t.debug_print("data send", "cherry")
            #else:
            #    show_return                                =   "Request not received"
        except Exception as e:
            t.debug_print("query exception "+fail_on+":"+str(e), "cherry")
            show_return                                     =   "Exception8 "+str(e)
        return show_return;
    index.exposed                                           =   True
# ======================================================================================================================
# поток, в котором работает http-сервер
# ======================================================================================================================
class cherry_thread(threading.Thread):
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def __init__(self, name):
        threading.Thread.__init__(self)
        self.name                                           =   name
        t.debug_print("Thread initialized", self.name)
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def run(self):
        try:
            t.debug_print(f"Настройка CherryPy веб-сервера...", self.name)
            t.debug_print(f"Интерфейс: {g.conf.http.listen_interface}", self.name)
            t.debug_print(f"Порт: {g.conf.http.listen_port}", self.name)
            
            cherrypy.config.update({'server.socket_host'        :   g.conf.http.listen_interface})
            cherrypy.config.update({'server.socket_port'        :   int(g.conf.http.listen_port)})
            cherrypy.config.update({'log.screen'                :   g.execution.running_in_console})
            
            t.debug_print(f"✓ CherryPy запущен на http://{g.conf.http.listen_interface}:{g.conf.http.listen_port}/", self.name)
            t.debug_print(f"✓ Веб-панель мониторинга: http://{g.conf.http.listen_interface}:{g.conf.http.listen_port}/", self.name)
            t.debug_print(f"✓ JSON API статистики: http://{g.conf.http.listen_interface}:{g.conf.http.listen_port}/stats_api", self.name)
            t.debug_print(f"✓ MCP: {'http://' + g.conf.http.listen_interface + ':' + g.conf.http.listen_port + '/mcp' if g.conf.mcp.enabled else 'выключен (MCP_ENABLED или MCP_CLICKHOUSE_USER не заданы)'}", self.name)
            
            conf                                                =   {'/': {}}
            cherrypy.quickstart(nikita_web(), config=conf)
        except Exception as e:
            t.debug_print(f"✗ Ошибка запуска CherryPy: {str(e)}", self.name)
            import traceback
            t.debug_print(f"✗ Traceback:\n{traceback.format_exc()}", self.name)
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def stop(self):
        t.debug_print("Останавливаем CherryPy веб-сервер...", self.name)
        cherrypy.engine.exit()
        t.debug_print("✓ CherryPy остановлен", self.name)
# ======================================================================================================================