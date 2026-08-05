#Requires -Version 5.1
<#
    Звук-Починить.ps1
    Чинит тихий/глухой звук в наушниках на Windows 10/11.

    Что делает:
      1. Отключает приглушение звука во время «связи» (Windows по умолчанию
         режет громкость на 80%, когда думает, что ты разговариваешь).
         Это самая частая причина «звук гавно».
      2. Отключает «улучшения звука» (эффекты) на выбранном устройстве —
         именно они делают звук глухим и ватным.
      3. Перезапускает службу звука, чтобы применилось без перезагрузки.

    Что НЕ делает: ничего не удаляет и не ставит. Все старые значения
    сохраняются в файл рядом со скриптом, откат — ключом -Undo.

    Запуск:   правой кнопкой по файлу → «Выполнить с помощью PowerShell»
    Откат:    в PowerShell от админа:  .\Звук-Починить.ps1 -Undo
#>

param(
    [switch]$Undo
)

$ErrorActionPreference = 'Stop'
$backupPath = Join-Path $PSScriptRoot 'звук-бэкап.json'

# ---------- проверка прав администратора ----------
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($id)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host ''
    Write-Host 'Нужны права администратора. Перезапускаю скрипт от админа...' -ForegroundColor Yellow
    Write-Host ''
    $argList = @('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$PSCommandPath`"")
    if ($Undo) { $argList += '-Undo' }
    try {
        Start-Process powershell.exe -Verb RunAs -ArgumentList $argList
    } catch {
        Write-Host 'Не дали права администратора. Запусти файл правой кнопкой -> "Запуск от имени администратора".' -ForegroundColor Red
        Read-Host 'Enter для выхода'
    }
    return
}

$DuckKey  = 'HKCU:\Software\Microsoft\Multimedia\Audio'
$DuckName = 'UserDuckingPreference'
$RenderRoot = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render'
$FxName   = '{1da5d803-d492-4edd-8c23-e0c0ffee7f0e},5'   # отключить эффекты
$NameProp = '{a45c254e-df1c-4efd-8020-67d146a850e0},2'   # понятное имя устройства

