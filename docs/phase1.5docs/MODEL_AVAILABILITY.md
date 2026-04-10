# Model Availability — Phase 1.5 Pipeline

## Pipeline Design: 3-Tier Comparison (6 Required Models)

The model comparison is structured as a deliberate 3-tier experiment:

| Tier | Purpose | Models | Backend |
|------|---------|--------|---------|
| **A — Baseline** | Establish quality ceiling with proven cloud models | Llama 3.1 8B, Llama 3.3 70B | Groq API (free) |
| **B — Small Model Viability** | Test whether ~4B param models are viable for legal drafting | Gemma 4 E4B, Qwen 3.5 4B | Ollama local |
| **C — Large Model Viability** | Test flagship-class models without local hardware cost | Gemma 4 31B Cloud, Qwen 3.5 397B MoE Cloud | Ollama cloud (free) |

**Research question**: Can small local models (Tier B) or free cloud-hosted large models (Tier C) match the quality of established Groq baselines (Tier A) for Maharashtra legal document generation?

---

## Required Models (6 — run by default with `--models all`)

### Tier A — Baseline (Groq Free Tier)

**1. Llama 3.1 8B** — `llama-3.1-8b-instant`
- Provider: Groq API
- Speed: ~560 T/sec, 250K TPM, 1K RPM
- RAM required: 0 (cloud)
- Pull: none (API key only)

**2. Llama 3.3 70B** — `llama-3.3-70b-versatile`
- Provider: Groq API
- Speed: ~280 T/sec, 300K TPM, 1K RPM
- RAM required: 0 (cloud)
- Pull: none (API key only)

### Tier B — Small Model Viability (Ollama Local)

**3. Gemma 4 E4B** — `gemma4:e4b`
- Provider: Ollama local
- Architecture: 4.5B effective params (8B with embeddings), multimodal, 128K context
- Disk: ~9.6 GB | RAM required: ~10 GB
- Pull: `ollama pull gemma4:e4b` ✅ already downloaded

**4. Qwen 3.5 4B** — `qwen3.5:4b`
- Provider: Ollama local
- Architecture: 4B dense, 256K context, thinking + non-thinking modes
- Disk: ~3.4 GB | RAM required: ~4 GB
- Pull: `ollama pull qwen3.5:4b` ✅ already downloaded

### Tier C — Large Model Viability (Ollama Cloud)

**5. Gemma 4 31B Cloud** — `gemma4:31b-cloud`
- Provider: Ollama-hosted cloud (free)
- Architecture: 31B dense, 256K context, multimodal
- RAM required: 0 (Ollama-hosted)
- Pull: none — `ollama run gemma4:31b-cloud` connects directly

**6. Qwen 3.5 397B MoE Cloud** — `qwen3.5:397b-cloud`
- Provider: Ollama-hosted cloud (free)
- Architecture: 397B total / **17B active per forward pass** (MoE), 1M context
- Throughput: 8.6× faster than Qwen3-Max at 32K context; 19× faster at 256K
- RAM required: 0 (Ollama-hosted)
- Pull: none — `ollama run qwen3.5:397b-cloud` connects directly

---

## Optional Models (excluded from default runs, available via `--include-large-models`)

| Key | Model ID | Provider | RAM |
|-----|----------|----------|-----|
| `qwen-2.5-7b` | `qwen2.5:7b` | Ollama local | 8 GB |
| `gemma-4-9b` | `gemma4:9b` | Ollama local | 10 GB |
| `qwen-3-32b` | `qwen3:32b` | Ollama local | 20 GB |
| `gemma-4-31b` | `gemma4:31b-instruct` | Ollama local | 20 GB |

---

## Setup

### One-time setup
```bash
# Tier A — just set GROQ_API_KEY in .env (no pull needed)

# Tier B — already downloaded
ollama pull gemma4:e4b      # ✅ done
ollama pull qwen3.5:4b      # ✅ done

# Tier C — no pull needed, Ollama connects to cloud on first run
ollama run gemma4:31b-cloud
ollama run qwen3.5:397b-cloud
```

### Verify local models
```bash
ollama list
# Should show: gemma4:e4b, qwen3.5:4b
```

---

## Performance Expectations

### Tier A — Groq API
| Model | Speed | Latency/doc |
|-------|-------|-------------|
| Llama 3.1 8B | ~560 T/sec | 2–4s |
| Llama 3.3 70B | ~280 T/sec | 3–6s |

### Tier B — Ollama Local
| Model | CPU | GPU | Latency/doc (CPU) |
|-------|-----|-----|-------------------|
| Gemma 4 E4B | ~20–35 T/sec | ~150+ T/sec | 15–40s |
| Qwen 3.5 4B | ~25–40 T/sec | ~180+ T/sec | 10–30s |

### Tier C — Ollama Cloud
| Model | Active Params | Speed | Latency/doc |
|-------|--------------|-------|-------------|
| Gemma 4 31B Cloud | 31B | ~100–200 T/sec | 4–10s |
| Qwen 3.5 397B MoE Cloud | 17B active | ~150–300 T/sec | 3–8s |

---

## Total Configurations

- **Required**: 6 models × 2 presets × 7 doc types = **84 runs**
- **With optional**: up to 10 models × 2 presets × 7 doc types = 140 runs
- **Total cost**: $0.00

---

## Hardware Requirements

| Scenario | RAM Needed |
|----------|-----------|
| Tier A + C only (no local models) | Any machine with internet |
| Full pipeline (all 6 required) | **~16 GB RAM** (Tier B needs ~14 GB peak) |
| With optional large local models | 32 GB RAM |
