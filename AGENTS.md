# Nikita

Nikita is a Python service that parses 1C:Enterprise event logs ("журнал регистрации", `.lgf`/`.lgp`
and SQLite `.lgd`) and exports the events to ClickHouse, with optional Solr and Redis. It runs as a
Windows service or a Linux systemd daemon, auto-discovers infobases in 1C clusters, keeps read
positions in SQLite, serves a web panel with a JSON API and an optional MCP endpoint. Actively
developed (last commit 2026-10-17). Human docs are in Russian: `README.md`, `docs/admin/`,
`docs/dev/`, `BUILD.md`, `CHANGELOG.md`.

## Layout

- `Nikita.py`: entry point. `start_all()` / `stop_all()`, config class `conf`, Windows service
  wrapper; `console` argument runs in the foreground.
- `src/`: `globals.py` (shared state `g`, `build_version`, regexes, `ibases_lock`), `parser.py`,
  `parser_state.py`, `state_manager.py` (SQLite read positions), `reader.py` (log readers),
  `sender.py` (ClickHouse), `solr.py`, `redis_manager.py`, `cherry.py` (CherryPy web panel and
  API), `mcp.py` + `mcp_query.py` (MCP `POST /mcp`, read-only ClickHouse `query` tool),
  `dictionaries.py`, `messenger.py`, `tools.py` (`t.debug_print()` and helpers).
- `tests/`: `unittest` tests (`test_*.py`); `manual_*.py` are manual scripts, not run by discovery.
- `scripts/`: Windows build, installer (NSIS), service and firewall `.cmd`/`.ps1` scripts,
  `b.compiles2pyd.py` (Cython build of `src/`), `share.1cdata.sh`.
- `sql/`: ClickHouse database, user and table DDL.
- `Nikita.service`: systemd unit; `Nikita.spec`: PyInstaller spec; `env.example`: config template.
- `epf/`: 1C Designer XML/BSL dump of the external data processor (`epf/Nikita.xml`);
  `Nikita.epf`, `Nikita.cfe`, `ЖурналРегистрации.epf`: binary 1C artifacts.
- `dlls/`, `licenses/`: vendored Windows DLLs and licences of bundled installers.
- `docs/admin/` (install, configuration, monitoring, scripts, troubleshooting) and `docs/dev/`
  (architecture, internals, ClickHouse schema, log formats, SQLite state, code style, contributing).

## Setup / Build / Run / Test

Python 3.10+ (per `README.md` and `docs/dev/contributing.md`).

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.lin            # Windows: requirements.win (pinned, includes pywin32, pyinstaller)
pip install -r tests/requirements.test.txt
cp env.example .env                        # set C1_SRVINFO_PATH or IBASE_N*, ClickHouse settings
python Nikita.py console                   # foreground run; web panel on HTTP_LISTEN_PORT (8984)
./run_tests.sh                             # creates venv if missing, installs test deps, unittest discover -v
python -m unittest tests.test_parser_regex # a single module
```

- Windows: `.\scripts\run-tests.ps1` runs the tests; `.\scripts\!c.build.win.distr.cmd` builds the
  distribution and installer; `.\scripts\build.ps1 -Optimize -NoTest` builds only `dist\Nikita\Nikita.exe`;
  `.\scripts\create-installer.ps1` builds only the installer. The service is controlled with
  `Nikita.exe install|start|stop|remove`. Details: `BUILD.md`.
- Linux service: `Nikita.service` runs `/opt/Nikita/venv/bin/python /opt/Nikita/Nikita.py console`
  as `usr1cv8:grp1cv8` with `EnvironmentFile=/opt/Nikita/.env`.
- `tests/test_build.py` compiles `src/` with Cython in place (`build_ext --inplace`) and then
  deletes the generated `*.so`/`*.pyd`/`*.c`; it needs Cython and a C compiler.
  `tests/test_lgd_real_file.py` has a full run that skips unless its real `.lgd` file exists.
- There is no CI.

## Conventions

- Code style (`docs/dev/code-style.md`, mandatory): align `=` of related assignments in one
  column with at least 3 spaces before and after it; not for annotated class attributes, dict
  literals or one-line assignments inside conditions. Type hints for new functions; docstrings and
  comments in Russian; wrap risky code in `try...except`; log through `t.debug_print()`; guard
  shared data with locks.
- Bump `build_version` in `src/globals.py` on code changes (manual build tag shown in logs and MCP).
- Record user-visible changes in `CHANGELOG.md` under `[Unreleased]` (Russian, Keep a Changelog).
- Register new `src` modules in `hiddenimports` of `Nikita.spec`, or the PyInstaller build misses them.
- Default branch `master`; work lands through PRs from `feat/...` or `fix/...` branches
  (`docs/dev/contributing.md` suggests `feature/`, `bugfix/`, `docs/`, `refactor/`). Recent commit
  subjects are English sentences; the contributing guide shows Russian examples.
- Line endings (`.gitattributes`): `*.py` LF; `*.bsl`, `*.xml`, `*.html` CRLF in the working tree;
  `*.epf`, `*.erf`, `*.bin`, `*.mxl`, `*.png` binary.

## Gotchas and boundaries

- Never commit `.env`, `Nikita.ini`, `Journal2Ct.ini`, state databases (`*.db`,
  `Nikita.parser.state.db*`), `venv/`, `dist/`, `build/`, `solr*/`, `java/` or compiled `src/*.pyd`,
  `src/*.c`, `src/*.so`. `parser.state` is tracked although `.gitignore` lists it; do not update it.
- Config is read from `.env`; variable names are in `env.example` and `docs/admin/configuration.md`
  (`C1_SRVINFO_PATH`, `IBASE_<N>`, `IBASE_<N>_JR`, `IBASE_<N>_FORMAT`, `CLICKHOUSE_*`, `SOLR_*`,
  `REDIS_*`, `HTTP_LISTEN_INTERFACE`, `HTTP_LISTEN_PORT`, `MCP_*`, `PARSER_THREADS`, `DEBUG_*`).
- `POST /mcp` has no authentication: enable `MCP_ENABLED` only with `HTTP_LISTEN_INTERFACE=127.0.0.1`
  behind a reverse proxy, as `env.example` says. Other endpoints include `/stats_api` and `/debug_logs`.
- `g.ibases_lock` is a `threading.RLock` on purpose: the infobase monitor re-enters it through
  `conf.detect2`; a plain `Lock` deadlocked the service 15 s after start.
- When ClickHouse is unavailable the parser must not advance its saved state; keep that fail-closed
  behaviour when touching `parser.py` or `sender.py`.
- Edit `.epf`/`.cfe` only in 1C Designer and keep the `epf/` dump in sync; never hand-edit binaries.
- Building needs network access: the Windows build downloads JDK 17, Apache Solr and NSIS.
