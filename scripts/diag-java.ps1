# diag-java.ps1 - Диагностика "UnsupportedClassVersionError" при запуске Solr/Jetty (Nikita)
#
# Симптом:
#   java.lang.UnsupportedClassVersionError: org/eclipse/jetty/start/Main
#   has been compiled by a more recent version of the Java Runtime
#   (class file version 55.0), this version of the Java Runtime only
#   recognizes class file versions up to 52.0
#
#   55.0 = Java 11  -> так скомпилирован Solr/Jetty (минимум, который ему нужен)
#   52.0 = Java 8   -> такая Java сейчас активна => ошибка
#   Solr в составе Nikita требует Java 17 (bundled Temurin 17).
#
# По умолчанию скрипт НИЧЕГО не меняет — только диагностика и вердикт.
# С ключом -Apply он прописывает SOLR_JAVA_HOME на bundled Java 17 и проверяет результат.
#
# ЗАПУСК — только диагностика:
#   powershell -NoProfile -ExecutionPolicy Bypass -File diag-java.ps1 -NikitaDir "C:\путь\к\Nikita"
#
# ЗАПУСК — диагностика + установка SOLR_JAVA_HOME
# (cmd/PowerShell ОТ АДМИНИСТРАТОРА — служба Nikita работает под LocalSystem):
#   powershell -NoProfile -ExecutionPolicy Bypass -File diag-java.ps1 -NikitaDir "C:\путь\к\Nikita" -Apply -RestartService
#
# -NikitaDir       — каталог установки Nikita (подпапки java\ и solr\)
# -SolrDir         — задать явно, если solr\ лежит не внутри NikitaDir
# -Apply           — прописать SOLR_JAVA_HOME (по умолчанию выключено, скрипт только читает)
# -Scope           — Machine (по умолчанию; для службы LocalSystem, нужен админ) | User
# -JavaHome        — явный путь к JDK17 (иначе берётся bundled <NikitaDir>\java)
# -RestartService  — перезапустить службу Nikita после установки

param(
    [string]$NikitaDir,
    [string]$SolrDir,
    [switch]$Apply,
    [ValidateSet('Machine','User')]
    [string]$Scope = 'Machine',
    [string]$JavaHome,
    [switch]$RestartService
)

$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

# ----------------------------- helpers -----------------------------

