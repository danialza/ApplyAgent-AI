"""Render a tailored LaTeX CV from the user's CV library.

Pipeline:

  1. Pull the `CVLibrary` row.
  2. If a JD is provided, rank every project / experience / publication /
     certification by overlap between its `tags` (or highlight text) and
     the JD's required + preferred skills.
  3. Truncate each section to a configurable cap.
  4. Bold any matched-skill mention inside highlight bullets.
  5. Fill a Jinja2 template that mirrors the user's exact LaTeX layout
     (charter font, 0.5 cm margins, hrulefill section rules, etc.).
  6. Optionally compile to PDF via `tectonic` (single-binary LaTeX) and
     return base64. Falls back to LaTeX-only when no compiler is found.

Everything is deterministic. No LLM calls — wording is preserved verbatim
from the library; the only mutation is **bolding** matched skill tokens.
"""
from __future__ import annotations

import base64
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from jinja2 import Environment, StrictUndefined

from app.models.schemas import CVLibraryOut, JobParsed
from app.services.synonyms import _GROUPS as _SYNONYM_GROUPS  # type: ignore  # noqa: PLC2701
from app.services.synonyms import canonical, group_key

logger = logging.getLogger("ai_job_cv_matcher.cv_renderer")


# ---------- LaTeX escaping ----------

# Single-pass mapping so we don't re-escape characters introduced by an
# earlier replacement (e.g. the `{}` inside `\textbackslash{}`).
_LATEX_ESCAPE_MAP: dict[str, str] = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_LATEX_ESCAPE_RE = re.compile("|".join(re.escape(c) for c in _LATEX_ESCAPE_MAP))


def latex_escape(value: str) -> str:
    """Escape a user-supplied string for safe inclusion in LaTeX.

    Single-pass regex substitution — never re-processes its own output,
    so backslashes don't end up double-escaped via the curly-brace pass.
    """
    if value is None:
        return ""
    return _LATEX_ESCAPE_RE.sub(lambda m: _LATEX_ESCAPE_MAP[m.group(0)], str(value))


def _bold_matches(text: str, terms: list[str]) -> str:
    """Wrap each occurrence of any term in `\textbf{…}` (case-insensitive).

    Operates on **already escaped** text. Whole-word matching with full
    boundaries on both sides — prevents `ts` from matching inside
    `consultants`, `ml` from matching inside `family`, etc. Tokens with
    `+` or `#` (e.g. `C++`) get the same boundary treatment.
    """
    if not terms or not text:
        return text
    # Filter out ultra-short aliases — too noisy regardless of boundary.
    sorted_terms = sorted({t for t in terms if t and len(t) >= 3}, key=len, reverse=True)
    for term in sorted_terms:
        escaped_term = latex_escape(term)
        if not escaped_term:
            continue
        # Right boundary blocks alphanumerics; left lookbehind blocks
        # both alphanumerics AND the inside of an existing \textbf{…}.
        pattern = re.compile(
            r"(?<!\\textbf\{)(?<![A-Za-z0-9_])"
            + re.escape(escaped_term)
            + r"(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )
        text = pattern.sub(
            lambda m: r"\textbf{" + m.group(0) + r"}",
            text,
        )
    return text


# ---------- JD relevance ----------

def _jd_terms(job: JobParsed | None) -> list[str]:
    """Canonical tokens from the JD. Used for ranking + display in
    `matched_skills`. The bolder uses `_bold_terms` to also match aliases."""
    if job is None:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for source in (job.required_skills, job.preferred_skills, job.technologies):
        for s in source or []:
            disp = canonical(s) or s
            key = (disp or "").lower()
            if key and key not in seen:
                seen.add(key)
                out.append(disp)
    return out


# Quantified-impact metrics inside bullets. Recruiters scan numbers
# first — career-ops bolds these to anchor the eye. Conservative
# patterns only, never bold a bare integer (years count as "1") to
# avoid noise.
_METRIC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b\d+(?:\.\d+)?\\?%"),                       # 30%, 12.5%
    re.compile(r"\b\d+(?:\.\d+)?x\b", re.IGNORECASE),         # 10x, 2.5x
    re.compile(r"[\$€£]\s?\d+(?:\.\d+)?[KMB]?\b"),            # $10K, €1.2M
    re.compile(r"\b\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?\b"),     # GPA 4.42/5.00
    re.compile(r"\b\d+\+?\s*years?\b", re.IGNORECASE),        # 5+ years
    re.compile(r"\b\d+\+?\s*(?:users|requests?/s|req/s|MAU|DAU|QPS|RPS|participants)\b", re.IGNORECASE),
    re.compile(r"\b\d+\s*(?:ms|s|GB|TB|MB)\b", re.IGNORECASE),
]


