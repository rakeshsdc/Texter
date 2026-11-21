"""Text extraction from DOCX and PDF files."""
import io
import re

REF_HEADING = re.compile(
    r"^\s*(references|bibliography|works cited|संदर्भ|सन्दर्भ|संदर्भ सूची|सन्दर्भ सूची|ग्रंथ सूची|ग्रन्थ सूची|"
    r"അവലംബം|അവലംബങ്ങൾ|ഗ്രന്ഥസൂചി|റഫറൻസുകൾ|റഫറൻസ്)\s*:?\s*$", re.I | re.M)


def _docx_text(data: bytes) -> str:
    import docx
    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            for c in row.cells:
                parts.append(c.text)
    return "\n".join(parts)


def _pdftotext(data: bytes, max_pages=None):
    """Poppler's pdftotext keeps Hindi and Malayalam letters in the right order. None if not installed."""
    import os
    import shutil
    import subprocess
    import tempfile
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "in.pdf")
        with open(src, "wb") as fh:
            fh.write(data)
        cmd = [exe, "-enc", "UTF-8"] + (["-l", str(max_pages)] if max_pages else []) + [src, "-"]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=90)
        except Exception:
            return None
    return r.stdout.decode("utf-8", "ignore") if r.returncode == 0 else None


def _pdf_text(data: bytes, max_pages=None) -> str:
    text = _pdftotext(data, max_pages)
    if text is not None:
        return text
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(data), strict=False)
    pages = r.pages if max_pages is None else r.pages[:max_pages]
    return "\n".join((pg.extract_text() or "") for pg in pages)


def extract_text(filename: str, data: bytes) -> str:
    """Return plain text for a .docx or .pdf upload."""
    low = filename.lower()
    if low.endswith(".docx"):
        return _docx_text(data)
    if low.endswith(".pdf"):
        return _pdf_text(data)
    raise ValueError(f"Unsupported file type: {filename} (use .docx or .pdf)")


def strip_references(text: str) -> str:
    """Cut the text at the last 'References' style heading (if it is in the back half)."""
    matches = list(REF_HEADING.finditer(text))
    if matches:
        m = matches[-1]
        if m.start() > len(text) * 0.4:
            return text[: m.start()]
    return text


# ---------------- images and scanned pages (OCR) ----------------
IMAGE_EXT = (".jpg", ".jpeg", ".png")
OCR_LANGUAGES = {"English": "eng", "Hindi": "hin", "Malayalam": "mal",
                 "English + Hindi": "eng+hin", "English + Malayalam": "eng+mal"}


def installed_ocr_languages():
    try:
        import pytesseract
        return set(pytesseract.get_languages(config=""))
    except Exception:
        return set()


def _check_lang(lang):
    missing = [x for x in lang.split("+") if x not in installed_ocr_languages()]
    if missing:
        raise RuntimeError(f"Text reading data for '{'+'.join(missing)}' is not installed on this server. "
                           "Add tesseract-ocr-hin / tesseract-ocr-mal to packages.txt")


def ocr_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _preprocess(img):
    """Straighten phone photos, flatten shadows and make the text dark on white."""
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(img).convert("L")
    w, h = img.size
    if w < 2000:
        k = 2200 / w
        img = img.resize((int(w * k), int(h * k)), Image.LANCZOS)
    elif w > 3500:
        k = 3000 / w
        img = img.resize((int(w * k), int(h * k)), Image.LANCZOS)
    try:
        import cv2
        import numpy as np
        a = cv2.GaussianBlur(np.array(img), (3, 3), 0)
        a = cv2.adaptiveThreshold(a, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 15)
        return Image.fromarray(a)
    except Exception:
        return ImageOps.autocontrast(img)


def ocr_image(img, lang="eng"):
    """Return (text, mean_confidence_percent) for a PIL image."""
    import pytesseract
    d = pytesseract.image_to_data(_preprocess(img), lang=lang, config="--oem 3 --psm 3",
                                  output_type=pytesseract.Output.DICT)
    lines, confs = {}, []
    for i, t in enumerate(d["text"]):
        t = t.strip()
        try:
            c = float(d["conf"][i])
        except (TypeError, ValueError):
            continue
        if not t or c < 0:
            continue
        lines.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(t)
        confs.append(c)
    text = "\n".join(" ".join(v) for _, v in sorted(lines.items()))
    return text, (sum(confs) / len(confs) if confs else 0.0)


def _ocr_pdf(data: bytes, max_pages: int = 15, lang="eng"):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(data)
    texts, confs = [], []
    for i in range(min(len(pdf), max_pages)):
        t, c = ocr_image(pdf[i].render(scale=2.5).to_pil(), lang)
        texts.append(t)
        confs.append(c)
    return "\n".join(texts), (sum(confs) / len(confs) if confs else 0.0)


def extract_document(filename: str, data: bytes, lang: str = "eng") -> dict:
    """Read .docx, .pdf (with OCR fallback for scans) or .jpg/.png photos.
    Returns {'text', 'method': 'text'|'ocr', 'conf': mean OCR confidence or None}."""
    low = filename.lower()
    if low.endswith(IMAGE_EXT):
        if not ocr_available():
            raise RuntimeError("Image reading (OCR) is not available on this server")
        _check_lang(lang)
        from PIL import Image
        text, conf = ocr_image(Image.open(io.BytesIO(data)), lang)
        return {"text": text, "method": "ocr", "conf": conf}
    text = extract_text(filename, data)
    if low.endswith(".pdf") and len(text.split()) < 30 and ocr_available():
        _check_lang(lang)
        text, conf = _ocr_pdf(data, lang=lang)
        return {"text": text, "method": "ocr", "conf": conf}
    return {"text": text, "method": "text", "conf": None}
