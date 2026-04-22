import html
import io
import re
from pathlib import Path

from docx import Document
from flask import Flask, jsonify, render_template_string, request
from pypdf import PdfReader
from werkzeug.exceptions import RequestEntityTooLarge


APP_TITLE = "RSVP app"
DEFAULT_WPM = 300
DEFAULT_HIGHLIGHT = "#ff5a5f"
DEFAULT_READER_BG = "#111723"
DEFAULT_SAMPLE_TEXT = """
Rapid Serial Visual Presentation (RSVP) is a reading technique that presents text sequentially, one word or phrase at a time, at a fixed position on a display screen. This method was initially developed as both a research tool and a potential reading aid, with applications ranging from cognitive psychology experiments to assistive technologies for individuals with visual impairments.

The term RSVP was first coined by Forster in 1970, who used it to describe the presentation of single words at rapid rates to a stationary position in the viewing field. The technique has evolved to include various unit sizes, from single words to phrases and sentences, with researchers exploring different presentation parameters to optimize reading efficiency. The fundamental principle of RSVP is to eliminate or greatly reduce saccadic eye movements (the rapid jumps between fixation points during normal reading), which are believed to consume cognitive resources and potentially slow reading speed.

RSVP reading fundamentally alters the cognitive processes involved in reading compared to traditional reading methods. In normal reading, eye movements play a crucial role in information acquisition, with readers making saccades (rapid eye movements) between fixation points where visual information is processed. These eye movements are not random but are guided by cognitive processes, including word recognition, syntactic parsing, and semantic integration.

When using RSVP, the elimination of saccadic eye movements changes the reading process in several ways:

Reduced visual search: Readers do not need to locate the next word or line.
Stable fixation point: The eyes remain fixed on a single location.
Controlled presentation rate: The reading pace is determined by the presentation speed rather than the reader's natural rhythm.

Research using eye-tracking and event-related potentials (ERPs) has shown that RSVP reading engages different neural pathways compared to traditional reading. The brain's reading network, which includes regions such as the visual word form area in the left fusiform gyrus, shows different activation patterns during RSVP reading, particularly in how rapidly information is processed and integrated.
""".strip()
MAX_UPLOAD_MB = 20
SUPPORTED_TYPES = {".txt", ".pdf", ".docx"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


def extract_text_from_file(uploaded_file):
    """Extract readable text from TXT, PDF, and DOCX uploads."""
    if not uploaded_file or not uploaded_file.filename:
        raise ValueError("Please choose a TXT, PDF, or DOCX file to upload.")

    extension = Path(uploaded_file.filename).suffix.lower()
    if extension not in SUPPORTED_TYPES:
        raise ValueError("Unsupported file type. Please upload a TXT, PDF, or DOCX file.")

    uploaded_file.stream.seek(0)
    file_bytes = uploaded_file.read()
    if not file_bytes:
        raise ValueError("The uploaded file appears to be empty.")

    try:
        if extension == ".txt":
            for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
                try:
                    return file_bytes.decode(encoding)
                except UnicodeDecodeError:
                    continue
            return file_bytes.decode("utf-8", errors="ignore")

        if extension == ".pdf":
            reader = PdfReader(io.BytesIO(file_bytes))
            pages = [(page.extract_text() or "").strip() for page in reader.pages]
            text = "\n".join(part for part in pages if part)
            if not text.strip():
                raise ValueError(
                    "We couldn't extract readable text from that PDF. Try another PDF or paste the text directly."
                )
            return text

        if extension == ".docx":
            document = Document(io.BytesIO(file_bytes))
            paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
            text = "\n".join(paragraphs)
            if not text.strip():
                raise ValueError(
                    "We couldn't find readable text in that DOCX file. Try another file or paste the text directly."
                )
            return text
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(
            f"Something went wrong while reading the {extension[1:].upper()} file. Please try another file."
        ) from exc

    raise ValueError("Unsupported file type. Please upload a TXT, PDF, or DOCX file.")


def clean_text(text):
    """Normalize whitespace without disturbing punctuation."""
    if not text:
        return ""
    text = text.replace("\ufeff", " ").replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def tokenize_text(text):
    """Split cleaned text into RSVP-friendly word tokens."""
    if not text:
        return []
    letter_separator_pattern = r"(?<=[^\W\d_])\s*(?:/|[-\u2010-\u2015\u2212])\s*(?=[^\W\d_])"
    normalized = re.sub(letter_separator_pattern, " ", text, flags=re.UNICODE)
    return re.findall(r"\S+", normalized)


def get_orp_index(word):
    """Return the display index of the ORP character, or None for numeric tokens."""
    if not word or any(character.isdigit() for character in word):
        return None

    alpha_positions = [index for index, character in enumerate(word) if character.isalpha()]
    if not alpha_positions:
        return None

    alpha_length = len(alpha_positions)
    if alpha_length <= 1:
        orp_core_index = 0
    elif alpha_length <= 5:
        orp_core_index = 1
    elif alpha_length <= 9:
        orp_core_index = 2
    elif alpha_length <= 13:
        orp_core_index = 3
    else:
        orp_core_index = 4

    return alpha_positions[min(orp_core_index, alpha_length - 1)]


def render_highlighted_word(word, color):
    """Render a word into left/focus/right HTML segments for ORP-style display."""
    word = word or ""
    orp_index = get_orp_index(word)

    if orp_index is None:
        return (
            '<span class="word-left"></span>'
            '<span class="word-focus" aria-hidden="true"></span>'
            f'<span class="word-right">{html.escape(word)}</span>'
        )

    left = html.escape(word[:orp_index])
    focus = html.escape(word[orp_index])
    right = html.escape(word[orp_index + 1 :])
    return (
        f'<span class="word-left">{left}</span>'
        f'<span class="word-focus" style="color: {html.escape(color)};">{focus}</span>'
        f'<span class="word-right">{right}</span>'
    )


def estimate_duration(word_count, wpm):
    """Estimate playback duration in seconds based on the current WPM."""
    if word_count <= 0 or wpm <= 0:
        return 0.0
    return (word_count / float(wpm)) * 60.0


@app.errorhandler(RequestEntityTooLarge)
def handle_large_upload(_error):
    message = f"That file is too large. Please keep uploads under {MAX_UPLOAD_MB} MB."
    if request.path == "/extract":
        return jsonify({"ok": False, "error": message}), 413
    return message, 413


@app.get("/")
def index():
    placeholder_word = render_highlighted_word("Ready", DEFAULT_HIGHLIGHT)
    return render_template_string(
        TEMPLATE,
        app_title=APP_TITLE,
        default_wpm=DEFAULT_WPM,
        default_highlight=DEFAULT_HIGHLIGHT,
        default_reader_bg=DEFAULT_READER_BG,
        default_sample_text=DEFAULT_SAMPLE_TEXT,
        placeholder_word=placeholder_word,
    )


@app.post("/extract")
def extract():
    try:
        pasted_text = request.form.get("text", "")
        uploaded_file = request.files.get("file")

        if pasted_text.strip():
            source_name = "Pasted text"
            raw_text = pasted_text
        elif uploaded_file and uploaded_file.filename:
            source_name = uploaded_file.filename
            raw_text = extract_text_from_file(uploaded_file)
        else:
            raise ValueError("Paste text or upload a TXT, PDF, or DOCX file to begin.")

        cleaned_text = clean_text(raw_text)
        tokens = tokenize_text(cleaned_text)
        if not tokens:
            raise ValueError("We couldn't find any readable words. Please try different content.")

        return jsonify(
            {
                "ok": True,
                "source": source_name,
                "cleaned_text": cleaned_text,
                "tokens": tokens,
                "word_count": len(tokens),
                "estimated_seconds": estimate_duration(len(tokens), DEFAULT_WPM),
                "first_word_html": render_highlighted_word(tokens[0], DEFAULT_HIGHLIGHT),
            }
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Something unexpected happened while preparing the text. Please try again.",
                }
            ),
            500,
        )