def _bold_metrics(text: str) -> str:
    """Wrap each quantified metric in `\\textbf{…}`. Skips matches that
    already sit inside an existing `\\textbf{…}` so we don't nest."""
    def _replace(m: re.Match[str]) -> str:
        s, e = m.span()
        # Look at a small window around the match to detect we're not
        # already inside \textbf{…}.
        window_start = max(0, s - 12)
        if r"\textbf{" in text[window_start:s]:
            return m.group(0)
        return r"\textbf{" + m.group(0) + r"}"

    for pat in _METRIC_PATTERNS:
        text = pat.sub(_replace, text)
    return text


def _bold_terms(jd_canonicals: list[str]) -> list[str]:
    """Expand canonical JD skills to every alias the synonym dictionary knows.

    `RAG` and `Retrieval Augmented Generation` should both be bolded if
    either form appears in the bullet text. Longer forms first so the
    regex matches them before they get partially captured by shorter ones.
    """
    if not jd_canonicals:
        return []
    canonical_set = {c.lower() for c in jd_canonicals}
    expanded: set[str] = set(jd_canonicals)
    for canonical_name, aliases in _SYNONYM_GROUPS:
        if canonical_name.lower() in canonical_set:
            expanded.add(canonical_name)
            for alias in aliases:
                if len(alias) >= 2:
                    expanded.add(alias)
    return sorted(expanded, key=len, reverse=True)


def _entry_score(entry_terms: Iterable[str], jd_groups: set[str]) -> int:
    """How many JD synonym groups this entry's tags / text covers."""
    if not jd_groups:
        return 0
    hits: set[str] = set()
    for t in entry_terms:
        if not t:
            continue
        gk = group_key(t)
        if gk in jd_groups:
            hits.add(gk)
    return len(hits)


def _rank_entries(
    items: list[Any],
    jd_terms: list[str],
    extract_terms: callable,
) -> list[Any]:
    """Stable sort: highest JD overlap first, ties keep original order.

    `extract_terms(item)` returns the strings to score against (tags +
    any prose bullets we want to consider).
    """
    if not items:
        return []
    if not jd_terms:
        return list(items)
    jd_groups = {group_key(t) for t in jd_terms if t}
    decorated = [
        (-_entry_score(extract_terms(item), jd_groups), idx, item)
        for idx, item in enumerate(items)
    ]
    decorated.sort()
    return [item for _, _, item in decorated]


# ---------- Template ----------

# Custom delimiters so Jinja doesn't fight LaTeX `{` / `}`.
_jinja_env = Environment(
    block_start_string="<%",
    block_end_string="%>",
    variable_start_string="<<",
    variable_end_string=">>",
    comment_start_string="<#",
    comment_end_string="#>",
    trim_blocks=True,
    lstrip_blocks=True,
    autoescape=False,
    undefined=StrictUndefined,
)
_jinja_env.filters["latex"] = latex_escape


