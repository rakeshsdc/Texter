"""Texter - Streamlit app. Run with:  streamlit run app.py"""
import pandas as pd
import streamlit as st

from veritext import APP_NAME, APP_TAGLINE
from veritext.extract import IMAGE_EXT, OCR_LANGUAGES, extract_document, ocr_available, strip_references
from veritext.names import student_name_from_filename
from veritext.report import build_class_report, build_single_report
from veritext.similarity import analyse_class, combine_web, excluded_grams, internal_repetition, level_for, make_doc
from veritext.websearch import check_web, make_search

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

st.set_page_config(page_title=APP_NAME, page_icon="📄", layout="wide")
st.title(f"📄 {APP_NAME}")
st.caption(APP_TAGLINE)
st.markdown("Developed by **Dr. Rakesh Chandran S.B**, Associate Professor of Physics & IQAC Coordinator, Sanatana Dharma College, Alappuzha")

st.markdown(
    f"**{APP_NAME}** checks documents for copied text. Upload Word (.docx) or PDF files, or photos of pages (JPEG/PNG), and the software "
    "produces a Word report with similarity percentages, sources and matching passages.")
h1, h2 = st.columns(2)
with h1:
    with st.container(border=True):
        st.subheader("Module 1: Plagiarism check")
        st.markdown(
            "**For:** one paper, such as a journal article, thesis chapter or manuscript.\n\n"
            "**What it does**\n"
            "1. Reads the text of the uploaded .docx or .pdf and leaves out the reference list.\n"
            "2. Searches the sentences of the paper (every sentence in Thorough mode) on the open web and in "
            "open-access scholarly databases (Europe PMC and OpenAlex).\n"
            "3. Opens the pages found, including open-access journal articles and PDF files, and compares each one "
            "with the whole paper word by word.\n"
            "4. Reports the similarity percentage, the sources with matching text, the matched passages and any "
            "repetition inside the paper.\n\n"
            "**Output:** a Word report.")
with h2:
    with st.container(border=True):
        st.subheader("Module 2: Assignment similarity")
        st.markdown(
            "**For:** a class set of assignments submitted as .docx or .pdf.\n\n"
            "**What it does**\n"
            "1. Reads each file (Word, PDF, or JPEG/PNG photos of printed or handwritten pages, read by text "
            "recognition) and takes the student's name from the file name. You can correct the names before "
            "the check. Several pages of one student are joined into one submission.\n"
            "2. Compares every submission with every other one to find students who copied from each other, "
            "even when one file is Word and the other is PDF.\n"
            "3. Also checks each submission on the web and in open-access databases, so students who copied from "
            "online sources are flagged.\n"
            "4. Ignores the question paper wording if you upload it.\n\n"
            "**Output:** a Word report with students ranked by similarity, showing the closest classmate, the web "
            "match and a flag, plus a CSV of the ranking.")
st.caption("Sources reached: public web pages, open-access journals and repositories, and PDFs found in search results. "
           "Paywalled journals and private student repositories cannot be searched, so a low result does not prove a "
           "text is original.")

# ---------------- sidebar: settings shared by both modules ----------------
with st.sidebar:
    st.header("Report settings")
    institution = st.text_input("Institution / department", "")
    prepared_by = st.text_input("Prepared by (optional)", "")
    subtitle = st.text_input("Report subtitle (optional)", "Prepared with free and open tools.")
    footer = st.text_input("Footer text (optional, page number is always shown)", "")
    st.header("Detection settings")
    n = st.slider("Phrase length (words)", 4, 10, 6, help="Longer = stricter. 6 is a good default.")
    min_run = st.slider("Minimum matching run (words)", 5, 20, 8,
                        help="Ignore matches shorter than this, such as common phrases.")
    high = st.slider("High similarity from (%)", 20, 80, 40)
    moderate = st.slider("Moderate similarity from (%)", 5, 40, 15)
    skip_refs = st.checkbox("Ignore the reference list", True)
    ocr_choice = st.selectbox("Language of photos and scanned pages", list(OCR_LANGUAGES),
                              help="Typed Word and PDF files in Hindi, Malayalam or English need no setting. "
                                   "This is only for reading text from images and scans.")
    ocr_lang = OCR_LANGUAGES[ocr_choice]


def make_meta(title):
    return {"institution": institution, "title": title, "prepared_by": prepared_by, "subtitle": subtitle,
            "footer": footer, "n": n, "min_run": min_run}


def read_files(files):
    out, problems, info = [], [], {}
    for f in files:
        try:
            res = extract_document(f.name, f.getvalue(), ocr_lang)
            text = res["text"]
            if skip_refs:
                text = strip_references(text)
            if len(text.split()) < 30:
                problems.append(f"{f.name}: very little readable text. For photos, use a sharp, upright, well-lit picture.")
            out.append((f.name, text))
            info[f.name] = res
        except Exception as e:
            problems.append(f"{f.name}: {e}")
    return out, problems, info


