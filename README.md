# Screen Translator v1.4 (macOS, fully offline)

> **Windows version:** see [README_WINDOWS.md](README_WINDOWS.md) (`win_app.py`, builds a `.exe`).

Translates text on your screen between **English, Thai and Japanese** (any
direction), with a control window, hover or full-overlay modes, and a
favourites list for revision. OCR is Apple Vision; translation is a local model.
Nothing leaves your Mac. Internet is used only when you choose to download a model.

## 1. Install (once, Apple Silicon, macOS 14+)
```
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py
```
On first launch the app offers to download the translation model (~1–2 GB) and
macOS asks for **Screen Recording** permission (System Settings → Privacy &
Security → Screen Recording). Allow it, then quit and reopen.

Already have models from v1.1/1.2? Copy your `models` folder into this one.

## 2. Make it a real app (optional)
```
source venv/bin/activate
./make_app.sh          # creates "Screen Translator.app" here; drag it to the Dock
```
The .app runs this folder's code with your venv, so keep the folder in place.
Logs: `~/Library/Logs/Screen Translator.log`.

## Using it
**Control window** (⌘, or the 文A menu-bar icon opens it again; closing it keeps the app running):
- **From / To**: English, Thai, Japanese; ⇄ swaps.
- **Mode**: *Hover* (default): each detected chunk gets a thin light-blue
  outline; rest the mouse on it and the translation appears just below.
  *Full overlay*: semi-transparent cards over everything.
- **Outline detected text** on/off (hover mode), **card opacity**, **text size**.
- **Area**: whole screen or a selected rectangle.
- **Tracking** (Standard / Live beta), **scan every**, **hover delay**,
  **OCR quality** (Fast / Balanced / Best), **model** (with download progress).
- **Pause**, **Favourites**, **Clear cache**, and live stats.

**Saving**: hold **⌥ Option** over a translation, click a sentence → ★ saved.
**Peeking**: hold **⌃ Control** to hide translations while held.
**Favourites window**: copy, delete, export CSV (for Anki etc.), show file.

## New in v1.4
- **Old translations no longer linger.** A chunk not found by a scan is hidden at
  once (and re-checked 0.7 s later), and between scans any chunk whose area of the
  screen changed (you scrolled, switched tab) is hidden within 0.4 s. Stale chunks
  are never merged into live cards any more.
- **Live tracking (beta)**: *Speed → Tracking → Live*. About 12 times a second the
  app measures how each chunk moved (per chunk, so panes scrolling separately and
  fixed sidebars work) and glides its translation along; chunks scrolled off
  screen vanish, replaced content hides. OCR only runs when the page settles.
  Uses more CPU than Standard.
- **Download progress bar** with size, speed and time left.
- **Select area…**: translate just a comic panel, a subtitle strip, or one window.
  Faster OCR, less clutter. *Whole screen* resets it.
- **Hold ⌃ Control** to hide all translations and peek at the original.
- The model loads in the background at launch, so the first translation is quick.
- Smarter chunks: lone number/quote lines (like `" 1 "` in a vertical speech
  bubble) join the text around them; touching chunks merge more readily; hover
  outlines that overlap become one; cards stay on screen.

## Why v1.3 was faster
- **Idle screen ≈ no work**: every 0.4 s a 160×100 thumbnail is compared with the
  last scan; OCR runs only if something changed, and waits for scrolling to stop.
- **No image round-trips**: the screen image goes straight to Vision (v1.2
  re-encoded every screenshot as PNG first).
- **Translations saved on disk**: anything seen before shows instantly, even
  after restarting (tested: 5 sentences 16.6 s → 0.00 s on a revisit).
- **OCR quality** setting: Balanced (default) reads at 1.5× instead of 2×
  resolution, ~44% fewer pixels for Vision; Fast uses 1×.
- **Faster model option** (4-bit), and generation length capped per sentence.

## Files
`app.py` app & menus · `ui.py` control + favourites windows · `overlay.py`
on-screen drawing · `capture.py` screen capture + Vision OCR · `change.py`
when to scan · `motion.py` live tracking + stale detection · `pipeline.py` chunks + translation queue · `chunker.py` /
`layout.py` / `tracker.py` grouping, card layout, scroll tracking ·
`translators.py` models · `cache.py` disk cache · `settings.py` · `favorites.py`
· `langtools.py` · `test_*.py`

Shared, platform-neutral core (also used by Windows): `engine.py`, `scene.py`, `pipeline.py`, `chunker.py`,
`layout.py`, `tracker.py`, `motion.py`, `change.py`, `cache.py`, `settings.py`, `favorites.py`, `translators.py`.

Data (settings, favourites, cache): `~/Library/Application Support/Screen Translator/`

## Models & licences
- **Hy-MT1.5-1.8B** (Tencent): best quality, uses context. 8-bit (default) or
  4-bit (faster). Commercial use allowed **except in the EU, UK and South Korea**.
- **NLLB-200 600M** (Meta): fastest, no context, non-commercial licence.