_LATEX_TEMPLATE = r"""
\documentclass[10pt, letterpaper]{article}

\usepackage[
    ignoreheadfoot,
    top=0.5cm,
    bottom=0.5cm,
    left=1cm,
    right=1cm,
    footskip=0.8cm,
]{geometry}
\usepackage{titlesec}
\usepackage{enumitem}
\usepackage[dvipsnames]{xcolor}
\definecolor{primaryColor}{RGB}{0,0,0}
\usepackage[
    pdftitle={<< header.name | latex >> - CV},
    pdfauthor={<< header.name | latex >>},
    colorlinks=true,
    urlcolor=primaryColor
]{hyperref}
\usepackage{changepage}
\usepackage{iftex}
\usepackage{needspace}

\ifPDFTeX
    \input{glyphtounicode}
    \pdfgentounicode=1
    \usepackage[T1]{fontenc}
    \usepackage[utf8]{inputenc}
    \usepackage{lmodern}
\fi

\usepackage{charter}

\pagestyle{empty}
\setcounter{secnumdepth}{0}
\setlength{\parindent}{0pt}
\setlength{\topskip}{0pt}
\pagenumbering{gobble}
\raggedright

\titleformat{\section}{\needspace{4\baselineskip}\bfseries\large}{}{0pt}{}
\titlespacing{\section}{-1pt}{0.18cm}{0.10cm}
\renewcommand\labelitemi{$\vcenter{\hbox{\small$\bullet$}}$}

\newenvironment{highlights}{
    \begin{itemize}[
        topsep=0.03cm,
        parsep=0.03cm,
        partopsep=0pt,
        itemsep=0pt,
        leftmargin=12pt
    ]
}{
    \end{itemize}
}

\newenvironment{onecolentry}{
    \begin{adjustwidth}{0cm}{0cm}
}{
    \end{adjustwidth}
}

\begin{document}

\begin{center}
    {\fontsize{18pt}{18pt}\selectfont << header.name | latex >>}

    \vspace{3pt}

    << header_line >>
\end{center}

\vspace{0.08cm}

<% if summary %>
\section{Professional Summary \hrulefill}
\begin{onecolentry}
<< summary >>
\end{onecolentry}

<% endif %>
<% if core_competencies %>
\section{Core Competencies \hrulefill}
\begin{onecolentry}
<< core_competencies >>
\end{onecolentry}

<% endif %>
<% if skills_groups %>
\section{Technical Skills \hrulefill}
\begin{onecolentry}
\begin{highlights}
<% for g in skills_groups %>
    \item \textbf{<< g.label | latex >>:} << g.items_rendered >>
<% endfor %>
\end{highlights}
\end{onecolentry}

<% endif %>
<% if education %>
\section{Education \hrulefill}
<% for e in education %>
\begin{onecolentry}
\textbf{<< e.institution | latex >>}, << e.degree | latex >> \hfill << e.period | latex >>
<% if e.highlights %>
\begin{highlights}
<% for h in e.highlights %>
    \item << h >>
<% endfor %>
\end{highlights}
<% endif %>
\end{onecolentry}

<% endfor %>
<% endif %>
<% if selected_projects %>
\section{Selected Projects \hrulefill}

<% for p in selected_projects %>
\begin{onecolentry}
\textbf{<< p.title | latex >>}<% if p.period %> \hfill << p.period | latex >><% endif %>
<% if p.highlights %>
\begin{highlights}
<% for h in p.highlights %>
    \item << h >>
<% endfor %>
\end{highlights}
<% endif %>
\end{onecolentry}

<% endfor %>
<% endif %>
<% if additional_projects %>
\section{Additional Projects \hrulefill}

<% for p in additional_projects %>
\begin{onecolentry}
\textbf{<< p.title | latex >>}<% if p.period %> \hfill << p.period | latex >><% endif %>
<% if p.highlights %>
\begin{highlights}
<% for h in p.highlights %>
    \item << h >>
<% endfor %>
\end{highlights}
<% endif %>
\end{onecolentry}

<% endfor %>
<% endif %>
<% if experience %>
\section{Professional Experience \hrulefill}

<% for x in experience %>
\begin{onecolentry}
\textbf{<< x.title | latex >>}<% if x.company %>, << x.company | latex >><% endif %> \hfill << x.period | latex >>
<% if x.highlights %>
\begin{highlights}
<% for h in x.highlights %>
    \item << h >>
<% endfor %>
\end{highlights}
<% endif %>
\end{onecolentry}

<% endfor %>
<% endif %>
<% if certifications %>
\section{Certifications \hrulefill}
\begin{onecolentry}
\begin{highlights}
<% for c in certifications %>
    \item << c.line >>
<% endfor %>
\end{highlights}
\end{onecolentry}

<% endif %>
<% if publications %>
\section{Publications \hrulefill}
\begin{onecolentry}
\begin{highlights}
<% for p in publications %>
    \item << p.line >>
<% endfor %>
\end{highlights}
\end{onecolentry}

<% endif %>
<% if languages %>
\section{Languages \hrulefill}
\begin{onecolentry}
\begin{highlights}
    \item << languages_line >>
\end{highlights}
\end{onecolentry}

<% endif %>
\end{document}
""".strip()


