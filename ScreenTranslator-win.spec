# PyInstaller spec for the Windows app:  pyinstaller ScreenTranslator-win.spec
# Produces dist/ScreenTranslator/ScreenTranslator.exe (+ its folder).
from PyInstaller.utils.hooks import collect_all

datas = [("ocr_models", "ocr_models"), ("icon.png", "."), ("llama", "llama")]
binaries, hiddenimports = [], ["win.app", "win.overlay", "win.ui", "win.ocr", "win.capture", "win.winapi"]
for pkg in ("rapidocr", "onnxruntime"):
    d, b, h = collect_all(pkg)
    # rapidocr's own model cache is not needed: our models are in ocr_models/
    datas += [x for x in d if "rapidocr/models" not in x[1].replace("\\", "/")]
    binaries += b
    hiddenimports += h

a = Analysis(
    ["win_app.py"],
    pathex=["."],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["mlx", "mlx_lm", "llama_cpp", "ctranslate2", "sentencepiece", "objc", "AppKit", "Foundation", "Quartz", "Vision",
              "ScreenCaptureKit", "tkinter", "matplotlib", "torch", "PySide6.QtWebEngineCore",
              "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ScreenTranslator",
          icon="icon.ico", console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="ScreenTranslator", upx=False)
