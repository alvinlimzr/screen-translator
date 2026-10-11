"""
Offline translators. All run locally; internet is only used to download a model.

  hymt8  Tencent Hy-MT1.5-1.8B, 8-bit. Best quality. Understands context.
  hymt4  Same model, 4-bit: faster and smaller, slightly less accurate.
  nllb   Meta NLLB-200 600M. Fastest, no context, weakest.

Hy-MT runs on MLX (Apple Silicon) or llama.cpp (any OS).

    python translators.py --download hymt8
    python translators.py hymt8 th en "ไก่ขันตั้งแต่ตีห้า" --context "ชีวิตในหมู่บ้านชนบท"

Licences: Hy-MT1.5 = Tencent HY Community License (commercial use allowed, but
NOT licensed in the EU, UK or South Korea). NLLB = CC-BY-NC (non-commercial).
"""
from __future__ import annotations

import os
import platform
import re
import sys
import time
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
# Models live next to the code on Mac; the Windows app points this at a writable
# per-user folder (%LOCALAPPDATA%) via SCREEN_TRANSLATOR_MODELS.
MODELS = os.environ.get("SCREEN_TRANSLATOR_MODELS") or os.path.join(HERE, "models")

LANG_NAMES = {"en": ("English", "英语"), "th": ("Thai", "泰语"), "ja": ("Japanese", "日语")}

# model key -> (MLX repo, MLX dir, GGUF repo, GGUF file, size note)
HYMT = {
    "hymt8": ("mlx-community/HY-MT1.5-1.8B-8bit", "hy-mt-mlx-8bit",
              "tencent/HY-MT1.5-1.8B-GGUF", "HY-MT1.5-1.8B-Q8_0.gguf", "~1.9 GB"),
    "hymt4": ("mlx-community/HY-MT1.5-1.8B-4bit", "hy-mt-mlx-4bit",
              "tencent/HY-MT1.5-1.8B-GGUF", "HY-MT1.5-1.8B-Q4_K_M.gguf", "~1.0 GB"),
}
GGUF_DIR = os.path.join(MODELS, "hy-mt")

PLAIN_PROMPT = "Translate the following segment into {target}, without additional explanation.\n\n{text}"
# Official contextual template from the Hy-MT1.5 model card (Chinese):
# "Using the information above, translate the text below into {target}; do not translate
#  the context above and do not add explanations:"
CONTEXT_PROMPT = "{context}\n参考上面的信息，把下面的文本翻译成{target}，注意不需要翻译上文，也不要额外解释：\n{text}"
GEN = dict(temperature=0.2, top_k=20, top_p=0.6, repetition_penalty=1.05)


def is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def max_tokens_for(text: str) -> int:
    """Stop runaway generations early: output rarely needs more than ~4 tokens
    per source character (and far fewer for most languages)."""
    return min(256, 24 + 4 * len(text))


