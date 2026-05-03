# %% [markdown]
# # Fine-tune a small LLM to extract structured JSON from CVs and JDs
#
# Target models (T4-friendly):
# - **Qwen2.5-0.5B-Instruct** — fastest baseline, fits comfortably on T4.
# - **Qwen2.5-1.5B-Instruct** — better quality, still T4-friendly in 4-bit.
# - **Gemma-2-2B-IT** — good middle ground (set `MODEL_ID` to switch).
# - **Llama-3.2-1B-Instruct** / **3B-Instruct** — Meta-licence required.
#
# This file is a hybrid script + notebook. Cells are marked with `# %%`
# so it works in:
# - **Colab** — upload, then `Runtime → Change runtime type → T4 GPU`,
#   open the file as a notebook, or paste each cell.
# - **VS Code Jupyter** — opens directly with cell rendering.
# - **Plain Python** — just `python finetune_json_extractor_unsloth.py`
#   (uses defaults; set env vars to override).
#
# **Privacy:** the defaults point at the bundled *synthetic* JSONL files
# in `training/data/synthetic/`. Do NOT commit private CVs.

# %% [markdown]
# ## 1. Install dependencies
#
# In Colab, run this cell first. Locally, install via your venv.

# %%
# !pip install --quiet --upgrade unsloth datasets trl transformers peft accelerate bitsandbytes

# %% [markdown]
# ## 2. Imports + configuration
#
# Override anything via environment variables before launching the script.

# %%
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Tunables — set as env vars to override (each has a sensible Colab T4 default).
MODEL_ID = os.getenv("MODEL_ID", "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit")
MAX_SEQ_LENGTH = int(os.getenv("MAX_SEQ_LENGTH", "4096"))
LOAD_IN_4BIT = os.getenv("LOAD_IN_4BIT", "true").lower() in {"1", "true", "yes"}

LORA_R = int(os.getenv("LORA_R", "16"))
LORA_ALPHA = int(os.getenv("LORA_ALPHA", "16"))
LORA_DROPOUT = float(os.getenv("LORA_DROPOUT", "0"))

LEARNING_RATE = float(os.getenv("LEARNING_RATE", "2e-4"))
MAX_STEPS = int(os.getenv("MAX_STEPS", "200"))
PER_DEVICE_BATCH = int(os.getenv("PER_DEVICE_BATCH", "2"))
GRAD_ACCUM = int(os.getenv("GRAD_ACCUM", "4"))
WARMUP_STEPS = int(os.getenv("WARMUP_STEPS", "10"))
LOGGING_STEPS = int(os.getenv("LOGGING_STEPS", "10"))
SAVE_STEPS = int(os.getenv("SAVE_STEPS", "50"))
SEED = int(os.getenv("SEED", "42"))

REPO_ROOT = Path(__file__).resolve().parents[2] if "__file__" in globals() else Path.cwd().parents[1]
DEFAULT_DATA = REPO_ROOT / "training" / "data" / "synthetic"
DATA_FILES = os.getenv("DATA_FILES", str(DEFAULT_DATA / "sample_cvs.jsonl") + "," + str(DEFAULT_DATA / "sample_jobs.jsonl"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(REPO_ROOT / "training" / "checkpoints" / "cv-jd-extractor")))
EXPORT_DIR = Path(os.getenv("EXPORT_DIR", str(REPO_ROOT / "training" / "checkpoints" / "cv-jd-extractor-merged")))

print(f"Model:        {MODEL_ID}")
print(f"Data files:   {DATA_FILES}")
print(f"Output dir:   {OUTPUT_DIR}")
print(f"Export dir:   {EXPORT_DIR}")

# %% [markdown]
# ## 3. Validate the dataset before we touch a GPU
#
# Catches malformed records early. Re-uses the project's validator.

# %%
# Make `_paths` + sibling validate_dataset importable when running as a script.
_HERE = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _paths  # noqa: F401  # adds backend/ to sys.path
from validate_dataset import validate_file  # type: ignore

