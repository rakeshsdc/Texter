"""Connectors for open scholarly sources.

Every search function returns a list of records (dicts made by make_record).
Only legally open-access PDFs are linked. Nothing is downloaded or stored.
"""
import os
import re
from concurrent.futures import ThreadPoolExecutor

import requests

TIMEOUT = 15
HEADERS = {"User-Agent": "Texter/1.0 (document similarity checker)"}


def _get(url, params=None, headers=None):
    h = dict(HEADERS)
    if headers:
        h.update(headers)
    r = requests.get(url, params=params, headers=h, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def make_record(**kw):
    rec = dict(
        title="", authors=[], year=None, doi=None, journal="",
        pdf_url=None, landing_url=None, source="", language=None,
        kind="article", oa_status=None,
        abstract="", topics=[], cited_by=None, publisher="", biblio="",
    )
    rec.update(kw)
    return rec


def clean_doi(text):
    """Extract a bare lowercase DOI from text or a doi.org URL."""
    m = re.search(r"10\.\d{4,9}/[^\s\"<>]+", text or "")
    return m.group(0).rstrip(".,;)").lower() if m else None


# --------------------------------------------------------------------------
# OpenAlex  (all disciplines; returns open-access PDF links when known)
# --------------------------------------------------------------------------
# OpenAlex "field" IDs. Humanities, social sciences and business are included.
FIELD_IDS = {
    "Any discipline": None,
    "Arts and Humanities": 12,
    "Social Sciences": 33,
    "Business, Management and Accounting": 14,
    "Economics, Econometrics and Finance": 20,
    "Psychology": 32,
    "Medicine": 27,
    "Nursing": 29,
    "Health Professions": 36,
    "Computer Science": 17,
    "Engineering": 22,
    "Mathematics": 26,
    "Physics and Astronomy": 31,
    "Chemistry": 16,
    "Biochemistry, Genetics and Molecular Biology": 13,
    "Agricultural and Biological Sciences": 11,
    "Earth and Planetary Sciences": 19,
    "Environmental Science": 23,
    "Materials Science": 25,
    "Pharmacology and Toxicology": 30,
    "Decision Sciences": 18,
}


def _openalex_params(email):
    p = {}
    if email:
        p["mailto"] = email
    key = os.getenv("OPENALEX_API_KEY")  # optional; check OpenAlex docs for current terms
    if key:
        p["api_key"] = key
    return p


def _abstract_from_index(inv):
    """OpenAlex stores abstracts as {word: [positions]}; rebuild the text."""
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def _parse_openalex(w):
    best = w.get("best_oa_location") or {}
    prim = w.get("primary_location") or {}
    src = prim.get("source") or {}
    authors = [
        a["author"]["display_name"]
        for a in (w.get("authorships") or [])[:8]
        if a.get("author") and a["author"].get("display_name")
    ]
    return make_record(
        title=w.get("title") or w.get("display_name") or "",
        authors=authors,
        year=w.get("publication_year"),
        doi=clean_doi(w.get("doi")),
        journal=src.get("display_name") or "",
        pdf_url=best.get("pdf_url"),
        landing_url=best.get("landing_page_url") or prim.get("landing_page_url") or w.get("doi"),
        source="OpenAlex",
        language=w.get("language"),
        kind=w.get("type") or "article",
        oa_status=(w.get("open_access") or {}).get("oa_status"),
        abstract=_abstract_from_index(w.get("abstract_inverted_index")),
        topics=[t.get("display_name", "") for t in (w.get("topics") or [])[:5] if t.get("display_name")],
        cited_by=w.get("cited_by_count"),
        publisher=(src.get("host_organization_name") or ""),
        biblio=_biblio(w.get("biblio") or {}),
    )


def _biblio(b):
    parts = []
    if b.get("volume"):
        parts.append(f"Vol. {b['volume']}")
    if b.get("issue"):
        parts.append(f"Issue {b['issue']}")
    if b.get("first_page"):
        pages = b["first_page"] + (f"-{b['last_page']}" if b.get("last_page") else "")
        parts.append(f"pp. {pages}")
    return ", ".join(parts)


def crossref_details(doi, email=None):
    """Publisher-supplied metadata (abstract, publisher, volume/pages) for a DOI.

    Many paywalled articles still expose an abstract through Crossref. This uses
    the official API, not scraping of publisher pages.
    """
    if not doi:
        return {}
    try:
        data = _get(f"https://api.crossref.org/works/{doi}",
                    {"mailto": email} if email else None)
    except Exception:
        return {}
    m = data.get("message", {})
    abstract = re.sub(r"<[^>]+>", " ", m.get("abstract") or "")
    abstract = re.sub(r"\s+", " ", abstract).strip()
    abstract = re.sub(r"^Abstract\s*", "", abstract)
    bits = []
    if m.get("volume"):
        bits.append(f"Vol. {m['volume']}")
    if m.get("issue"):
        bits.append(f"Issue {m['issue']}")
    if m.get("page"):
        bits.append(f"pp. {m['page']}")
    return {
        "abstract": abstract,
        "publisher": m.get("publisher") or "",
        "biblio": ", ".join(bits),
        "topics": (m.get("subject") or [])[:5],
        "cited_by": m.get("is-referenced-by-count"),
    }


def openalex_search(query, email=None, field=None, oa_only=False, limit=15):
    filters = []
    fid = FIELD_IDS.get(field)
    if fid:
        filters.append(f"primary_topic.field.id:{fid}")
    if oa_only:
        filters.append("is_oa:true")
    params = {"search": query, "per-page": limit, **_openalex_params(email)}
    if filters:
        params["filter"] = ",".join(filters)
    data = _get("https://api.openalex.org/works", params)
    return [_parse_openalex(w) for w in data.get("results", [])]


def openalex_doi(doi, email=None):
    data = _get(f"https://api.openalex.org/works/https://doi.org/{doi}", _openalex_params(email))
    return [_parse_openalex(data)]


# --------------------------------------------------------------------------
# Unpaywall  (DOI -> legal open-access PDF). Requires an email address.
# --------------------------------------------------------------------------
def unpaywall_lookup(doi, email):
    """Return (pdf_url, landing_url) or (None, None)."""
    if not email or not doi:
        return None, None
    try:
        d = _get(f"https://api.unpaywall.org/v2/{doi}", {"email": email})
    except Exception:
        return None, None
    loc = d.get("best_oa_location") or {}
    return loc.get("url_for_pdf"), loc.get("url")


# --------------------------------------------------------------------------
# CORE  (thousands of repositories). Free API key: https://core.ac.uk/services/api
# --------------------------------------------------------------------------
def core_search(query, limit=10):
    key = os.getenv("CORE_API_KEY")
    if not key:
        return []
    data = _get(
        "https://api.core.ac.uk/v3/search/works",
        {"q": query, "limit": limit},
        headers={"Authorization": f"Bearer {key}"},
    )
    out = []
    for w in data.get("results", []):
        pdf = w.get("downloadUrl")
        if not pdf:
            urls = w.get("sourceFulltextUrls") or []
            pdf = urls[0] if urls else None
        out.append(make_record(
            title=w.get("title") or "",
            authors=[a.get("name", "") for a in (w.get("authors") or [])[:8]],
            year=w.get("yearPublished"),
            doi=clean_doi(w.get("doi")),
            journal=((w.get("publisher") or "")),
            pdf_url=pdf,
            landing_url=(w.get("links") or [{}])[0].get("url") if w.get("links") else None,
            source="CORE",
            language=(w.get("language") or {}).get("code") if isinstance(w.get("language"), dict) else None,
        ))
    return out


# --------------------------------------------------------------------------
# Zenodo  (open repository: papers, reports, theses)
# --------------------------------------------------------------------------
def zenodo_search(query, limit=10):
    data = _get("https://zenodo.org/api/records", {"q": query, "size": limit, "sort": "bestmatch"})
    out = []
    for h in data.get("hits", {}).get("hits", []):
        md = h.get("metadata", {})
        pdf = None
        for f in h.get("files", []) or []:
            if str(f.get("key", "")).lower().endswith(".pdf"):
                pdf = (f.get("links") or {}).get("self")
                break
        rtype = (md.get("resource_type") or {}).get("type", "")
        out.append(make_record(
            title=md.get("title", ""),
            authors=[c.get("name", "") for c in (md.get("creators") or [])[:8]],
            year=(md.get("publication_date") or "")[:4] or None,
            doi=clean_doi(h.get("doi") or md.get("doi")),
            pdf_url=pdf,
            landing_url=(h.get("links") or {}).get("self_html"),
            source="Zenodo",
            language=md.get("language"),
            kind=rtype or "article",
        ))
    return out


# --------------------------------------------------------------------------
# DOAJ  (curated open-access journals; metadata is CC0)
# Field names follow DOAJ's article search format; not yet tested live.
# DOAJ documents a limit of about 2 requests per second.
# --------------------------------------------------------------------------
def doaj_search(query, limit=10):
    from urllib.parse import quote
    # DOAJ uses Elasticsearch query syntax; strip characters that break it
    q = re.sub(r"[^\w\s\u0900-\u097F\u0D00-\u0D7F]", " ", query).strip()
    if not q:
        return []
    data = _get(f"https://doaj.org/api/search/articles/{quote(q, safe='')}", {"pageSize": limit})
    out = []
    for h in data.get("results", []):
        b = h.get("bibjson", {})
        doi = None
        for i in b.get("identifier", []) or []:
            if str(i.get("type", "")).lower() == "doi":
                doi = clean_doi(i.get("id"))
        pdf = page = None
        for l in b.get("link", []) or []:
            url = l.get("url")
            if not url:
                continue
            if str(l.get("content_type", "")).upper() == "PDF" or url.lower().endswith(".pdf"):
                pdf = pdf or url
            else:
                page = page or url
        out.append(make_record(
            title=b.get("title", ""),
            authors=[a.get("name", "") for a in (b.get("author") or [])[:8]],
            year=b.get("year"),
            doi=doi,
            journal=(b.get("journal") or {}).get("title", ""),
            publisher=(b.get("journal") or {}).get("publisher", ""),
            pdf_url=pdf,
            landing_url=page or (f"https://doi.org/{doi}" if doi else None),
            source="DOAJ",
            abstract=b.get("abstract", "") or "",
            topics=[s.get("term", "") for s in (b.get("subject") or [])[:5] if s.get("term")],
            oa_status="DOAJ open-access journal",
        ))
    return out


# --------------------------------------------------------------------------
# Books: DOAB (open-access academic books) and Open Library (Internet Archive)
# Response shapes follow each service's documentation; not yet tested live.
# --------------------------------------------------------------------------
DOAB_BASE = "https://directory.doabooks.org"


def doab_search(query, limit=10):
    data = _get(f"{DOAB_BASE}/rest/search",
                {"query": query, "expand": "metadata,bitstreams"},
                headers={"Accept": "application/json"})
    items = data if isinstance(data, list) else []
    out = []
    for it in items[:limit]:
        md = {}
        for m in it.get("metadata", []) or []:
            md.setdefault(m.get("key"), []).append(m.get("value"))
        pdf = None
        for bs in it.get("bitstreams", []) or []:
            link = bs.get("retrieveLink") or ""
            if "pdf" in str(bs.get("mimeType", "")).lower() and link:
                pdf = link if link.startswith("http") else DOAB_BASE + link
                break
        doi = None
        for k, vals in md.items():
            if k and "identifier" in k:
                for v in vals:
                    doi = doi or clean_doi(v)
        year = (md.get("dc.date.issued") or [""])[0][:4] or None
        out.append(make_record(
            title=it.get("name") or (md.get("dc.title") or [""])[0],
            authors=(md.get("dc.contributor.author") or md.get("dc.contributor.editor") or [])[:8],
            year=year,
            doi=doi,
            pdf_url=pdf,
            landing_url=f"{DOAB_BASE}/handle/{it['handle']}" if it.get("handle") else None,
            source="DOAB",
            kind="book",
            publisher=(md.get("publisher.name") or md.get("dc.publisher") or [""])[0],
            abstract=(md.get("dc.description.abstract") or [""])[0],
            oa_status="open-access book",
        ))
    return out


def openlibrary_search(query, limit=10):
    """Books with a readable copy. Only public-domain scans are marked free to read;
    borrow-only titles are labelled and get no PDF button."""
    data = _get("https://openlibrary.org/search.json", {
        "q": query, "limit": limit * 2,
        "fields": "key,title,author_name,first_publish_year,ia,has_fulltext,public_scan_b,ebook_access",
    })
    out = []
    for d in data.get("docs", []):
        if not d.get("has_fulltext"):
            continue
        ia = (d.get("ia") or [None])[0]
        free = bool(d.get("public_scan_b"))
        landing = f"https://archive.org/details/{ia}" if (ia and free) else f"https://openlibrary.org{d.get('key', '')}"
        out.append(make_record(
            title=d.get("title", ""),
            authors=(d.get("author_name") or [])[:8],
            year=d.get("first_publish_year"),
            landing_url=landing,
            source="Open Library",
            kind="book",
            publisher="Internet Archive scan, free to read online" if free else "Borrow only (may need a free account)",
            oa_status="free to read" if free else "borrowable",
        ))
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------
# DSpace 7 repositories (many university repositories). Config-driven.
#
# NOTE: OAI-PMH is meant for harvesting, not keyword search, so keyword search
# uses the DSpace 7 "discover" REST API instead. Only repositories running
# DSpace 7+ with the REST API enabled will work. Add yours below after
# opening  <api_base>/api  in a browser to confirm it responds.
# --------------------------------------------------------------------------
DSPACE_REPOS = [
    {"name": "University of Calicut (scholar.uoc.ac.in)",
     "api_base": "https://scholar.uoc.ac.in/server",
     "ui_base": "https://scholar.uoc.ac.in"},
    {"name": "CUSAT (dspace.cusat.ac.in)",
     "api_base": "https://dspace.cusat.ac.in/server",
     "ui_base": "https://dspace.cusat.ac.in"},
    # Template for more repositories:
    # {"name": "Example University", "api_base": "https://dspace.example.edu/server",
    #  "ui_base": "https://dspace.example.edu"},
]


def _dspace_pdf(repo, item_uuid):
    """Find the PDF of one DSpace 7 item.

    The search results do not list files, so each item needs two extra calls:
    its bundles, then the files in the ORIGINAL bundle. If an item has several
    PDFs (for example a thesis split into chapters), the largest one is used.
    Returns a download link, or None.
    """
    try:
        b = _get(f"{repo['api_base']}/api/core/items/{item_uuid}/bundles")
        for bundle in (b.get("_embedded") or {}).get("bundles", []):
            if bundle.get("name") != "ORIGINAL":
                continue
            bs = _get(f"{repo['api_base']}/api/core/bundles/{bundle['uuid']}/bitstreams")
            pdfs = [s for s in (bs.get("_embedded") or {}).get("bitstreams", [])
                    if str(s.get("name", "")).lower().endswith(".pdf")]
            if pdfs:
                best = max(pdfs, key=lambda s: s.get("sizeBytes") or 0)
                href = ((best.get("_links") or {}).get("content") or {}).get("href")
                return href or f"{repo['api_base']}/api/core/bitstreams/{best['uuid']}/content"
    except Exception:
        return None
    return None


def dspace_search(query, limit=10):
    out = []
    for repo in DSPACE_REPOS:
        try:
            data = _get(
                f"{repo['api_base']}/api/discover/search/objects",
                {"query": query, "size": limit, "dsoType": "ITEM"},
            )
        except Exception:
            continue
        objs = (((data.get("_embedded") or {}).get("searchResult") or {})
                .get("_embedded") or {}).get("objects", [])
        found = []
        for o in objs:
            item = (o.get("_embedded") or {}).get("indexableObject") or {}
            md = item.get("metadata") or {}

            def first(key, md=md):
                v = md.get(key) or []
                return v[0].get("value") if v else ""

            rec = make_record(
                title=item.get("name") or first("dc.title"),
                authors=[a.get("value", "") for a in (md.get("dc.contributor.author") or [])[:8]],
                year=(first("dc.date.issued") or "")[:4] or None,
                doi=clean_doi(first("dc.identifier.uri")),
                landing_url=f"{repo['ui_base']}/items/{item.get('uuid')}",
                source=repo["name"],
                language=first("dc.language.iso") or None,
                kind=first("dc.type") or "thesis/report",
                abstract=first("dc.description.abstract") or "",
            )
            found.append((rec, item.get("uuid")))

        # Look up each item's PDF, a few at a time
        with ThreadPoolExecutor(max_workers=6) as ex:
            links = list(ex.map(lambda ru: _dspace_pdf(repo, ru[1]) if ru[1] else None, found))
        for (rec, _), pdf in zip(found, links):
            rec["pdf_url"] = pdf
            out.append(rec)
    return out


# --------------------------------------------------------------------------
# Reports / grey literature fallback (trusted domains only)
# Uses the optional `ddgs` package (web search). Safe to skip if not installed.
# --------------------------------------------------------------------------
TRUSTED_DOMAINS = [
    "worldbank.org", "unesco.org", "oecd.org", "rbi.org.in", "niti.gov.in",
    "undp.org", "who.int", "ilo.org", "imf.org", "unicef.org", "data.gov.in",
    "prsindia.org", "ncert.nic.in", "ugc.gov.in", "mospi.gov.in", "icssr.org",
    "dea.gov.in", "pib.gov.in", "sansad.in",
]


def reports_search(query, limit=10):
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return []
    sites = " OR ".join(f"site:{d}" for d in TRUSTED_DOMAINS)
    q = f"{query} filetype:pdf ({sites})"
    try:
        with DDGS() as d:
            results = list(d.text(q, max_results=limit))
    except Exception:
        return []
    out = []
    for r in results:
        url = r.get("href") or ""
        out.append(make_record(
            title=r.get("title", ""),
            pdf_url=url if url.lower().endswith(".pdf") else None,
            landing_url=url,
            source="Web (trusted report sites)",
            kind="report",
        ))
    return out
