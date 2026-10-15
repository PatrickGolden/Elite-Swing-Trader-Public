# =============================================================================

# 6-MODEL AI CONSENSUS - Integration Guide

# Adding Gemini, Groq Llama, and DeepSeek to your existing system

# =============================================================================

> Status: Legacy reference only. The production pipeline is now quant-first and no longer uses the `brain/` 6-model path.
> Use this document only for historical context; do not use it for current implementation changes.

## 🎯 Overview

This upgrade adds 3 new FREE AI models to your existing Claude + Grok consensus system:

- **Google Gemini 2.5 Flash** (15% weight) - Fast + strong JSON structuring
- **Groq Llama 3.3 70B** (10% weight) - Ultra-fast inference
- **DeepSeek R1** (15% weight) - Best pure reasoning

**Total: 6 models with weighted voting and ≥4/6 agreement requirement**

---

## 📦 Files Provided

```
brain/
├── models.py          # 6-model configuration with weights
├── consensus.py       # Async consensus engine with retry logic
└── __init__.py        # (create if needed)

test_models.py         # Test script to verify all 6 models
.env.example           # API keys template
requirements.txt       # Python dependencies
INTEGRATION_GUIDE.md   # This file
```

---

## 🚀 Installation (5 Minutes)

### **STEP 1: Install Dependencies**

```bash
cd your-trading-app
pip install litellm>=1.50.0 aiohttp>=3.9.0 python-dotenv>=1.0.0
```

Or using your existing requirements:

```bash
pip install -r requirements.txt
```

---

### **STEP 2: Create Brain Directory Structure**

```bash
# If you don't already have a brain/ directory:
mkdir -p brain
touch brain/__init__.py
```

Your structure should look like:

```
your-trading-app/
├── brain/
│   ├── __init__.py
│   ├── models.py       # NEW - Copy here
│   └── consensus.py    # NEW - Copy here
├── test_models.py      # NEW - Copy here
├── .env                # UPDATE with new keys
└── requirements.txt
```

---

### **STEP 3: Copy Files**

```bash
# Copy the new files to your project
cp models.py brain/models.py
cp consensus.py brain/consensus.py
cp test_models.py test_models.py
```

---

### **STEP 4: Get API Keys (FREE)**

#### **A. Google Gemini**