# =================== ОТКАТ ===================
if ($Undo) {
    if (-not (Test-Path $backupPath)) {
        Write-Host "Файла с бэкапом нет: $backupPath" -ForegroundColor Red
        Read-Host 'Enter для выхода'; return
    }
    $b = Get-Content $backupPath -Raw -Encoding UTF8 | ConvertFrom-Json

    if ($null -eq $b.Ducking) {
        Remove-ItemProperty -Path $DuckKey -Name $DuckName -ErrorAction SilentlyContinue
        Write-Host 'Приглушение при связи: вернул значение по умолчанию.'
    } else {
        Set-ItemProperty -Path $DuckKey -Name $DuckName -Value ([int]$b.Ducking) -Type DWord
        Write-Host "Приглушение при связи: вернул $($b.Ducking)."
    }

    foreach ($d in $b.Devices) {
        $p = Join-Path $d.Path 'Properties'
        if (-not (Test-Path $p)) { continue }
        try {
            if ($null -eq $d.Fx) {
                Remove-ItemProperty -Path $p -Name $FxName -ErrorAction SilentlyContinue
            } else {
                Set-ItemProperty -Path $p -Name $FxName -Value ([int]$d.Fx) -Type DWord
            }
            Write-Host "Улучшения звука: вернул как было -> $($d.Name)"
        } catch {
            Write-Host "Не смог откатить $($d.Name): $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    Restart-Service audiosrv -Force -ErrorAction SilentlyContinue
    Write-Host ''
    Write-Host 'Откат готов.' -ForegroundColor Green
    Read-Host 'Enter для выхода'
    return
}

# =================== СПИСОК УСТРОЙСТВ ===================
Write-Host ''
Write-Host '=== Устройства вывода звука ===' -ForegroundColor Cyan
Write-Host ''

$devices = @()
foreach ($k in (Get-ChildItem $RenderRoot -ErrorAction SilentlyContinue)) {
    $state = (Get-ItemProperty -Path $k.PSPath -Name 'DeviceState' -ErrorAction SilentlyContinue).DeviceState
    if ($state -ne 1) { continue }          # 1 = включено и подключено
    $props = Join-Path $k.PSPath 'Properties'
    $name = $null
    if (Test-Path $props) {
        $name = (Get-ItemProperty -Path $props -Name $NameProp -ErrorAction SilentlyContinue).$NameProp
    }
    if ([string]::IsNullOrWhiteSpace($name)) { $name = $k.PSChildName }
    $devices += [pscustomobject]@{ Name = $name; Path = $k.PSPath }
}

if ($devices.Count -eq 0) {
    Write-Host 'Не нашёл ни одного активного устройства вывода.' -ForegroundColor Red
    Read-Host 'Enter для выхода'; return
}

for ($i = 0; $i -lt $devices.Count; $i++) {
    Write-Host ("  [{0}] {1}" -f ($i + 1), $devices[$i].Name)
}
Write-Host ("  [0] все сразу")
Write-Host ''
$ans = Read-Host 'Номер твоих наушников (или 0 для всех)'

$target = @()
if ($ans -eq '0') {
    $target = $devices
} else {
    $n = 0
    if (-not [int]::TryParse($ans, [ref]$n) -or $n -lt 1 -or $n -gt $devices.Count) {
        Write-Host 'Не понял номер. Выхожу.' -ForegroundColor Red
        Read-Host 'Enter для выхода'; return
    }
    $target = @($devices[$n - 1])
}

# =================== БЭКАП ===================
$backup = [ordered]@{ Ducking = $null; Devices = @() }
$backup.Ducking = (Get-ItemProperty -Path $DuckKey -Name $DuckName -ErrorAction SilentlyContinue).$DuckName
foreach ($d in $target) {
    $p = Join-Path $d.Path 'Properties'
    $cur = $null
    if (Test-Path $p) { $cur = (Get-ItemProperty -Path $p -Name $FxName -ErrorAction SilentlyContinue).$FxName }
    $backup.Devices += [pscustomobject]@{ Name = $d.Name; Path = $d.Path; Fx = $cur }
}
$backup | ConvertTo-Json -Depth 5 | Out-File -FilePath $backupPath -Encoding UTF8
Write-Host ''
Write-Host "Старые значения сохранил: $backupPath" -ForegroundColor DarkGray

# =================== ПРАВКА 1: приглушение при связи ===================
if (-not (Test-Path $DuckKey)) { New-Item -Path $DuckKey -Force | Out-Null }
Set-ItemProperty -Path $DuckKey -Name $DuckName -Value 3 -Type DWord   # 3 = действие не требуется
Write-Host 'Готово: Windows больше не приглушает звук во время связи.' -ForegroundColor Green

# =================== ПРАВКА 2: улучшения звука ===================
$failed = @()
foreach ($d in $target) {
    $p = Join-Path $d.Path 'Properties'
    try {
        if (-not (Test-Path $p)) { New-Item -Path $p -Force | Out-Null }
        Set-ItemProperty -Path $p -Name $FxName -Value 1 -Type DWord
        Write-Host "Готово: отключил улучшения звука -> $($d.Name)" -ForegroundColor Green
    } catch {
        $failed += $d.Name
        Write-Host "Не смог тронуть: $($d.Name)" -ForegroundColor Yellow
    }
}

# =================== ПЕРЕЗАПУСК СЛУЖБЫ ===================
try {
    Restart-Service audiosrv -Force
    Write-Host 'Готово: служба звука перезапущена.' -ForegroundColor Green
} catch {
    Write-Host 'Служба звука не перезапустилась — просто перезагрузи ноут.' -ForegroundColor Yellow
}

if ($failed.Count -gt 0) {
    Write-Host ''
    Write-Host 'На эти устройства прав не хватило (Windows их защищает):' -ForegroundColor Yellow
    $failed | ForEach-Object { Write-Host "   $_" }
    Write-Host 'Для них сними галку вручную: mmsys.cpl -> устройство -> Свойства -> Enhancements/Улучшения -> Отключить все.'
}

# =================== ОСТАЛОСЬ ОДИН КЛИК ===================
Write-Host ''
Write-Host '=== Остался один шаг вручную, автоматом это не выставить ===' -ForegroundColor Cyan
Write-Host 'Сейчас откроется окно "Звук". Дальше:'
Write-Host '   1. вкладка "Воспроизведение" -> выбери наушники -> "Свойства"'
Write-Host '   2. вкладка "Дополнительно" -> формат по умолчанию:'
Write-Host '      "24 бит, 48000 Гц (Студийная запись)"'
Write-Host '   3. вкладка "Связь" (в первом окне) -> "Действие не требуется"'
Write-Host '   4. ОК'
Write-Host ''
Read-Host 'Enter — открыть окно "Звук"'
Start-Process control.exe -ArgumentList 'mmsys.cpl'

Write-Host ''
Write-Host 'Всё. Если станет хуже — запусти:  .\Звук-Починить.ps1 -Undo' -ForegroundColor Cyan
Read-Host 'Enter для выхода'