def clean_output(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^(translation|english|thai|japanese)\s*[:：]\s*", "", s, flags=re.I)
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'“”「」":
        s = s[1:-1].strip()
    return s


def first_paragraph(source: str, output: str) -> str:
    """A one-line source gets a one-line answer: the model sometimes continues with
    translations of the context (the neighbouring text) on new lines."""
    if "\n" in source.strip():
        return output
    parts = [p.strip() for p in output.split("\n") if p.strip()]
    return parts[0] if parts else output


def looks_leaked(source: str, output: str) -> bool:
    """With context, a short source sometimes gets the context translated too."""
    return len(output) > max(48, 5 * len(source))


def llama_server_path() -> Optional[str]:
    """Bundled llama.cpp server (Windows build ships llama/llama-server.exe)."""
    p = os.environ.get("SCREEN_TRANSLATOR_LLAMA_SERVER")
    if p and os.path.exists(p):
        return p
    base = getattr(sys, "_MEIPASS", None) or HERE
    exe = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    for d in (os.path.join(base, "llama"), os.path.join(HERE, "llama")):
        if os.path.exists(os.path.join(d, exe)):
            return os.path.join(d, exe)
    return None


class LlamaServer:
    """llama.cpp's own server, started privately on 127.0.0.1 (nothing leaves the PC).
    Prebuilt binaries need no compiler, and the Vulkan build uses the GPU on
    Intel / AMD / NVIDIA; if the GPU can't be used it restarts on the CPU."""

    def __init__(self, exe: str, model: str):
        self.exe, self.model, self.proc, self.port = exe, model, None, None

    def start(self, timeout: float = 120):
        import socket
        import subprocess
        import urllib.request
        for gpu_layers in ("99", "0"):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                self.port = sock.getsockname()[1]
            flags = 0x08000000 if sys.platform == "win32" else 0        # CREATE_NO_WINDOW
            self.proc = subprocess.Popen(
                [self.exe, "-m", self.model, "--host", "127.0.0.1", "--port", str(self.port),
                 "-c", "2048", "-ngl", gpu_layers, "--log-disable"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
            end = time.time() + timeout
            while time.time() < end and self.proc.poll() is None:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                        if b"ok" in r.read():
                            import atexit
                            atexit.register(self.stop)
                            return
                except OSError:
                    time.sleep(0.3)
            self.stop()
        raise RuntimeError("translation server failed to start")

    def chat(self, prompt: str, max_tokens: int) -> str:
        import json
        import urllib.request
        body = json.dumps({"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
                           "temperature": GEN["temperature"], "top_k": GEN["top_k"], "top_p": GEN["top_p"],
                           "repeat_penalty": GEN["repetition_penalty"]}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions", body,
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except Exception:
                self.proc.kill()


class HyMTTranslator:
    def __init__(self, key: str = "hymt8", engine: str = "auto"):
        self.key = key
        mlx_repo, mlx_dir, gguf_repo, gguf_file, _ = HYMT[key]
        if engine == "auto":
            engine = "mlx" if is_apple_silicon() else ("server" if llama_server_path() else "llama")
        self.engine = engine
        self.path = os.path.join(MODELS, mlx_dir) if engine == "mlx" else os.path.join(GGUF_DIR, gguf_file)
        self.loaded = False

    def available(self) -> bool:
        return os.path.exists(self.path)

    def _load(self):
        # Loaded lazily on the translating thread (MLX streams are per-thread).
        if not self.available():
            raise RuntimeError(f"model not downloaded: {self.key}")
        if self.engine == "mlx":
            from mlx_lm import load
            self.model, self.tok = load(self.path)
        elif self.engine == "server":
            self.server = LlamaServer(llama_server_path(), self.path)
            self.server.start()
        else:
            from llama_cpp import Llama
            self.llm = Llama(self.path, n_ctx=2048, n_gpu_layers=-1, verbose=False)
        self.loaded = True

    def _generate(self, prompt: str, max_tokens: int) -> str:
        if not self.loaded:
            self._load()
        msgs = [{"role": "user", "content": prompt}]
        if self.engine == "server":
            return self.server.chat(prompt, max_tokens)
        if self.engine == "mlx":
            from mlx_lm import generate
            from mlx_lm.sample_utils import make_logits_processors, make_sampler
            text = self.tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
            return generate(self.model, self.tok, prompt=text, max_tokens=max_tokens,
                            sampler=make_sampler(temp=GEN["temperature"], top_p=GEN["top_p"],
                                                 top_k=GEN["top_k"]),
                            logits_processors=make_logits_processors(
                                repetition_penalty=GEN["repetition_penalty"]),
                            verbose=False)
        r = self.llm.create_chat_completion(
            msgs, temperature=GEN["temperature"], top_k=GEN["top_k"], top_p=GEN["top_p"],
            repeat_penalty=GEN["repetition_penalty"], max_tokens=max_tokens)
        return r["choices"][0]["message"]["content"]

    def translate(self, text: str, context: Optional[str] = None,
                  src: str = "th", tgt: str = "en") -> str:
        en_name, zh_name = LANG_NAMES[tgt]
        mt = max_tokens_for(text)
        if context:
            out = first_paragraph(text, clean_output(self._generate(CONTEXT_PROMPT.format(
                context=context, target=zh_name, text=text), mt)))
            if out and not looks_leaked(text, out):
                return out
        return first_paragraph(text, clean_output(self._generate(
            PLAIN_PROMPT.format(target=en_name, text=text), mt)))


NLLB_REPO = "JustFrederik/nllb-200-distilled-600M-ct2-int8"
NLLB_DIR = os.path.join(MODELS, "nllb-600m-int8")
NLLB_CODES = {"th": "tha_Thai", "ja": "jpn_Jpan", "en": "eng_Latn"}


class NLLBTranslator:
    key = "nllb"

    def __init__(self, beam_size: int = 2):
        self.beam = beam_size
        self.path = NLLB_DIR
        self.loaded = False

    def available(self) -> bool:
        return os.path.exists(os.path.join(NLLB_DIR, "model.bin"))

    def _load(self):
        import ctranslate2
        import sentencepiece as spm
        self.tr = ctranslate2.Translator(NLLB_DIR, device="cpu", compute_type="int8")
        self.sp = spm.SentencePieceProcessor(model_file=os.path.join(NLLB_DIR, "sentencepiece.bpe.model"))
        self.loaded = True

    def translate(self, text, context=None, src="th", tgt="en"):
        if not self.loaded:
            self._load()
        s, t = NLLB_CODES[src], NLLB_CODES[tgt]
        r = self.tr.translate_batch([[s] + self.sp.encode(text, out_type=str) + ["</s>"]],
                                    target_prefix=[[t]], beam_size=self.beam,
                                    max_decoding_length=256, repetition_penalty=1.1)[0]
        return self.sp.decode([x for x in r.hypotheses[0] if x != t]).strip()


class NoTranslator:
    key = "none"
    loaded = True

    def available(self):
        return True

    def translate(self, text, context=None, src="th", tgt="en"):
        return "· " + text


def make_translator(key: str, engine: str = "auto"):
    if key in HYMT:
        return HyMTTranslator(key, engine)
    if key == "nllb":
        return NLLBTranslator()
    return NoTranslator()


def dir_bytes(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def repo_bytes(repo: str, filename: Optional[str] = None) -> int:
    """Total download size, from the Hub's file metadata (0 if unknown)."""
    try:
        from huggingface_hub import HfApi
        info = HfApi().model_info(repo, files_metadata=True)
        sizes = {s.rfilename: (s.size or 0) for s in info.siblings}
        return sizes.get(filename, 0) if filename else sum(sizes.values())
    except Exception:
        return 0


def fetch(repo: str, local_dir: str, filename: Optional[str] = None,
          progress=lambda frac, msg: None, poll: float = 0.5):
    """Download a repo (or one file) into local_dir, reporting real byte progress:
    progress(fraction 0..1 or None if unknown, "420 MB of 1.9 GB · 12 MB/s · 2 min left")."""
    import threading
    from huggingface_hub import hf_hub_download, snapshot_download
    os.makedirs(local_dir, exist_ok=True)
    total = repo_bytes(repo, filename)
    err = []

    def run():
        try:
            if filename:
                hf_hub_download(repo, filename, local_dir=local_dir)
            else:
                snapshot_download(repo, local_dir=local_dir)
        except Exception as e:          # reported below
            err.append(e)

    t = threading.Thread(target=run, daemon=True)
    t0 = time.time()
    hist = [(t0, dir_bytes(local_dir))]          # recent samples -> smoothed speed
    t.start()
    while t.is_alive():
        t.join(poll)
        now, got = time.time(), dir_bytes(local_dir)
        hist.append((now, got))
        hist = [h for h in hist if now - h[0] <= 4.0] or hist[-1:]
        speed = (got - hist[0][1]) / max(1e-3, now - hist[0][0])
        rate = f" · {human(speed)}/s" if now - t0 >= 1.5 else ""
        if total:
            frac = min(0.99, got / total)
            left = (total - got) / speed if speed > 1 else None
            eta = ""
            if left is not None and now - t0 >= 3 and left >= 1:
                eta = f" · {left / 60:.0f} min left" if left >= 90 else f" · {left:.0f} s left"
            progress(frac, f"{human(got)} of {human(total)}{rate}{eta}")
        else:
            progress(None, f"{human(got)}{rate}")
    if err:
        raise err[0]
    progress(1.0, "Download finished")


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB", "MB") else f"{n:.1f} GB"
        n /= 1024
    return f"{n:.1f} GB"


def download(key: str, engine: str = "auto", progress=lambda frac, msg: print(msg)):
    """Fetch a model into ./models (needs internet, once)."""
    if key == "nllb":
        fetch(NLLB_REPO, NLLB_DIR, progress=progress)
        return
    mlx_repo, mlx_dir, gguf_repo, gguf_file, _ = HYMT[key]
    if engine == "mlx" or (engine == "auto" and is_apple_silicon()):
        fetch(mlx_repo, os.path.join(MODELS, mlx_dir), progress=progress)
    else:
        fetch(gguf_repo, GGUF_DIR, gguf_file, progress=progress)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", choices=["hymt8", "hymt4", "nllb"])
    ap.add_argument("--engine", choices=["auto", "mlx", "llama", "server"], default="auto")
    ap.add_argument("--context")
    ap.add_argument("model", nargs="?", choices=["hymt8", "hymt4", "nllb"])
    ap.add_argument("src", nargs="?")
    ap.add_argument("tgt", nargs="?")
    ap.add_argument("texts", nargs="*")
    a = ap.parse_args()
    if a.download:
        download(a.download, a.engine,
                 progress=lambda f, m: print(f"\r{'' if f is None else f'{f * 100:5.1f}%  '}{m}   ",
                                             end="", flush=True))
        print()
        sys.exit()
    if not a.model or not a.texts:
        sys.exit(__doc__)
    t = make_translator(a.model, a.engine)
    if not t.available():
        sys.exit(f"Model not downloaded. Run: python translators.py --download {a.model}")
    for s in a.texts:
        t0 = time.perf_counter()
        print(f"{s}\n  -> {t.translate(s, a.context, a.src, a.tgt)}  ({time.perf_counter() - t0:.2f}s)")
