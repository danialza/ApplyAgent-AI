# Training data preparation

This folder contains everything needed to build an instruction-tuning
dataset for a small open model (Gemma, Qwen, Llama, …) that converts raw
**CV text** or **job-description text** into the structured JSON the
matcher consumes.

> **No training happens here.** The scripts only prepare the dataset.
> Fine-tuning itself is intentionally out of scope so this module stays
> portable and CPU-friendly.

## Privacy ⚠️

- **Do not commit real CVs or scraped JDs** into this repository. Both
  contain personal data and almost certainly belong to other people.
- `data/raw/` and `data/processed/` are git-ignored by default — see
  `training/.gitignore`. Only `data/synthetic/` is committed.
- The synthetic samples in `data/synthetic/` are hand-written placeholder
  data that contain no real personal information.
- If you fine-tune on private data: keep the resulting JSONL **and** any
  trained adapters off the public repo.
- Consider stripping emails / phone numbers / URLs from `input` text
  before training if your downstream use cases don't need them.

## Folder layout

```
training/
├── data/
│   ├── raw/             # private CVs / JDs you collected (git-ignored)
│   ├── processed/       # JSONL files produced by build_dataset.py (git-ignored)
│   └── synthetic/       # safe-to-publish synthetic samples (committed)
│       ├── sample_cvs.jsonl
│       └── sample_jobs.jsonl
├── scripts/
│   ├── _paths.py        # adds backend/ to sys.path
│   ├── build_dataset.py # writes JSONL from DB / CSV / synthetic source
│   └── validate_dataset.py
└── README.md
```

## Record format

One JSON object per line (JSONL). Fields are exactly:

```json
{
  "instruction": "Extract structured information from this CV and return valid JSON.",
  "input": "<raw CV text>",
  "output": "<valid JSON string>"
}
```

The instruction strings are fixed:

| Source | Instruction                                                              |
|--------|--------------------------------------------------------------------------|
| CV     | `Extract structured information from this CV and return valid JSON.`     |
| JD     | `Extract structured information from this job description and return valid JSON.` |

`output` is a *string* containing valid JSON — that matches the prompt
contract used in `backend/app/services/llm_extraction_service.py`, so
the same prompt format works for inference and for training.

### CV output schema

```json
{
  "name": "",
  "summary": "",
  "skills": [],
  "education": [],
  "experience": [],
  "projects": [],
  "certifications": [],
  "languages": [],
  "contact": {
    "email": "",
    "phone": "",
    "linkedin": "",
    "github": "",
    "website": ""
  }
}
```

### Job output schema

```json
{
  "job_title": "",
  "company": "",
  "location": "",
  "salary": "",
  "employment_type": "",
  "remote_type": "",
  "required_skills": [],
  "preferred_skills": [],
  "responsibilities": [],
  "qualifications": [],
  "experience_level": "",
  "education_requirements": [],
  "technologies": [],
  "soft_skills": []
}
```

Both schemas are validated by `validate_dataset.py`.

## Building a dataset

Run from the `training/scripts/` directory.

### 1. From the backend SQLite DB (CVs)

The backend stores every uploaded CV's raw text. The script re-parses each
row with the *current* parser and writes one record per CV.

```bash
cd training/scripts
python build_dataset.py cvs --output ../data/processed/cvs.jsonl
```

### 2. From a CSV of jobs

Same CSV contract as `POST /api/match/batch-csv` (required column:
`description`).

```bash
python build_dataset.py jobs-csv ../data/raw/jobs.csv \
  --output ../data/processed/jobs.jsonl
```

### 3. From the bundled synthetic samples

Useful for smoke-testing the pipeline and for the public repo.

```bash
python build_dataset.py synthetic --output ../data/processed/synthetic.jsonl
```

## Validating

```bash
python validate_dataset.py ../data/processed/*.jsonl
```

Per record the validator checks:

1. The line is valid JSON.
2. Top-level keys `instruction`, `input`, `output` are present.
3. `input` is non-empty.
4. `output` is a string that decodes into a JSON object.
5. The decoded object has every key required by the CV / JD schema (it
   classifies which by inspecting the instruction).

Exit code is non-zero on any failure, so the script slots into CI.

