# =============================================================================
# QUICK START - 6-Model AI Consensus
# =============================================================================

> Status: Legacy reference only. The active production pipeline is quant-first and does not run the `brain/` 6-model workflow.
> Keep for historical notes; do not follow this guide for current setup.

## 📦 9 Files Provided

**Core System:**
1. `brain/models.py` - 6-model configuration (425 lines)
2. `brain/consensus.py` - Async consensus engine (390 lines)
3. `brain/__init__.py` - Module exports (34 lines)

**Testing & Examples:**
4. `test_models.py` - Verify all 6 models work (180 lines)
5. `fastapi_example.py` - FastAPI integration example (180 lines)

**Configuration:**
6. `.env.example` - API keys template
7. `requirements.txt` - Python dependencies

**Documentation:**
8. `INTEGRATION_GUIDE.md` - Complete setup guide (500+ lines)
9. `README.md` - Quick reference

---

## ⚡ 5-Minute Setup

### **STEP 1: Install Dependencies**
```bash
pip install litellm aiohttp python-dotenv
```

### **STEP 2: Create Structure**
```bash
mkdir -p brain
touch brain/__init__.py
```

### **STEP 3: Copy Files**
```bash
# Download all files, then:
cp models.py brain/models.py
cp consensus.py brain/consensus.py
cp __init__.py brain/__init__.py
cp test_models.py .
```

### **STEP 4: Get API Keys (FREE)**

**Gemini:** https://aistudio.google.com/app/apikey  
**Groq:** https://console.groq.com/keys  
**DeepSeek:** https://platform.deepseek.com/api_keys

### **STEP 5: Add to .env**
```bash
GEMINI_API_KEY=AIzaSy-your-key-here
GROQ_API_KEY=gsk_your-key-here
DEEPSEEK_API_KEY=sk-your-key-here
```

### **STEP 6: Test**
```bash
python test_models.py
```

**Should see:**
```
✓ 6/6 models responded successfully (100%)
✓ All models working perfectly!
```

### **STEP 7: Integrate**
```python
from brain import get_consensus

@app.post("/api/analyze")
async def analyze_coin(coin_data: dict):
    consensus = await get_consensus(coin_data)
    return consensus
```

**Done!** 🎉

---

## 🎯 What You Get

✅ **6 AI models** (3 new + 3 existing)  
✅ **3 FREE models** (Gemini, Groq, DeepSeek)  
✅ **Weighted voting** (25%, 20%, 15%, 15%, 15%, 10%)  
✅ **≥4/6 agreement** requirement  
✅ **≥78% confidence** threshold  
✅ **Exponential backoff** retry logic  
✅ **Async/concurrent** execution  
✅ **LiteLLM** unified interface  
✅ **Production-ready** error handling  

---

## 📊 Model Breakdown

| Model | Weight | Free? | Speed | Purpose |
|-------|--------|-------|-------|---------|
| Claude Sonnet 4.6 | 25% | No | 2s | Most reliable |
| Grok-4 | 20% | No | 1.5s | Strong reasoning |
| **Gemini 2.5 Flash** | 15% | **Yes** | 0.5s | Fast + structured |
| Claude Haiku | 15% | No | 1s | Fast screening |
| **DeepSeek R1** | 15% | **Yes** | 1.5s | Pure reasoning |
| **Llama 3.3 70B** | 10% | **Yes** | 0.3s | Ultra-fast |

---

## 🧪 Testing Commands

```bash
# Test all models
python test_models.py

# Test single model
python test_models.py --model "Gemini 2.5 Flash"

# Run example API
python fastapi_example.py
```

---

## 📁 File Structure

```
your-app/
├── brain/
│   ├── __init__.py          # Module exports
│   ├── models.py            # 6-model configuration
│   └── consensus.py         # Async consensus engine
├── test_models.py           # Test script
├── fastapi_example.py       # API example
├── .env                     # Your API keys (add 3 new)
└── requirements.txt         # Dependencies
```

---

## 🔍 Verification

After setup, verify:

1. ✅ All 6 API keys in `.env`
2. ✅ `python test_models.py` shows 6/6 success
3. ✅ Consensus returns expected format
4. ✅ Agreement/confidence thresholds work
5. ✅ Retry logic handles rate limits

---

## 💡 Key Features

**Consensus Rules:**
- Require ≥4/6 models to agree (66.7%)
- Average confidence ≥78%
- Default to HOLD if not met

**Error Handling:**
- 3 retries per model
- Exponential backoff (1s, 2s, 4s)
- Graceful degradation (works with ≥4 responses)

**Performance:**
- All models queried concurrently
- Total time: 1.5-3 seconds
- Free tier friendly (respects rate limits)

---

## 🚀 Next Steps

1. **Install:** Copy files and install dependencies
2. **Configure:** Add 3 new API keys to `.env`
3. **Test:** Run `python test_models.py`
4. **Integrate:** Use `get_consensus()` in your app
5. **Deploy:** Works with your existing FastAPI/PostgreSQL setup

---

## 📚 Documentation

- **Quick Start:** This file
- **Full Guide:** `INTEGRATION_GUIDE.md` (detailed setup)
- **API Example:** `fastapi_example.py` (usage patterns)
- **Config:** `brain/models.py` (weights, thresholds)
- **Logic:** `brain/consensus.py` (implementation)

---

## ✅ Success Criteria

You'll know it's working when:

1. `test_models.py` shows 6/6 success
2. Individual responses include all metadata
3. Consensus aggregation uses weighted voting
4. Agreement/confidence thresholds enforced
5. Your API returns consensus in < 3 seconds

---

**All files ready! Start with: `python test_models.py`** 🎯
