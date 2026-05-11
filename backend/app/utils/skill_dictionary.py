"""Configurable skill dictionary used by the JD parser.

Each entry maps a **canonical** skill name to a list of accepted **aliases**.
Aliases are matched case-insensitively against the JD text using word
boundaries. The canonical form is what gets returned in the parsed result so
downstream matching/comparison is consistent across CVs and JDs.

Add new skills by extending the relevant category dict; keep aliases lowercase.
"""
from __future__ import annotations

import re
from functools import lru_cache

# ---------- AI / ML / data ----------

AI_ML_SKILLS: dict[str, list[str]] = {
    "Python": ["python", "python3"],
    "FastAPI": ["fastapi"],
    "Machine Learning": ["machine learning", "ml"],
    "Deep Learning": ["deep learning", "dl"],
    "NLP": ["nlp", "natural language processing"],
    "RAG": ["rag", "retrieval augmented generation", "retrieval-augmented generation"],
    "LLM": ["llm", "llms", "large language model", "large language models"],
    "LangChain": ["langchain", "lang chain"],
    "Vector Database": ["vector database", "vector db", "vector store"],
    "FAISS": ["faiss"],
    "Chroma": ["chroma", "chromadb"],
    "PyTorch": ["pytorch", "torch"],
    "TensorFlow": ["tensorflow", "tf"],
    "Hugging Face": ["hugging face", "huggingface", "transformers"],
    "scikit-learn": ["scikit-learn", "sklearn", "scikit learn"],
    "Pandas": ["pandas"],
    "NumPy": ["numpy"],
    "OpenAI": ["openai", "open ai"],
    "Anthropic": ["anthropic", "claude"],
    "Computer Vision": ["computer vision", "cv"],
    "MLOps": ["mlops", "ml ops"],
    "Reinforcement Learning": [
        "reinforcement learning", "rl", "deep reinforcement learning", "deep rl",
    ],
    "Multi-agent Reinforcement Learning": [
        "multi-agent reinforcement learning", "multi-agent rl", "multi agent rl",
        "marl", "multi-agent systems", "multi agent systems",
    ],
    "PPO": ["ppo", "proximal policy optimization", "proximal policy optimisation"],
    "DQN": ["dqn", "deep q-network", "deep q network"],
    "SAC": ["sac", "soft actor-critic", "soft actor critic"],
    "A2C": ["a2c", "advantage actor-critic", "advantage actor critic"],
    "OpenAI Gym": ["openai gym", "gym", "gymnasium"],
    "Rust": ["rust"],
    "Java": ["java"],
    "SQL": ["sql"],
    "Docker": ["docker"],
    "Kubernetes": ["kubernetes", "k8s"],
    "AWS": ["aws", "amazon web services"],
    "Azure": ["azure", "microsoft azure"],
    "GCP": ["gcp", "google cloud", "google cloud platform"],
    "Git": ["git"],
    "React": ["react", "react.js", "reactjs"],
    "Next.js": ["next.js", "nextjs", "next js"],
    "TypeScript": ["typescript"],  # bare "ts" too noisy in JD scans; handled by synonyms.py
    "Node.js": ["node.js", "nodejs", "node"],
}

# ---------- Web / e-commerce ----------

WEB_ECOMMERCE_SKILLS: dict[str, list[str]] = {
    "WordPress": ["wordpress", "word press"],
    "WooCommerce": ["woocommerce", "woo commerce"],
    "PHP": ["php"],
    "JavaScript": ["javascript"],  # bare "js" matches "Next.js" — handled by synonyms.py
    "SEO": ["seo", "search engine optimization", "search engine optimisation"],
    "Google Analytics": ["google analytics", "ga4"],
    "Shopify": ["shopify"],
    "Magento": ["magento"],
    "HTML": ["html", "html5"],
    "CSS": ["css", "css3"],
    "Tailwind CSS": ["tailwind", "tailwind css", "tailwindcss"],
    "REST API": ["rest", "rest api", "restful"],
    "GraphQL": ["graphql"],
}

# ---------- Robotics / control ----------