data_paths = [Path(p.strip()) for p in DATA_FILES.split(",") if p.strip()]
total = 0
for p in data_paths:
    report = validate_file(p)
    print(f"{p.name}: ok={report.ok} errors={len(report.errors)}")
    if report.errors:
        for e in report.errors[:5]:
            print(" -", e)
        raise SystemExit(f"Validation failed for {p}.")
    total += report.ok
print(f"Total usable records: {total}")

# %% [markdown]
# ## 4. Load the dataset and format prompts
#
# Prompt template matches the structure used by
# `backend/app/services/llm_extraction_service.py` so a fine-tuned adapter
# slots into the existing pipeline:
#
# ```
# ### Instruction
# {instruction}
#
# ### Input
# {input}
#
# ### Response
# {output}    # JSON string
# ```
#
# We pad each example with the tokenizer's EOS so the model learns to stop.

# %%
from datasets import load_dataset, concatenate_datasets

datasets_list = []
for p in data_paths:
    ds = load_dataset("json", data_files=str(p))["train"]
    datasets_list.append(ds)
dataset = concatenate_datasets(datasets_list).shuffle(seed=SEED)
print(f"Examples: {len(dataset)}")

PROMPT_TEMPLATE = (
    "### Instruction\n{instruction}\n\n"
    "### Input\n{input}\n\n"
    "### Response\n{output}"
)


def _format_row(row, eos: str) -> dict[str, str]:
    text = PROMPT_TEMPLATE.format(
        instruction=row["instruction"],
        input=row["input"],
        output=row["output"],
    ) + eos
    return {"text": text}


# %% [markdown]
# ## 5. Load the model with Unsloth (4-bit on T4) and apply LoRA

# %%
from unsloth import FastLanguageModel

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name=MODEL_ID,
    max_seq_length=MAX_SEQ_LENGTH,
    load_in_4bit=LOAD_IN_4BIT,
    dtype=None,  # auto-pick bf16/fp16 based on GPU.
)

# LoRA — only train q/k/v/o + the MLP projections. Rank 16 is a solid
# starting point for tiny instruction-tuning datasets.
model = FastLanguageModel.get_peft_model(
    model,
    r=LORA_R,
    lora_alpha=LORA_ALPHA,
    lora_dropout=LORA_DROPOUT,
    target_modules=[
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ],
    bias="none",
    use_gradient_checkpointing="unsloth",  # saves T4 memory.
    random_state=SEED,
)

# Now tokenizer.eos_token is known — apply prompt formatting.
EOS = tokenizer.eos_token or ""
formatted = dataset.map(lambda r: _format_row(r, EOS), remove_columns=dataset.column_names)
print("Formatted example:\n", formatted[0]["text"][:600], "…")

# %% [markdown]
# ## 6. Train with TRL's `SFTTrainer`
#
# Defaults are tuned for ~5–500 examples on T4. With more data, raise
# `MAX_STEPS` (or switch to `num_train_epochs`).

# %%
from trl import SFTTrainer, SFTConfig

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=formatted,
    dataset_text_field="text",
    max_seq_length=MAX_SEQ_LENGTH,
    args=SFTConfig(
        output_dir=str(OUTPUT_DIR),
        per_device_train_batch_size=PER_DEVICE_BATCH,
        gradient_accumulation_steps=GRAD_ACCUM,
        max_steps=MAX_STEPS,
        learning_rate=LEARNING_RATE,
        warmup_steps=WARMUP_STEPS,
        logging_steps=LOGGING_STEPS,
        save_steps=SAVE_STEPS,
        save_total_limit=2,
        optim="adamw_8bit",
        weight_decay=0.0,
        lr_scheduler_type="cosine",
        seed=SEED,
        bf16=False,  # T4 doesn't support bf16; keep fp16.
        fp16=True,
        report_to="none",
    ),
)