# ---------- Header line composition ----------

def _header_line(header) -> str:
    """Build the contact line under the name. Mixes plain text + hyperlinks."""
    parts: list[str] = []
    if header.location:
        parts.append(latex_escape(header.location))
    if header.email:
        parts.append(rf"\href{{mailto:{header.email}}}{{{latex_escape(header.email)}}}")
    if header.phone:
        parts.append(latex_escape(header.phone))
    if header.website:
        url = header.website
        display = url.replace("https://", "").replace("http://", "")
        parts.append(rf"\href{{{url}}}{{{latex_escape(display)}}}")
    if header.linkedin:
        url = header.linkedin
        display = url.replace("https://", "").replace("http://", "")
        parts.append(rf"\href{{{url}}}{{{latex_escape(display)}}}")
    if header.github:
        url = header.github
        display = url.replace("https://", "").replace("http://", "")
        parts.append(rf"\href{{{url}}}{{{latex_escape(display)}}}")
    return " \\quad | \\quad\n    ".join(parts)


# ---------- Public API ----------

@dataclass
class RenderResult:
    latex: str
    pdf_b64: str = ""
    compiled: bool = False
    compile_error: str = ""
    sections_chosen: dict[str, list[str]] = field(default_factory=dict)
    matched_skills: list[str] = field(default_factory=list)