TEMPLATE = (
    r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ app_title }}</title>
  <style>
    @import url("https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500;600&display=swap");

    :root {
      --bg: #090d14;
      --bg-elevated: rgba(19, 25, 38, 0.92);
      --bg-soft: rgba(24, 31, 46, 0.84);
      --surface-border: rgba(255, 255, 255, 0.08);
      --surface-border-strong: rgba(255, 255, 255, 0.12);
      --text: #f3f6fb;
      --text-soft: rgba(243, 246, 251, 0.72);
      --text-muted: rgba(243, 246, 251, 0.52);
      --accent: {{ default_highlight }};
      --reader-bg: {{ default_reader_bg }};
      --shadow: 0 24px 70px rgba(0, 0, 0, 0.38);
      --radius-xl: 28px;
      --radius-lg: 22px;
      --radius-md: 16px;
      --transition: 180ms ease;
    }

    * {
      box-sizing: border-box;
    }

    html, body {
      min-height: 100%;
    }

    body {
      margin: 0;
      color: var(--text);
      font-family: "Space Grotesk", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(255, 90, 95, 0.14), transparent 28%),
        radial-gradient(circle at top right, rgba(106, 166, 255, 0.16), transparent 24%),
        linear-gradient(180deg, #0b1019 0%, #070a11 100%);
    }

    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image:
        linear-gradient(rgba(255, 255, 255, 0.02) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255, 255, 255, 0.02) 1px, transparent 1px);
      background-size: 40px 40px;
      mask-image: radial-gradient(circle at center, black 30%, transparent 90%);
      opacity: 0.55;
    }

    .app-shell {
      position: relative;
      max-width: 1480px;
      margin: 0 auto;
      padding: 20px 24px 24px;
    }

    .hero {
      text-align: center;
      margin-bottom: 14px;
    }

    .hero h1 {
      margin: 0;
      font-size: clamp(2.5rem, 4vw, 4.75rem);
      line-height: 1;
      font-weight: 700;
      letter-spacing: -0.05em;
    }

    .hero p {
      margin: 10px auto 0;
      max-width: 760px;
      color: var(--text-soft);
      font-size: 1.02rem;
      line-height: 1.6;
    }

    .layout {
      display: grid;
      grid-template-columns: 1fr;
      gap: 18px;
      align-items: start;
      width: 100%;
      max-width: none;
      margin: 0 auto;
    }

    .layout > .card {
      width: 100%;
    }

    .card {
      background: linear-gradient(180deg, rgba(22, 29, 43, 0.96), rgba(13, 18, 29, 0.94));
      border: 1px solid var(--surface-border);
      border-radius: var(--radius-xl);
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
    }

    .panel {
      padding: 18px;
      width: min(100%, 880px);
      margin: 0 auto;
    }

    .panel h2 {
      margin: 0;
      font-size: 1.06rem;
      font-weight: 600;
      letter-spacing: -0.02em;
    }

    .panel-subtitle {
      margin: 6px 0 0;
      color: var(--text-soft);
      font-size: 0.9rem;
      line-height: 1.5;
    }

    .stack {
      display: grid;
      gap: 12px;
      margin-top: 14px;
    }

    .panel-grid {
      display: grid;
      grid-template-columns: minmax(0, 1.45fr) minmax(250px, 0.9fr);
      gap: 14px;
      align-items: start;
    }

    .panel-main,
    .panel-side {
      display: grid;
      gap: 12px;
    }

    label {
      display: block;
      margin-bottom: 6px;
      color: var(--text-soft);
      font-size: 0.88rem;
      font-weight: 500;
    }

    textarea,
    input[type="file"],
    input[type="range"],
    input[type="color"] {
      width: 100%;
    }

    textarea {
      min-height: 116px;
      resize: vertical;
      padding: 12px 14px;
      border: 1px solid var(--surface-border-strong);
      border-radius: var(--radius-lg);
      background: rgba(8, 13, 22, 0.86);
      color: var(--text);
      font: inherit;
      line-height: 1.5;
      outline: none;
      transition: border-color var(--transition), box-shadow var(--transition), transform var(--transition);
    }

    textarea:focus,
    input[type="file"]:focus-visible,
    input[type="range"]:focus-visible,
    input[type="color"]:focus-visible {
      border-color: rgba(255, 90, 95, 0.5);
      box-shadow: 0 0 0 4px rgba(255, 90, 95, 0.12);
    }

    textarea::placeholder {
      color: var(--text-muted);
    }

    .upload-shell {
      padding: 10px 12px;
      border: 1px dashed rgba(255, 255, 255, 0.15);
      border-radius: var(--radius-lg);
      background: rgba(9, 13, 21, 0.72);
    }

    input[type="file"] {
      color: var(--text-soft);
      font: inherit;
      background: transparent;
      border: none;
      outline: none;
    }

    input[type="file"]::file-selector-button {
      margin-right: 14px;
      padding: 10px 14px;
      border: none;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.1);
      color: var(--text);
      font: inherit;
      font-weight: 600;
      cursor: pointer;
      transition: background var(--transition), transform var(--transition);
    }

    input[type="file"]::file-selector-button:hover {
      background: rgba(255, 255, 255, 0.16);
      transform: translateY(-1px);
    }

    .action-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: -4px;
      width: 100%;
      max-width: 1320px;
      margin-left: auto;
      margin-right: auto;
    }

    button {
      appearance: none;
      border: none;
      border-radius: 999px;
      padding: 11px 16px;
      font: inherit;
      font-size: 0.92rem;
      font-weight: 700;
      letter-spacing: 0.01em;
      cursor: pointer;
      color: var(--text);
      transition: transform var(--transition), opacity var(--transition), box-shadow var(--transition), background var(--transition);
    }

    button:hover:not(:disabled) {
      transform: translateY(-1px);
    }

    button:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }

    .primary-button {
      background: linear-gradient(135deg, #ff5a5f, #e84452);
      box-shadow: 0 12px 32px rgba(255, 90, 95, 0.24);
    }

    .secondary-button {
      background: rgba(255, 255, 255, 0.08);
    }

    .ghost-button {
      background: rgba(122, 165, 255, 0.13);
    }

    .control-group {
      padding: 12px 14px 14px;
      border-radius: var(--radius-lg);
      border: 1px solid rgba(255, 255, 255, 0.06);
      background: rgba(9, 13, 21, 0.54);
    }

    .control-topline {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }

    .control-topline strong {
      font-size: 1.08rem;
      font-weight: 700;
      letter-spacing: -0.03em;
    }

    .hint {
      color: var(--text-muted);
      font-size: 0.84rem;
      line-height: 1.45;
    }
"""
    r"""
    input[type="range"] {
      appearance: none;
      height: 8px;
      border-radius: 999px;
      background: linear-gradient(90deg, rgba(255, 90, 95, 0.7), rgba(122, 165, 255, 0.7));
      outline: none;
    }

    input[type="range"]::-webkit-slider-thumb {
      appearance: none;
      width: 20px;
      height: 20px;
      border-radius: 50%;
      background: #fff;
      border: 3px solid #101827;
      cursor: pointer;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
    }

    input[type="range"]::-moz-range-thumb {
      width: 20px;
      height: 20px;
      border-radius: 50%;
      background: #fff;
      border: 3px solid #101827;
      cursor: pointer;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
    }

    .color-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }

    .color-card {
      padding: 10px 12px;
      border-radius: var(--radius-md);
      border: 1px solid rgba(255, 255, 255, 0.08);
      background: rgba(10, 14, 22, 0.64);
    }

    .color-card input[type="color"] {
      height: 38px;
      border: none;
      border-radius: 12px;
      background: transparent;
      cursor: pointer;
    }

    .shortcut-line {
      color: var(--text-muted);
      font-size: 0.8rem;
      line-height: 1.5;
    }

    .reader-card {
      display: grid;
      grid-template-rows: auto 1fr auto;
      min-height: 500px;
      overflow: hidden;
      width: 100%;
      max-width: 1320px;
      margin: 0 auto;
    }

    .reader-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      padding: 16px 18px 0;
    }

    .reader-meta h2 {
      margin: 0;
      font-size: 1.2rem;
    }

    .reader-meta p {
      margin: 6px 0 0;
      color: var(--text-soft);
      font-size: 0.88rem;
    }

    .badge-row {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 8px;
      max-width: 620px;
    }

    .badge {
      padding: 7px 10px;
      border-radius: 999px;
      border: 1px solid rgba(255, 255, 255, 0.08);
      background: rgba(255, 255, 255, 0.04);
      color: var(--text-soft);
      font-size: 0.72rem;
      font-weight: 600;
      letter-spacing: 0.02em;
      text-transform: uppercase;
    }

    .metric-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }

    .metric-badge span {
      color: var(--text);
      font-size: 0.76rem;
      font-weight: 700;
      letter-spacing: 0;
      text-transform: none;
    }

    .reader-stage-shell {
      padding: 12px 8px 14px;
    }

    .reader-stage {
      position: relative;
      height: 100%;
      min-height: 300px;
      border-radius: calc(var(--radius-xl) + 8px);
      background:
        radial-gradient(circle at 50% 50%, rgba(255, 255, 255, 0.03), transparent 48%),
        linear-gradient(180deg, rgba(255, 255, 255, 0.02), rgba(255, 255, 255, 0.01)),
        var(--reader-bg);
      border: 1px solid rgba(255, 255, 255, 0.08);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03);
      overflow: hidden;
    }

    .reader-stage::before,
    .reader-stage::after {
      content: "";
      position: absolute;
      left: 4%;
      right: 4%;
      height: 1px;
      background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.08), transparent);
    }

    .reader-stage::before {
      top: 16%;
    }

    .reader-stage::after {
      bottom: 16%;
    }

    .guide-line {
      position: absolute;
      top: 12%;
      bottom: 12%;
      left: 50%;
      width: 1px;
      background: linear-gradient(180deg, transparent, rgba(255, 255, 255, 0.32), transparent);
      transform: translateX(-50%);
      pointer-events: none;
    }

    .reader-overlay {
      position: absolute;
      inset: 0;
      background:
        radial-gradient(circle at center, transparent 0%, transparent 42%, rgba(0, 0, 0, 0.12) 100%);
      pointer-events: none;
    }

    .stage-nav {
      position: absolute;
      top: 50%;
      z-index: 2;
      width: 52px;
      height: 52px;
      padding: 0;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 50%;
      border: 1px solid rgba(255, 255, 255, 0.14);
      background: rgba(8, 13, 22, 0.72);
      box-shadow: 0 12px 28px rgba(0, 0, 0, 0.24);
      font-size: 1.4rem;
      line-height: 1;
      transform: translateY(-50%);
      backdrop-filter: blur(8px);
    }

    .stage-nav:hover:not(:disabled) {
      transform: translateY(-50%) scale(1.03);
    }

    .stage-nav-left {
      left: 14px;
    }

    .stage-nav-right {
      right: 14px;
    }

    .stage-help {
      position: absolute;
      top: 14px;
      right: 16px;
      z-index: 2;
      max-width: min(260px, calc(100% - 96px));
      padding: 8px 10px;
      border-radius: 12px;
      border: 1px solid rgba(255, 255, 255, 0.08);
      background: rgba(8, 13, 22, 0.58);
      color: var(--text-muted);
      font-size: 0.72rem;
      line-height: 1.45;
      text-align: right;
      backdrop-filter: blur(8px);
    }

    .word-shell {
      position: absolute;
      left: 50%;
      top: 50%;
      display: inline-flex;
      align-items: baseline;
      white-space: pre;
      font-family: "IBM Plex Mono", monospace;
      font-size: clamp(3rem, 8vw, 6.5rem);
      font-weight: 600;
      line-height: 1;
      letter-spacing: -0.04em;
      color: #eef2fb;
      transform: translate(-50%, -50%);
      text-rendering: geometricPrecision;
      will-change: transform;
    }