1. Go to: [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
2. Click "Create API Key"
3. Copy key starting with `AIzaSy...`
4. Free tier: ~10-15 requests/minute

#### **B. Groq**

1. Go to: [https://console.groq.com/keys](https://console.groq.com/keys)
2. Click "Create API Key"
3. Copy key starting with `gsk_...`
4. Free tier: 30 requests/minute, ultra-fast

#### **C. DeepSeek**

1. Go to: [https://platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys)
2. Click "Create API Key"
3. Copy key starting with `sk-...`
4. Free tier: 60 requests/minute

---

### **STEP 5: Update .env File**

Add these 3 new lines to your `.env`:

```bash
# NEW: Add these API keys
GEMINI_API_KEY=AIzaSy-your-gemini-key-here
GROQ_API_KEY=gsk_your-groq-key-here
DEEPSEEK_API_KEY=sk-your-deepseek-key-here

# EXISTING: Keep your current keys
ANTHROPIC_API_KEY=sk-ant-your-existing-key
XAI_API_KEY=xai-your-existing-key
```

---

### **STEP 6: Test All Models**

```bash
python test_models.py
```

**Expected output:**

```
============================================================
TESTING 6-MODEL AI CONSENSUS SYSTEM
============================================================

AI MODEL CONFIGURATION - 6-MODEL CONSENSUS SYSTEM
============================================================

1. Claude Sonnet 4.6
   Provider: anthropic
   Weight: 25%
   
[... all 6 models listed ...]

============================================================
ENVIRONMENT CHECK
============================================================
✓ ANTHROPIC_API_KEY: **********abc
✓ XAI_API_KEY: **********xyz
✓ GEMINI_API_KEY: **********def
✓ GROQ_API_KEY: **********ghi
✓ DEEPSEEK_API_KEY: **********jkl

============================================================
QUERYING MODELS
============================================================

🤖 Querying 6-model AI consensus...
✓ Claude Sonnet 4.6: BUY
✓ Claude Haiku: BUY
✓ Grok-4: BUY
✓ Gemini 2.5 Flash: BUY
✓ Llama 3.3 70B: HOLD
✓ DeepSeek R1: BUY
✓ Received 6/6 responses in 2.1s

============================================================
CONSENSUS RESULT
============================================================

Consensus Signal: BUY
Average Confidence: 82.5%
Agreement: 83.3%
Models Responded: 6/6
Consensus Reached: ✓ YES

============================================================
TEST SUMMARY
============================================================

✓ 6/6 models responded successfully (100%)
✓ All models working perfectly!
============================================================
```

---

## 🔌 Integration with Your FastAPI App

### **Option A: Replace Existing Consensus Logic**

If you have an existing `get_ai_consensus()` function:

```python
# BEFORE (your old code)
from your_old_module import get_ai_consensus

# AFTER (new 6-model system)
from brain.consensus import get_consensus

# In your API endpoint:
@app.post("/api/analyze")
async def analyze_coin(coin_data: dict):
    """Analyze coin with 6-model consensus"""
    
    # Call new consensus system
    consensus = await get_consensus(coin_data)
    
    return {
        "signal": consensus["consensus_signal"],
        "confidence": consensus["confidence"],
        "agreement": consensus["agreement_pct"],
        "reasoning": consensus["reasoning"],
        "votes": consensus["votes"],
        "models": consensus["models_responded"]
    }
```

---

### **Option B: Side-by-Side Integration**

Keep your old system and add the new one:

```python
from brain.consensus import get_consensus as get_6model_consensus
from your_old_module import get_ai_consensus as get_old_consensus

@app.post("/api/analyze")
async def analyze_coin(coin_data: dict, use_6models: bool = True):
    """Analyze with either old or new consensus"""
    
    if use_6models:
        consensus = await get_6model_consensus(coin_data)
    else:
        consensus = await get_old_consensus(coin_data)
    
    return consensus
```

---

## 📊 Consensus Logic

### **Voting Weights:**

- Claude Sonnet 4.6: **25%** (highest - most reliable)
- Grok-4: **20%** (strong reasoning)
- Gemini 2.5 Flash: **15%** (fast + structured)
- Claude Haiku: **15%** (fast screening)
- DeepSeek R1: **15%** (pure reasoning)
- Llama 3.3 70B: **10%** (ultra-fast)

### **Consensus Requirements:**

- **Agreement:** ≥4/6 models must agree (66.7%)
- **Confidence:** Average confidence ≥78%
- **Fallback:** If requirements not met → signal = HOLD

### **Example Scenarios:**

**Scenario 1: Strong Consensus**

```
BUY:  Sonnet (25%) + Grok (20%) + Gemini (15%) + Haiku (15%) = 75%
HOLD: Llama (10%) + DeepSeek (15%) = 25%

Result: BUY (75% agreement, ≥66.7% ✓)
```

**Scenario 2: Weak Consensus**

```
BUY:  Sonnet (25%) + Grok (20%) = 45%
HOLD: Gemini (15%) + Haiku (15%) + Llama (10%) + DeepSeek (15%) = 55%

Result: HOLD (55% agreement, but split votes → default HOLD)
```

---

## 🛡️ Error Handling

### **Retry Logic:**

- **Max retries:** 3 per model
- **Backoff:** Exponential (1s, 2s, 4s)
- **Timeout:** 30s per model
- **Graceful degradation:** Works with ≥4/6 models

### **Rate Limit Handling:**

```python
# Automatic retry with exponential backoff
⏳ Gemini 2.5 Flash: Rate limit, retry 1/3 in 1.0s
⏳ Gemini 2.5 Flash: Rate limit, retry 2/3 in 2.0s
✓ Gemini 2.5 Flash: Success
```

### **Common Errors:**

**1. Missing API Key:**

```
⚠️  Gemini 2.5 Flash: Missing GEMINI_API_KEY in environment
```

**Fix:** Add key to `.env` file

**2. Rate Limited:**

```
✗ Gemini 2.5 Flash: Rate limit exceeded after 3 attempts
```

**Fix:** Wait 1 minute or upgrade to paid tier

**3. Insufficient Responses:**

```
Consensus Signal: INSUFFICIENT_DATA
Reasoning: Only 3/6 models responded (need ≥4)
```

**Fix:** Check API keys, retry, or lower threshold in `models.py`

---

## 🧪 Testing

### **Test All Models:**

```bash
python test_models.py
```

### **Test Single Model:**

```bash
python test_models.py --model "Gemini 2.5 Flash"
python test_models.py --model "Llama 3.3 70B"
python test_models.py --model "DeepSeek R1"
```

### **Test in Your App:**

```python
import asyncio
from brain.consensus import get_consensus

async def test():
    coin_data = {
        "name": "Bitcoin",
        "symbol": "BTC",
        "current_price": 45000.0,
        "price_change_24h": 2.3,
        "price_change_7d": 5.8,
        "indicators": {
            "rsi": 55.0,
            "macd_signal": "BUY",
            "momentum": 1.2,
            "volatility": 42.0
        },
        "regime": "BULL",
        "fear_greed": 65
    }
    
    result = await get_consensus(coin_data)
    print(result)

asyncio.run(test())
```

---

## 🔧 Customization

### **Adjust Weights:**

Edit `brain/models.py`:

```python
AIModel(
    name="Gemini 2.5 Flash",
    weight=0.20,  # Increase from 0.15 to 0.20
    ...
)
```

**Note:** Total weights must sum to 1.0

### **Adjust Thresholds:**

Edit `brain/models.py`:

```python
CONSENSUS_MIN_AGREEMENT = 5  # Require 5/6 instead of 4/6
CONSENSUS_MIN_CONFIDENCE = 80.0  # Require 80% instead of 78%
```

### **Change Retry Settings:**

Edit `brain/models.py`:

```python
MAX_RETRIES = 5  # More retries
BASE_RETRY_DELAY = 2.0  # Longer delays
```

---

## 📈 Performance

### **Speed:**

- **Concurrent:** All 6 models queried simultaneously
- **Typical time:** 1.5-3 seconds total
- **Fastest:** Groq Llama (~0.3s)
- **Slowest:** Anthropic Sonnet (~2s)

### **Cost:**

- **FREE models:** Gemini, Groq, DeepSeek (no cost!)
- **Paid models:** Anthropic ($3-15/M tokens), xAI ($5-10/M tokens)
- **Estimated:** ~$0.01-0.05 per analysis (depending on usage)

### **Rate Limits (Free Tier):**

- Gemini: ~10-15 RPM
- Groq: 30 RPM
- DeepSeek: 60 RPM
- Anthropic: 50 RPM
- xAI: 60 RPM

**Parallel processing respects rate limits via retry logic**

---

## 🐛 Troubleshooting

### **Problem: Only 3 models responding**

**Solution:**

1. Check API keys in `.env`
2. Verify rate limits not exceeded
3. Run `python test_models.py` to diagnose

### **Problem: JSON parsing errors**

**Solution:**

- Already handled! Code strips markdown fences automatically
- Retries on parse errors

### **Problem: Timeouts**

**Solution:**

- Increase timeout in `models.py`: `MODEL_TIMEOUT = 60.0`
- Check internet connection
- Some models (DeepSeek R1) are slower

### **Problem: Models disagree (no consensus)**

**Solution:**

- This is normal! Consensus requires ≥4/6 agreement
- Check individual responses in `consensus['individual_responses']`
- Adjust thresholds if needed

---

## ✅ Verification Checklist

After installation:

- All 6 API keys added to `.env`
- `litellm`, `aiohttp`, `python-dotenv` installed
  - On Windows litellm may need Rust and MSVC build tools for fastuuid/tokenizers
  (Python 3.14 has no wheels yet). Either install Rust + Visual Studio Build
  Tools or use Python 3.13/earlier. Tests already mock litellm to avoid this.
- Files copied to `brain/` directory
- `python test_models.py` shows 6/6 success
- Consensus logic integrated into FastAPI endpoint
- Test analysis returns expected format
- Error handling works (try with 1 missing key)
- Rate limiting handled gracefully

---

## 📚 Additional Resources

### **LiteLLM Documentation:**

- [https://docs.litellm.ai/docs/](https://docs.litellm.ai/docs/)

### **Model Documentation:**

- Gemini: [https://ai.google.dev/gemini-api/docs](https://ai.google.dev/gemini-api/docs)
- Groq: [https://console.groq.com/docs](https://console.groq.com/docs)
- DeepSeek: [https://platform.deepseek.com/docs](https://platform.deepseek.com/docs)

### **API Key Management:**

- Use environment variables (never hardcode!)
- Rotate keys regularly
- Monitor usage in provider dashboards

---

## 🎉 Done!

Your trading app now has:

- ✅ 6-model AI consensus
- ✅ 3 new FREE models
- ✅ Weighted voting (totals 100%)
- ✅ ≥4/6 agreement requirement
- ✅ ≥78% confidence threshold
- ✅ Exponential backoff retry logic
- ✅ Graceful error handling
- ✅ Async/concurrent execution

**Total setup time: ~5 minutes**
**Total cost: FREE (for 3 new models)**

---

**Questions? Check individual responses in test output for debugging!**