trainer_stats = trainer.train()
print("Train metrics:", trainer_stats.metrics)

# %% [markdown]
# ## 7. Save the LoRA adapter
#
# Adapter only — small (~50–200 MB depending on rank/model). Reuse with the
# same base model later.

# %%
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
model.save_pretrained(str(OUTPUT_DIR))
tokenizer.save_pretrained(str(OUTPUT_DIR))
print(f"Adapter saved to: {OUTPUT_DIR}")

# %% [markdown]
# ## 8. Quick inference smoke-test
#
# Builds an inference-only prompt (without `### Response` content) and asks
# the model to fill it in. We then `json.loads` the output to confirm
# valid JSON.

# %%
FastLanguageModel.for_inference(model)  # 2x faster generation.

SAMPLE_CV = """\
Jane Doe
San Francisco, CA | jane.doe@example.com | +1 (415) 555-0199

PROFESSIONAL SUMMARY
Backend engineer with 6 years building distributed systems in Python and Go.

TECHNICAL SKILLS
Python, Go, FastAPI, PostgreSQL, Docker, Kubernetes, AWS

WORK EXPERIENCE
Senior Backend Engineer — Acme Corp (2021 - Present)
- Designed a multi-tenant billing service handling 5M events/day.

EDUCATION
B.Sc. Computer Science — UC Berkeley (2014 - 2018)
"""

SAMPLE_JD = """\
Job Title: Senior AI Engineer
Company: Cortex Labs
Location: Berlin, Germany (Hybrid)

Required skills
- 5+ years of Python.
- Strong background in Machine Learning, NLP, and LLMs.
- Hands-on with FAISS or Chroma.
"""