## Fine-tuning script (Colab T4-friendly)

A ready-to-run script lives at:

```
training/scripts/finetune_json_extractor_unsloth.py
```

It's written as a `# %%`-cell hybrid: open in Colab / VS Code Jupyter as a
notebook, or run as a plain script. It performs the full pipeline:

1. Install dependencies (commented `pip` cell — uncomment in Colab).
2. Read configuration from env vars (model, paths, LoRA hyper-params).
3. Validate the JSONL with `validate_dataset.py` *before* touching a GPU.
4. Load + concatenate the JSONL files into a single shuffled dataset.
5. Load the base model with **Unsloth** in **4-bit** (T4-friendly).
6. Apply **LoRA** (rank 16, q/k/v/o + MLP projections, gradient
   checkpointing).
7. Format the prompt template (`### Instruction / ### Input / ### Response`)
   so it matches the runtime extractor in
   `backend/app/services/llm_extraction_service.py`.
8. Train with TRL's `SFTTrainer` (cosine schedule, AdamW 8-bit, fp16).
9. Save the adapter (`save_pretrained`).
10. Run a smoke-test inference pass on a synthetic CV + JD and assert the
    output parses with `json.loads`.
11. Optionally export merged 16-bit weights for OpenAI-compatible servers
    (`EXPORT_MERGED=true`).

### Default models (T4-friendly)

| `MODEL_ID`                                       | Notes                                |
|--------------------------------------------------|--------------------------------------|
| `unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit`         | Fastest baseline                     |
| `unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit` (default) | Good quality, fits easily on T4    |
| `unsloth/gemma-2-2b-it-bnb-4bit`                 | Gemma small instruct                 |
| `unsloth/Llama-3.2-1B-Instruct-bnb-4bit`         | Meta-licence required                |
| `unsloth/Llama-3.2-3B-Instruct-bnb-4bit`         | Tighter on memory; reduce seq length |

### Run in Colab

1. Upload the file (or clone the repo).
2. `Runtime → Change runtime type → T4 GPU`.
3. Run the install cell, then the rest top-to-bottom.

### Run locally

```bash
cd training/scripts
# (CUDA + bitsandbytes are required for 4-bit; CPU-only setups won't work.)
pip install --upgrade unsloth datasets trl transformers peft accelerate bitsandbytes

# Defaults use the bundled synthetic dataset.
python finetune_json_extractor_unsloth.py

# Override anything via env vars:
MODEL_ID=unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit \
DATA_FILES=../data/processed/cvs.jsonl,../data/processed/jobs.jsonl \
MAX_STEPS=400 \
EXPORT_MERGED=true \
python finetune_json_extractor_unsloth.py
```

### Connecting the trained adapter to the FastAPI backend

The backend already has an OpenAI-compatible LLM extraction layer (see
`backend/app/services/llm_extraction_service.py`). Two routes:

**Local OpenAI-compatible server** (recommended):

```bash
# After EXPORT_MERGED=true:
pip install vllm
vllm serve training/checkpoints/cv-jd-extractor-merged \
  --port 8001 --served-model-name cv-jd-extractor

# Backend env:
export USE_LLM_EXTRACTION=true
export OPENAI_API_KEY=local                      # any non-empty string
export OPENAI_BASE_URL=http://127.0.0.1:8001/v1
export LLM_MODEL_NAME=cv-jd-extractor
uvicorn app.main:app --reload
```

Ollama / LM Studio work the same way — they expose
`http://localhost:<port>/v1/chat/completions` and the backend doesn't
care which one is behind the URL.

**Direct in-process** — write a small `local_llm_client.py` in
`backend/app/services/` that loads the LoRA adapter via `peft.PeftModel`
and exposes the same `extract_cv` / `extract_job` API. Switch with an env
var. Out of scope for this script — see step 10 in the file for a sketch.

### Troubleshooting (also expanded inline in the script)

- **CUDA OOM on T4** — reduce `MAX_SEQ_LENGTH` to 2048, set
  `PER_DEVICE_BATCH=1` + `GRAD_ACCUM=8`, switch to the 0.5B base, or
  restart the runtime to defragment.