"""
    r"""
    .word-focus {
      color: var(--accent);
    }

    .reader-footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 0 18px 14px;
    }

    .notice {
      min-height: 0;
      padding: 10px 12px;
      border-radius: var(--radius-lg);
      border: 1px solid transparent;
      background: rgba(255, 255, 255, 0.04);
      color: var(--text-soft);
      font-size: 0.86rem;
      line-height: 1.4;
      flex: 1;
    }

    .notice[data-variant="success"] {
      border-color: rgba(87, 219, 149, 0.22);
      color: #cff6df;
      background: rgba(87, 219, 149, 0.08);
    }

    .notice[data-variant="error"] {
      border-color: rgba(255, 90, 95, 0.24);
      color: #ffd7da;
      background: rgba(255, 90, 95, 0.08);
    }

    .notice[data-variant="info"] {
      border-color: rgba(122, 165, 255, 0.24);
      color: #d7e6ff;
      background: rgba(122, 165, 255, 0.08);
    }

    .status-pill {
      display: none;
    }

    @media (max-width: 1120px) {
      .reader-card {
        min-height: 480px;
      }
    }

    @media (max-width: 760px) {
      .app-shell {
        padding: 18px 14px 20px;
      }

      .panel {
        padding: 16px;
        width: 100%;
      }

      .panel-grid {
        grid-template-columns: 1fr;
      }

      .reader-header,
      .reader-footer {
        padding-left: 18px;
        padding-right: 18px;
      }

      .reader-header,
      .reader-footer {
        flex-direction: column;
        align-items: stretch;
      }

      .action-row {
        gap: 8px;
      }

      .action-row button {
        flex: 1 1 auto;
      }

      .badge-row {
        justify-content: flex-start;
        max-width: none;
      }

      .reader-stage {
        min-height: 260px;
      }

      .stage-nav {
        width: 46px;
        height: 46px;
      }

      .stage-nav-left {
        left: 10px;
      }

      .stage-nav-right {
        right: 10px;
      }

      .stage-help {
        top: 10px;
        right: 10px;
        max-width: calc(100% - 84px);
        font-size: 0.68rem;
      }

      .word-shell {
        font-size: clamp(2.35rem, 12vw, 4.1rem);
      }
    }
  </style>