def render_cv(
    library: CVLibraryOut,
    *,
    job: JobParsed | None = None,
    max_selected_projects: int = 4,
    max_additional_projects: int = 3,
    max_experience: int = 4,
    compile_pdf: bool = False,
    min_competency_rating: int = 3,
    core_competencies_override: list[str] | None = None,
) -> RenderResult:
    """Render a tailored CV. See module docstring for the pipeline."""
    jd_terms = _jd_terms(job)
    jd_groups = {group_key(t) for t in jd_terms if t}
    bold_terms = _bold_terms(jd_terms)

    # ---- Rank and cap each section.
    selected = _rank_entries(
        list(library.selected_projects),
        jd_terms,
        lambda p: list(p.tags or []) + list(p.highlights or []),
    )[:max_selected_projects] if max_selected_projects else []

    additional = _rank_entries(
        list(library.additional_projects),
        jd_terms,
        lambda p: list(p.tags or []) + list(p.highlights or []),
    )[:max_additional_projects] if max_additional_projects else []

    experience = _rank_entries(
        list(library.experience),
        jd_terms,
        lambda x: list(x.tags or []) + list(x.highlights or []),
    )[:max_experience] if max_experience else []

    # Certifications + publications: rank by tag overlap; keep all.
    certifications = _rank_entries(
        list(library.certifications),
        jd_terms,
        lambda c: list(c.tags or []) + [c.name, c.issuer],
    )
    publications = _rank_entries(
        list(library.publications),
        jd_terms,
        lambda p: list(p.tags or []) + [p.title, p.venue],
    )

    # ---- Skills groups: reorder so categories with JD-matched items come first.
    def group_score(g) -> int:
        return _entry_score(g.items, jd_groups)

    skills_groups_sorted = sorted(
        list(library.skills_groups), key=lambda g: -group_score(g),
    )

    # ---- Bold + escape all bullet text in one pass.
    def render_bullet(text: str) -> str:
        # Order matters: JD-skill bolding runs against the escaped
        # text first, then metric bolding catches quantified impact
        # (5+ years, 30%, $1M, 10x). _bold_metrics is nest-safe so
        # it won't double-wrap a number that happens to be inside a
        # skill term that was already bolded.
        escaped = latex_escape(text)
        escaped = _bold_matches(escaped, bold_terms)
        return _bold_metrics(escaped)

    def render_skill_items(items: list[str]) -> str:
        # Skills line: bold matched ones; leave separators plain.
        rendered = [render_bullet(s) for s in items]
        return ", ".join(rendered)

    def render_education(entries):
        out = []
        for e in entries:
            out.append({
                "institution": e.institution,
                "degree": e.degree,
                "period": e.period,
                "highlights": [render_bullet(h) for h in (e.highlights or [])],
            })
        return out

    def render_projects(entries):
        out = []
        for p in entries:
            out.append({
                "title": p.title,
                "period": p.period,
                "highlights": [render_bullet(h) for h in (p.highlights or [])],
            })
        return out

    def render_experience(entries):
        out = []
        for x in entries:
            out.append({
                "title": x.title,
                "company": x.company,
                "period": x.period,
                "highlights": [render_bullet(h) for h in (x.highlights or [])],
            })
        return out

    def render_certifications(entries):
        out = []
        for c in entries:
            issuer = latex_escape(c.issuer).strip()
            # Career-ops convention: bold the credential NAME, leave
            # issuer plain. Recruiters search for cert names
            # ("Azure AI Fundamentals", "Deep Learning Specialisation"),
            # not the issuing org.
            name_raw = latex_escape(c.name).strip()
            name_bold = rf"\textbf{{{name_raw}}}"
            line = (issuer + ": " + name_bold) if issuer else name_bold
            out.append({"line": line})
        return out

    def render_publications(entries):
        out = []
        for p in entries:
            status = latex_escape(p.status).strip()
            title_render = render_bullet(p.title)
            venue = latex_escape(p.venue).strip()
            tail = " — " + venue if venue else ""
            line = (rf"\textbf{{{status}:}} " + title_render + tail) if status else (title_render + tail)
            out.append({"line": line})
        return out

    # Languages line — bold each language name. Source entries arrive
    # in one of two shapes after parsing:
    #   "English: Native"            → bold "English"
    #   "Native language(s): Farsi"  → bold "Native language(s)"
    #   plain "English"              → bold the whole token
    def _render_lang(raw: str) -> str:
        s = raw.strip()
        if ":" in s:
            name, _, rest = s.partition(":")
            return rf"\textbf{{{latex_escape(name.strip())}}}: {latex_escape(rest.strip())}"
        return rf"\textbf{{{latex_escape(s)}}}"

    languages_line = ", ".join(_render_lang(l) for l in (library.languages or []))

    skills_groups_payload = [
        {"label": g.label, "items_rendered": render_skill_items(g.items)}
        for g in skills_groups_sorted
    ]

    # ---- Core Competencies row (career-ops style).
    # Pick up to 8 JD keywords the CV's skills actually back. Output as a
    # comma-separated bold-chip line inside the existing onecolentry
    # environment so it matches Danial's template aesthetic.
    cv_skill_keys: set[str] = set()
    for g in library.skills_groups or []:
        for s in g.items or []:
            cv_skill_keys.add(group_key(s))
    # Stretch competencies — user-curated, rating-gated. They count as
    # "skills the CV backs" only when the candidate self-rated them at
    # or above the requested threshold AND the JD asks for them.
    comp_by_key: dict[str, "CompetencyEntry"] = {}  # type: ignore[name-defined]
    for c in getattr(library, "core_competencies", None) or []:
        if int(getattr(c, "rating", 0) or 0) < min_competency_rating:
            continue
        comp_by_key[group_key(c.name)] = c
        cv_skill_keys.add(group_key(c.name))
    # LLM-synthesised compound phrases (career-ops Core Competencies
    # row) win when the route passes them in. Falls back to the simple
    # JD ∩ CV-skills intersection — which only catches single-token
    # matches like "Python", "C++".
    if core_competencies_override:
        grounded = [p for p in core_competencies_override if p][:8]
    else:
        grounded = [t for t in jd_terms if group_key(t) in cv_skill_keys][:8]
    if grounded:
        # Render as bold-comma-separated terms; LaTeX escape each term.
        core_competencies = " \\quad{}|\\quad{} ".join(
            f"\\textbf{{{latex_escape(t)}}}" for t in grounded
        )
    else:
        core_competencies = ""

    # ---- Render template.
    template = _jinja_env.from_string(_LATEX_TEMPLATE)
    latex = template.render(
        header=library.header,
        header_line=_header_line(library.header),
        summary=render_bullet(library.summary) if library.summary else "",
        core_competencies=core_competencies,
        skills_groups=skills_groups_payload,
        education=render_education(library.education),
        selected_projects=render_projects(selected),
        additional_projects=render_projects(additional),
        experience=render_experience(experience),
        certifications=render_certifications(certifications),
        publications=render_publications(publications),
        languages=library.languages,
        languages_line=languages_line,
    )

    sections_chosen = {
        "selected_projects": [p.title for p in selected],
        "additional_projects": [p.title for p in additional],
        "experience": [x.title for x in experience],
        "publications": [p.title for p in publications],
        "certifications": [c.name for c in certifications],
    }
    matched_skills = jd_terms

    result = RenderResult(
        latex=latex,
        sections_chosen=sections_chosen,
        matched_skills=matched_skills,
    )

    if compile_pdf:
        pdf_bytes, err = _compile_pdf(latex)
        if pdf_bytes:
            result.pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")
            result.compiled = True
        else:
            result.compile_error = err
    return result


