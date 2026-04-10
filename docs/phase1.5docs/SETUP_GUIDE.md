# Phase 1.5 Model Setup Guide - 100% Free Solution

## Quick Start (5 Minutes)

### Step 1: Install Ollama
```bash
# Linux/Mac
curl -fsSL https://ollama.com/install.sh | sh

# Windows
# Download from https://ollama.com/download/windows
```

### Step 2: Pull Required Models (~47GB, one-time)
```bash
# Qwen models
ollama pull qwen2.5:7b      # ~4.7GB, for 2B/7B variants
ollama pull qwen3:32b       # ~19GB

# Gemma models
ollama pull gemma4:9b       # ~5.5GB
ollama pull gemma4:31b-instruct  # ~18GB
```

### Step 3: Verify Installation
```bash
ollama list
# Should show all 4 models

# Test a model
ollama run qwen2.5:7b "Hello, how are you?"
```

### Step 4: Set Up Groq API (Free)
1. Go to https://console.groq.com
2. Sign up (no credit card required)
3. Create API key
4. Add to `.env`:
```bash
GROQ_API_KEY=your_key_here
```

## Hardware Requirements

### Minimum (Can run 2B-9B models)
- **RAM**: 16GB
- **Disk**: 25GB free
- **CPU**: Modern multi-core (Intel i5/AMD Ryzen 5 or better)

### Recommended (Can run all models including 31B-32B)
- **RAM**: 32GB
- **Disk**: 50GB free
- **CPU**: High-end multi-core (Intel i7/AMD Ryzen 7 or better)
- **GPU**: Optional (NVIDIA with 8GB+ VRAM speeds up inference 5-10x)

### With GPU (Optimal)
- **GPU**: NVIDIA RTX 3060 (12GB VRAM) or better
- **RAM**: 16GB system RAM
- **Disk**: 50GB free

## Model Comparison

| Model | Size | RAM | Speed (CPU) | Speed (GPU) | Use Case |
|-------|------|-----|-------------|-------------|----------|
| Llama 3.1 8B | Cloud | 0GB | 560 T/s | N/A | Fast baseline |
| Llama 3.3 70B | Cloud | 0GB | 280 T/s | N/A | Large baseline |
| Qwen 2.5 7B | 4.7GB | 8GB | 10-20 T/s | 100+ T/s | Small variant |
| Qwen 3 32B | 19GB | 20GB | 5-10 T/s | 50+ T/s | Large variant |
| Gemma 4 9B | 5.5GB | 10GB | 15-25 T/s | 120+ T/s | Medium variant |
| Gemma 4 31B | 18GB | 20GB | 5-10 T/s | 60+ T/s | Large variant |

## Troubleshooting

### "Out of Memory" Error
**Solution**: Run smaller models first, or add swap space:
```bash
# Linux: Add 16GB swap
sudo fallocate -l 16G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

### Slow Inference on CPU
**Expected**: Large models (31B-32B) are slow on CPU (30-120s per document)
**Solutions**:
1. Use GPU if available (5-10x faster)
2. Use smaller models for testing (7B-9B)
3. Run experiments overnight for large models

### Ollama Not Found
```bash
# Check if Ollama is running
ollama list

# Start Ollama service (Linux)
systemctl start ollama

# Start Ollama service (Mac)
# Ollama runs automatically after installation
```

### Model Download Interrupted
```bash
# Resume download
ollama pull qwen3:32b
# Ollama automatically resumes from where it stopped
```

## Docker Deployment (For Cloud/Teammate)

### Dockerfile
```dockerfile
FROM ollama/ollama:latest

# Pull models during build (optional, can be done at runtime)
RUN ollama serve & sleep 5 && \
    ollama pull qwen2.5:7b && \
    ollama pull qwen3:32b && \
    ollama pull gemma4:9b && \
    ollama pull gemma4:31b-instruct

EXPOSE 11434
CMD ["ollama", "serve"]
```

### docker-compose.yml
```yaml
version: '3.8'
services:
  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    deploy:
      resources:
        limits:
          memory: 32G

volumes:
  ollama_data:
```

### Deploy
```bash
docker-compose up -d

# Pull models (one-time)
docker exec -it ollama_container ollama pull qwen2.5:7b
docker exec -it ollama_container ollama pull qwen3:32b
docker exec -it ollama_container ollama pull gemma4:9b
docker exec -it ollama_container ollama pull gemma4:31b-instruct
```

## Cost Comparison

### This Solution (Groq + Ollama)
- **Setup cost**: $0
- **Per-run cost**: $0
- **Monthly cost**: $0
- **Hardware**: Use existing machine or cloud VM

### Alternative (All Cloud APIs)
- **Setup cost**: $0
- **Per-run cost**: ~$0.01-$0.02 per document
- **Monthly cost**: ~$50-$100 for research experiments
- **Hardware**: None needed

## Performance Benchmarks

### Expected Generation Times (Per Document)

| Model | CPU (16-core) | GPU (RTX 3060) | Groq Cloud |
|-------|---------------|----------------|------------|
| Llama 3.1 8B | N/A | N/A | 2-3s |
| Llama 3.3 70B | N/A | N/A | 4-6s |
| Qwen 2.5 7B | 30-60s | 5-10s | N/A |
| Qwen 3 32B | 90-180s | 15-30s | N/A |
| Gemma 4 9B | 25-50s | 4-8s | N/A |
| Gemma 4 31B | 90-180s | 15-30s | N/A |

### Full Experiment (14 configs × 7 doc types = 98 runs)

| Hardware | Total Time | Cost |
|----------|-----------|------|
| CPU only | ~3-4 hours | $0 |
| GPU (RTX 3060) | ~30-45 min | $0 |
| Groq + GPU | ~20-30 min | $0 |

## Next Steps

1. ✅ Install Ollama and pull models
2. ✅ Set up Groq API key
3. ✅ Test each model with a simple prompt
4. ✅ Run model comparison script
5. ✅ Analyze results for research paper

## Support

- **Ollama Docs**: https://ollama.com/docs
- **Groq Docs**: https://console.groq.com/docs
- **Gemma 4 Info**: https://ai.google.dev/gemma
- **Qwen 3.5 Info**: https://qwenlm.github.io/