</head>
<body>
  <div class="app-shell">
    <header class="hero">
      <h1>{{ app_title }}</h1>
    </header>

    <main class="layout">
      <section class="card reader-card">
        <div class="reader-header">
          <div class="reader-meta">
            <h2>Reading Area</h2>
            <p id="source-label">No text loaded yet</p>
          </div>
          <div class="badge-row">
            <div class="badge" id="state-badge">Idle</div>
            <div class="badge metric-badge">Words <span id="word-count">0</span></div>
            <div class="badge metric-badge">Time <span id="estimated-time">0 sec</span></div>
            <div class="badge metric-badge">Word <span id="word-index">0 / 0</span></div>
            <div class="badge metric-badge">Progress <span id="progress-value">0%</span></div>
          </div>
        </div>

        <div class="reader-stage-shell">
          <div id="reader-stage" class="reader-stage">
            <div class="guide-line" aria-hidden="true"></div>
            <div class="reader-overlay" aria-hidden="true"></div>
            <button id="previous-word-button" class="stage-nav stage-nav-left" type="button" aria-label="Show previous word" disabled>&larr;</button>
            <button id="next-word-button" class="stage-nav stage-nav-right" type="button" aria-label="Show next word" disabled>&rarr;</button>
            <div class="stage-help">
              Click arrows or press <strong>&larr;</strong> / <strong>&rarr;</strong> to step one word and pause.<br>
              <strong>&uarr;</strong> / <strong>&darr;</strong> change speed. <strong>Space</strong> pauses. <strong>R</strong> restarts.
            </div>
            <div id="word-shell" class="word-shell">{{ placeholder_word|safe }}</div>
          </div>
        </div>

        <div class="reader-footer">
          <div id="notice" class="notice" data-variant="info">Paste text or upload a document, then press Start to begin.</div>
          <div id="status-pill" class="status-pill">Waiting</div>
        </div>
      </section>

      <div class="action-row">
        <button id="start-button" class="primary-button" type="button">Start</button>
        <button id="pause-button" class="secondary-button" type="button" disabled>Pause / Resume</button>
        <button id="restart-button" class="ghost-button" type="button" disabled>Restart</button>
      </div>

      <section class="card panel">
        <h2>Text Source</h2>
        <p class="panel-subtitle">Paste or upload your text, then tune speed and colors without taking over the whole page.</p>

        <div class="stack">
          <div class="panel-grid">
            <div class="panel-main">
              <div>
                <label for="text-input">Paste text</label>
                <textarea id="text-input" placeholder="Paste an article, chapter, speech, or abstract here...">{{ default_sample_text }}</textarea>
              </div>

              <div class="upload-shell">
                <label for="file-input">Upload document</label>
                <input id="file-input" type="file" accept=".txt,.pdf,.docx">
                <div class="hint">Supported formats: TXT, PDF, DOCX</div>
              </div>
            </div>

            <div class="panel-side">
              <div class="control-group">
                <div class="control-topline">
                  <div>
                    <label for="wpm-slider">Reading speed</label>
                    <div class="hint">Up and Down arrows adjust speed while reading</div>
                  </div>
                  <strong><span id="wpm-value">{{ default_wpm }}</span> WPM</strong>
                </div>
                <input id="wpm-slider" type="range" min="100" max="1000" step="10" value="{{ default_wpm }}">
              </div>

              <div class="color-grid">
                <div class="color-card">
                  <label for="highlight-color">Highlight color</label>
                  <input id="highlight-color" type="color" value="{{ default_highlight }}">
                </div>
                <div class="color-card">
                  <label for="reader-color">Reader background</label>
                  <input id="reader-color" type="color" value="{{ default_reader_bg }}">
                </div>
              </div>

              <div class="shortcut-line">
                Shortcuts: <strong>Space</strong> pause or resume, <strong>Left / Right</strong> step words and pause, <strong>Up / Down</strong> change speed, <strong>R</strong> restart.
              </div>
            </div>
          </div>
        </div>
      </section>
    </main>
  </div>

  <script>
    const state = {
      tokens: [],
      index: 0,
      isPlaying: false,
      timerId: null,
      wpm: {{ default_wpm }},
      highlightColor: "{{ default_highlight }}",
      readerColor: "{{ default_reader_bg }}",
      source: "",
    };

    const elements = {
      textInput: document.getElementById("text-input"),
      fileInput: document.getElementById("file-input"),
      startButton: document.getElementById("start-button"),
      pauseButton: document.getElementById("pause-button"),
      restartButton: document.getElementById("restart-button"),
      previousWordButton: document.getElementById("previous-word-button"),
      nextWordButton: document.getElementById("next-word-button"),
      wpmSlider: document.getElementById("wpm-slider"),
      wpmValue: document.getElementById("wpm-value"),
      highlightColor: document.getElementById("highlight-color"),
      readerColor: document.getElementById("reader-color"),
      wordCount: document.getElementById("word-count"),
      estimatedTime: document.getElementById("estimated-time"),
      wordIndex: document.getElementById("word-index"),
      progressValue: document.getElementById("progress-value"),
      sourceLabel: document.getElementById("source-label"),
      stateBadge: document.getElementById("state-badge"),
      statusPill: document.getElementById("status-pill"),
      notice: document.getElementById("notice"),
      readerStage: document.getElementById("reader-stage"),
      wordShell: document.getElementById("word-shell"),
    };

    function estimateDuration(wordCount, wpm) {
      if (!wordCount || !wpm) {
        return 0;
      }
      return (wordCount / wpm) * 60;
    }

    function formatDuration(seconds) {
      const totalSeconds = Math.max(0, Math.round(seconds));
      if (totalSeconds < 60) {
        return `${totalSeconds} sec`;
      }
      const minutes = Math.floor(totalSeconds / 60);
      const remainder = totalSeconds % 60;
      return remainder ? `${minutes} min ${remainder} sec` : `${minutes} min`;
    }