function Section($t) {
    Write-Host ""
    Write-Host ("=" * 72) -ForegroundColor DarkGray
    Write-Host "  $t" -ForegroundColor Cyan
    Write-Host ("=" * 72) -ForegroundColor DarkGray
}
function KV($k, $v) { Write-Host ("  {0,-20} {1}" -f $k, $v) }
function Ok($m)     { Write-Host "  [OK]   $m" -ForegroundColor Green }
function Warn($m)   { Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Fail($m)   { Write-Host "  [FAIL] $m" -ForegroundColor Red }
function OrNone($v) { if ($v) { $v } else { "(не задано)" } }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# class-file major -> Java major: 49=5, 52=8, 55=11, 61=17
function ClassToJava([int]$m) { if ($m -ge 49) { $m - 44 } else { 0 } }

# major-версия Java из java.exe (логика как в common.ps1 / src/solr.py)
function Get-JavaMajor([string]$javaExe) {
    if (-not $javaExe -or -not (Test-Path $javaExe)) { return 0 }
    try { $out = (& $javaExe -version 2>&1 | Out-String) } catch { return 0 }
    $m = [regex]::Match($out, 'version "([^"]+)"')
    if (-not $m.Success) { return 0 }
    $v = $m.Groups[1].Value
    if ($v.StartsWith("1.")) { return [int]($v.Split('.')[1]) }
    return [int](($v -split '[.\-+]')[0])
}

function Get-JavaLine([string]$javaExe) {
    if (-not $javaExe -or -not (Test-Path $javaExe)) { return "(нет java.exe)" }
    try {
        # "$_" приводит строки stderr к тексту, иначе PS рендерит их как NativeCommandError
        $out = & $javaExe -version 2>&1 | ForEach-Object { "$_" }
        if ($out) { return ($out | Select-Object -First 1).Trim() }
        return "(нет вывода)"
    } catch { return "(ошибка запуска)" }
}

# Найти bin\java.exe прямо в каталоге или во вложенной jdk-*\ папке
function Find-JavaExe([string]$dir) {
    if (-not $dir -or -not (Test-Path $dir)) { return $null }
    $direct = Join-Path $dir "bin\java.exe"
    if (Test-Path $direct) { return $direct }
    $nested = Get-ChildItem $dir -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { Join-Path $_.FullName "bin\java.exe" } |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    return $nested
}

Write-Host ""
Write-Host "  ДИАГНОСТИКА JAVA для Solr/Jetty (Nikita)" -ForegroundColor White
Write-Host "  $(Get-Date)  |  PowerShell $($PSVersionTable.PSVersion)  |  OS x$([IntPtr]::Size * 8)" -ForegroundColor DarkGray

# ----------------------- 1. смысл ошибки -------------------------

Section "1. Что означает ошибка (расшифровка строки лога, НЕ текущее состояние)"
Write-Host "  UnsupportedClassVersionError ... class file version 55.0 ... up to 52.0"
Write-Host "  (это разбор сообщения из лога; что активно СЕЙЧАС — см. разделы 2 и 6)" -ForegroundColor DarkGray
KV "class 55.0 =" "Java 11  (под такую версию собран Solr/Jetty — минимум)"
KV "class 52.0 =" "Java 8   (на такой Java ловилась ошибка в логе)"
Write-Host "  Solr в составе Nikita требует Java 17." -ForegroundColor Yellow

# ------------------- 2. активная Java и env ----------------------

Section "2. Активная Java (PATH) и переменные окружения"

$activeMajor = 0
$whereJava = @(& where.exe java 2>$null)
if ($whereJava.Count -gt 0) {
    Write-Host "  where java:"
    $whereJava | ForEach-Object { Write-Host "      $_" }
    $first = $whereJava[0]
    KV "java -version" (Get-JavaLine $first)
    $activeMajor = Get-JavaMajor $first
    if ($activeMajor -gt 0) {
        if ($activeMajor -lt 17) { Fail "Активная Java в PATH = Java $activeMajor (нужно 17)" }
        else { Ok "Активная Java в PATH = Java $activeMajor" }
    }
} else {
    Warn "java не найдена в PATH (where java ничего не вернул)"
}

Write-Host ""
KV "JAVA_HOME"      (OrNone $env:JAVA_HOME)
KV "SOLR_JAVA_HOME" (OrNone $env:SOLR_JAVA_HOME)
KV "JRE_HOME"       (OrNone $env:JRE_HOME)
KV "SOLR_DIR"       (OrNone $env:SOLR_DIR)

foreach ($varName in @('JAVA_HOME', 'SOLR_JAVA_HOME')) {
    $val = [System.Environment]::GetEnvironmentVariable($varName)
    if ($val) {
        $je = Find-JavaExe $val
        if ($je) {
            $mj = Get-JavaMajor $je
            if ($mj -ge 17) { Ok "$varName -> Java $mj  ($je)" }
            else { Fail "$varName -> Java $mj — мало, нужно 17  ($je)" }
        } else {
            Warn "$varName указывает на путь без bin\java.exe: $val"
        }
    }
}

# --------------- 3. bundled Java и Solr из NikitaDir -------------

Section "3. Bundled Java и Solr (каталог Nikita)"

if (-not $NikitaDir) {
    Warn "Параметр -NikitaDir не задан. Разделы 3 и 4 пропущены."
    Warn "Повторите запуск: .\diag-java.ps1 -NikitaDir `"C:\путь\к\Nikita`""
}

$bundledHome  = $null
$bundledMajor = 0

if ($NikitaDir) {
    KV "Nikita dir" $NikitaDir
    $javaRoot = Join-Path $NikitaDir "java"
    if (Test-Path $javaRoot) {
        $bundledExe = Find-JavaExe $javaRoot
        if ($bundledExe) {
            $bundledHome  = Split-Path (Split-Path $bundledExe -Parent) -Parent
            $bundledMajor = Get-JavaMajor $bundledExe
            KV "bundled java" "Java $bundledMajor  ($bundledExe)"
            if ($bundledMajor -ge 17) { Ok "Bundled JDK 17 на месте — Solr нужно запускать ИМЕННО им" }
            else { Fail "Bundled java = Java $bundledMajor (ожидалась 17 — битая/старая установка)" }
        } else {
            Fail "Каталог '$javaRoot' есть, но bin\java.exe внутри не найден (не распаковалось?)"
        }
    } else {
        Fail "Каталог '$javaRoot' отсутствует — bundled Java 17 не установлена"
        Warn "Запустите .\scripts\download-deps.ps1 для загрузки bundled Java"
    }

    if (-not $SolrDir) {
        $sd = Join-Path $NikitaDir "solr"
        if (Test-Path $sd) { $SolrDir = $sd }
    }
}
if (-not $SolrDir -and $env:SOLR_DIR) { $SolrDir = $env:SOLR_DIR }
KV "Solr dir" (OrNone $SolrDir)

# ---------- 4. под какую Java собран start.jar -------------------

Section "4. Требуемая версия из start.jar (org/eclipse/jetty/start/Main)"

$startJar = $null
if ($SolrDir) {
    foreach ($rel in @("server\start.jar", "start.jar")) {
        $jarPath = Join-Path $SolrDir $rel
        if (Test-Path $jarPath) { $startJar = $jarPath; break }
    }
}

if ($startJar) {
    KV "start.jar" $startJar
    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop
        $zip = [System.IO.Compression.ZipFile]::OpenRead($startJar)
        try {
            $entry = $zip.Entries |
                Where-Object { $_.FullName -eq 'org/eclipse/jetty/start/Main.class' } |
                Select-Object -First 1
            if ($entry) {
                $s   = $entry.Open()
                $buf = New-Object byte[] 8
                $off = 0
                while ($off -lt 8) {
                    $n = $s.Read($buf, $off, 8 - $off)
                    if ($n -le 0) { break }
                    $off += $n
                }
                $s.Close()
                $classMajor = ($buf[6] -shl 8) -bor $buf[7]
                $needJava   = ClassToJava $classMajor
                KV "class file version" "$classMajor.0  => требует Java $needJava+"
            } else {
                Warn "В start.jar не найден org/eclipse/jetty/start/Main.class"
            }
        } finally { $zip.Dispose() }
    } catch {
        Warn "Не удалось прочитать start.jar: $($_.Exception.Message)"
    }
} elseif ($SolrDir) {
    Warn "start.jar не найден в '$SolrDir\server\start.jar' и '$SolrDir\start.jar'"
} else {
    Warn "SolrDir не определён — раздел пропущен"
}

# -------------- 5. все JDK/JRE, найденные в системе -------------

Section "5. Установленные в системе JDK/JRE"

$found = @()

# Стандартные каталоги
$roots = @(
    "C:\Program Files\Java",
    "C:\Program Files\Eclipse Adoptium",
    "C:\Program Files\Eclipse Foundation",
    "C:\Program Files\Microsoft",
    "C:\Program Files\Amazon Corretto",
    "C:\Program Files\Zulu",
    "C:\Program Files\BellSoft",
    "C:\Program Files\Semeru",
    "C:\Program Files (x86)\Java"
)
foreach ($r in $roots) {
    if (Test-Path $r) {
        Get-ChildItem $r -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $je = Join-Path $_.FullName "bin\java.exe"
            if (Test-Path $je) {
                $found += [pscustomobject]@{ Path = $_.FullName; Major = (Get-JavaMajor $je) }
            }
        }
    }
}

# Реестр JavaSoft и Adoptium (JavaHome под версионными ключами)
$regKeys = @(
    "HKLM:\SOFTWARE\JavaSoft\JDK",
    "HKLM:\SOFTWARE\JavaSoft\Java Development Kit",
    "HKLM:\SOFTWARE\Eclipse Adoptium\JDK",
    "HKLM:\SOFTWARE\Microsoft\JDK"
)
foreach ($rk in $regKeys) {
    if (-not (Test-Path $rk)) { continue }
    Get-ChildItem $rk -ErrorAction SilentlyContinue | ForEach-Object {
        $jh = (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).JavaHome
        if (-not $jh) {
            # Temurin: HKLM:\...\JDK\17\hotspot\MSI -> JavaHome
            $sub = Get-ChildItem $_.PSPath -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($sub) { $jh = (Get-ItemProperty $sub.PSPath -ErrorAction SilentlyContinue).JavaHome }
        }
        if ($jh -and (Test-Path (Join-Path $jh "bin\java.exe"))) {
            $found += [pscustomobject]@{
                Path  = $jh
                Major = (Get-JavaMajor (Join-Path $jh "bin\java.exe"))
            }
        }
    }
}

foreach ($f in $found) { $f.Path = $f.Path.TrimEnd('\') }   # нормализуем хвостовой слэш из реестра
$found = $found | Sort-Object Path -Unique
if ($found.Count -gt 0) {
    foreach ($f in $found) {
        $tag = if ($f.Major -ge 17)  { "[годится для Solr]" }
               elseif ($f.Major -gt 0) { "[Java $($f.Major) — мало]" }
               else { "[версия не определена]" }
        Write-Host ("  Java {0,-3} {1,-22} {2}" -f $f.Major, $tag, $f.Path)
    }
} else {
    Warn "JDK в стандартных каталогах/реестре не найдены"
}

# ---- 6. применение: per-service переменная службы + живой процесс Solr ----

Section "6. Применение SOLR_JAVA_HOME (служба Nikita и живой процесс Solr)"

# 6a. Переменная в ВЕТКЕ СЛУЖБЫ Nikita — именно туда её пишет установщик (per-service)
$svcSolrHome  = $null
$svcSolrMajor = 0
$svc = Get-Service -Name 'Nikita' -ErrorAction SilentlyContinue
if ($svc) {
    $acct = (Get-CimInstance Win32_Service -Filter "Name='Nikita'" -ErrorAction SilentlyContinue).StartName
    KV "Служба Nikita" "статус=$($svc.Status); учётка=$(OrNone $acct)"
    $envVals = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\Nikita" -Name Environment -ErrorAction SilentlyContinue).Environment
    if ($envVals) {
        $hit = $envVals | Where-Object { $_ -match '^SOLR_JAVA_HOME=' } | Select-Object -First 1
        if ($hit) {
            $svcSolrHome  = ($hit -replace '^SOLR_JAVA_HOME=', '')
            $sje          = Find-JavaExe $svcSolrHome
            $svcSolrMajor = if ($sje) { Get-JavaMajor $sje } else { 0 }
            KV "per-service SOLR_JAVA_HOME" $svcSolrHome
            if ($svcSolrMajor -ge 17) { Ok "В ветке службы задана Java $svcSolrMajor — установщик (per-service) отработал" }
            elseif ($svcSolrMajor -gt 0) { Fail "per-service SOLR_JAVA_HOME -> Java $svcSolrMajor (нужно 17)" }
            else { Fail "per-service SOLR_JAVA_HOME указывает на путь без java.exe: $svcSolrHome" }
        } else {
            Warn "В Services\Nikita\Environment есть записи, но SOLR_JAVA_HOME среди них нет:"
            $envVals | ForEach-Object { Write-Host "      $_" }
        }
    } else {
        Fail "У службы Nikita НЕТ Environment с SOLR_JAVA_HOME — установщик с per-service правкой не применён (старый build / не переустанавливали)"
    }
} else {
    Warn "Служба 'Nikita' не зарегистрирована — per-service переменную проверить негде (console-режим?)"
}

# 6b. ЖИВОЙ процесс Solr: какую java.exe он РЕАЛЬНО использует (ground truth)
$runSolrExe   = $null
$runSolrMajor = 0
$javaProcs = @(Get-CimInstance Win32_Process -Filter "Name='java.exe'" -ErrorAction SilentlyContinue)
if ($javaProcs.Count -gt 0) {
    Write-Host "  Запущенные java.exe:"
    foreach ($p in $javaProcs) {
        $isSolr = ($p.CommandLine -match 'start\.jar|jetty|solr')
        Write-Host ("      pid=$($p.ProcessId)  $($p.ExecutablePath)" + $(if ($isSolr) { '   <-- Solr' } else { '' }))
        if ($isSolr -and -not $runSolrExe) { $runSolrExe = $p.ExecutablePath }
    }
    # единственный java.exe почти наверняка и есть Solr (когда CommandLine не читается без админа)
    if (-not $runSolrExe -and $javaProcs.Count -eq 1) { $runSolrExe = $javaProcs[0].ExecutablePath }
    if ($runSolrExe) {
        $runSolrMajor = Get-JavaMajor $runSolrExe
        if ($runSolrMajor -ge 17) { Ok "Живой Solr использует Java $runSolrMajor — ПРИМЕНЕНО верно ($runSolrExe)" }
        elseif ($runSolrMajor -gt 0) { Fail "Живой Solr работает на Java $runSolrMajor — НЕ применилось ($runSolrExe)" }
        else { Warn "Версию Java живого Solr определить не удалось: $runSolrExe" }
    } else {
        Warn "java.exe есть, но процесс Solr не опознан (CommandLine пуст? запустите скрипт ОТ АДМИНИСТРАТОРА)"
    }
} else {
    Warn "Запущенных java.exe нет — Solr сейчас не работает. Запустите Nikita/службу и повторите раздел 6."
}

# ------------------------- 7. вердикт ----------------------------

Section "7. ВЕРДИКТ И ЧТО ДЕЛАТЬ"

# Рантайм — самый надёжный сигнал: что реально использует живой Solr
if ($runSolrMajor -ge 17) {
    Ok "Живой Solr уже на Java $runSolrMajor — SOLR_JAVA_HOME применяется. Если Solr всё равно падает, причина НЕ в версии Java — смотрите solr\server\logs\*."
} elseif ($runSolrMajor -gt 0) {
    Fail "Живой Solr на Java $runSolrMajor — переменная не долетела до процесса Solr (см. раздел 6: задана ли она в ветке службы; запускаете ли Solr через службу Nikita, а не вручную)."
}

$jdk17 = $null
if ($bundledHome -and $bundledMajor -ge 17) {
    $jdk17 = $bundledHome
} else {
    $sys17 = $found | Where-Object { $_.Major -ge 17 } | Select-Object -First 1
    if ($sys17) { $jdk17 = $sys17.Path }
}

if ($activeMajor -gt 0 -and $activeMajor -lt 17) {
    Fail "Причина: Solr запускается на Java $activeMajor, нужна Java 17. Отсюда UnsupportedClassVersionError."
} elseif ($activeMajor -eq 0) {
    Warn "Активная Java не определена — но ошибка 'up to 52.0' означает Java 8 в PATH."
} else {
    Ok "Активная Java в PATH = Java $activeMajor."
}

Write-Host ""
if ($jdk17) {
    Write-Host "  Подходящая Java 17 найдена:" -ForegroundColor Green
    Write-Host "      $jdk17" -ForegroundColor Green
    Write-Host ""
    Write-Host "  РЕШЕНИЕ — указать Solr эту Java через SOLR_JAVA_HOME:" -ForegroundColor White
    Write-Host ""
    Write-Host "    1) Разово в текущем окне cmd (потом запускать Nikita из него же):"
    Write-Host "         set SOLR_JAVA_HOME=$jdk17" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "    2) Постоянно для пользователя (новые окна):"
    Write-Host "         setx SOLR_JAVA_HOME `"$jdk17`"" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "    3) В файле .env рядом с Nikita:"
    Write-Host "         SOLR_JAVA_HOME=$jdk17" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  ВАЖНО: запускайте Solr через Nikita, а не 'solr.cmd start' / 'java -jar start.jar'"
    Write-Host "  напрямую — при прямом запуске Solr берёт java из PATH (у вас там Java 8)."
    Write-Host ""
    Write-Host "  Прописать это автоматически — перезапустите скрипт с -Apply (cmd ОТ АДМИНИСТРАТОРА):" -ForegroundColor White
    Write-Host "         diag-java.ps1 -NikitaDir <каталог Nikita> -Apply -RestartService" -ForegroundColor Cyan
} else {
    Fail "Java 17 в системе НЕ найдена (ни bundled, ни установленная)."
    Write-Host ""
    Write-Host "  РЕШЕНИЕ:" -ForegroundColor White
    Write-Host "    - Запустите .\scripts\download-deps.ps1 — скачает Temurin 17 в .\java"
    Write-Host "    - Или переустановите Nikita из дистрибутива (содержит bundled java\)"
    Write-Host "    - Или установите Eclipse Temurin / Microsoft OpenJDK 17 вручную"
    Write-Host "      и задайте SOLR_JAVA_HOME на путь к нему"
}

