# Screen Translator for Windows

Same app as the Mac version: on-screen translation between **English, Thai and
Japanese**, hover or full-overlay mode, live tracking (beta), area selection,
favourites. Fully offline after a one-time model download.

| | Mac | Windows |
|---|---|---|
| Screen capture | ScreenCaptureKit | `mss` + overlay excluded via `SetWindowDisplayAffinity` |
| OCR | Apple Vision | RapidOCR / PaddleOCR models (bundled, 42 MB) |
| Translation | Hy-MT 1.8B on MLX | Hy-MT 1.8B on llama.cpp server (Vulkan GPU, CPU fallback) |
| UI | AppKit, menu bar 文A | Qt (PySide6), system tray |
| Save a sentence | ⌥ Option + click | **Shift + click** |
| Peek at original | hold ⌃ Control | hold **Ctrl** |

Requires Windows 10 (version 2004 or later) or Windows 11, 64-bit.

## Option A — build in the cloud (no Windows PC needed)
1. Create a GitHub repo and push this folder to it.
2. GitHub → **Actions** → **Build Windows app** → **Run workflow**.
3. When it finishes (~10 min), download **ScreenTranslator-windows** from the
   run's *Artifacts*, unzip anywhere, run **ScreenTranslator.exe**.
   Pushing a tag like `v1.0` also attaches a zip to a GitHub Release.

## Option B — build on a Windows PC
Install Python 3.12 (64-bit) from python.org, then in PowerShell in this folder:
```
Set-ExecutionPolicy -Scope Process Bypass
.\build_windows.ps1
```
Result: `dist\ScreenTranslator\ScreenTranslator.exe` (+ `dist\ScreenTranslator-windows.zip`).

## Option C — run from source (for development)
```
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements-windows.txt
.venv\Scripts\python win_app.py
```
(Download `llama-<tag>-bin-win-vulkan-x64.zip` from llama.cpp releases into
`llama\` first, as `build_windows.ps1` does.)

## First launch
- The app offers to download the translation model (Hy-MT 1.8B, 4-bit, ~1.1 GB)
  with a progress bar. That's the only time it uses the internet.
- Windows SmartScreen may warn because the .exe isn't code-signed: *More info →
  Run anyway*. (Signing needs a paid certificate; worth it before public release.)
- Data: `%APPDATA%\Screen Translator` (settings, favourites, translation cache);
  models: `%LOCALAPPDATA%\Screen Translator\models`.

## Not yet verified on a real Windows PC
Everything except the Windows-only calls was run on Linux: OCR on real Thai and
Japanese screenshots, translation, the engine, and the Qt overlay/control
window rendered off-screen. To check on the PC: click-through, Shift-click
saving, capture exclusion (the status line should say "overlay hidden from
capture"), tray icon, and GPU use by the translation server.