tab1, tab2 = st.tabs(["Module 1: Plagiarism check", "Module 2: Assignment similarity"])

# ---------------- Module 1: one paper against the web ----------------
with tab1:
    st.write("Upload one **.docx** or **.pdf**. Distinctive passages are searched on the web and the matching pages are compared word by word.")
    title1 = st.text_input("Report title", "Independent Similarity Check Report", key="t1")
    up = st.file_uploader("Paper", type=["docx", "pdf", "jpg", "jpeg", "png"], key="up_single")
    c1, c2 = st.columns(2)
    depth = c1.radio("Search depth", ["Thorough: every sentence", "Standard: 30 sentences", "Quick: 12 sentences"],
                     help="Thorough searches every sentence. Sentences already found in a matched source are skipped, "
                          "so copied papers finish sooner. A long paper can take 5 to 10 minutes.")
    max_q = {"T": None, "S": 30, "Q": 12}[depth[0]]
    pages_cap = {"T": 150, "S": 50, "Q": 25}[depth[0]]
    excl = c2.text_input("Ignore web addresses containing (comma separated)",
                         help="For example the journal page where this paper is already published.")
    use_sch1 = st.checkbox("Also search open-access scholarly databases (Europe PMC, OpenAlex)", True, key="sch1",
                           help="Finds open-access journal articles and reads their PDFs. PDFs found in web results are always read.")
    if st.button("Run plagiarism check", type="primary", disabled=up is None):
        files, problems, info1 = read_files([up])
        if info1.get(up.name, {}).get("method") == "ocr":
            st.info(f"Text was read from an image or scan (confidence {info1[up.name]['conf']:.0f}%). Typed text works best.")
        for p in problems:
            st.warning(p)
        if files:
            doc = make_doc(files[0][0], files[0][1], n)
            bar = st.progress(0.0, text="Starting")
            web = check_web(doc, n=n, min_run=min_run, max_queries=max_q, max_pages=pages_cap, search_fn=make_search(use_sch1),
                            exclude_substrings=[x.strip() for x in excl.split(",") if x.strip()],
                            progress=lambda f, m: bar.progress(min(f, 1.0), text=m))
            bar.empty()
            st.session_state["single"] = (doc, web, internal_repetition(doc, n))
    if "single" in st.session_state:
        doc, web, rep = st.session_state["single"]
        lvl = level_for(web["total_pct"], high, moderate)
        m1, m2, m3 = st.columns(3)
        m1.metric("Estimated similarity", f"{web['total_pct']:.1f}%")
        m2.metric("Level", lvl)
        m3.metric("Sources with matches", len(web["sources"]))
        if web["sources"]:
            st.dataframe(pd.DataFrame([{"Match %": round(s["pct"], 1), "Title": s["title"], "Address": s["url"],
                                        "Longest run": s["longest_run"]} for s in web["sources"]]),
                         use_container_width=True, hide_index=True)
        for e in web["errors"][:3]:
            st.caption(f"Note: {e}")
        data = build_single_report(make_meta(title1), doc, web, rep,
                                   f"Phrase length {n} words; minimum run {min_run} words", high, moderate)
        st.download_button("Download report (.docx)", data,
                           file_name=f"Similarity_Report_{doc.name.rsplit('.', 1)[0]}.docx", mime=DOCX)

