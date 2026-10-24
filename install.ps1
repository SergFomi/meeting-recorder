# Установка Рекордера встреч: окружение Python, зависимости, ярлыки на рабочем столе и в автозагрузке.
# Запуск: правый клик по файлу -> «Выполнить с помощью PowerShell»,
# либо в папке проекта:  powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = $null
foreach ($v in "3.13", "3.12", "3.11") {
    & py "-$v" --version *> $null
    if ($LASTEXITCODE -eq 0) { $py = $v; break }
}
if (-not $py) { throw "Нужен Python 3.11-3.13 (https://www.python.org/downloads/). Поставьте и запустите установку снова." }
& git --version *> $null
if ($LASTEXITCODE -ne 0) { throw "Нужен git (https://git-scm.com/download/win): модель GigaAM ставится из GitHub." }

Write-Host "Создаю окружение (Python $py)..."
& py "-$py" -m venv .venv
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $venvPy -m pip install --upgrade pip

& nvidia-smi *> $null
if ($LASTEXITCODE -eq 0) { $index = "https://download.pytorch.org/whl/cu128"; Write-Host "Нашёл видеокарту NVIDIA: ставлю torch с CUDA." }
else { $index = "https://download.pytorch.org/whl/cpu"; Write-Host "Видеокарты NVIDIA нет: ставлю torch для процессора." }
& $venvPy -m pip install torch==2.11.0 torchaudio==2.11.0 --index-url $index
& $venvPy -m pip install -r requirements.txt

# Ярлыки: на рабочий стол и в автозагрузку. Запускают сторожа, он перезапускает программу, если она вылетит.
$pyw = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
$shell = New-Object -ComObject WScript.Shell
$targets = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Рекордер встреч.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Startup")) "MeetingRecorder.lnk")
)
foreach ($path in $targets) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $pyw
    $lnk.Arguments = "-m mrec.supervisor"
    $lnk.WorkingDirectory = $PSScriptRoot
    $lnk.IconLocation = (Join-Path $PSScriptRoot "icon.ico")
    $lnk.Save()
}
Write-Host "Готово. Запускаю программу: значок появится в трее (у часов)."
Start-Process $pyw -ArgumentList "-m", "mrec.supervisor" -WorkingDirectory $PSScriptRoot