def _infer(instruction: str, text: str, max_new_tokens: int = 800) -> str:
    prompt = (
        f"### Instruction\n{instruction}\n\n"
        f"### Input\n{text}\n\n"
        f"### Response\n"
    )
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    out = model.generate(
        **inputs,
        max_new_tokens=max_new_tokens,
        do_sample=False,
        temperature=0.1,
        eos_token_id=tokenizer.eos_token_id,
    )
    decoded = tokenizer.decode(out[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)
    return decoded.strip()


def _check_json(label: str, text: str) -> None:
    print(f"\n--- {label} ---")
    print(text[:600], "…" if len(text) > 600 else "")
    try:
        parsed = json.loads(text)
        keys = sorted(parsed.keys()) if isinstance(parsed, dict) else type(parsed).__name__
        print(f"valid JSON ✓ keys={keys}")
    except json.JSONDecodeError as e:
        print(f"INVALID JSON ✗ {e.msg}")


cv_out = _infer("Extract structured information from this CV and return valid JSON.", SAMPLE_CV)
_check_json("CV", cv_out)

jd_out = _infer("Extract structured information from this job description and return valid JSON.", SAMPLE_JD)
_check_json("JD", jd_out)

# %% [markdown]
# ## 9. (Optional) Export merged 16-bit weights for OpenAI-compatible servers
#
# Skip this on T4 if RAM is tight — the LoRA adapter alone is enough for
# `peft` inference. Merging is needed when you want to serve the model with
# **vLLM**, **llama.cpp**, **Ollama**, or **LM Studio** without keeping the
# base + adapter separately.

# %%
if os.getenv("EXPORT_MERGED", "false").lower() in {"1", "true", "yes"}:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    # `merged_16bit` works on T4 for ≤3B models; use `merged_4bit_forced` if OOM.
    model.save_pretrained_merged(str(EXPORT_DIR), tokenizer, save_method="merged_16bit")
    print(f"Merged model saved to: {EXPORT_DIR}")
else:
    print("Skipping merged export (set EXPORT_MERGED=true to enable).")

# %% [markdown]
# ## 10. Connecting the adapter back to the FastAPI backend
#
# The backend already has an OpenAI-compatible LLM extraction layer
# (`backend/app/services/llm_extraction_service.py`). Two options:
#
# ### A) Serve locally with an OpenAI-compatible endpoint
#
# Easiest path. The backend's `OPENAI_BASE_URL` already supports any
# compatible server. Examples:
#
# ```bash
# # Option 1: vLLM (after merging weights in step 9)
# pip install vllm
# vllm serve training/checkpoints/cv-jd-extractor-merged \
#   --port 8001 --served-model-name cv-jd-extractor
#
# # Option 2: Ollama (after exporting GGUF)
# # Convert to GGUF first, then `ollama create cv-jd-extractor -f Modelfile`
# ollama serve   # exposes localhost:11434
#
# # Option 3: LM Studio — load the merged checkpoint and start the local server.
# ```
#
# Then point the backend at it:
#
# ```bash
# export USE_LLM_EXTRACTION=true
# export OPENAI_API_KEY=local           # any non-empty string for compat servers
# export OPENAI_BASE_URL=http://127.0.0.1:8001/v1   # or http://127.0.0.1:11434/v1 for Ollama
# export LLM_MODEL_NAME=cv-jd-extractor
# uvicorn app.main:app --reload
# ```
#
# That's it — the existing extraction wrapper will hit the local model and
# fall back to the rule-based parser if anything goes wrong.
#
# ### B) Direct in-process inference (no server)
#
# Add a small alternative client implementation at
# `backend/app/services/local_llm_client.py` that loads the LoRA adapter
# with `peft.PeftModel` and exposes the same `extract_cv` / `extract_job`
# interface. Switch via an env var (e.g. `LLM_BACKEND=local`). This keeps
# everything in one process at the cost of locking the API server to one
# GPU process. Out of scope for this script.

# %% [markdown]
# ## 11. Troubleshooting
#
# ### CUDA out-of-memory on T4
# - `MAX_SEQ_LENGTH=2048` (CVs longer than this get truncated — inspect
#   them or chunk before training).
# - `PER_DEVICE_BATCH=1` and `GRAD_ACCUM=8` to keep the effective batch.
# - Switch to a smaller base model (`Qwen2.5-0.5B-Instruct-bnb-4bit`).
# - Make sure `use_gradient_checkpointing="unsloth"` is set.
# - Restart the runtime if memory is fragmented after multiple runs.
#
# ### Model returns invalid JSON
# - Train longer (e.g. `MAX_STEPS=400`) — JSON syntax stabilises with more
#   exposure to the prompt template.
# - At inference time, set `do_sample=False` and a low `temperature`. Some
#   serving stacks support `response_format={"type":"json_object"}`; use
#   it if available.
# - Strip ```` ```json ```` fences before `json.loads` (the backend's
#   `_coerce_json` helper already does this — fine-tuning rarely benefits
#   from that crutch but it's a safety net).
# - Keep the rule-based parser as the fallback. The orchestrator in
#   `backend/app/services/extraction.py` does this already.
#
# ### Overfitting on a tiny dataset
# - Synthetic-only training sets are easy to memorise. Symptoms: training
#   loss → 0 but the model regurgitates a fixed JSON regardless of input.
# - Mitigations: lower `MAX_STEPS`, set `LORA_DROPOUT=0.05`, add data via
#   `build_dataset.py jobs-csv` from a real (non-PII) CSV, or paraphrase
#   the JD inputs (different bullet ordering, capitalisation).
# - Hold out 10% as a manual eval set — even checking five examples by eye
#   tells you whether the model generalises.
#
# ### When to stick with the rule-based parser
# - Latency-critical paths (the rule-based parser is < 10 ms per CV; the
#   LLM is 200–2000 ms even on GPU).
# - Privacy-sensitive deployments where you can't run a model locally.
# - Edge-deployed builds where the model + adapter don't fit.
# - Whenever the rule-based output is already 95%+ correct on your data.
#   Run a sample through both before paying the LLM cost.
