"""Insert two synthetic demo CVs into the backend DB.

Useful for first-time setups, screenshots, and demos where you don't want
to upload real CVs. Idempotent: re-running won't insert duplicates.

    cd backend
    python -m scripts.seed_demo_data
"""
from __future__ import annotations

from app.db.database import SessionLocal, init_db
from app.models.db_models import CV
from app.services.cv_parser import parse_cv_text


_DEMO_CVS: list[tuple[str, str]] = [
    (
        "alice_strong.txt",
        """\
Alice Strong
San Francisco, CA | alice@example.com | +1 (415) 555-0199
linkedin.com/in/alicestrong | github.com/alicestrong | alicestrong.dev

PROFESSIONAL SUMMARY
Senior AI engineer with 6 years building production RAG systems and
distributed services in Python.

TECHNICAL SKILLS
Python, FastAPI, Machine Learning, Deep Learning, NLP, RAG, LLM, FAISS,
PyTorch, Hugging Face, Docker, Kubernetes, AWS, TypeScript, Next.js

WORK EXPERIENCE
Senior AI Engineer — Acme Corp (2019 - 2024)
- Built retrieval-augmented generation pipelines on top of FAISS and LangChain.
- Shipped FastAPI services on AWS in Docker / Kubernetes.
- Mentored 3 junior engineers and led the inference infra workstream.

EDUCATION
M.Sc. Computer Science — UC Berkeley (2017 - 2019)

PROJECTS
- OpenObserve: open-source observability dashboard, 1.2k GitHub stars.

CERTIFICATIONS
- AWS Certified Solutions Architect — Associate (2022)

LANGUAGES
English (Native), Spanish (Conversational)
""",
    ),
    (
        "carol_web.txt",
        """\
Carol Web
Remote (UK) | carol@example.com | +44 20 7946 0958
linkedin.com/in/carolweb | github.com/carolweb

ABOUT
WordPress developer with 5 years building WooCommerce stores and
content sites with strong SEO performance.

SKILLS
WordPress, WooCommerce, PHP, JavaScript, HTML, CSS, SEO, Google Analytics,
Shopify

EXPERIENCE
WordPress Developer — Pixel & Co (2019 - 2024)
- Built and maintained 30+ WooCommerce stores end-to-end.
- Improved organic traffic by an average of 40% via on-page SEO and
  Google Analytics tracking improvements.

EDUCATION
Diploma in Web Development — General Assembly (2018)

LANGUAGES
English (Native), French (Conversational)
""",
    ),
]


def seed() -> None:
    init_db()
    inserted = 0
    skipped = 0
    with SessionLocal() as db:
        for filename, text in _DEMO_CVS:
            existing = db.query(CV).filter(CV.filename == filename).first()
            if existing:
                skipped += 1
                continue
            parsed = parse_cv_text(text)
            cv = CV(
                filename=filename,
                name=parsed.name,
                summary=parsed.summary,
                skills=parsed.skills,
                education=parsed.education,
                experience=parsed.experience,
                projects=parsed.projects,
                certifications=parsed.certifications,
                languages=parsed.languages,
                email=parsed.email,
                phone=parsed.phone,
                linkedin=parsed.linkedin,
                github=parsed.github,
                portfolio=parsed.portfolio,
                raw_text=text,
            )
            db.add(cv)
            inserted += 1
        db.commit()
    print(f"Seeded {inserted} demo CV(s); {skipped} already present.")


if __name__ == "__main__":
    seed()
