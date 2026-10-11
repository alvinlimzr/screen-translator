# Build ScreenTranslator.exe on a Windows PC (PowerShell):
#   Set-ExecutionPolicy -Scope Process Bypass; .\build_windows.ps1
# Needs Python 3.12 (64-bit) from python.org. Result: dist\ScreenTranslator-windows.zip
$ErrorActionPreference = "Stop"
$LlamaTag = "b11552"          # llama.cpp release with the prebuilt Vulkan server

if (-not (Test-Path .venv)) { py -3.12 -m venv .venv }
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\pip install -r requirements-windows.txt

if (-not (Test-Path llama\llama-server.exe)) {
    $zip = "llama-$LlamaTag-bin-win-vulkan-x64.zip"
    Invoke-WebRequest "https://github.com/ggml-org/llama.cpp/releases/download/$LlamaTag/$zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath llama -Force
    Remove-Item $zip
    # some releases nest the files in a sub-folder: flatten
    $inner = Get-ChildItem llama -Recurse -Filter llama-server.exe | Select-Object -First 1
    if ($inner.DirectoryName -ne (Resolve-Path llama).Path) {
        Get-ChildItem $inner.DirectoryName | Move-Item -Destination llama -Force
    }
}

.\.venv\Scripts\python -m PyInstaller --noconfirm ScreenTranslator-win.spec
Compress-Archive -Path dist\ScreenTranslator\* -DestinationPath dist\ScreenTranslator-windows.zip -Force
Write-Host "Built: dist\ScreenTranslator\ScreenTranslator.exe  (zip: dist\ScreenTranslator-windows.zip)"