- **Model returns invalid JSON** — train longer (`MAX_STEPS=400`), keep
  greedy decoding (`do_sample=False`), use `response_format` if the
  serving stack supports it, and rely on the orchestrator's rule-based
  fallback.
- **Overfitting on a tiny dataset** — symptoms: training loss → 0 but the
  model returns the same JSON regardless of input. Mitigate with lower
  `MAX_STEPS`, `LORA_DROPOUT=0.05`, more data via
  `build_dataset.py jobs-csv`, and a hold-out eval set.
- **When to stick with the rule-based parser** — latency-critical paths,
  privacy-sensitive deployments, edge builds where the adapter doesn't
  fit, or any case where the rule-based output is already ≥ 95% correct.

## Going from JSONL to a fine-tuned model

The dataset is intentionally framework-agnostic. Below are minimal
sketches — you'll need GPU (or Colab / a hosted runner) to actually
train. **Don't run these against real CVs in the public repo.**

### Unsloth (recommended for portfolio-scale runs)

```python
# pip install unsloth datasets trl
from unsloth import FastLanguageModel
from datasets import load_dataset
from trl import SFTTrainer, SFTConfig

dataset = load_dataset("json", data_files="training/data/processed/synthetic.jsonl")["train"]

def to_prompt(row):
    return {"text": (
        f"### Instruction\n{row['instruction']}\n\n"
        f"### Input\n{row['input']}\n\n"
        f"### Response\n{row['output']}"
    )}

dataset = dataset.map(to_prompt, remove_columns=dataset.column_names)

model, tokenizer = FastLanguageModel.from_pretrained(
    "unsloth/gemma-2-2b-it-bnb-4bit",   # or qwen2.5-3b, llama-3.2-3b
    max_seq_length=4096,
    load_in_4bit=True,
)
model = FastLanguageModel.get_peft_model(
    model,
    r=16, lora_alpha=16, lora_dropout=0,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
)

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    dataset_text_field="text",
    args=SFTConfig(
        output_dir="checkpoints/cv-jd-extractor",
        per_device_train_batch_size=2,
        gradient_accumulation_steps=4,
        max_steps=200,
        learning_rate=2e-4,
        warmup_steps=10,
        logging_steps=10,
        save_steps=50,
    ),
)
trainer.train()
model.save_pretrained_merged("checkpoints/cv-jd-extractor-merged", tokenizer)
```

### Vanilla PEFT + Transformers

```python
# pip install peft transformers datasets accelerate bitsandbytes
from datasets import load_dataset
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments, Trainer

ds = load_dataset("json", data_files="training/data/processed/synthetic.jsonl")["train"]
tok = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-3B-Instruct")
model = AutoModelForCausalLM.from_pretrained("Qwen/Qwen2.5-3B-Instruct", load_in_4bit=True)
model = get_peft_model(model, LoraConfig(r=16, lora_alpha=16, target_modules=["q_proj", "v_proj"]))
# … tokenize with the same prompt template as Unsloth above, then run Trainer.
```

### Recommended hyper-parameters as a starting point

| Setting          | Value                                |
|------------------|--------------------------------------|
| LoRA rank        | 16                                   |
| LoRA alpha       | 16                                   |
| Target modules   | q_proj, k_proj, v_proj, o_proj       |
| Sequence length  | 4096 (CVs are long)                  |
| Batch size       | 2 × grad-accum 4 (effective 8)       |
| Optimiser        | AdamW                                |
| LR               | 2e-4                                 |
| Steps            | start with 200; scale with dataset   |

## Tips for collecting more data

- Use the existing rule-based parser to **bootstrap** outputs, then hand-correct.
- The LLM extractor (`backend/app/services/llm_extraction_service.py`) can
  be used to produce silver-standard outputs at scale; review a sample
  before committing to it.
- Augment by paraphrasing JDs (different bullet orderings, mixed
  capitalisation) to teach the model robustness.
- Keep CV PII out of the dataset unless you have explicit consent.

## Running the included synthetic sample

```bash
cd training/scripts
python validate_dataset.py ../data/synthetic/sample_cvs.jsonl ../data/synthetic/sample_jobs.jsonl
```

Should print `errors: 0` for both files.