# ---------------- Module 2: class assignments ----------------
with tab2:
    st.write("Upload every student's submission: **.docx**, **.pdf**, or **.jpg/.png** photos of printed or handwritten pages. "
             "Name each file with the student's name, for example `Rahul Kumar - Assignment 1.pdf` or "
             "`Rahul Kumar - page 1.jpg`. Files with the same student name are joined into one submission. "
             "Names are read from the file names and can be corrected below. "
             "Submissions are compared with each other, and can also be checked for text copied from the web.")
    title2 = st.text_input("Report title", "Assignment Similarity Report", key="t2")
    files = st.file_uploader("Submissions", type=["docx", "pdf", "jpg", "jpeg", "png"], accept_multiple_files=True, key="up_class")
    tmpl = st.file_uploader("Optional: question paper or template to ignore", type=["docx", "pdf", "jpg", "jpeg", "png"], key="up_tmpl")
    tolerant = st.checkbox("Use tolerant matching for photographed or handwritten pages", True,
                           help="Text read from photos contains reading errors, so shorter phrases are matched for those submissions. "
                                "A high result is a strong signal. A low result is less certain.")
    w1, w2 = st.columns(2)
    do_web = w1.checkbox("Also check every submission against the web", True)
    use_sch2 = st.checkbox("Include open-access scholarly databases (Europe PMC, OpenAlex)", True, key="sch2", disabled=not do_web)
    web_q = w2.slider("Passages searched per submission", 3, 15, 6,
                      help="Each submission takes about 10 to 30 seconds. Fewer passages is faster.", disabled=not do_web)
    if files:
        if len(files) < 2:
            st.info("Upload at least two submissions to compare.")
        names = [student_name_from_filename(f.name) for f in files]
        edited = st.data_editor(pd.DataFrame({"File": [f.name for f in files], "Student name": names}),
                                disabled=["File"], hide_index=True, use_container_width=True, key="names")
        if any(f.name.lower().endswith(IMAGE_EXT) for f in files) and not ocr_available():
            st.error("Reading text from images is not available on this server. Add packages.txt to the repository (see README).")
        if st.button("Check submissions", type="primary", disabled=len(files) < 2):
            read, problems, info = read_files(files)
            for p in problems:
                st.warning(p)
            lookup = dict(zip(edited["File"], edited["Student name"]))
            texts, confs, previews = {}, {}, []
            for fn, text in read:
                nm = (lookup.get(fn) or fn).strip()
                texts[nm] = (texts.get(nm, "") + "\n" + text).strip()
                if info[fn]["method"] == "ocr":
                    confs.setdefault(nm, []).append(info[fn]["conf"])
                    previews.append((fn, info[fn]["conf"], text))
            ocr_conf = {nm: sum(c) / len(c) for nm, c in confs.items()}
            docs = [make_doc(nm, t, n, ocr=nm in ocr_conf, conf=ocr_conf.get(nm)) for nm, t in texts.items()]
            tmpl_text = ""
            if tmpl is not None:
                tr, _, _ = read_files([tmpl])
                tmpl_text = tr[0][1] if tr else ""
            ex = excluded_grams(tmpl_text, n)
            tol = None
            if ocr_conf and tolerant:
                n_t, run_t = 4, max(5, min_run - 2)
                for d in docs:
                    d.alt = make_doc(d.name, d.text, n_t)
                tol = {"excluded": excluded_grams(tmpl_text, n_t), "min_run": run_t}
            with st.spinner("Comparing every pair..."):
                pairs, rows = analyse_class(docs, ex, min_run, high, moderate, tol=tol)
            web_by_name = {}
            if do_web:
                bar = st.progress(0.0, text="Checking the web")
                for i, d in enumerate(docs):
                    if d.ocr and (d.conf or 0) < 60:        # poorly read text makes useless web searches
                        continue
                    web_by_name[d.name] = check_web(
                        d, n=n, min_run=min_run, max_queries=web_q, excluded_grams=ex, max_pages=10, delay=0.7,
                        search_fn=make_search(use_sch2),
                        progress=lambda f, m, i=i, d=d: bar.progress(min((i + f) / len(docs), 1.0), text=f"{d.name}: {m}"))
                bar.empty()
                rows = combine_web(rows, web_by_name, high, moderate)
            st.session_state["class"] = (pairs, rows, len(docs), do_web, bool(tmpl))
            st.session_state["class_ocr"] = (sorted(ocr_conf.items()), previews)
    if "class" in st.session_state:
        pairs, rows, ndocs, web_run, used_tmpl = st.session_state["class"]
        ocr_notes, previews = st.session_state.get("class_ocr", ([], []))
        if previews:
            with st.expander(f"Text read from {len(previews)} image/scan file(s). Check it looks right."):
                for fn, c, t in previews:
                    st.markdown(f"**{fn}**: reading confidence {c:.0f}%, {len(t.split())} words")
                    st.text(t[:800] + (" ..." if len(t) > 800 else ""))
        st.subheader("Students ranked by similarity")
        table = []
        for i, r in enumerate(rows):
            row = {"Rank": i + 1, "Student": r["student"], "Highest similarity %": round(r.get("overall", r["max"]), 1),
                   "Closest classmate": f"{r['partner']} ({r['max']:.1f}%)" if r["max"] > 0 else "-"}
            if web_run:
                row["Web match %"] = round(r["web_pct"], 1) if r.get("web_pct") is not None else None
                row["Flag"] = r["flag"]
            row["Level"] = r["level"]
            table.append(row)
        df = pd.DataFrame(table)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.subheader("Top classmate pairs")
        st.dataframe(pd.DataFrame([{"Student A": p.a, "Student B": p.b, "A in B %": round(p.cov_a, 1),
                                    "B in A %": round(p.cov_b, 1), "Longest run (words)": p.longest_run}
                                   for p in pairs[:20]]), use_container_width=True, hide_index=True)
        note = f"Phrase length {n} words; minimum run {min_run} words" + ("; question text ignored" if used_tmpl else "")
        data = build_class_report(make_meta(title2), rows, pairs, note, ndocs, web_run=web_run,
                                  high=high, moderate=moderate, ocr_notes=ocr_notes)
        st.download_button("Download class report (.docx)", data, file_name="Assignment_Similarity_Report.docx", mime=DOCX)
        st.download_button("Download ranking (.csv)", df.to_csv(index=False).encode("utf-8"),
                           file_name="similarity_ranking.csv", mime="text/csv")

st.divider()
st.caption("Similarity is not proof of plagiarism. Review every flagged match before drawing conclusions.")