# -------- 8. установка SOLR_JAVA_HOME (только при -Apply) --------

if ($Apply) {
    Section "8. УСТАНОВКА SOLR_JAVA_HOME"

    # Выбор целевого JDK: -JavaHome > bundled > системный JDK17
    $target = $JavaHome
    if (-not $target) {
        if ($bundledHome -and $bundledMajor -ge 17) { $target = $bundledHome }
        elseif ($jdk17) { $target = $jdk17 }
    }

    if (-not $target) {
        Fail "Java 17 не найдена — нечего прописывать."
        Warn "Укажите -JavaHome `"<путь к JDK17>`" или установите bundled java (download-deps.ps1)."
    } else {
        # Проверяем, что по пути реально Java >= 17, и нормализуем на корень JDK (родитель bin\)
        $tje = Find-JavaExe $target
        $tmj = if ($tje) { Get-JavaMajor $tje } else { 0 }
        if (-not $tje -or $tmj -lt 17) {
            Fail "По пути '$target' не найдена Java 17 (определено: $tmj). Установка отменена."
        } else {
            $target = Split-Path (Split-Path $tje -Parent) -Parent
            KV "Цель" "SOLR_JAVA_HOME = $target  (Java $tmj)"
            KV "Область (Scope)" $Scope

            if ($Scope -eq 'Machine' -and -not (Test-Admin)) {
                Fail "Для -Scope Machine нужны права администратора (служба Nikita = LocalSystem)."
                Warn "Запустите cmd/PowerShell от администратора и повторите,"
                Warn "либо -Scope User (но службу LocalSystem пользовательская переменная НЕ увидит)."
            } else {
                try {
                    [System.Environment]::SetEnvironmentVariable('SOLR_JAVA_HOME', $target, $Scope)
                    $env:SOLR_JAVA_HOME = $target   # и в текущем процессе сразу
                    Ok "SOLR_JAVA_HOME записан (Scope=$Scope)."

                    # Верификация: читаем значение обратно из той же области и проверяем версию
                    $readback = [System.Environment]::GetEnvironmentVariable('SOLR_JAVA_HOME', $Scope)
                    if ($readback -eq $target) {
                        Ok "Проверка: в реестре ($Scope) = $readback"
                        $rje = Find-JavaExe $readback
                        $rmj = if ($rje) { Get-JavaMajor $rje } else { 0 }
                        if ($rmj -ge 17) { Ok "Проверка: по этому пути Java $rmj — ОК" }
                        else { Fail "Проверка: по записанному пути Java не найдена/мало ($rmj)" }
                    } else {
                        Fail "Проверка не прошла: в реестре '$readback', ожидалось '$target'"
                    }
                } catch {
                    Fail "Не удалось записать переменную: $($_.Exception.Message)"
                }
            }

            # Перезапуск службы, чтобы Solr увидел новую переменную
            if ($RestartService) {
                $svc = Get-Service -Name 'Nikita' -ErrorAction SilentlyContinue
                if (-not $svc) {
                    Warn "Служба 'Nikita' не найдена (вероятно, console-режим — перезапустите Nikita вручную)."
                } elseif (-not (Test-Admin)) {
                    Warn "Нет прав администратора — служба не перезапущена. Вручную: sc stop Nikita && sc start Nikita"
                } else {
                    try {
                        if ($svc.Status -eq 'Running') {
                            Restart-Service -Name 'Nikita' -Force -ErrorAction Stop
                            Ok "Служба Nikita перезапущена"
                        } else {
                            Start-Service -Name 'Nikita' -ErrorAction Stop
                            Ok "Служба Nikita запущена"
                        }
                    } catch {
                        Fail "Не удалось перезапустить службу: $($_.Exception.Message)"
                    }
                }
            } else {
                Warn "Перезапустите Nikita, чтобы Solr увидел переменную: sc stop Nikita && sc start Nikita"
            }
        }
    }
}

Write-Host ""
Write-Host ("=" * 72) -ForegroundColor DarkGray
if ($Apply) {
    Write-Host "  Готово. Если переменная записана — перезапустите Solr/службу Nikita и проверьте java.exe Solr'а." -ForegroundColor DarkGray
} else {
    Write-Host "  Диагностика завершена. Скрипт ничего не менял (для установки добавьте -Apply)." -ForegroundColor DarkGray
}
Write-Host ("=" * 72) -ForegroundColor DarkGray
Write-Host ""
