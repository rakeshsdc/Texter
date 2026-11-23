"""Find related open-access documents (journals, repositories) for a paper.

DOAJ, Zenodo and DSpace search titles and abstracts, not body text. So the paper's title,
keywords and most frequent terms are used to find related documents; their PDFs are then
read in full by the similarity check.
"""
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from . import scholar as S

SOURCES = ["OpenAlex", "Zenodo", "DOAJ", "CORE", "University repositories"]
_STOP = set("""about above after again against because before being below between could does doing during
further having their there these they this those through under until very were what when where which while
whom with would your should shall might other another often every using used also than then into from have
been more most such only over same some will each both those which however therefore thus whether""".split())
_LABEL = re.compile(r"^\s*(keywords?|key\s*words?|index terms|कीवर्ड|मुख्य शब्द|താക്കോൽ വാക്കുകൾ|കീവേഡ്)\s*[:\-–—]\s*(.+)$",
                    re.I | re.M)


def guess_title(text):
    for line in text.splitlines():
        line = line.strip()
        if 15 <= len(line) <= 160 and not line.endswith("."):
            return line
    return ""


def find_keywords(text):
    m = _LABEL.search(text[:6000])
    if not m:
        return []
    return [k.strip() for k in re.split(r"[,;·|]", m.group(2)) if 2 < len(k.strip()) < 40][:6]


def top_terms(text, k=5):
    words = re.findall(r"[^\W\d_]+", text.lower())
    c = Counter()
    for w in words:
        indic = bool(re.search(r"[ऀ-ॿഀ-ൿ]", w))
        if len(w) >= (5 if indic else 6) and w not in _STOP:
            c[w] += 1
    return [w for w, _ in c.most_common(k)]


def candidate_queries(text):
    qs = []
    t = guess_title(text)
    if t:
        qs.append(t)
    kws = find_keywords(text)
    if kws:
        qs.append(" ".join(kws[:4]))
    terms = top_terms(text)
    if terms:
        qs.append(" ".join(terms[:4]))
    out, seen = [], set()
    for q in qs:
        if q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out[:3]


def _norm(t):
    return re.sub(r"[^a-z0-9ऀ-ॿഀ-ൿ]", "", (t or "").lower())


def merge(records):
    merged, index = [], {}
    for r in records:
        keys = [k for k in (r.get("doi"), _norm(r.get("title"))) if k]
        hit = next((index[k] for k in keys if k in index), None)
        if hit is None:
            r = dict(r, sources=[r.get("source", "")])
            merged.append(r)
            for k in keys:
                index[k] = r
        else:
            for f in ("pdf_url", "landing_url", "doi"):
                if not hit.get(f) and r.get(f):
                    hit[f] = r[f]
            if r.get("source") and r["source"] not in hit["sources"]:
                hit["sources"].append(r["source"])
            for k in keys:
                index.setdefault(k, hit)
    return merged


def discover(text, sources, email=None, per_source=10, max_docs=12, progress=None):
    """Return (seeds, notes). seeds: [{"href", "title", "via"}] with PDFs first."""
    sources = [s for s in sources if s in SOURCES]
    queries = candidate_queries(text)
    notes, jobs = [], []
    for q in queries:
        for s in sources:
            if s == "OpenAlex":
                jobs.append((s, lambda q=q: S.openalex_search(q, email, None, True, per_source)))
            elif s == "Zenodo":
                jobs.append((s, lambda q=q: S.zenodo_search(q, per_source)))
            elif s == "DOAJ":
                jobs.append((s, lambda q=q: S.doaj_search(q, per_source)))
            elif s == "CORE" and os.environ.get("CORE_API_KEY"):
                jobs.append((s, lambda q=q: S.core_search(q, per_source)))
            elif s == "University repositories":
                jobs.append((s, lambda q=q: S.dspace_search(q, per_source)))
    results = []
    if jobs:
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = [(n, ex.submit(fn)) for n, fn in jobs]
            for n, f in futs:
                try:
                    results.extend(f.result())
                except Exception as e:
                    msg = f"{n} could not be searched ({type(e).__name__})"
                    if msg not in notes:
                        notes.append(msg)
    records = merge(results)
    if email:
        def enrich(r):
            try:
                return S.unpaywall_lookup(r["doi"], email)
            except Exception:
                return None, None
        todo = [r for r in records if r.get("doi") and not r.get("pdf_url")][:15]
        with ThreadPoolExecutor(max_workers=6) as ex:
            for r, (pdf, landing) in zip(todo, ex.map(enrich, todo)):
                if pdf:
                    r["pdf_url"] = pdf
    records.sort(key=lambda r: not r.get("pdf_url"))
    seeds = []
    for r in records:
        u = r.get("pdf_url")           # only open PDFs: full text is needed for comparison
        if u:
            seeds.append({"href": u, "title": r.get("title") or u, "via": ", ".join(r["sources"])})
        if len(seeds) >= max_docs:
            break
    return seeds, notes
