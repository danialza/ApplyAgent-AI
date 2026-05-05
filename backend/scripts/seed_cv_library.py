"""Seed the CV library with Danial's actual CV content.

Run once after first boot:
    cd backend && python -m scripts.seed_cv_library

Idempotent: re-running replaces the existing library row. Edit this file
or call PUT /api/cv/library from the UI to keep updating the source of
truth.
"""
from __future__ import annotations

from datetime import datetime

from app.db.database import SessionLocal, init_db
from app.models.db_models import CVLibrary


# Source of truth lifted directly from the user's LaTeX CV.
LIBRARY = {
    "header": {
        "name": "Danial Zafaranchizadeh Moghaddam",
        "location": "London, United Kingdom",
        "email": "danial.za@outlook.com",
        "phone": "07304 152749",
        "website": "https://danielz.co.uk",
        "linkedin": "",
        "github": "https://github.com/danialza",
    },
    "summary": (
        "Applied AI engineer with an MSc in Artificial Intelligence & Robotics "
        "(Distinction, GPA 4.42/5.00), with hands-on experience building "
        "production-minded AI features, LLM systems, RAG pipelines, "
        "MCP-style integrations, and backend AI services for real business "
        "workflows. Strong practical background in Python, FastAPI, REST APIs, "
        "vector databases, structured extraction, evaluation-aware AI design, "
        "and end-to-end delivery from concept to usable product. I enjoy "
        "building reliable AI capabilities that improve workflows, surface "
        "insights, and create measurable operational value."
    ),
    "skills_groups": [
        {
            "label": "Languages",
            "items": ["Python", "SQL", "JavaScript", "PHP", "Dart"],
        },
        {
            "label": "LLM / Applied AI",
            "items": [
                "prompt engineering", "LLM workflows",
                "retrieval-augmented generation (RAG)", "structured prompting",
                "tool-calling patterns", "agentic workflows",
            ],
        },
        {
            "label": "Integration Layers",
            "items": [
                "REST API design", "FastAPI", "Flask", "API integrations",
                "backend AI services", "MCP-style concepts", "orchestration layers",
            ],
        },
        {
            "label": "Retrieval / Knowledge Systems",
            "items": [
                "Qdrant", "FAISS", "embeddings", "semantic search",
                "vector databases", "document ingestion pipelines",
                "grounded response generation",
            ],
        },
        {
            "label": "Evaluation / Quality",
            "items": [
                "explainable scoring", "evidence extraction",
                "evaluation-aware workflow design", "schema validation",
                "iterative improvement", "reliable output structure",
            ],
        },
        {
            "label": "Data / Engineering",
            "items": [
                "Pandas", "NumPy", "SQLite", "PostgreSQL", "Redis",
                "structured and unstructured data processing",
            ],
        },
        {
            "label": "Deployment / Delivery",
            "items": [
                "Docker", "Git", "Linux/Ubuntu",
                "reproducible prototype development",
                "testing-aware implementation", "workflow automation",
            ],
        },
    ],
    "education": [
        {
            "institution": "University of Hertfordshire",
            "degree": "MSc in Artificial Intelligence & Robotics",
            "period": "2025--2026",
            "highlights": [
                "Distinction, overall GPA: 4.42/5.00",
                "Focused on artificial intelligence, machine learning, robotics, "
                "reinforcement learning, and practical system implementation.",
            ],
        },
    ],
    "selected_projects": [
        {
            "title": "AI Job-CV Matching Agent",
            "period": "2026",
            "tags": [
                "Python", "FastAPI", "FAISS", "RAG", "embeddings",
                "sentence-transformers", "SQLite", "Docker", "Next.js",
                "LLM", "structured extraction", "Pydantic",
            ],
            "highlights": [
                "Built an end-to-end explainable AI system that ingests CVs and "
                "job descriptions, ranks candidate fit, and shows transparent "
                "score breakdowns.",
                "Implemented structured parsing, hard-constraint checks, "
                "semantic search over embeddings, rule-based scoring, and an "
                "optional LLM extraction layer.",
                "Built retrieval and evaluation-oriented workflows using "
                "FastAPI, FAISS, sentence-transformers, SQLite, Docker, and Next.js.",
                "Focused on reusable AI components, production-minded workflow "
                "design, and evidence-backed outputs for decision support.",
            ],
        },
        {
            "title": "TalkingHeadAI",
            "period": "2026",
            "tags": [
                "Qdrant", "PostgreSQL", "Redis", "RAG", "embeddings",
                "real-time", "memory", "LLM", "orchestration",
            ],
            "highlights": [
                "Built a real-time conversational AI platform with "
                "mentor-approved answers, RAG-generated responses, long-term "
                "user memory, and a mentor dashboard.",
                "Designed knowledge retrieval and routing logic using Qdrant, "
                "PostgreSQL, Redis, embeddings, and orchestration of multiple "
                "AI services.",
                "Implemented memory-aware workflows, retrieval pipelines, "
                "backend APIs, and practical internal review loops for quality "
                "improvement and knowledge reuse.",
            ],
        },
        {
            "title": "NSP AI Enquiry Workflow",
            "period": "2026",
            "tags": [
                "Python", "LLM", "prompt engineering", "structured extraction",
                "JSON", "schema validation", "summarisation", "automation",
            ],
            "highlights": [
                "Built an AI workflow that converts unstructured customer "
                "enquiry emails into structured, workflow-ready JSON outputs.",
                "Used Python, LLM APIs, prompt engineering, schema-driven "
                "extraction, and summarisation to support downstream automation "
                "and faster decision-making.",
                "Focused on reliable output structure, practical integration, "
                "and measurable operational usefulness.",
            ],
        },
        {
            "title": "ParsaVision / GorillaZone AI Assistant",
            "period": "2025--2026",
            "tags": [
                "FastAPI", "Qdrant", "RAG", "embeddings",
                "knowledge retrieval", "context-aware", "LLM",
            ],
            "highlights": [
                "Worked on an AI assistant system using FastAPI, Qdrant, and "
                "retrieval-oriented design across multiple business and "
                "support data sources.",
                "Built knowledge-grounded RAG workflows over website content, "
                "support data, and connected sources to create reliable, "
                "context-aware AI responses.",
                "Focused on practical business integration, reusable AI "
                "workflow design, and iterative improvement of system behaviour.",
            ],
        },
        {
            "title": "LLM / RAG / Agentic AI Systems",
            "period": "2025--2026",
            "tags": [
                "Python", "vector databases", "agentic workflows",
                "prompt engineering", "tool-calling", "LLM", "RAG",
            ],
            "highlights": [
                "Built practical AI systems using Python, vector databases, "
                "and multi-step workflow design.",
                "Worked on prompt engineering, tool-connected workflows, "
                "grounded response generation, and backend services that "
                "transform unstructured data into decision-ready outputs.",
            ],
        },
    ],
    "additional_projects": [
        {
            "title": "CNN-Based Persian Digit Recognition (PyTorch)",
            "period": "2025",
            "tags": ["PyTorch", "computer vision", "CNN", "image classification"],
            "highlights": [
                "Built and evaluated a CNN-based image classification pipeline "
                "using PyTorch.",
                "Worked across dataset preparation, training, validation, and "
                "performance analysis on real image data.",
            ],
        },
        {
            "title": "From Prompt to Agent Workshop -- Organizer and Speaker",
            "period": "April 2026",
            "tags": [
                "LLM", "prompt engineering", "RAG", "MCP", "teaching",
                "agentic workflows",
            ],
            "highlights": [
                "Helped organise and deliver a hands-on AI workshop for "
                "approximately 210 participants at the University of Hertfordshire.",
                "Supported practical teaching around LLM API calls, prompt "
                "engineering, retrieval workflows, MCP-style concepts, and "
                "applied AI prototyping.",
            ],
        },
    ],
    "experience": [
        {
            "title": "Co-Founder -- Systems & Technical Lead",
            "company": "Karkia Pardazesh Firouzeh",
            "period": "Oct 2017--Dec 2024",
            "tags": [
                "Python", "APIs", "automation", "backend", "integrations",
                "workflow design", "leadership",
            ],
            "highlights": [
                "Led the design and delivery of software systems, backend "
                "integrations, automation workflows, and technical solutions "
                "for business needs.",
                "Worked across Python-based tools, APIs, internal process "
                "improvement, and digital product development, with strong "
                "ownership from requirements gathering to implementation and "
                "delivery.",
                "Built practical experience in translating operational pain "
                "points into usable technical systems, workflow improvements, "
                "and automation-oriented solutions.",
            ],
        },
        {
            "title": "Senior Systems Developer",
            "company": "Green Wing Co. W.L.L",
            "period": "Jan 2020--Apr 2023",
            "tags": [
                "production", "integrations", "backend", "scalability",
                "operational continuity",
            ],
            "highlights": [
                "Worked on production platforms, integrations, and "
                "backend-connected systems in business environments.",
                "Supported stable delivery, operational continuity, and "
                "scalable digital implementation.",
            ],
        },
    ],
    "publications": [
        {
            "status": "Under Submission",
            "title": (
                "Reinforcement Learning-Based Constrained Control of "
                "Euler--Lagrange Systems Using Progressive Barrier Lyapunov "
                "Functions"
            ),
            "venue": "",
            "tags": ["reinforcement learning", "control systems", "robotics"],
        },
        {
            "status": "Under Submission",
            "title": (
                "Hardware-Aware Real-Motor Validation of a Nussbaum-Function "
                "PID Controller for a Low-Cost Manipulator Joint Using "
                "Optuna-Guided Tuning"
            ),
            "venue": "",
            "tags": ["PID", "control", "robotics", "Optuna", "hyperparameter tuning"],
        },
    ],
    "certifications": [
        {
            "issuer": "Microsoft Certified",
            "name": "Azure AI Fundamentals (AI-900)",
            "tags": ["Azure", "AI", "cloud"],
        },
        {
            "issuer": "NVIDIA Deep Learning Institute",
            "name": (
                "Fundamentals of Deep Learning; Building Transformer-Based "
                "Natural Language Processing Applications; Computer Vision "
                "for Industrial Inspection"
            ),
            "tags": ["deep learning", "NLP", "transformers", "computer vision"],
        },
        {
            "issuer": "Google",
            "name": "5-Day AI Agents Intensive Course",
            "tags": ["AI agents", "LLM", "agentic workflows"],
        },
        {
            "issuer": "HarvardX",
            "name": "CS50P -- Introduction to Programming with Python",
            "tags": ["Python"],
        },
    ],
    "languages": [
        "English: Professional working proficiency",
        "Farsi: Native",
        "Turkish/Azerbaijani: Fluent",
    ],
}


def seed() -> None:
    init_db()
    with SessionLocal() as db:
        row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
        if row is None:
            row = CVLibrary(id=1)
            db.add(row)
        for key, value in LIBRARY.items():
            setattr(row, key, value)
        row.updated_at = datetime.utcnow()
        db.commit()
    print("Seeded CV library (id=1).")


if __name__ == "__main__":
    seed()