ROBOTICS_SKILLS: dict[str, list[str]] = {
    "ROS": ["ros"],
    "ROS2": ["ros2", "ros 2"],
    "Robotics": ["robotics"],
    "Control Systems": ["control systems", "control system"],
    "PID": ["pid", "pid controller", "pid control"],
    "Gazebo": ["gazebo"],
    "MATLAB": ["matlab"],
    "Simulink": ["simulink"],
    "SLAM": ["slam"],
    "OpenCV": ["opencv", "open cv"],
    "C++": ["c++", "cpp"],
    "Embedded Systems": ["embedded systems", "embedded"],
}

# ---------- Soft skills ----------

SOFT_SKILLS: dict[str, list[str]] = {
    "Communication": ["communication", "communicate", "communicating"],
    "Teamwork": ["teamwork", "team player"],
    "Collaboration": ["collaboration", "collaborative", "collaborate"],
    "Leadership": ["leadership", "leading", "leader"],
    "Problem Solving": ["problem solving", "problem-solving", "problem solver"],
    "Critical Thinking": ["critical thinking"],
    "Adaptability": ["adaptability", "adaptable", "flexible"],
    "Creativity": ["creativity", "creative"],
    "Time Management": ["time management"],
    "Attention to Detail": ["attention to detail", "detail oriented", "detail-oriented"],
    "Analytical Thinking": ["analytical", "analytical thinking", "analytical skills"],
    "Mentoring": ["mentoring", "mentor", "coaching"],
    "Ownership": ["ownership", "self starter", "self-starter"],
    "Stakeholder Management": ["stakeholder management", "stakeholder"],
}


# ---------- Aggregated dictionaries ----------

def all_technical_skills() -> dict[str, list[str]]:
    """Merged map of every technical category. Canonical → aliases."""
    merged: dict[str, list[str]] = {}
    for source in (AI_ML_SKILLS, WEB_ECOMMERCE_SKILLS, ROBOTICS_SKILLS):
        for canonical, aliases in source.items():
            merged.setdefault(canonical, []).extend(aliases)
    return merged


@lru_cache(maxsize=1)
def _compiled_index() -> list[tuple[re.Pattern[str], str]]:
    """Compile (regex, canonical) pairs for technical skills, longest-first.

    Longest-first prevents short aliases ("ml") from shadowing long ones
    ("machine learning"). Word boundaries keep matches honest; we widen the
    boundary for tokens with `+` or `#` so "C++" and "C#" still match.
    """
    pairs: list[tuple[str, str]] = []
    for canonical, aliases in all_technical_skills().items():
        for alias in aliases:
            pairs.append((alias, canonical))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    compiled: list[tuple[re.Pattern[str], str]] = []
    for alias, canonical in pairs:
        if any(ch in alias for ch in "+#"):
            # Custom boundary to avoid `\b` swallowing `+`/`#`.
            pattern = rf"(?<![A-Za-z0-9_]){re.escape(alias)}(?![A-Za-z0-9_])"
        else:
            pattern = rf"\b{re.escape(alias)}\b"
        compiled.append((re.compile(pattern, re.IGNORECASE), canonical))
    return compiled


@lru_cache(maxsize=1)
def _compiled_soft_index() -> list[tuple[re.Pattern[str], str]]:
    pairs: list[tuple[str, str]] = []
    for canonical, aliases in SOFT_SKILLS.items():
        for alias in aliases:
            pairs.append((alias, canonical))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return [
        (re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE), canonical)
        for alias, canonical in pairs
    ]


def find_technical_skills(text: str) -> list[str]:
    """Return canonical technical skills found in `text`, preserving order."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for pattern, canonical in _compiled_index():
        if canonical in seen:
            continue
        if pattern.search(text):
            found.append(canonical)
            seen.add(canonical)
    return found


def find_soft_skills(text: str) -> list[str]:
    """Return canonical soft skills found in `text`."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for pattern, canonical in _compiled_soft_index():
        if canonical in seen:
            continue
        if pattern.search(text):
            found.append(canonical)
            seen.add(canonical)
    return found