"""
    r"""
    function getOrpIndex(word) {
      if (!word || /\d/.test(word)) {
        return null;
      }

      const alphaPositions = [];
      for (let i = 0; i < word.length; i += 1) {
        if (/\p{L}/u.test(word[i])) {
          alphaPositions.push(i);
        }
      }

      if (!alphaPositions.length) {
        return null;
      }

      const alphaLength = alphaPositions.length;
      let focusIndex = 0;
      if (alphaLength <= 1) {
        focusIndex = 0;
      } else if (alphaLength <= 5) {
        focusIndex = 1;
      } else if (alphaLength <= 9) {
        focusIndex = 2;
      } else if (alphaLength <= 13) {
        focusIndex = 3;
      } else {
        focusIndex = 4;
      }

      return alphaPositions[Math.min(focusIndex, alphaPositions.length - 1)];
    }

    function clearPlaybackTimer() {
      if (state.timerId) {
        window.clearTimeout(state.timerId);
        state.timerId = null;
      }
    }

    function clamp(value, min, max) {
      return Math.min(Math.max(value, min), max);
    }

    function setNotice(message, variant) {
      elements.notice.textContent = message;
      elements.notice.dataset.variant = variant || "info";
    }

    function setReaderColors() {
      document.documentElement.style.setProperty("--accent", state.highlightColor);
      document.documentElement.style.setProperty("--reader-bg", state.readerColor);
      const focus = elements.wordShell.querySelector(".word-focus");
      if (focus && focus.textContent) {
        focus.style.color = state.highlightColor;
      }
    }

    function alignWordShell() {
      window.requestAnimationFrame(() => {
        const focus = elements.wordShell.querySelector(".word-focus");
        const shellRect = elements.wordShell.getBoundingClientRect();

        if (!shellRect.width) {
          return;
        }

        let anchorOffset = shellRect.width / 2;
        if (focus && focus.textContent) {
          const focusRect = focus.getBoundingClientRect();
          anchorOffset = (focusRect.left - shellRect.left) + (focusRect.width / 2);
        }

        elements.wordShell.style.transform = `translate(${-anchorOffset}px, -50%)`;
      });
    }

    function getWordFontSize(word) {
      const length = (word || "").length;
      if (length >= 18) {
        return "clamp(1.9rem, 4.6vw, 4.2rem)";
      }
      if (length >= 14) {
        return "clamp(2.2rem, 5vw, 4.8rem)";
      }
      if (length >= 11) {
        return "clamp(2.5rem, 5.8vw, 5.4rem)";
      }
      return "clamp(3rem, 8vw, 6.5rem)";
    }

    function renderWord(word) {
      const displayWord = word || "Ready";
      const orpIndex = getOrpIndex(displayWord);
      elements.wordShell.style.fontSize = getWordFontSize(displayWord);

      elements.wordShell.innerHTML = "";

      const left = document.createElement("span");
      left.className = "word-left";

      const focus = document.createElement("span");
      focus.className = "word-focus";

      const right = document.createElement("span");
      right.className = "word-right";

      if (orpIndex === null) {
        right.textContent = displayWord;
      } else {
        left.textContent = displayWord.slice(0, orpIndex);
        focus.textContent = displayWord.slice(orpIndex, orpIndex + 1);
        focus.style.color = state.highlightColor;
        right.textContent = displayWord.slice(orpIndex + 1);
      }

      elements.wordShell.append(left, focus, right);
      alignWordShell();
    }

    function calculateDelay(word) {
      const baseDelay = 60000 / state.wpm;
      const stripped = (word || "").replace(/[^\p{L}\p{N}'’-]/gu, "");
      const length = stripped.length;
      let extraDelay = 0;

      if (length <= 2) {
        extraDelay -= baseDelay * 0.22;
      } else if (length <= 4) {
        extraDelay -= baseDelay * 0.1;
      }

      if (/[.!?]["')\]]*$/.test(word)) {
        extraDelay += baseDelay * 1.2;
      } else if (/[,;:]["')\]]*$/.test(word)) {
        extraDelay += baseDelay * 0.55;
      }

      if (length >= 8) {
        extraDelay += baseDelay * 0.22;
      }
      if (length >= 12) {
        extraDelay += baseDelay * 0.28;
      }
      if (length >= 16) {
        extraDelay += baseDelay * 0.18;
      }

      return Math.max(baseDelay * 0.55, baseDelay + extraDelay);
    }

    function updateStats() {
      const total = state.tokens.length;
      const visibleIndex = total ? state.index + 1 : 0;
      const progress = total ? Math.round((visibleIndex / total) * 100) : 0;

      elements.wordCount.textContent = String(total);
      elements.estimatedTime.textContent = formatDuration(estimateDuration(total, state.wpm));
      elements.wordIndex.textContent = `${visibleIndex} / ${total}`;
      elements.progressValue.textContent = `${progress}%`;
      elements.wpmValue.textContent = String(state.wpm);

      if (!total) {
        elements.stateBadge.textContent = "Idle";
        elements.statusPill.textContent = "Waiting";
      } else if (state.isPlaying) {
        elements.stateBadge.textContent = "Playing";
        elements.statusPill.textContent = `Running · ${progress}%`;
      } else if (visibleIndex >= total && total > 0) {
        elements.stateBadge.textContent = "Done";
        elements.statusPill.textContent = "Finished";
      } else {
        elements.stateBadge.textContent = "Paused";
        elements.statusPill.textContent = `Paused · ${progress}%`;
      }
    }

    function updateButtons() {
      const hasTokens = state.tokens.length > 0;
      elements.pauseButton.disabled = !hasTokens;
      elements.restartButton.disabled = !hasTokens;
      elements.previousWordButton.disabled = !hasTokens || state.index <= 0;
      elements.nextWordButton.disabled = !hasTokens || state.index >= state.tokens.length - 1;
      elements.pauseButton.textContent = state.isPlaying ? "Pause" : "Resume";
    }

    function stopPlayback(markComplete = false) {
      clearPlaybackTimer();
      state.isPlaying = false;
      updateButtons();
      updateStats();

      if (markComplete) {
        setNotice("Reading complete. Press Restart to play again or adjust settings and start over.", "success");
      }
    }

    function scheduleNextWord() {
      clearPlaybackTimer();

      if (!state.isPlaying || !state.tokens.length) {
        return;
      }

      const currentWord = state.tokens[state.index];
      const delay = calculateDelay(currentWord);

      state.timerId = window.setTimeout(() => {
        if (!state.isPlaying) {
          return;
        }

        if (state.index >= state.tokens.length - 1) {
          stopPlayback(true);
          return;
        }

        state.index += 1;
        renderWord(state.tokens[state.index]);
        updateStats();
        scheduleNextWord();
      }, delay);
    }
"""
    r"""
    function playCurrentSequence(fromStart) {
      if (!state.tokens.length) {
        setNotice("Load some text first, then start the reader.", "error");
        return;
      }

      clearPlaybackTimer();
      if (fromStart) {
        state.index = 0;
      }

      state.isPlaying = true;
      renderWord(state.tokens[state.index]);
      updateButtons();
      updateStats();
      setNotice("Playback is running. Use Space to pause or the left and right arrows to step word by word.", "info");
      scheduleNextWord();
    }

    function togglePlayback() {
      if (!state.tokens.length) {
        return;
      }

      if (!state.isPlaying && state.index >= state.tokens.length - 1) {
        playCurrentSequence(true);
        return;
      }

      if (state.isPlaying) {
        stopPlayback(false);
        setNotice("Playback paused. Press Space or Resume to continue.", "info");
      } else {
        playCurrentSequence(false);
      }
    }

    function restartPlayback() {
      if (!state.tokens.length) {
        return;
      }
      playCurrentSequence(true);
    }

    function stepWord(direction) {
      if (!state.tokens.length) {
        setNotice("Load some text first, then use the arrows to move word by word.", "error");
        return;
      }

      clearPlaybackTimer();
      state.isPlaying = false;

      const previousIndex = state.index;
      state.index = clamp(state.index + direction, 0, state.tokens.length - 1);

      renderWord(state.tokens[state.index]);
      updateButtons();
      updateStats();

      if (state.index === previousIndex) {
        const edgeMessage = direction < 0 ? "You are already at the first word." : "You are already at the last word.";
        setNotice(`${edgeMessage} Playback is paused.`, "info");
        return;
      }

      const stepLabel = direction < 0 ? "previous" : "next";
      setNotice(`Showing the ${stepLabel} word. Playback is paused.`, "info");
    }

    function applyWpm(nextWpm) {
      state.wpm = clamp(Number(nextWpm), 100, 1000);
      elements.wpmSlider.value = String(state.wpm);
      updateStats();

      if (state.tokens.length) {
        if (state.isPlaying) {
          scheduleNextWord();
        }
        setNotice(`Reading speed updated to ${state.wpm} WPM.`, "info");
      }
    }

    async function prepareReader() {
      const formData = new FormData();
      formData.append("text", elements.textInput.value || "");

      if (elements.fileInput.files[0]) {
        formData.append("file", elements.fileInput.files[0]);
      }

      elements.startButton.disabled = true;
      setNotice("Preparing text for RSVP playback...", "info");

      try {
        const response = await fetch("/extract", {
          method: "POST",
          body: formData,
        });

        const payload = await response.json();
        if (!response.ok || !payload.ok) {
          throw new Error(payload.error || "We couldn't prepare the text.");
        }

        state.tokens = payload.tokens || [];
        state.index = 0;
        state.source = payload.source || "Pasted text";
        elements.sourceLabel.textContent = `${state.source} · ${state.tokens.length} words ready`;

        renderWord(state.tokens[0]);
        updateButtons();
        updateStats();
        playCurrentSequence(true);
      } catch (error) {
        state.tokens = [];
        state.index = 0;
        state.source = "";
        clearPlaybackTimer();
        state.isPlaying = false;
        elements.sourceLabel.textContent = "No text loaded yet";
        renderWord("Ready");
        updateButtons();
        updateStats();
        setNotice(error.message || "Something unexpected happened while preparing the reader.", "error");
      } finally {
        elements.startButton.disabled = false;
      }
    }

    function isTypingContext() {
      const active = document.activeElement;
      if (!active) {
        return false;
      }

      if (active.tagName === "TEXTAREA") {
        return true;
      }

      if (active.tagName === "INPUT") {
        const type = (active.getAttribute("type") || "").toLowerCase();
        return !["range", "color", "file", "button", "submit"].includes(type);
      }

      return active.isContentEditable;
    }

    elements.startButton.addEventListener("click", prepareReader);
    elements.pauseButton.addEventListener("click", togglePlayback);
    elements.restartButton.addEventListener("click", restartPlayback);
    elements.previousWordButton.addEventListener("click", () => stepWord(-1));
    elements.nextWordButton.addEventListener("click", () => stepWord(1));

    elements.wpmSlider.addEventListener("input", (event) => {
      applyWpm(event.target.value);
    });

    elements.highlightColor.addEventListener("input", (event) => {
      state.highlightColor = event.target.value;
      setReaderColors();
      renderWord(state.tokens[state.index] || "Ready");
    });

    elements.readerColor.addEventListener("input", (event) => {
      state.readerColor = event.target.value;
      setReaderColors();
    });

    window.addEventListener("resize", alignWordShell);

    window.addEventListener("keydown", (event) => {
      if (isTypingContext()) {
        return;
      }

      if (event.code === "Space") {
        event.preventDefault();
        togglePlayback();
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        stepWord(-1);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        stepWord(1);
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        applyWpm(state.wpm - 10);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        applyWpm(state.wpm + 10);
      } else if (event.key.toLowerCase() === "r") {
        event.preventDefault();
        restartPlayback();
      }
    });

    document.fonts?.ready?.then(alignWordShell);
    setReaderColors();
    updateButtons();
    updateStats();
    renderWord("Ready");
  </script>
</body>
</html>
"""
)


if __name__ == "__main__":
    app.run(host="localhost", port=5000, debug=False)