# ---------- PDF compilation ----------

def _compile_pdf(latex: str) -> tuple[bytes | None, str]:
    """Compile LaTeX → PDF using `tectonic` if available, else `pdflatex`.

    Returns (pdf_bytes_or_None, error_message). Never raises.
    """
    compiler: list[str]
    if shutil.which("tectonic"):
        compiler = ["tectonic", "--outdir"]  # outdir is appended below
        kind = "tectonic"
    elif shutil.which("pdflatex"):
        compiler = ["pdflatex", "-interaction=nonstopmode", "-output-directory"]
        kind = "pdflatex"
    else:
        return None, (
            "No LaTeX compiler found. Install `tectonic` (single-binary, "
            "recommended): `brew install tectonic` on macOS."
        )

    with tempfile.TemporaryDirectory(prefix="cv_render_") as tmp:
        tmp_path = Path(tmp)
        tex_path = tmp_path / "cv.tex"
        tex_path.write_text(latex, encoding="utf-8")

        try:
            if kind == "tectonic":
                proc = subprocess.run(
                    ["tectonic", "--outdir", str(tmp_path), "--keep-logs",
                     "--chatter", "minimal", str(tex_path)],
                    capture_output=True, text=True, timeout=60,
                )
            else:
                # pdflatex twice for cross-references; CVs rarely need it,
                # but it's cheap and safer.
                for _ in range(2):
                    proc = subprocess.run(
                        ["pdflatex", "-interaction=nonstopmode",
                         "-output-directory", str(tmp_path), str(tex_path)],
                        capture_output=True, text=True, timeout=60,
                    )
        except subprocess.TimeoutExpired:
            return None, "LaTeX compilation timed out (>60s)."
        except Exception as exc:  # noqa: BLE001
            return None, f"LaTeX compilation failed to launch: {exc}"

        pdf_path = tmp_path / "cv.pdf"
        if proc.returncode != 0 or not pdf_path.exists():
            log_excerpt = (proc.stdout or "")[-2000:] + "\n---\n" + (proc.stderr or "")[-1000:]
            return None, f"LaTeX compile failed (rc={proc.returncode}): {log_excerpt[-1000:]}"

        return pdf_path.read_bytes(), ""
