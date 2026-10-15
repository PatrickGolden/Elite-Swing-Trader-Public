function manualRefresh() {
  console.log("Manual refresh triggered");
  var btn = document.getElementById('refresh-btn');
  var icon = document.getElementById('refresh-icon');
  if (btn) btn.classList.add('spinning');
  if (icon) icon.textContent = '…';
  fetch('/api/refresh', { method: 'POST' })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (typeof loadData === 'function') loadData();
    })
    .catch(function(err) {
      console.error("Refresh failed:", err);
      if (typeof loadData === 'function') loadData();
    })
    .finally(function() {
      if (btn) btn.classList.remove('spinning');
      if (icon) icon.textContent = '\u21BB';
    });
}

// ── State ────────────────────────────────────────────────────
var allCoins    = [];
var activeFilter = 'ALL';
var searchQuery  = '';
var _globalAccountSize = 0;
var _globalTotalCash = 0;   // Same denominator as backend open_exposure_pct (risk tile)
var _globalRiskCapPct = 25;
var lastDisplayedUpdated = '';  // track server last_updated so we can refetch when background refresh completes
var sortKey      = 'score';
var sortDir      = -1;          // -1 = desc, 1 = asc

// Phase tracking for day counters
var phaseHistory = JSON.parse(localStorage.getItem('phaseHistory') || '{}');
// Format: { fear_greed: { phase: 'Extreme Fear', since: '2024-02-15' }, sentiment: { phase: 'Bullish', since: '2024-02-10' } }

function sharpeColorClass(val) {
  if (val == null || val === '' || val === '\u2014') return '';
  var n = Number(val);
  if (n >= 1.8) return 'sharpe-green';
  if (n >= 1.5) return 'sharpe-yellow';
  return 'sharpe-red';
}

// ============================================================
// REPORTS TAB — Manual & Test Documentation (links only)
// ============================================================

// ── Helpers ──────────────────────────────────────────────────

function fmt(n, decimals) {
  if (decimals === undefined) decimals = 2;
  if (n == null || isNaN(n)) return '—';
  return Number(n).toFixed(decimals);
}

function fmtPrice(n) {
  if (n == null) return '—';
  if (n >= 1000)  return '$' + Number(n).toLocaleString('en-US', {maximumFractionDigits: 0});
  if (n >= 1)     return '$' + Number(n).toFixed(2);
  if (n >= 0.01)  return '$' + Number(n).toFixed(4);
  return '$' + Number(n).toFixed(6);
}

function fmtMcap(n) {
  if (!n) return '—';
  if (n >= 1e12) return '$' + (n/1e12).toFixed(2) + 'T';
  if (n >= 1e9)  return '$' + (n/1e9).toFixed(2) + 'B';
  if (n >= 1e6)  return '$' + (n/1e6).toFixed(2) + 'M';
  return '$' + n.toLocaleString();
}

function changeCls(v) {
  if (v == null) return 'neu';
  return v > 0 ? 'pos' : v < 0 ? 'neg' : 'neu';
}

function changeStr(v) {
  if (v == null) return '—';
  return (v > 0 ? '+' : '') + fmt(v, 2) + '%';
}

function rsiColor(rsi) {
  if (rsi < 30) return '#00e87a';
  if (rsi < 45) return '#7dda58';
  if (rsi < 55) return '#ffb800';
  if (rsi < 70) return '#ff8c42';
  return '#ff3a6e';
}

function scoreColor(s) {
  if (s >= 65) return '#00e87a';
  if (s >= 50) return '#7dda58';
  if (s >= 35) return '#ffb800';
  return '#ff3a6e';
}

function macdHTML(m) {
  if (m === 'bullish') return '<span class="macd-bull">↑ Bull</span>';
  if (m === 'bearish') return '<span class="macd-bear">↓ Bear</span>';
  return '<span class="macd-neu">— Neu</span>';
}

function getNestedVal(obj, key) {
  if (key.indexOf('.') !== -1) {
    var parts = key.split('.');
    var a = parts[0];
    var b = parts[1];
    return ((obj[a] || {})[b]);
  }
  return obj[key];
}

// ── Runtime counter (continuous) ──────────────────────────────
var runtimeCounterInterval = null;
function formatRuntime(ms) {
  if (ms < 0) return '—';
  var s = Math.floor(ms / 1000) % 60;
  var m = Math.floor(ms / 60000) % 60;
  var h = Math.floor(ms / 3600000) % 24;
  var d = Math.floor(ms / 86400000);
  var parts = [];
  if (d > 0) parts.push(d + 'd');
  parts.push(h + 'h', m + 'm', s + 's');
  return parts.join(' ');
}
function startRuntimeCounter(serverStartedAt) {
  if (runtimeCounterInterval) clearInterval(runtimeCounterInterval);
  var el = document.getElementById('runtime-counter');
  if (!el) return;
  function tick() {
    var start = new Date(serverStartedAt).getTime();
    var elapsed = Date.now() - start;
    el.textContent = 'Runtime: ' + formatRuntime(elapsed);
  }
  tick();
  runtimeCounterInterval = setInterval(tick, 1000);
}

// ── Data fetch ───────────────────────────────────────────────

function loadData(retryCount) {
  retryCount = retryCount || 0;
  fetch('/api/data?_=' + Date.now(), { cache: 'no-store' })
    .then(function(res) {
      return res.json().then(function(data) {
        if (!res.ok && data && data.error) {
          console.warn('API error:', data.error);
        }
        return data;
      });
    })
    .then(function(data) {
      console.log('Data received:', { coins: (data.coins || []).length, last_updated: data.last_updated });

      var perf = data.performance_analytics || {};
      data.performance_analytics = {
        sharpe_ratio: perf.sharpe_ratio != null ? perf.sharpe_ratio : 0,
        sortino_ratio: perf.sortino_ratio != null ? perf.sortino_ratio : 0,
        max_drawdown_pct: perf.max_drawdown_pct != null ? perf.max_drawdown_pct : 0,
        expectancy_pct: perf.expectancy_pct != null ? perf.expectancy_pct : 0,
        win_rate_pct: perf.win_rate_pct != null ? perf.win_rate_pct : 0,
        win_rate_by_regime: perf.win_rate_by_regime || {},
        win_rate_by_regime_scenario: perf.win_rate_by_regime_scenario || {}
      };
      data.model_attribution = data.model_attribution || { by_model: {}, monthly_ranking: [], recommendations: [] };
      var pr = data.portfolio_risk_summary || {};
      data.portfolio_risk_summary = {
        concentration_limit_pct: pr.concentration_limit_pct != null ? pr.concentration_limit_pct : 5,
        max_single_position_pct: pr.max_single_position_pct != null ? pr.max_single_position_pct : 0,
        var_1d_usd: pr.var_1d_usd != null ? pr.var_1d_usd : 0,
        total_risk_pct: pr.total_risk_pct != null ? pr.total_risk_pct : 0,
        open_exposure_pct: pr.open_exposure_pct != null ? pr.open_exposure_pct : (pr.total_risk_pct || 0),
        risk_cap_pct: pr.risk_cap_pct != null ? pr.risk_cap_pct : 6,
        concentration_breaches: pr.concentration_breaches != null ? pr.concentration_breaches : 0,
        correlation_cap_pct: pr.correlation_cap_pct != null ? pr.correlation_cap_pct : 35,
        high_corr_count: pr.high_corr_count != null ? pr.high_corr_count : 0,
        suggestions: Array.isArray(pr.suggestions) ? pr.suggestions : []
      };

      var coins = data.coins || [];
      var lastUpd = data.last_updated || '';

      // Always update overview tiles (regime, sentiment, onchain, etc.) so they show data even while waiting for coins
      try { updateOverview(data); } catch (e) { console.error('updateOverview:', e); }

      if (coins.length === 0 && (lastUpd === 'Never' || !lastUpd) && retryCount < 60) {
        var loadingEl = document.getElementById('loading');
        if (loadingEl) loadingEl.innerHTML = '<p>Waiting for initial data load... (' + (retryCount * 5) + 's)</p>';
        setTimeout(function() { loadData(retryCount + 1); }, 5000);
        return;
      }

      try {
        console.log('Data received from backend:', data);
        allCoins = coins;
        fetchGlobalMarketCap();
        applyFilters();
        var mainTable = document.getElementById('main-table');
        if (mainTable) mainTable.style.display = '';
        var loadingEl = document.getElementById('loading');
        if (loadingEl) loadingEl.style.display = 'none';
        var aiSection = document.getElementById('ai-section');
        if (aiSection) aiSection.style.display = '';
        if (typeof loadAccountOverview === 'function') loadAccountOverview();
        var perfPanel = document.getElementById('tab-performance');
        if (perfPanel && perfPanel.classList.contains('active') && typeof loadPerformanceTab === 'function') loadPerformanceTab();
        var ts = data.last_updated;
        lastDisplayedUpdated = ts || '';
        var lastUpdatedEl = document.getElementById('last-updated');
        if (lastUpdatedEl) lastUpdatedEl.innerHTML = ts ? 'Updated: <span>' + ts + '</span>' : '';
        if (data.server_started_at) startRuntimeCounter(data.server_started_at);
        if (typeof loadExecutionConfig === 'function') loadExecutionConfig();
      } catch (e) {
        console.error('UI rendering failed:', e);
        document.body.innerHTML = '<h1 style="color:red;padding:50px;text-align:center;">Dashboard data loaded but UI rendering failed.<br><br>Check console (F12) for details.<br><br>Try hard refresh (Ctrl+Shift+R).</h1>';
      }
    })
    .catch(function(e) {
      console.error('loadData error:', e);
      var r = retryCount || 0;
      if (r < 10) {
        var loadingEl = document.getElementById('loading');
        if (loadingEl) loadingEl.innerHTML = '<p>Connecting to server... (' + (r * 5) + 's)</p>';
        setTimeout(function() { loadData(r + 1); }, 5000);
        return;
      }
      var loadingEl = document.getElementById('loading');
      if (loadingEl) loadingEl.style.display = 'none';
      var errContainer = document.getElementById('error-container');
      if (errContainer) errContainer.innerHTML = '<div class="error-box"><h3>Failed to load data</h3><p>Check console (F12). Error: ' + (e.message || e) + '</p><p>Ensure the server is running (python app.py) and try Refresh.</p></div>';
      try { if (typeof updateOverview === 'function') updateOverview(getDefaultOverviewData()); } catch (x) {}
    });
}

// ── Phase Tracking for Day Counters ──────────────────────────
function updatePhaseTracking(type, currentPhase) {
  if (!currentPhase || currentPhase === '—') return 0;
  
  var now = new Date().toISOString().split('T')[0]; // YYYY-MM-DD
  
  if (!phaseHistory[type] || phaseHistory[type].phase !== currentPhase) {
    // Phase changed, start new tracking
    phaseHistory[type] = { phase: currentPhase, since: now };
    localStorage.setItem('phaseHistory', JSON.stringify(phaseHistory));
    return 0; // First day
  }
  
  // Calculate days in current phase
  var sinceDate = new Date(phaseHistory[type].since);
  var nowDate = new Date(now);
  var daysDiff = Math.floor((nowDate - sinceDate) / (1000 * 60 * 60 * 24));
  
  return daysDiff;
}

// ── Overview bar ─────────────────────────────────────────────

function updateOverview(data) {
  if (!data) data = getDefaultOverviewData ? getDefaultOverviewData() : {};
  // Test mode banner
  var banner = document.getElementById('test-mode-banner');
  if (banner) banner.classList.toggle('hidden', !data.test_mode);

  var fg  = data.fear_greed || {};
  var mac = data.macro      || {};
  var sen = data.sentiment  || {};
  var sum = data.summary    || {};

  // Fear & Greed
  var fgVal  = fg.value || '—';
  var fgLbl  = fg.label || '—';
  var fgValEl = document.getElementById('fg-value');
  if (fgValEl) fgValEl.textContent = fgVal;
  var fgLblEl = document.getElementById('fg-label');
  if (fgLblEl) fgLblEl.textContent = fgLbl;

  // Track phase and show day counter
  var fgDays = updatePhaseTracking('fear_greed', fgLbl);
  var fgDaysElem = document.getElementById('fg-days');
  if (fgDaysElem) {
    if (fgDays > 0) fgDaysElem.textContent = 'Day ' + fgDays + ' in ' + fgLbl;
    else if (fgDays === 0 && fgLbl !== '—') fgDaysElem.textContent = 'New phase: ' + fgLbl;
    else fgDaysElem.textContent = '';
  }

  // Color class for F&G card
  var fgCard = fgValEl ? fgValEl.closest('.overview-card') : null;
  if (fgCard) {
    fgCard.className = 'overview-card';
    if (typeof fgVal === 'number') {
      if (fgVal <= 20) fgCard.classList.add('fg-extreme-fear');
      else if (fgVal <= 40) fgCard.classList.add('fg-fear');
      else if (fgVal <= 60) fgCard.classList.add('fg-neutral');
      else if (fgVal <= 80) fgCard.classList.add('fg-greed');
      else fgCard.classList.add('fg-extreme-greed');
    }
  }

  // Total Market Cap (if available in data)
  if (data.global_market_cap) {
    var gmc = data.global_market_cap;
    var mcapEl = document.getElementById('total-mcap');
    if (mcapEl) {
      var mcapT = gmc.total_market_cap_usd ? (gmc.total_market_cap_usd / 1e12).toFixed(2) : '—';
      mcapEl.textContent = mcapT !== '—' ? '$' + mcapT + 'T' : '—';
    }
    var updatePerf = function(id, value) {
      var elem = document.getElementById(id);
      if (!elem) return;
      if (value != null) {
        var sign = value >= 0 ? '+' : '';
        elem.textContent = sign + value.toFixed(2) + '%';
        elem.className = 'mcap-perf-value ' + (value > 0 ? 'positive' : value < 0 ? 'negative' : 'neutral');
      } else {
        elem.textContent = '—';
        elem.className = 'mcap-perf-value neutral';
      }
    };
    updatePerf('mcap-1d', gmc.market_cap_change_percentage_24h_usd);
    updatePerf('mcap-1w', gmc.market_cap_change_percentage_7d_usd);
    updatePerf('mcap-1m', gmc.market_cap_change_percentage_30d_usd);
  } else {
    fetchGlobalMarketCap();
  }

  // Market Regime (handle both object and string from API). Show regime value when we have it; "Loading…" only before any data.
  var reg = data.regime || {};
  if (typeof reg === 'string') reg = { regime: reg, confidence: null };
  var lastUpd = data.last_updated || '';
  var regimeReady = lastUpd && lastUpd !== 'Never';
  var regimeName = (reg.regime != null && reg.regime !== '')
    ? (reg.regime).toString()
    : (regimeReady ? '—' : 'Loading…');
  var regimeConf = reg.confidence != null ? reg.confidence : (regimeReady ? null : null);
  var rv = document.getElementById('regime-value');
  if (rv) rv.textContent = regimeName;
  var rc = document.getElementById('regime-confidence');
  if (rc) rc.textContent = regimeConf != null ? regimeConf + '% confidence' : '—';
  var regimeDays = updatePhaseTracking('regime', regimeName);
  var regimeDaysElem = document.getElementById('regime-days');
  if (regimeDaysElem) {
    if (regimeDays > 0) regimeDaysElem.textContent = 'Day ' + regimeDays + ' in ' + regimeName;
    else if (regimeDays === 0 && regimeName !== '—') regimeDaysElem.textContent = 'New regime: ' + regimeName;
    else regimeDaysElem.textContent = '';
  }
  var regimeStrategyElem = document.getElementById('regime-strategy');
  if (regimeStrategyElem) {
    var rec = (reg.strategy_adjustment && reg.strategy_adjustment.recommendation) ? reg.strategy_adjustment.recommendation : '';
    regimeStrategyElem.textContent = rec;
  }

  // On-Chain (CryptoQuant) status and live values
  var onchainStatus = data.onchain_status || 'fallback';
  var onchainOverview = data.onchain_overview || null;
  var onchainEl = document.getElementById('onchain-status');
  var onchainSubEl = document.getElementById('onchain-status-sub');
  var onchainValuesEl = document.getElementById('onchain-values');
  if (onchainEl) {
    onchainEl.textContent = onchainStatus === 'live' ? 'Live' : 'Fallback';
    onchainEl.style.color = onchainStatus === 'live' ? 'var(--green)' : 'var(--text-dim)';
  }
  if (onchainSubEl) onchainSubEl.textContent = onchainStatus === 'live' ? 'Real data' : 'CryptoQuant (no key)';
  if (onchainValuesEl) {
    if (onchainStatus === 'live' && onchainOverview) {
      var s = onchainOverview.sopr != null ? Number(onchainOverview.sopr).toFixed(3) : '—';
      var n = onchainOverview.exchange_netflow_24h != null ? Number(onchainOverview.exchange_netflow_24h).toFixed(0) : '—';
      var l = onchainOverview.lth_supply_change_24h_pct != null ? (Number(onchainOverview.lth_supply_change_24h_pct) >= 0 ? '+' : '') + Number(onchainOverview.lth_supply_change_24h_pct).toFixed(2) + '%' : '—';
      onchainValuesEl.textContent = 'SOPR: ' + s + ' | Netflow: ' + n + ' | LTH \u0394: ' + l;
    } else {
      onchainValuesEl.textContent = '';
    }
  }

  // Sentiment (handle missing or malformed)
  var sentLabel = (sen && (sen.label || sen.phase)) || '—';
  var sl = document.getElementById('sent-label');
  if (sl) sl.textContent = sentLabel;
  var sa = document.getElementById('sent-articles');
  if (sa) sa.textContent = sen.articles ? sen.articles + ' articles' : 'No data';
  var sentSourceEl = document.getElementById('sent-source');
  if (sentSourceEl) sentSourceEl.textContent = (sen && sen.source) ? 'Source: ' + sen.source : '';

  var sentDays = updatePhaseTracking('sentiment', sentLabel);
  var sentDaysElem = document.getElementById('sent-days');
  if (sentDaysElem) {
    if (sentDays > 0) sentDaysElem.textContent = 'Day ' + sentDays + ' in ' + sentLabel;
    else if (sentDays === 0 && sentLabel !== '—') sentDaysElem.textContent = 'New phase: ' + sentLabel;
    else sentDaysElem.textContent = '';
  }

  // Macro
  var fed = mac.fed_rate || {};
  var fedRate = document.getElementById('fed-rate');
  if (fedRate) fedRate.textContent = fed.value != null ? fed.value + '%' : '—';
  var fedDate = document.getElementById('fed-date');
  if (fedDate) fedDate.textContent = fed.date ? 'As of ' + fed.date : 'FRED';

  var t10 = mac.t10y || {};
  var t10El = document.getElementById('t10y');
  if (t10El) t10El.textContent = t10.value != null ? t10.value + '%' : '—';
  var t10Date = document.getElementById('t10y-date');
  if (t10Date) t10Date.textContent = t10.date ? 'As of ' + t10.date : 'FRED';

  var cpi = mac.cpi || {};
  var cpiEl = document.getElementById('cpi');
  if (cpiEl) cpiEl.textContent = cpi.value != null ? cpi.value + '%' : '—';
  var cpiDate = document.getElementById('cpi-date');
  if (cpiDate) cpiDate.textContent = cpi.date ? 'As of ' + cpi.date : 'FRED';

  var m2 = mac.m2 || {};
  var m2El = document.getElementById('m2');
  if (m2El) m2El.textContent = m2.value != null ? m2.value + '%' : '—';
  var m2Date = document.getElementById('m2-date');
  if (m2Date) m2Date.textContent = m2.date ? 'As of ' + m2.date : 'FRED';

  // Portfolio Risk Summary (correlation, concentration, exposure, VaR)
  var pr = data.portfolio_risk_summary || {};
  var totalRiskEl = document.getElementById('portfolio-total-risk');
  var corrRiskEl = document.getElementById('portfolio-correlated-risk');
  var concEl = document.getElementById('portfolio-concentration');
  var varEl = document.getElementById('portfolio-var');
  var suggEl = document.getElementById('portfolio-suggestions');
  var riskCard = document.getElementById('portfolio-risk-card');
  var exposurePct = pr.open_exposure_pct != null ? pr.open_exposure_pct : pr.total_risk_pct;
  if (totalRiskEl) totalRiskEl.textContent = exposurePct != null ? exposurePct + '%' : '—';
  if (corrRiskEl) corrRiskEl.textContent = pr.correlated_risk_pct != null
    ? 'Corr (>\u2009 0.85): ' + pr.correlated_risk_pct + '%' : '—';
  if (concEl) {
    var maxPct = pr.max_single_position_pct != null ? pr.max_single_position_pct : null;
    concEl.textContent = maxPct != null ? 'Max pos: ' + maxPct + '%' : '—';
    concEl.style.color = (pr.concentration_breaches || 0) > 0 ? 'var(--amber)' : 'var(--text-dim)';
  }
  if (varEl) {
    var varUsd = pr.var_1d_usd;
    varEl.textContent = varUsd != null ? '95% VaR: $' + Number(varUsd).toLocaleString('en-US', {maximumFractionDigits:0}) : '—';
  }
  if (riskCard) {
    var hasBreach = (pr.concentration_breaches || 0) > 0 || (pr.total_risk_pct || 0) > 80;
    riskCard.style.borderLeft = hasBreach ? '3px solid var(--amber)' : '';
  }
  if (suggEl) {
    var suggestions = (pr.suggestions && Array.isArray(pr.suggestions)) ? pr.suggestions : [];
    if (suggestions.length) {
      suggEl.innerHTML = suggestions.map(function(s) { return '<div>' + String(s).replace(/</g, '&lt;') + '</div>'; }).join('');
      suggEl.style.color = 'var(--amber)';
    } else {
      suggEl.textContent = (pr.high_corr_count || 0) > 0 ? 'Cap ' + (pr.correlation_cap_pct || 35) + '% high-BTC' : 'OK';
      suggEl.style.color = 'var(--text-secondary)';
    }
  }

  // Risk Budget tile — also cache risk cap and total_cash globally for positions/markets tables
  if (pr.risk_cap_pct != null) _globalRiskCapPct = pr.risk_cap_pct;
  var accountCash = Number(data.account_total_cash) || 0;
  if (accountCash > 0) _globalTotalCash = accountCash;
  var rbValue = document.getElementById('risk-budget-value');
  var rbDetail = document.getElementById('risk-budget-detail');
  var rbStatus = document.getElementById('risk-budget-status');
  var rbCard = document.getElementById('risk-budget-card');
  var riskCap = pr.risk_cap_pct || 6;
  var currentExposure = pr.open_exposure_pct != null ? pr.open_exposure_pct : (pr.total_risk_pct || 0);
  var usedPct = riskCap > 0 ? Math.min(100, Math.round(currentExposure / riskCap * 100)) : 0;
  var headroom = Math.max(0, riskCap - currentExposure);
  if (rbValue) rbValue.textContent = usedPct + '% used';
  if (typeof renderRiskBullet === 'function') renderRiskBullet(usedPct, riskCap, currentExposure);
  if (rbDetail) rbDetail.textContent = currentExposure.toFixed(1) + '% / ' + riskCap + '% cap';
  if (rbStatus) {
    if (usedPct >= 100) {
      rbStatus.textContent = 'FULL — auto-exec blocked';
      rbStatus.style.color = 'var(--red)';
    } else if (usedPct >= 80) {
      rbStatus.textContent = headroom.toFixed(1) + '% headroom';
      rbStatus.style.color = 'var(--amber)';
    } else {
      rbStatus.textContent = headroom.toFixed(1) + '% headroom';
      rbStatus.style.color = 'var(--green)';
    }
  }
  if (rbCard) {
    rbCard.style.borderLeft = usedPct >= 100 ? '3px solid var(--red)' : usedPct >= 80 ? '3px solid var(--amber)' : '';
  }

  // Signal counts
  var sb = document.getElementById('sig-buy');
  if (sb) sb.textContent = sum.buy != null ? sum.buy : '—';
  var sh = document.getElementById('sig-hold');
  if (sh) sh.textContent = sum.hold != null ? sum.hold : '—';
  var ss = document.getElementById('sig-sell');
  if (ss) ss.textContent = sum.sell != null ? sum.sell : '—';
  // Signals last 30 days (incl. medium/hold)
  var s30 = data.signal_30d || {};
  var sig30El = document.getElementById('sig-30d');
  if (sig30El) {
    var b = s30.buy != null ? s30.buy : 0;
    var h = s30.hold != null ? s30.hold : 0;
    var s = s30.sell != null ? s30.sell : 0;
    sig30El.textContent = (b || h || s) ? ('30d: BUY ' + b + ' · HOLD ' + h + ' · SELL ' + s) : '—';
  }

  // Actionable tier counts (quant + veto)
  var ultraCount = 0;
  var strongCount = 0;
  var moderateCount = 0;
  
  if (data.coins && Array.isArray(data.coins)) {
    data.coins.forEach(function(coin) {
      var aiRec = coin.ai_recommendation;
      if (aiRec && aiRec.consensus) {
        var signal = aiRec.consensus.consensus_signal;
        if (signal === 'ULTRA_BUY') ultraCount++;
        else if (signal === 'STRONG_BUY') strongCount++;
        else if (signal === 'MODERATE_BUY') moderateCount++;
      }
    });
  }
  
  var au = document.getElementById('ai-ultra');
  if (au) au.textContent = ultraCount || '—';
  var as_ = document.getElementById('ai-strong');
  if (as_) as_.textContent = strongCount || '—';
  var am = document.getElementById('ai-moderate');
  if (am) am.textContent = moderateCount || '—';
  // AI last 30 days (incl. moderate + HOLD)
  var a30 = data.ai_30d || {};
  var ai30El = document.getElementById('ai-30d');
  if (ai30El) {
    var u = a30.ultra != null ? a30.ultra : 0;
    var st = a30.strong != null ? a30.strong : 0;
    var m = a30.moderate != null ? a30.moderate : 0;
    var ah = a30.hold != null ? a30.hold : 0;
    ai30El.textContent = (u || st || m || ah) ? ('30d: Ultra ' + u + ' · Strong ' + st + ' · Moderate ' + m + ' · HOLD ' + ah) : '—';
  }
}

// ── Filters & search ─────────────────────────────────────────

function setFilter(f) {
  activeFilter = f;
  document.querySelectorAll('.filter-tab').forEach(function(t) {
    t.classList.toggle('active', t.dataset.filter === f);
  });
  applyFilters();
}

function toggleOnchainCols(show) {
  try { localStorage.setItem('showOnchainCols', show ? '1' : '0'); } catch (e) {}
  document.body.classList.toggle('show-onchain-cols', !!show);
}

function applyFilters() {
  searchQuery = document.getElementById('search').value.toLowerCase().trim();

  var filtered = allCoins.filter(function(c) {
    var matchFilter = activeFilter === 'ALL' || c.signal === activeFilter;
    var matchSearch = !searchQuery || (c.name && c.name.toLowerCase().indexOf(searchQuery) >= 0) || (c.symbol && c.symbol.toLowerCase().indexOf(searchQuery) >= 0);
    return matchFilter && matchSearch;
  });

  filtered = filtered.slice().sort(function(a, b) {
    var av = getNestedVal(a, sortKey);
    var bv = getNestedVal(b, sortKey);
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === 'string') return sortDir * av.localeCompare(bv);
    return sortDir * (av - bv);
  });

  renderTable(filtered);
  var rc = document.getElementById('results-count');
  if (rc) rc.textContent = filtered.length + ' of ' + allCoins.length + ' coins';
}

// ── Table render ─────────────────────────────────────────────

function renderTable(coins) {
  var tbody = document.getElementById('table-body');
  if (!coins.length) {
    tbody.innerHTML = '<tr><td colspan="20" style="text-align:center;padding:40px;color:var(--text-dim)">No coins match your filter.</td></tr>';
    return;
  }

  tbody.innerHTML = coins.map(function(c) {
    var ind = c.indicators || {};
    var risk = c.risk || {};
    var rsi = ind.rsi != null ? ind.rsi : 50;
    var bbp = ind.bb_pos != null ? ind.bb_pos : 50;
    var mom = ind.momentum != null ? ind.momentum : 0;
    var score = c.score != null ? c.score : 0;
    var signal = c.signal || 'HOLD';
    var rowCls = signal.toLowerCase() + '-row';
    var rsiColor_ = rsiColor(rsi);
    var scoreColor_ = scoreColor(score);
    var q = c.ai_recommendation && c.ai_recommendation.quant;
    var qt = q ? (q.quant_tier || '—') : '—';
    var quantCell = qt === 'HOLD' ? '<span style="color:var(--text-dim);font-size:11px">—</span>' : '<span class="quant-tier-badge" style="font-size:11px;color:var(--cyan)">' + qt + '</span>';
    var rec = c.ai_recommendation;
    var cons = rec && rec.consensus;
    var finalSig = cons ? (cons.consensus_signal || '—') : null;
    var veto = cons && cons.veto_reason;
    var aiCell = (!finalSig || finalSig === '—') ? '<span style="color:var(--text-dim);font-size:11px">—</span>' : (function() {
      var tip = veto ? ('VETO: ' + (veto || '').replace(/"/g, '')) : (cons.quant_tier ? 'Quant: ' + cons.quant_tier : 'Click for details');
      var vetoBadge = veto ? '<span class="veto-badge" onclick="event.stopPropagation(); showAIAnalysis(\'' + (c.id || '').replace(/'/g, "\\'") + '\', \'' + (c.name || '').replace(/'/g, "\\'") + '\')" title="' + (veto || '').replace(/"/g, '&quot;') + '" style="display:inline-block;font-size:9px;padding:1px 4px;border-radius:3px;margin-left:4px;cursor:pointer">VETO</span>' : '';
      return '<span class="ai-signal-badge ' + (finalSig + '').replace(/_/g, ' ') + '" onclick="event.stopPropagation(); showAIAnalysis(\'' + (c.id || '').replace(/'/g, "\\'") + '\', \'' + (c.name || '').replace(/'/g, "\\'") + '\')" style="cursor:pointer" title="' + (tip || '').replace(/"/g, '&quot;') + '">' + (finalSig + '').replace(/_/g, ' ') + '</span>' + vetoBadge;
    })();
    var e = c.enhanced || {};
    var soprCell = e.sopr != null ? '<span style="font-size:11px">' + Number(e.sopr).toFixed(3) + '</span>' : '—';
    var netflowCell = e.exchange_netflow_24h != null ? '<span style="font-size:11px">' + (Number(e.exchange_netflow_24h) >= 0 ? '+' : '') + Number(e.exchange_netflow_24h).toFixed(0) + '</span>' : '—';
    var lthCell = e.lth_supply_change_24h_pct != null ? '<span style="font-size:11px">' + (Number(e.lth_supply_change_24h_pct) >= 0 ? '+' : '') + Number(e.lth_supply_change_24h_pct).toFixed(2) + '%</span>' : '—';
    var stopVal = c.stop_loss_pct != null ? c.stop_loss_pct : (risk.stop_loss_pct != null ? risk.stop_loss_pct : '—');
    var targetVal = c.take_profit_pct != null ? c.take_profit_pct : (risk.take_profit_pct != null ? risk.take_profit_pct : (risk.target_pct != null ? risk.target_pct : '—'));
    var rrVal = c.risk_reward_ratio != null ? c.risk_reward_ratio : (risk.rr_ratio != null ? risk.rr_ratio : '—');
    var riskCell = c.signal === 'BUY' ? '<div>Stop: ' + stopVal + '% | Target: ' + targetVal + '%' + (c.dynamic_targets ? ' &#127919;' : '') + '</div><div class="risk-detail">R:R ' + rrVal + ':1 · ' + (risk.position_size || '&lt;1%') + '</div>' : '<span style="color:var(--text-dim)">—</span>';
    var posSize = (rec && rec.position_sizing) ? Number(rec.position_sizing.position_size_usd || 0) : 0;
    var mktBudgetPct = (_globalAccountSize > 0 && posSize > 0) ? (posSize / _globalAccountSize * 100) : 0;
    var mktBudgetCell = (c.signal === 'BUY' && mktBudgetPct > 0)
      ? '<span style="font-size:11px;color:' + (mktBudgetPct > _globalRiskCapPct * 0.3 ? 'var(--amber)' : 'var(--text-secondary)') + '">' + mktBudgetPct.toFixed(1) + '%</span>'
      : '<span style="color:var(--text-dim)">—</span>';
    var badgeList = (q && q.signal_badges && Array.isArray(q.signal_badges)) ? q.signal_badges : [];
    var badgeLabels = { blood_in_the_street: 'Blood', dip: 'Dip', pullback_in_trend: 'Pullback', volatility_harvest: 'Vol', rally: 'Rally', pump_cap: 'Pump cap', capitulation: 'Capit', bull_participant: 'Bull', momentum_continuation: 'Momentum', intra_swing_sniper: 'Sniper', vol_contraction: 'Vol contraction', dynamic_prob: 'Dynamic prob', light_sniper_entry: 'Sniper', narrative_spike: 'Narrative', light_whale_accum: 'Whale accum' };
    var badgesHtml = badgeList.length ? '<div class="strategy-badges-row" style="margin-top:4px;display:flex;flex-wrap:wrap;gap:3px;justify-content:center">' + badgeList.map(function(b){ var lbl = badgeLabels[b] || b.replace(/_/g,' '); return '<span class="strategy-badge ' + b + '" title="' + lbl.replace(/"/g,'&quot;') + '">' + lbl + '</span>'; }).join('') + '</div>' : '';
    return '<tr class="' + rowCls + '"><td class="rank-num">' + (c.market_cap_rank || '—') + '</td><td><div class="coin-cell"><img class="coin-img" src="' + (c.image || '') + '" alt="' + (c.name || '').replace(/"/g, '&quot;') + '" loading="lazy" onerror="this.style.display=\'none\'" /><div><div class="coin-name">' + (c.name || '').replace(/</g, '&lt;') + '</div></div><span class="coin-sym">' + (c.symbol || '').toUpperCase() + '</span></div></td><td class="right price">' + fmtPrice(c.current_price) + '</td><td class="right ' + changeCls(c.price_change_percentage_1h_in_currency) + '">' + changeStr(c.price_change_percentage_1h_in_currency) + '</td><td class="right ' + changeCls(c.price_change_percentage_24h) + '">' + changeStr(c.price_change_percentage_24h) + '</td><td class="right ' + changeCls(c.price_change_percentage_7d_in_currency) + '">' + changeStr(c.price_change_percentage_7d_in_currency) + '</td><td class="right mcap">' + fmtMcap(c.market_cap) + '</td><td class="center"><div class="rsi-cell"><div class="rsi-bar-wrap"><div class="rsi-bar" style="width:' + rsi + '%;background:' + rsiColor_ + '"></div></div><span style="color:' + rsiColor_ + '">' + fmt(rsi, 0) + '</span></div></td><td class="center ' + changeCls(mom) + '">' + (mom > 0 ? '+' : '') + fmt(mom, 1) + '</td><td class="center">' + macdHTML(ind.macd) + '</td><td class="right"><div class="score-cell"><div class="score-bar-wrap"><div class="score-bar" style="width:' + score + '%;background:' + scoreColor_ + '"></div></div><span class="score-num" style="color:' + scoreColor_ + '">' + score + '</span></div></td><td class="center"><span class="signal-badge ' + signal + '">' + signal + '</span></td><td class="center">' + quantCell + '</td><td class="center">' + aiCell + badgesHtml + '</td><td class="center col-onchain" title="Spent Output Profit Ratio. Above 1 = profit-taking.">' + soprCell + '</td><td class="center col-onchain" title="Exchange netflow 24h. Positive = selling pressure.">' + netflowCell + '</td><td class="center col-onchain" title="LTH supply change 24h %.">' + lthCell + '</td><td class="risk-cell" title="' + (c.hypothesis || '').replace(/"/g, '&quot;') + '">' + riskCell + '</td><td class="center">' + mktBudgetCell + '</td><td class="center"><button class="ai-btn" onclick="aiAnalyzeCoin(\'' + (c.id || '').replace(/'/g, "\\'") + '\', \'' + (c.name || '').replace(/'/g, "\\'") + '\')" style="margin-bottom:4px;display:block;width:100%">&#129302; Analyze</button><button class="quick-log-btn" onclick="quickLogTrade(\'' + (c.id || '') + '\')" title="Auto-fill journal with this trade">&#128210; Quick Log</button></td></tr>';
  }).join('');
}

// ── Sort ─────────────────────────────────────────────────────

function sortBy(key) {
  if (sortKey === key) {
    sortDir *= -1;
  } else {
    sortKey = key;
    sortDir = -1;
  }
  // Update header classes
  document.querySelectorAll('thead th').forEach(function(th) {
    th.classList.remove('sort-asc', 'sort-desc');
  });
  applyFilters();
}

// ── Manual refresh (defined at top of script) ─────────────────

// ── Auto-Execution (Coinbase / Kraken) ───────────────────────
function loadExecutionConfig() {
  fetch('/api/execution-config')
    .then(function(res) { return res.json(); })
    .then(function(data) {
      // Default-on semantics: only explicit false means off (matches server get_execution_config)
      var enabled = data.auto_execution_enabled !== false;
    var exchange = (data.auto_execution_exchange || 'coinbase').toLowerCase();
    var statusEl = document.getElementById('execution-status');
    var toggleBtn = document.getElementById('execution-toggle');
    var selectEl = document.getElementById('execution-exchange');
    if (statusEl) statusEl.textContent = enabled ? 'On' : 'Off';
    if (toggleBtn) {
      toggleBtn.disabled = false;
      toggleBtn.removeAttribute('title');
      if (enabled) toggleBtn.classList.add('active'); else toggleBtn.classList.remove('active');
    }
    if (selectEl) selectEl.value = exchange === 'kraken' ? 'kraken' : 'coinbase';
    var staleOn = data.stale_position_rule_enabled !== false;
    var staleStatusEl = document.getElementById('stale-position-rule-status');
    var staleToggleBtn = document.getElementById('stale-position-rule-toggle');
    if (staleStatusEl) staleStatusEl.textContent = staleOn ? 'On' : 'Off';
    if (staleToggleBtn) { if (staleOn) staleToggleBtn.classList.add('active'); else staleToggleBtn.classList.remove('active'); }
    })
    .catch(function(e) {
      console.error('Load execution config:', e);
      var toggleBtn = document.getElementById('execution-toggle');
      var statusEl = document.getElementById('execution-status');
      if (statusEl) statusEl.textContent = '—';
      if (toggleBtn) {
        toggleBtn.disabled = false;
        toggleBtn.title = 'Could not load settings; click to try again';
      }
    });
}

var _executionToggleInFlight = false;
function toggleAutoExecution() {
  if (_executionToggleInFlight) return;
  _executionToggleInFlight = true;
  var toggleBtn = document.getElementById('execution-toggle');
  if (toggleBtn) toggleBtn.disabled = true;
  fetch('/api/execution-config')
    .then(function(res) { return res.json(); })
    .then(function(data) {
      var cur = data.auto_execution_enabled !== false;
      var next = !cur;
      if (next === false) {
        var okOff = window.confirm('Turn Auto-Exec OFF? This will stop automated entries/exits until you turn it back ON.');
        if (!okOff) throw new Error('__auto_exec_toggle_cancelled__');
      }
      var body = { auto_execution_enabled: next };
      if (next === false) {
        body.disable_intent = 'dashboard_manual';
        body.disable_confirmed = true;
      }
      return adminFetchJson('/api/execution-config', { method: 'POST', body: body });
    })
    .then(function(updated) {
      if (updated.error) {
        alert(updated.error);
        return;
      }
      var on = updated.auto_execution_enabled !== false;
      var statusEl = document.getElementById('execution-status');
      if (statusEl) statusEl.textContent = on ? 'On' : 'Off';
      if (toggleBtn) {
        if (on) toggleBtn.classList.add('active'); else toggleBtn.classList.remove('active');
      }
    })
    .catch(function(e) {
      if (e && e.message === '__auto_exec_toggle_cancelled__') return;
      console.error('Toggle auto-execution:', e);
      alert('Failed to update auto-execution');
    })
    .finally(function() {
      _executionToggleInFlight = false;
      if (toggleBtn) toggleBtn.disabled = false;
    });
}

function saveExecutionExchange() {
  var selectEl = document.getElementById('execution-exchange');
  var exchange = selectEl ? selectEl.value : 'coinbase';
  adminFetchJson('/api/execution-config', {
    method: 'POST',
    body: { auto_execution_exchange: exchange },
  })
    .then(function(updated) {
      if (updated.error) alert(updated.error);
    })
    .catch(function(e) { console.error('Save execution exchange:', e); });
}

function toggleStalePositionRule() {
  fetch('/api/execution-config')
    .then(function(res) { return res.json(); })
    .then(function(data) {
      var next = !(data.stale_position_rule_enabled !== false);
      return adminFetchJson('/api/execution-config', {
        method: 'POST',
        body: { stale_position_rule_enabled: next },
      });
    })
    .then(function(updated) {
      if (updated.error) {
        alert(updated.error);
        return;
      }
      var on = updated.stale_position_rule_enabled !== false;
      var statusEl = document.getElementById('stale-position-rule-status');
      var toggleBtn = document.getElementById('stale-position-rule-toggle');
      if (statusEl) statusEl.textContent = on ? 'On' : 'Off';
      if (toggleBtn) { if (on) toggleBtn.classList.add('active'); else toggleBtn.classList.remove('active'); }
    })
    .catch(function(e) {
      console.error('Toggle 14d sell rule:', e);
      alert('Failed to update 14d sell rule');
    });
}

// ── Test Mode ────────────────────────────────────────────────
function loadTestMode() {
  fetch('/api/test-mode')
    .then(function(res) { return res.json(); })
    .then(function(data) {
      var on = !!data.test_mode;
      var statusEl = document.getElementById('test-mode-status');
      var toggleBtn = document.getElementById('test-mode-toggle');
      var banner = document.getElementById('test-mode-banner');
      if (statusEl) statusEl.textContent = on ? 'On' : 'Off';
      if (toggleBtn) { if (on) toggleBtn.classList.add('active'); else toggleBtn.classList.remove('active'); }
      if (banner) banner.classList.toggle('hidden', !on);
    })
    .catch(function(e) { console.error('Load test mode:', e); });
}

function toggleTestMode() {
  fetch('/api/test-mode')
    .then(function(res) { return res.json(); })
    .then(function(data) {
      var next = !data.test_mode;
      if (!next) {
        return fetch('/api/testing/positions').then(function(r) { return r.json(); })
          .then(function(t) {
            var positions = (t && t.positions) || [];
            var openCount = positions.filter(function(p) { return p.status === 'OPEN'; }).length;
            var closeTestPositions = openCount > 0 && confirm('You have ' + openCount + ' test position(s) open. Close them when switching to live?');
            return fetch('/api/test-mode', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ test_mode: next, close_test_positions: !!closeTestPositions }) }).then(function(r) { return r.json(); });
          });
      }
      return fetch('/api/test-mode', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ test_mode: next }) }).then(function(r) { return r.json(); });
    })
    .then(function(updated) {
      if (updated && updated.error) { alert(updated.error); return; }
      var statusEl = document.getElementById('test-mode-status');
      var toggleBtn = document.getElementById('test-mode-toggle');
      var banner = document.getElementById('test-mode-banner');
      if (statusEl) statusEl.textContent = updated.test_mode ? 'On' : 'Off';
      if (toggleBtn) { if (updated.test_mode) toggleBtn.classList.add('active'); else toggleBtn.classList.remove('active'); }
      if (banner) banner.classList.toggle('hidden', !updated.test_mode);
      if (updated && updated.closed_test_positions) { console.log('Closed ' + updated.closed_test_positions + ' test position(s)'); }
    })
    .catch(function(e) { console.error('Toggle test mode:', e); alert('Failed to update test mode'); });
}

// ── Performance tab (mirrors recursive optimizer email; sync with format_report_html) ──
function fetchJsonWithRetry(url, options) {
  options = options || {};
  var retries = Number.isFinite(options.retries) ? options.retries : 2;
  var timeoutMs = Number.isFinite(options.timeoutMs) ? options.timeoutMs : 8000;
  var retryDelayMs = Number.isFinite(options.retryDelayMs) ? options.retryDelayMs : 600;
  var fetchOptions = options.fetchOptions || {};

  function wait(ms) {
    return new Promise(function(resolve) { setTimeout(resolve, ms); });
  }

  function doFetch(attempt) {
    var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var timer = controller ? setTimeout(function() { controller.abort(); }, timeoutMs) : null;
    var reqOptions = controller ? Object.assign({}, fetchOptions, { signal: controller.signal }) : fetchOptions;

    return fetch(url, reqOptions)
      .then(function(res) {
        return res.text().then(function(rawText) {
          var data = {};
          if (rawText && rawText.trim()) {
            try {
              data = JSON.parse(rawText);
            } catch (_) {
              throw new Error('Invalid JSON from ' + url + ' (HTTP ' + res.status + ')');
            }
          }
          if (!res.ok) {
            var msg = (data && (data.error || data.message)) || ('HTTP ' + res.status);
            throw new Error(msg);
          }
          return data || {};
        });
      })
      .catch(function(err) {
        var isAbort = err && (err.name === 'AbortError');
        var isNetwork = err && /Failed to fetch|NetworkError/i.test(String(err.message || err));
        var retryable = isAbort || isNetwork;
        if (retryable && attempt < retries) {
          return wait(retryDelayMs * (attempt + 1)).then(function() { return doFetch(attempt + 1); });
        }
        throw err;
      })
      .finally(function() {
        if (timer) clearTimeout(timer);
      });
  }

  return doFetch(0);
}

/** Merge optional X-Admin-Token from sessionStorage when ADMIN_API_TOKEN is set server-side (see docs/ADMIN_API.md). */
function mergeAdminHeaders(headers) {
  var out = {};
  if (headers && typeof headers === 'object' && !(headers instanceof Headers)) {
    Object.keys(headers).forEach(function(k) { out[k] = headers[k]; });
  }
  try {
    var t = sessionStorage.getItem('admin_api_token');
    if (t) out['X-Admin-Token'] = t;
  } catch (e) {}
  return out;
}

/** Prompt once per tab to store ADMIN_API_TOKEN for guarded write APIs (journal close, sync, etc.). */
function promptForAdminApiToken() {
  try {
    if (sessionStorage.getItem('admin_api_token')) return true;
  } catch (e) {
    return false;
  }
  var t = window.prompt(
    'This action requires your Admin API token (same value as ADMIN_API_TOKEN in .env).\n\n' +
    'Paste the token below (stored only in this browser tab):'
  );
  if (!t || !String(t).trim()) return false;
  try {
    sessionStorage.setItem('admin_api_token', String(t).trim());
    return true;
  } catch (e) {
    alert('Could not save token to sessionStorage: ' + e);
    return false;
  }
}

/** fetch + JSON body; merges X-Admin-Token; on 403 prompts for token once and retries. */
function adminFetchJson(url, options, _retried) {
  var opts = options || {};
  var headers = mergeAdminHeaders(opts.headers || {});
  if (opts.body && typeof opts.body === 'object' && !(opts.body instanceof FormData)) {
    opts.body = JSON.stringify(opts.body);
    if (!headers['Content-Type']) headers['Content-Type'] = 'application/json';
  }
  opts.headers = headers;
  return fetch(url, opts).then(function(res) {
    return res.json().then(function(data) {
      if (res.status === 403 && !_retried && promptForAdminApiToken()) {
        return adminFetchJson(url, options, true);
      }
      if (!res.ok) {
        var err = (data && data.error) || ('Request failed (' + res.status + ')');
        if (res.status === 403) {
          err =
            'Access denied (403). Paste ADMIN_API_TOKEN from .env in the prompt, or run in console: ' +
            "sessionStorage.setItem('admin_api_token', '<token>'); location.reload();";
        }
        throw new Error(err);
      }
      return data;
    });
  });
}

function loadBacktestApiExtras() {
  var el = document.getElementById('perf-backtest-api-extra');
  if (!el) return;
  el.innerHTML = '<span style="color:var(--text-dim);">Loading /api/backtest/suggestions and /api/backtest/performance...</span>';
  Promise.all([
    fetch('/api/backtest/suggestions').then(function(r) { return r.json(); }),
    fetch('/api/backtest/performance').then(function(r) { return r.json(); })
  ]).then(function(pair) {
    var sugg = pair[0] || {};
    var perfApi = pair[1] || {};
    var sugStr = sugg.error ? ('Error: ' + escapeHtml(String(sugg.error))) : escapeHtml(JSON.stringify(sugg).slice(0, 1500));
    var perfStr = perfApi.note
      ? escapeHtml(String(perfApi.note))
      : escapeHtml(JSON.stringify(perfApi).slice(0, 800));
    el.innerHTML =
      '<div style="font-size:12px;color:var(--text-secondary);line-height:1.5;">' +
      '<strong>/api/backtest/suggestions</strong><pre style="margin:6px 0;font-size:11px;white-space:pre-wrap;word-break:break-word;">' + sugStr + '</pre>' +
      '<strong>/api/backtest/performance</strong><p style="margin:6px 0;">' + perfStr + '</p>' +
      '</div>';
  }).catch(function(e) {
    el.innerHTML = '<span style="color:var(--red);">Failed: ' + escapeHtml(e.message) + '</span>';
  });
}

function resendOptimizerReport() {
  if (!window.confirm('Resend latest optimizer report email with fresh data (today\'s window)?')) return;
  fetch('/api/optimizer/resend', {
    method: 'POST',
    headers: mergeAdminHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ for_today_only: true })
  })
    .then(function(r) { return r.json().then(function(j) { return { ok: r.ok, status: r.status, j: j }; }); })
    .then(function(x) {
      if (!x.ok) throw new Error((x.j && (x.j.error || x.j.message)) || ('HTTP ' + x.status));
      alert((x.j && x.j.message) ? x.j.message : 'Done.');
      loadPerformanceTab();
    })
    .catch(function(e) { alert('Resend failed: ' + e.message); });
}

function runRoadmapAutomationCycle(dryRun) {
  var msg = dryRun
    ? 'Run roadmap automation cycle in dry-run mode (no state writes)?'
    : 'Run roadmap automation LIVE? This may apply transitions and write roadmap state.';
  if (!window.confirm(msg)) return;
  fetch('/api/roadmap/automation/run', {
    method: 'POST',
    headers: mergeAdminHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ dry_run: !!dryRun })
  })
    .then(function(r) { return r.json().then(function(j) { return { ok: r.ok, status: r.status, j: j }; }); })
    .then(function(x) {
      if (!x.ok) throw new Error((x.j && (x.j.error || x.j.message)) || ('HTTP ' + x.status));
      alert('Roadmap cycle finished. Check logs or refresh Roadmap tab for details.');
      loadRoadmapTab();
    })
    .catch(function(e) { alert('Roadmap automation failed: ' + e.message); });
}

function loadPerformanceTab() {
  var perfContainer = document.getElementById('tab-performance');
  var headerEl = document.getElementById('perf-recursive-header');
  if (headerEl) headerEl.innerHTML = '<span style="color:var(--text-dim);">Loading...</span>';
  fetchJsonWithRetry('/api/optimizer/last-report', { retries: 2, timeoutMs: 8000, retryDelayMs: 500 })
    .then(function(data) {
      if (data.error) {
        if (headerEl) headerEl.innerHTML = '<p style="color:var(--amber);">' + (data.error || 'No optimizer cycle run yet') + '. Run optimizer (4:00 AM ET) or resend report to see data.</p>';
        document.getElementById('perf-last-cycle-label').textContent = 'Last cycle: —';
        renderRecursiveSectionsEmpty();
      } else {
        renderRecursiveReportSections(data);
        var snap = data.snapshot || {};
        document.getElementById('perf-last-cycle-label').textContent = 'Last cycle: #' + (snap.cycle || '—') + ' — ' + (snap.date || '—');
      }
      return fetch('/api/monthly-review?days=30');
    })
    .catch(function(e) {
      console.error('Load performance tab:', e);
      var msg = String((e && e.message) || e || '');
      var backendHint = /Failed to fetch|NetworkError|AbortError|timed out/i.test(msg)
        ? 'Cannot reach backend API. Verify the Flask server is running and reachable from this dashboard.'
        : msg;
      if (headerEl) headerEl.innerHTML = '<p style="color:var(--amber);">Failed to load report. ' + backendHint + '</p>';
      document.getElementById('perf-last-cycle-label').textContent = 'Last cycle: —';
      renderRecursiveSectionsEmpty();
      return fetch('/api/monthly-review?days=30');
    })
    .then(function(res) { return res && res.ok ? res.json() : null; })
    .then(function(reviewData) {
      if (!reviewData || reviewData.error) return;
      var perf = reviewData.performance || {};
      var attr = reviewData.model_attribution || {};
      var stats = (reviewData.performance || {});
      var sharpe = perf.sharpe_ratio;
      var sharpeHero = document.getElementById('perf-sharpe-hero');
      if (sharpeHero) {
        sharpeHero.textContent = sharpe != null ? Number(sharpe).toFixed(2) : '—';
        sharpeHero.className = 'perf-metric-value ' + (typeof sharpeColorClass === 'function' ? sharpeColorClass(sharpe) : '');
      }
      var perfSortino = document.getElementById('perf-sortino');
      if (perfSortino) perfSortino.textContent = perf.sortino_ratio != null ? Number(perf.sortino_ratio).toFixed(2) : '—';
      var perfMaxdd = document.getElementById('perf-maxdd');
      if (perfMaxdd) perfMaxdd.textContent = perf.max_drawdown_pct != null ? perf.max_drawdown_pct + '%' : '—';
      var perfExp = document.getElementById('perf-expectancy');
      if (perfExp) perfExp.textContent = perf.expectancy_pct != null ? perf.expectancy_pct + '%' : '—';
      var perfWr = document.getElementById('perf-winrate');
      if (perfWr) perfWr.textContent = perf.win_rate_pct != null ? perf.win_rate_pct + '%' : '—';
      var regimeOrder = ['BULL', 'BEAR', 'SIDEWAYS', 'VOLATILE'];
      var regimeData = perf.win_rate_by_regime || {};
      var regimeTbody = document.getElementById('perf-regime-tbody');
      if (regimeTbody) {
        var regimes = regimeOrder.filter(function(r) { return regimeData[r]; }).concat(Object.keys(regimeData).filter(function(r) { return regimeOrder.indexOf(r) < 0; }));
        regimeTbody.innerHTML = regimes.length ? regimes.map(function(r) {
          var d = regimeData[r];
          return '<tr><td>' + r + '</td><td>' + (d.trades || 0) + '</td><td>' + (d.win_rate_pct != null ? d.win_rate_pct : '—') + '%</td><td>' + (d.avg_win_pct != null ? d.avg_win_pct : '—') + '%</td><td>' + (d.avg_loss_pct != null ? d.avg_loss_pct : '—') + '%</td><td>' + (d.total_pnl_pct != null ? d.total_pnl_pct : '—') + '%</td></tr>';
        }).join('') : '<tr><td colspan="6" style="text-align:center;color:var(--text-dim);">No regime data yet.</td></tr>';
      }
      var scenarioLabels = { default: 'Default', sniper: 'Sniper', dip: 'Dip', blood_in_the_street: 'Blood in the street', pullback_in_trend: 'Pullback', bull_participant: 'Bull participant', vol_contraction: 'Vol contraction', volatility_harvest: 'Volatility harvest', capitulation: 'Capitulation', rally: 'Rally' };
      var regimeScenarioData = perf.win_rate_by_regime_scenario || {};
      var regimeScenarioTbody = document.getElementById('perf-regime-scenario-tbody');
      if (regimeScenarioTbody) {
        var keys = Object.keys(regimeScenarioData);
        if (keys.length) {
          keys.sort(function(a, b) {
            var pa = a.split(' · '), pb = b.split(' · ');
            var ra = regimeOrder.indexOf(pa[0]) >= 0 ? regimeOrder.indexOf(pa[0]) : 99;
            var rb = regimeOrder.indexOf(pb[0]) >= 0 ? regimeOrder.indexOf(pb[0]) : 99;
            return ra !== rb ? ra - rb : (pa[1] || '').localeCompare(pb[1] || '');
          });
          regimeScenarioTbody.innerHTML = keys.map(function(key) {
            var parts = key.split(' · ');
            var d = regimeScenarioData[key];
            var scenarioLabel = scenarioLabels[parts[1]] || parts[1] || 'default';
            return '<tr><td>' + (parts[0] || key) + '</td><td>' + scenarioLabel + '</td><td>' + (d.trades || 0) + '</td><td>' + (d.win_rate_pct != null ? d.win_rate_pct : '—') + '%</td><td>' + (d.avg_win_pct != null ? d.avg_win_pct : '—') + '%</td><td>' + (d.avg_loss_pct != null ? d.avg_loss_pct : '—') + '%</td><td>' + (d.total_pnl_pct != null ? d.total_pnl_pct : '—') + '%</td></tr>';
          }).join('');
        } else {
          regimeScenarioTbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-dim);">No regime/scenario data yet.</td></tr>';
        }
      }
      var ranking = attr.monthly_ranking || [];
      var byModel = attr.by_model || {};
      var attrTbody = document.getElementById('perf-attribution-tbody');
      if (attrTbody) {
        var list = ranking.length ? ranking : Object.keys(byModel).map(function(m) { return { model: m }; });
        attrTbody.innerHTML = list.length ? list.map(function(r, i) {
          var d = byModel[r.model] || {};
          var sc = typeof sharpeColorClass === 'function' ? sharpeColorClass(d.sharpe) : '';
          return '<tr><td style="color:var(--text-dim)">' + (i + 1) + '</td><td>' + (r.model || '') + '</td><td>' + (d.trades || 0) + '</td><td>' + (d.win_rate_pct != null ? d.win_rate_pct : '—') + '%</td><td>$' + (d.pnl_usd != null ? d.pnl_usd : 0).toLocaleString('en-US', {maximumFractionDigits:0}) + '</td><td class="' + sc + '">' + (d.sharpe != null ? d.sharpe : '—') + '</td></tr>';
        }).join('') : '<tr><td colspan="6" style="text-align:center;color:var(--text-dim);">No model attribution data yet.</td></tr>';
      }
      var recsEl = document.getElementById('perf-attribution-recs');
      if (recsEl) recsEl.innerHTML = (attr.recommendations || []).length ? '<strong>Recommendations:</strong> ' + (attr.recommendations || []).join(' ') : '';
    });

  // Load equity curve + P&L charts
  if (typeof loadPerformanceCharts === 'function') loadPerformanceCharts();
  loadBacktestApiExtras();
}

function renderRecursiveSectionsEmpty() {
  ['perf-paper-evidence','perf-regression-status','perf-weekly-consolidated','perf-loss-analysis','perf-hold-missed','perf-path-performance','perf-manual-overrides','perf-auto-applied','perf-recommendations-review','perf-evaluations'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) { el.innerHTML = ''; el.style.display = (id === 'perf-manual-overrides' || id === 'perf-weekly-consolidated' || id === 'perf-regression-status') ? 'none' : ''; }
  });
}

function renderRecursiveReportSections(data) {
  var snap = data.snapshot || {};
  var headerEl = document.getElementById('perf-recursive-header');
  var b7 = snap.booked_trades_7d != null ? snap.booked_trades_7d : 0, b30 = snap.booked_trades_30d != null ? snap.booked_trades_30d : 0;
  headerEl.innerHTML = '<div style="font-size: 15px; font-weight: 600; color: var(--cyan); margin-bottom: 8px;">Recursive Optimizer — Cycle #' + (snap.cycle || '—') + '</div>' +
    '<p style="margin: 4px 0; font-size: 14px;">' + (snap.date || '—') + ' &nbsp;|&nbsp; Regime: <b>' + (snap.regime || '—') + '</b> &nbsp;|&nbsp; ' + (snap.system_closed || 0) + ' closed / ' + (snap.system_open || 0) + ' open (system trades)</p>' +
    '<p style="margin: 4px 0; font-size: 13px; color: var(--text-dim);"><b>Booked (system trades)</b> — win rate (% of closed trades that were winners): 7d <b>' + (snap.win_rate_7d != null ? snap.win_rate_7d : '—') + '%</b> (' + b7 + ' trades) &nbsp;|&nbsp; 30d <b>' + (snap.win_rate_30d != null ? snap.win_rate_30d : '—') + '%</b> (' + b30 + ' trades)</p>' +
    '<p style="margin: 4px 0; font-size: 13px; color: var(--text-dim);">' + (snap.signals_screened_30d || 0) + ' signals screened (30d)</p>' +
    '<p style="margin: 4px 0; font-size: 13px; color: var(--text-dim);">Threshold changes: ' + (data.auto_applied || []).length + ' auto-applied &nbsp;|&nbsp; ' + ((data.loss_based_recommendations || []).length + (data.pending_review || []).length) + ' need review &nbsp;|&nbsp; ' + (data.evaluations || []).filter(function(e){ return e.status === 'maturing'; }).length + ' still maturing (waiting for more trades)' + (snap.manual_override_count ? ' &nbsp;|&nbsp; ' + snap.manual_override_count + ' manual override trades (excluded)' : '') + '</p>' +
    '<p style="margin: 6px 0 0; font-size: 12px; color: var(--text-dim);" title="Edge-driven sizing when edge_probability and rr_ratio are available">Position sizing: fractional Kelly (edge-driven) when edge &amp; R:R available.</p>';
  var paperEl = document.getElementById('perf-paper-evidence');
  if (paperEl) {
    var paperReport = data.paper_evidence_report || {};
    var paperAttr = paperReport.paper_attribution || {};
    var policySkips = paperReport.policy_skip_summary || {};
    var promos = data.promotion_recommendations || paperAttr.promotion_recommendations || [];
    if (paperAttr.closed_count || policySkips.total) {
      paperEl.style.display = 'block';
      var pexp = paperAttr.expectancy_pct;
      var pexpStr = pexp != null ? Number(pexp).toFixed(2) + '%' : 'n/a';
      var html = '<h3 style="margin:0 0 8px; font-size: 14px; color: #9b7ede;">Paper Evidence (30d)</h3>' +
        '<p style="margin: 4px 0; font-size: 13px;">Closed: <b>' + (paperAttr.closed_count || 0) + '</b> &nbsp;|&nbsp; Win rate: <b>' + (paperAttr.win_rate_pct != null ? paperAttr.win_rate_pct : '—') + '%</b> &nbsp;|&nbsp; Expectancy: <b>' + pexpStr + '</b> &nbsp;|&nbsp; MODERATE: <b>' + (paperAttr.moderate_count || 0) + '</b></p>';
      if (policySkips.total) {
        html += '<p style="margin: 4px 0; font-size: 13px; color: var(--text-dim);">Policy skips: <b>' + policySkips.total + '</b> &nbsp;|&nbsp; With 7d forward: <b>' + (policySkips.with_forward || 0) + '</b> &nbsp;|&nbsp; Regrets (&ge;5%): <b>' + (policySkips.regret_count || 0) + '</b>';
        if (policySkips.avg_forward_7d != null) html += ' &nbsp;|&nbsp; Avg fwd: <b>' + policySkips.avg_forward_7d + '%</b>';
        html += '</p>';
      }
      if (promos.length) {
        html += '<table class="history-table" style="margin-top:8px;"><thead><tr><th>Slice</th><th>n</th><th>WR%</th><th>Expectancy</th><th>Policy</th></tr></thead><tbody>';
        promos.slice(0, 8).forEach(function(pr) {
          var st = pr.stats || {};
          var slice = pr.slice || (pr.action && pr.action.slice) || '—';
          html += '<tr><td>' + escapeHtml(String(slice)) + '</td><td>' + (st.n_closed != null ? st.n_closed : '—') + '</td><td>' + (st.win_rate_pct != null ? st.win_rate_pct : '—') + '</td><td>' + (st.expectancy_pct != null ? st.expectancy_pct : '—') + '</td><td>' + escapeHtml(pr.policy || (pr.action && pr.action.policy) || '—') + '</td></tr>';
        });
        html += '</tbody></table>';
      }
      html += '<p style="margin: 8px 0 0; font-size: 11px; color: var(--text-dim);">Relaxations use paper at 0.75×; auto-apply requires live confirmation. Report: data/paper_evidence_report.json</p>';
      paperEl.innerHTML = html;
    } else {
      paperEl.style.display = 'none';
      paperEl.innerHTML = '';
    }
  }
  var regStatus = data.regression_model_status || {};
  var regEl = document.getElementById('perf-regression-status');
  if (regEl) {
    if (regStatus.active != null || regStatus.n_samples != null) {
      regEl.style.display = 'block';
      var trainedAt = regStatus.trained_at ? (regStatus.trained_at + '').substring(0, 10) : '—';
      var activeLabel = regStatus.active ? '<span style="color: var(--green);">Active</span>' : '<span style="color: var(--amber);">Fallback (additive only)</span>';
      regEl.innerHTML = '<h3 style="margin:0 0 8px; font-size: 14px; color: var(--cyan);">Regression Model Status</h3>' +
        '<p style="margin: 4px 0; font-size: 13px;">Trained: ' + escapeHtml(trainedAt) + ' &nbsp;|&nbsp; n_samples: <b>' + (regStatus.n_samples != null ? regStatus.n_samples : '—') + '</b>' + (regStatus.min_required != null ? ' (min ' + regStatus.min_required + ' to activate)' : '') + ' &nbsp;|&nbsp; ' + activeLabel + '</p>' +
        '<p style="margin: 0; font-size: 12px; color: var(--text-dim);" title="Weekly logistic regression on historical trades; blends with additive score when active">When active, regression probability is blended with additive score for quant tier.</p>';
    } else {
      regEl.style.display = 'none';
      regEl.innerHTML = '';
    }
  }
  var weekly = data.weekly_consolidated || [];
  var weeklyConflicts = data.weekly_conflicts || [];
  var weeklyEl = document.getElementById('perf-weekly-consolidated');
  if (weeklyEl) {
    if (weekly.length || weeklyConflicts.length) {
      weeklyEl.style.display = 'block';
      weeklyEl.innerHTML = '<h3 style="margin:0 0 8px; font-size: 14px; color: var(--cyan);">Weekly Consolidated Recommendations (Fri)</h3>' +
        (weeklyConflicts.length ? '<p style="margin:0 0 8px; font-weight: bold; color: var(--amber);">Conflicts (excluded):</p><ul style="margin:0 0 12px;">' + weeklyConflicts.map(function(c){ return '<li>' + escapeHtml(c) + '</li>'; }).join('') + '</ul>' : '') +
        (weekly.length ? '<p style="margin:0 0 8px;"><strong>Consolidated:</strong></p><ul style="margin:0;">' + weekly.map(function(r){ return '<li><b>' + escapeHtml(r.parameter || '') + '</b>: ' + (r.current_value != null ? r.current_value + ' → ' + r.suggested_value : r.suggested_value) + ' — ' + escapeHtml(r.reason || '') + '</li>'; }).join('') + '</ul>' : '');
    } else {
      weeklyEl.style.display = 'none';
    }
  }
  var lossAnalysis = data.loss_analysis || {};
  var losses = lossAnalysis.losses || [];
  var wins = lossAnalysis.wins || [];
  var lossEl = document.getElementById('perf-loss-analysis');
  lossEl.innerHTML = '<h3 style="margin:0 0 8px; font-size: 14px; color: var(--red);">Loss Analysis (system trades only)</h3>' +
    '<p style="margin: 4px 0;">' + losses.length + ' losses vs ' + wins.length + ' wins &nbsp;|&nbsp; Avg loss: ' + (lossAnalysis.avg_loss_pct != null ? lossAnalysis.avg_loss_pct.toFixed(1) : '0') + '% &nbsp;|&nbsp; Avg win: +' + (lossAnalysis.avg_win_pct != null ? lossAnalysis.avg_win_pct.toFixed(1) : '0') + '%</p>' +
    (losses.length ? '<table class="history-table" style="margin-top:8px;"><thead><tr><th>Trade</th><th>Coin</th><th>P&L</th><th>Regime</th><th>Path</th><th>Score</th><th>Exit</th></tr></thead><tbody>' +
      losses.map(function(p) {
        var tid = p.trade_id || ('TRD-' + (p.id != null ? String(p.id).padStart(5,'0') : '0'));
        var sym = p.coin_symbol || p.coin_id || '?';
        var pnl = p.pnl_pct != null ? p.pnl_pct : 0;
        var regime = (p.market_regime || p.regime || '?').toUpperCase();
        var path = p.quant_path || 'default';
        var score = p.confirmation_score != null ? p.confirmation_score : '';
        var exitR = (p.exit_reason || '?').replace(/_/g, ' ');
        if (exitR === 'Hit AI Target') exitR = 'Hit target';
        return '<tr><td>' + escapeHtml(tid) + '</td><td>' + escapeHtml(sym) + '</td><td style="color:var(--red)">' + Number(pnl).toFixed(1) + '%</td><td>' + escapeHtml(regime) + '</td><td>' + escapeHtml(path) + '</td><td>' + escapeHtml(String(score)) + '</td><td>' + escapeHtml(exitR) + '</td></tr>';
      }).join('') + '</tbody></table>' +
      '<p style="margin:8px 0 4px; font-weight: bold;">Patterns:</p><ul style="margin:0 0 8px;">' +
      (lossAnalysis.loss_regimes && Object.keys(lossAnalysis.loss_regimes).length ? '<li>All ' + losses.length + ' losses in ' + Object.keys(lossAnalysis.loss_regimes).join(', ') + '</li>' : '') +
      (lossAnalysis.score_gap != null ? '<li>Score gap: ' + lossAnalysis.score_gap + ' pts</li>' : '') +
      '<li>Stop-loss hits: ' + (lossAnalysis.stop_loss_hits || 0) + '/' + losses.length + '</li>' +
      '</ul>' +
      ((data.loss_based_recommendations || []).length ? '<p style="margin:8px 0 4px; font-weight: bold;">Threshold recommendations from losses:</p><ul style="margin:0;">' + (data.loss_based_recommendations || []).map(function(r){ return '<li><b>' + escapeHtml(r.parameter || '') + '</b>: ' + (r.current_value != null ? r.current_value + ' → ' + r.suggested_value : r.suggested_value) + ' — ' + escapeHtml(r.reason || '') + '</li>'; }).join('') + '</ul>' : '')
    : '<p style="color:var(--text-dim);">No losses in system trades.</p>');
  var screenSum = data.screening_summary || {};
  var missed = data.missed_summary || {};
  var totalScreen = screenSum.total || 0;
  var tiers = screenSum.tier_breakdown || {};
  var tierParts = Object.keys(tiers).length ? Object.keys(tiers).sort(function(a,b){ return (tiers[b]||0) - (tiers[a]||0); }).map(function(k){ return k + ': ' + tiers[k]; }).join(', ') : '';
  var topMissed = data.top_missed_by_regret || [];
  var tierToParam = { 'MODERATE_BUY': 'DIP_MIN_SCORE', 'STRONG_BUY': 'PULLBACK_MIN_SCORE', 'ULTRA_BUY': 'BEAR_REGIME_MIN_SCORE', 'HOLD': 'BEAR_REGIME_MIN_SCORE', 'WEAK_BUY': 'DIP_MIN_SCORE' };
  var missedHtml = '<h3 style="margin:0 0 8px; font-size: 14px; color: var(--green);">Hold &amp; Missed Opportunity</h3>' +
    '<p style="margin: 4px 0;">' + totalScreen + ' signals screened in last 30d</p>' +
    (tierParts ? '<p style="margin: 4px 0;">Breakdown: ' + tierParts + '</p>' : '') +
    '<p style="margin: 4px 0;">' + (missed.recommendations_missed != null ? missed.recommendations_missed : 0) + ' HOLD/WEAK_BUY later gained &gt;5% in 7d &nbsp;|&nbsp; ' + (missed.screening_missed != null ? missed.screening_missed : 0) + ' screening failures later gained &gt;5%</p>';
  if (topMissed.length) {
    missedHtml += '<p style="margin: 8px 0 4px; font-weight: bold;">Top missed by regret (7d gain % — suggested threshold fix):</p>' +
      '<table class="history-table" style="margin-top:6px;"><thead><tr><th>Symbol</th><th>Regret (7d %)</th><th>Source</th><th>Tier</th><th>Suggested fix</th></tr></thead><tbody>';
    topMissed.forEach(function(m) {
      var sym = m.symbol || m.coin_id || '?';
      var o7 = m.outcome_7d != null ? Number(m.outcome_7d).toFixed(1) : '—';
      var src = m.source || '—';
      var tier = (m.quant_tier || m.consensus_signal || m.consensus_quality || 'HOLD').toString().trim().toUpperCase() || 'HOLD';
      var param = tierToParam[tier] || 'DIP_MIN_SCORE';
      missedHtml += '<tr><td>' + escapeHtml(sym) + '</td><td style="color:var(--green);">+' + o7 + '%</td><td>' + escapeHtml(src) + '</td><td>' + escapeHtml(tier) + '</td><td style="font-size:12px; color:var(--text-dim);">Relax ' + escapeHtml(param) + '</td></tr>';
    });
    missedHtml += '</tbody></table>';
  }
  document.getElementById('perf-hold-missed').innerHTML = missedHtml;
  var pathPerf = data.path_performance || [];
  var pathEl = document.getElementById('perf-path-performance');
  pathEl.innerHTML = '<h3 style="margin:0 0 8px; font-size: 14px; color: var(--cyan);">Performance by Regime & Quant Path (system trades only)</h3>' +
    (pathPerf.length ? '<table class="history-table" style="margin-top:8px;"><thead><tr><th>Regime</th><th>Path</th><th>Trades</th><th>Win Rate</th><th>Avg Win</th><th>Avg Loss</th><th>Net P&L</th></tr></thead><tbody>' +
      pathPerf.map(function(row) {
        var wr = row.win_rate != null ? row.win_rate : 0;
        var wrColor = wr >= 55 ? 'var(--green)' : wr < 45 ? 'var(--red)' : 'var(--text-primary)';
        var pnlColor = (row.net_pnl != null && row.net_pnl >= 0) ? 'var(--green)' : 'var(--red)';
        return '<tr><td>' + escapeHtml(row.regime || '') + '</td><td>' + escapeHtml(row.path || '') + '</td><td>' + (row.trades || 0) + '</td><td style="color:' + wrColor + '">' + Number(wr).toFixed(1) + '%</td><td style="color:var(--green)">+' + (row.avg_win != null ? Number(row.avg_win).toFixed(1) : '0') + '%</td><td style="color:var(--red)">' + (row.avg_loss != null ? Number(row.avg_loss).toFixed(1) : '0') + '%</td><td style="color:' + pnlColor + '">$' + (row.net_pnl != null ? Number(row.net_pnl).toFixed(2) : '0') + '</td></tr>';
      }).join('') + '</tbody></table>' : '<p style="color:var(--text-dim);">Insufficient system trades for path performance table.</p>');
  var manualOverrides = data.manual_override_positions || [];
  var manualEl = document.getElementById('perf-manual-overrides');
  if (manualOverrides.length) {
    manualEl.style.display = 'block';
    manualEl.innerHTML = '<h3 style="margin:0 0 8px; font-size: 14px; color: var(--amber);">Manual Override Trades (excluded from analysis)</h3><p style="font-size: 13px; color: var(--text-dim); margin-bottom: 8px;">These positions were entered when the system said HOLD.</p>' +
      manualOverrides.map(function(p){ var sym = p.coin_symbol || p.coin_id || '?'; var regime = (p.market_regime || p.regime || '?').toUpperCase(); var pnl = p.pnl_pct != null ? p.pnl_pct : 0; return '<p style="margin:2px 0;">' + escapeHtml(sym) + ' (' + escapeHtml(regime) + ') — ' + Number(pnl).toFixed(1) + '% — CLOSED</p>'; }).join('');
  } else {
    manualEl.style.display = 'none';
    manualEl.innerHTML = '';
  }
  var auto = data.auto_applied || [];
  document.getElementById('perf-auto-applied').innerHTML = '<h3 style="margin:0 0 8px; font-size: 14px; color: var(--green);">Auto-applied threshold changes</h3>' +
    '<p style="margin:0 0 8px; font-size: 12px; color: var(--text-dim);" title="When enough trades exist, recommendations are validated on a 70/30 time-ordered split.">Walk-forward validated when sufficient trades.</p>' +
    (auto.length ? '<ul style="margin:0;">' + auto.map(function(a){ return '<li><b>' + escapeHtml(a.parameter || '') + '</b>: ' + a.old_value + ' → ' + a.new_value + '</li>'; }).join('') + '</ul>' : '<p style="color:var(--text-dim);">No changes met the safety criteria this cycle.</p>');
  var allRecs = (data.loss_based_recommendations || []).concat(data.pending_review || []);
  document.getElementById('perf-recommendations-review').innerHTML = '<h3 style="margin:0 0 8px; font-size: 14px; color: var(--amber);">Recommendations needing your review</h3>' +
    (allRecs.length ? allRecs.map(function(r) {
      var recId = r.rec_id || ('rec-' + (snap.cycle || '') + '-' + (r.parameter || 'unknown').replace(/\s/g, '_') + '-' + (r.suggested_value != null ? r.suggested_value : '0'));
      var param = r.parameter || '?';
      var suggested = r.suggested_value;
      var applyCmd = (param && typeof suggested === 'number') ? 'python -c "from recursive_optimizer import apply_single; apply_single(\'' + param + '\', ' + suggested + ')"' : '';
      var deferCmd = 'python -c "from recursive_optimizer import defer_recommendation; defer_recommendation(\'' + recId + '\')"';
      return '<div style="margin-bottom: 12px; padding: 8px; background: var(--bg-deep); border-radius: 6px;"><b>' + escapeHtml(param) + '</b>' + (r.description ? ' (' + escapeHtml(r.description) + ')' : '') + '<br>' +
        (r.current_value != null && suggested != null ? 'Suggested: ' + r.current_value + ' → ' + suggested + '<br>' : (suggested ? 'Suggested: ' + escapeHtml(String(suggested)) + '<br>' : '')) +
        '<span style="color:var(--text-dim);">' + escapeHtml(r.reason || '') + '</span><br>' +
        '<span style="font-size: 11px;">ID: <code>' + escapeHtml(recId) + '</code></span>' +
        (applyCmd ? '<br><span style="font-size: 11px;">To apply: <code>' + escapeHtml(applyCmd) + '</code></span>' : '') +
        '<br><span style="font-size: 11px;">To defer: <code>' + escapeHtml(deferCmd) + '</code></span></div>';
    }).join('') : '<p style="color:var(--text-dim);">No pending recommendations this cycle.</p>');
  var evals = data.evaluations || [];
  document.getElementById('perf-evaluations').innerHTML = '<h3 style="margin:0 0 8px; font-size: 14px; color: var(--cyan);">How are applied changes performing?</h3>' +
    (evals.length ? evals.map(function(e){ return '<p style="margin: 4px 0;"><b>' + escapeHtml(e.parameter || '') + '</b> ' + e.old_value + ' → ' + e.new_value + ' on ' + (e.applied_at ? e.applied_at.substring(0,10) : '—') + ' &nbsp;|&nbsp; ' + (e.trades_since || 0) + '/' + (e.target_trades || 0) + ' trades &nbsp;|&nbsp; ' + (e.status || '').toUpperCase() + '</p><p style="margin:0 0 8px; color:var(--text-dim);">' + escapeHtml(e.verdict || '') + '</p>'; }).join('') : '<p style="color:var(--text-dim);">No previous changes to evaluate yet.</p>');
}

function escapeHtml(s) {
  if (s == null) return '';
  var div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

function openMonthlyReviewModal() {
  fetch('/api/monthly-review?days=30')
    .then(function(res) { return res.json(); })
    .then(function(data) {
    if (data.error) {
      document.getElementById('monthly-review-content').innerHTML = '<p style="color:var(--red);">' + data.error + '</p>';
    } else {
      var perf = data.performance || {};
      var attr = data.model_attribution || {};
      var risk = data.risk || {};
      var sharpe = perf.sharpe_ratio;
      var sharpeCls = sharpeColorClass(sharpe);
      var sharpeColor = sharpeCls === 'sharpe-green' ? 'var(--green)' : sharpeCls === 'sharpe-yellow' ? 'var(--amber)' : sharpeCls === 'sharpe-red' ? 'var(--red)' : 'var(--text-mono)';
      var html = '<div style="display:grid;gap:24px;">';
      html += '<div style="padding:20px;background:var(--bg-card);border:1px solid var(--border-dim);border-radius:var(--radius-lg);">';
      html += '<div style="font-size:11px;color:var(--text-dim);text-transform:uppercase;margin-bottom:6px;">Sharpe Ratio</div>';
      html += '<div style="font-family:var(--font-data);font-size:36px;font-weight:700;color:' + sharpeColor + ';">' + (sharpe != null ? Number(sharpe).toFixed(2) : '\u2014') + '</div>';
      html += '<div style="font-size:11px;color:var(--text-dim);margin-top:4px;">Risk-adjusted return. Target &gt;1.8 for this strategy.</div></div>';
      html += '<div><h4 style="margin:0 0 8px 0;color:var(--cyan);">Key Metrics</h4><p>Sortino: ' + (perf.sortino_ratio != null ? perf.sortino_ratio : '\u2014') + ' | Max DD: ' + (perf.max_drawdown_pct != null ? perf.max_drawdown_pct : '\u2014') + '% | Expectancy: ' + (perf.expectancy_pct != null ? perf.expectancy_pct : '\u2014') + '% | Win rate: ' + (perf.win_rate_pct != null ? perf.win_rate_pct : '\u2014') + '% | P&L: $' + (perf.total_pnl_usd != null ? perf.total_pnl_usd : 0).toLocaleString('en-US', {maximumFractionDigits:0}) + '</p></div>';
      html += '<div><h4 style="margin:0 0 8px 0;color:var(--cyan);">Risk</h4><p>Total exposure: ' + (risk.open_exposure_pct != null ? risk.open_exposure_pct : risk.total_risk_pct != null ? risk.total_risk_pct : '\u2014') + '% | Max position: ' + (risk.max_single_position_pct != null ? risk.max_single_position_pct : '\u2014') + '% | 95% VaR: $' + (risk.var_1d_usd != null ? risk.var_1d_usd : 0).toLocaleString('en-US', {maximumFractionDigits:0}) + '</p></div>';
      var ranking = attr.monthly_ranking || [];
      if (ranking.length) {
        html += '<div><h4 style="margin:0 0 8px 0;color:var(--cyan);">Model Attribution (Ranking)</h4><table style="width:100%;font-size:13px;"><tr><th style="text-align:left">#</th><th>Model</th><th>P&L $</th><th>Win Rate</th><th>Sharpe</th></tr>';
        ranking.forEach(function(r, i) {
          html += '<tr><td>' + (i+1) + '</td><td>' + (r.model || '') + '</td><td>$' + (r.pnl_usd != null ? r.pnl_usd : 0).toLocaleString('en-US', {maximumFractionDigits:0}) + '</td><td>' + (r.win_rate_pct != null ? r.win_rate_pct : '\u2014') + '%</td><td>' + (r.sharpe != null ? r.sharpe : '\u2014') + '</td></tr>';
        });
        html += '</table></div>';
      }
      var recs = attr.recommendations || [];
      if (recs.length) {
        html += '<div><h4 style="margin:0 0 8px 0;color:var(--cyan);">Model Recommendations</h4><ul style="margin:0;padding-left:20px;">' + recs.map(function(r) { return '<li>' + r.replace(/</g, '&lt;') + '</li>'; }).join('') + '</ul></div>';
      }
      html += '</div>';
      document.getElementById('monthly-review-content').innerHTML = html;
    }
    document.getElementById('monthly-review-modal').classList.add('open');
    })
    .catch(function(e) {
      console.error('Load monthly review:', e);
      document.getElementById('monthly-review-content').innerHTML = '<p style="color:var(--red);">Failed to load: ' + e.message + '</p>';
      document.getElementById('monthly-review-modal').classList.add('open');
    });
}

function closeMonthlyReviewModal() {
  document.getElementById('monthly-review-modal').classList.remove('open');
}

function runBacktestForPerformance() {
  var btn = document.getElementById('perf-run-backtest-btn');
  var statusEl = document.getElementById('perf-backtest-status');
  var summaryEl = document.getElementById('perf-walkforward-summary');
  if (btn) btn.disabled = true;
  if (statusEl) statusEl.textContent = 'Running...';
  fetch('/api/backtest/run', { method: 'POST', headers: mergeAdminHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ days: 90 }) })
    .then(function(res) { return res.json(); })
    .then(function(data) {
    if (data.error) {
      if (statusEl) statusEl.textContent = 'Error: ' + data.error;
    } else {
      var wf = data.walk_forward || {};
      var periods = wf.periods || [];
      var passed = data.sharpe_passed;
      if (statusEl) statusEl.textContent = passed ? 'Sharpe >1.0 passed' : 'Sharpe below threshold';
      if (summaryEl) {
        var avgSharpe = periods.length ? (periods.reduce(function(a,p){ return a + (p.sharpe_ratio || 0); }, 0) / periods.length).toFixed(2) : '—';
        var s = '<strong>Average Sharpe across test periods:</strong> ' + avgSharpe + ' | Periods: ' + periods.length + ' | Sharpe &gt;1.0: ' + (passed ? 'Yes' : 'No');
        if (periods.length) {
          var last = periods[periods.length-1];
          s += '<br><span style="color:var(--text-dim);">Last period: ' + (last.start || '') + ' to ' + (last.end || '') + ' — Trades: ' + (last.trades || 0) + ', Win rate: ' + (last.win_rate_pct != null ? last.win_rate_pct : '—') + '%, Sharpe: ' + (last.sharpe_ratio != null ? last.sharpe_ratio : '—') + '</span>';
        }
        summaryEl.innerHTML = s;
      }
    }
    if (btn) btn.disabled = false;
    })
    .catch(function(e) {
      if (statusEl) statusEl.textContent = 'Error: ' + e.message;
      if (btn) btn.disabled = false;
    });
}

function loadLearningReportSummary() {
  var statusEl = document.getElementById('perf-learning-status');
  var summaryEl = document.getElementById('perf-learning-summary');
  if (statusEl) statusEl.textContent = 'Loading...';
  fetch('/api/learning-report')
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (statusEl) statusEl.textContent = '';
      if (data.note && data.run_timestamp == null) {
        if (summaryEl) summaryEl.innerHTML = '<span style="color:var(--text-dim);">' + data.note + '</span>';
        return;
      }
      var runTs = data.run_timestamp ? new Date(data.run_timestamp).toLocaleString() : '—';
      var backtest = data.backtest || {};
      var sharpePassed = backtest.sharpe_passed != null ? (backtest.sharpe_passed ? 'Yes' : 'No') : '—';
      var totalSignals = backtest.total_signals != null ? backtest.total_signals : '—';
      var missed = data.missed_opportunity || {};
      var recMissed = (missed.summary && missed.summary.recommendations_missed != null) ? missed.summary.recommendations_missed : 0;
      var screenMissed = (missed.summary && missed.summary.screening_missed != null) ? missed.summary.screening_missed : 0;
      var suggCount = (data.suggestions && data.suggestions.length) ? data.suggestions.length : 0;
      var html = '<strong>Last run:</strong> ' + runTs + '<br>';
      html += '<strong>Backtest (90d):</strong> Sharpe passed ' + sharpePassed + ' | Signals ' + totalSignals + '<br>';
      html += '<strong>Missed opportunities:</strong> ' + recMissed + ' from recommendations, ' + screenMissed + ' from screening<br>';
      html += '<strong>Suggestions:</strong> ' + suggCount + ' (quant / brain / veto / backtest). See data/learning_report.json for full report and workflow.';
      if (summaryEl) summaryEl.innerHTML = html;
    })
    .catch(function(e) {
      if (statusEl) statusEl.textContent = 'Error: ' + e.message;
      if (summaryEl) summaryEl.innerHTML = '<span style="color:var(--red);">Failed to load report.</span>';
    });
}

function loadExecutionSummary() {
  var days = document.getElementById('reports-exec-summary-days') ? document.getElementById('reports-exec-summary-days').value : '7';
  var bodyEl = document.getElementById('reports-exec-summary-body');
  var exportEl = document.getElementById('reports-exec-summary-export');
  if (bodyEl) bodyEl.textContent = 'Loading...';
  fetch('/api/reports/execution-summary?days=' + encodeURIComponent(days))
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (data.error) {
        if (bodyEl) bodyEl.textContent = 'Error: ' + data.error;
        return;
      }
      var lines = [
        'Days: ' + data.days,
        'Fill count: ' + data.fill_count,
        'Order count: ' + data.order_count,
        'Total fees (USD): ' + data.total_fees_usd,
        'Slippage bps (avg/min/max): ' + (data.slippage_bps_avg != null ? data.slippage_bps_avg : '—') + ' / ' + (data.slippage_bps_min != null ? data.slippage_bps_min : '—') + ' / ' + (data.slippage_bps_max != null ? data.slippage_bps_max : '—'),
        'By action: ' + (data.by_action && Object.keys(data.by_action).length ? JSON.stringify(data.by_action) : '—')
      ];
      if (bodyEl) bodyEl.textContent = lines.join('\n');
      if (exportEl) exportEl.href = '/api/reports/execution-summary?days=' + encodeURIComponent(days) + '&export=1';
    })
    .catch(function(e) {
      if (bodyEl) bodyEl.textContent = 'Error: ' + (e.message || 'Failed to load');
    });
}

function loadReconciliationHistory() {
  var limit = document.getElementById('reports-recon-history-limit') ? document.getElementById('reports-recon-history-limit').value : '50';
  var bodyEl = document.getElementById('reports-recon-history-body');
  if (bodyEl) bodyEl.textContent = 'Loading...';
  fetch('/api/reports/reconciliation-history?limit=' + encodeURIComponent(limit))
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (data.error) {
        if (bodyEl) bodyEl.textContent = 'Error: ' + data.error;
        return;
      }
      var entries = data.entries || [];
      if (entries.length === 0) {
        if (bodyEl) bodyEl.textContent = 'No entries yet. Run Reconcile or Sync position quantities to log.';
        return;
      }
      var lines = entries.map(function(e) {
        return (e.ts || '') + '  ' + (e.source || '') + '  ' + (e.status || '') + (e.summary ? '  ' + JSON.stringify(e.summary) : '') + (e.discrepancies && e.discrepancies.length ? '  discrepancies: ' + e.discrepancies.length : '');
      });
      if (bodyEl) bodyEl.textContent = lines.join('\n');
    })
    .catch(function(e) {
      if (bodyEl) bodyEl.textContent = 'Error: ' + (e.message || 'Failed to load');
    });
}

// Default overview data (paint immediately so tiles never appear blank)
function getDefaultOverviewData() {
  return {
    fear_greed: {},
    macro: {},
    sentiment: {},
    summary: { total: 0, buy: 0, hold: 0, sell: 0 },
    signal_30d: {},
    ai_30d: {},
    regime: { regime: 'UNKNOWN', confidence: 0 },
    onchain_status: 'fallback',
    onchain_overview: {},
    global_market_cap: {},
    portfolio_risk_summary: {},
    test_mode: false,
    coins: []
  };
}

// ── Overview tiles: fetch /api/summary immediately so regime, sentiment, onchain populate
function loadOverviewTiles() {
  fetch('/api/summary')
    .then(function(res) {
      if (!res.ok) throw new Error('Summary API ' + res.status);
      return res.json();
    })
    .then(function(data) {
      var merged = {
        fear_greed: data.fear_greed || {},
        macro: data.macro || {},
        sentiment: data.sentiment || {},
        summary: data.summary || {},
        regime: data.regime || { regime: 'UNKNOWN', confidence: 0 },
        onchain_status: data.onchain_status || 'fallback',
        onchain_overview: data.onchain_overview || {},
        global_market_cap: data.global_market_cap || {},
        portfolio_risk_summary: data.portfolio_risk_summary || {},
        cost_budget_status: data.cost_budget_status || {},
        signal_30d: data.signal_30d || {},
        ai_30d: data.ai_30d || {},
        test_mode: !!data.test_mode,
        last_updated: data.last_updated,
        coins: []
      };
      try {
        if (typeof updateOverview === 'function') updateOverview(merged);
      } catch (err) { console.error('updateOverview in loadOverviewTiles:', err); }
    })
    .catch(function(e) {
      console.error('loadOverviewTiles fetch:', e);
      try { if (typeof updateOverview === 'function') updateOverview(getDefaultOverviewData()); } catch (x) {}
    });
}

// ── Poll for background refresh: refetch when server last_updated changes ──
var dataPollInterval = null;
function startDataPoll() {
  if (dataPollInterval) return;
  dataPollInterval = setInterval(function() {
    fetch('/api/summary', { cache: 'no-store' })
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) {
        if (!data) return;
        var serverUpdated = (data.last_updated || '').toString();
        if (serverUpdated && serverUpdated !== lastDisplayedUpdated && typeof loadData === 'function') {
          console.log('Data updated on server, refreshing...');
          loadData();
        }
      })
      .catch(function() {});
  }, 50000);
}

// ── Init ─────────────────────────────────────────────────────
function initDashboard() {
  // Paint overview tiles with defaults IMMEDIATELY so they never appear blank
  try {
    if (typeof updateOverview === 'function') updateOverview(getDefaultOverviewData());
  } catch (e) { console.error('Initial updateOverview:', e); }
  loadOverviewTiles();
  loadData();
  loadExecutionConfig();
  loadTestMode();
  startDataPoll();
  (function () {
    var show = localStorage.getItem('showOnchainCols') === '1';
    var cb = document.getElementById('toggle-onchain-cols');
    if (cb) { cb.checked = show; toggleOnchainCols(show); }
  })();
  // Restore collapsed overview groups from localStorage
  restoreOverviewGroups();
  // Initialize Lucide SVG icons
  if (typeof lucide !== 'undefined') { try { lucide.createIcons(); } catch(e) { console.debug('Lucide init:', e); } }
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initDashboard);
} else {
  initDashboard();
}

// ── AI Brain Functions ────────────────────────────────────────

function buildPickCard(r, delay) {
  if (r.error) {
    return '<div class="pick-card" style="animation-delay:' + delay + 's">' +
      '<p style="color:var(--red);font-family:var(--font-data);font-size:12px">\u26A0 ' + r.error + '</p>' +
    '</div>';
  }

  var signal = r.signal || 'HOLD';
  var risks  = (r.key_risks || []).map(function(k) {
    return '<span class="risk-tag">' + k + '</span>';
  }).join('');

  var risksBlock = risks ? '<div class="pick-risks"><div class="pick-risks-label">KEY RISKS</div><div class="risk-tags">' + risks + '</div></div>' : '';
  var altBlock = r.alternative ? '<div class="pick-alt">\u2190 Alt: <span>' + r.alternative + '</span></div>' : '';
  var macroBlock = r.macro_impact ? '<div class="pick-alt" style="margin-top:4px">\uD83C\uDF0D <span style="color:var(--text-dim)">' + r.macro_impact + '</span></div>' : '';

  return '<div class="pick-card ' + signal + '" style="animation-delay:' + delay + 's">' +
      '<div class="pick-header">' +
        '<div class="pick-coin"><div>' +
          '<div class="pick-coin-name">' + (r.coin_name || '—') + '</div>' +
          '<div class="pick-coin-sym">' + (r.coin_symbol || '') + ' · $' + (r.coin_price ? Number(r.coin_price).toLocaleString() : '—') + '</div>' +
        '</div></div>' +
        '<div class="pick-meta">' +
          '<span class="signal-badge ' + signal + '">' + signal + '</span>' +
          '<span class="confidence-badge ' + (r.confidence || 'Low') + '">' + (r.confidence || '—') + '</span>' +
          '<span class="pick-timeframe">' + (r.timeframe || '') + '</span>' +
        '</div>' +
      '</div>' +
      '<div class="pick-hypothesis">' + (r.hypothesis || '—') + '</div>' +
      '<div class="pick-grid">' +
        '<div class="pick-stat"><div class="pick-stat-label">Entry</div><div class="pick-stat-value" style="font-size:11px;color:var(--text-secondary)">' + (r.entry_strategy || '—') + '</div></div>' +
        '<div class="pick-stat"><div class="pick-stat-label">R:R Ratio</div><div class="pick-stat-value" style="color:var(--cyan)">' + (r.risk_reward || '—') + '</div></div>' +
        '<div class="pick-stat"><div class="pick-stat-label">Stop Loss</div><div class="pick-stat-value" style="color:var(--red)">' + (r.stop_loss || '—') + '</div></div>' +
        '<div class="pick-stat"><div class="pick-stat-label">Take Profit</div><div class="pick-stat-value" style="color:var(--green)">' + (r.take_profit || '—') + '</div></div>' +
      '</div>' +
      risksBlock +
      '<div class="pick-summary">\uD83D\uDCA1 ' + (r.summary || '—') + '</div>' +
      altBlock + macroBlock +
      '<div class="pick-analyzed">Analyzed at ' + (r.analyzed_at || '—') + ' ' + (r.from_cache ? '(cached)' : '') + '</div>' +
    '</div>';
}

function loadTopPicks() {
  var btn = document.getElementById('top-picks-btn');
  var icon = document.getElementById('picks-btn-icon');
  var container = document.getElementById('picks-container');

  btn.disabled = true;
  icon.style.animation = 'spin 0.8s linear infinite';
  icon.style.display = 'inline-block';

  container.innerHTML = '<div class="picks-loading"><div class="spinner"></div><p>Loading quant top BUY candidates... (~15 seconds)</p></div>';

  fetch('/api/ai/top-picks', { method: 'GET' })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      var picks = data.picks || [];
    if (!picks.length) {
      container.innerHTML = '<p style="color:var(--text-dim);font-family:var(--font-data);font-size:13px;padding:20px 0">No BUY candidates found — try refreshing data first.</p>';
      return;
    }

      container.innerHTML = '<div class="picks-grid">' + picks.map(function(p, i) { return buildPickCard(p, i * 0.06); }).join('') + '</div>';
    })
    .catch(function(e) {
      container.innerHTML = '<p style="color:var(--red);font-family:var(--font-data);font-size:13px;padding:20px 0">Error: ' + e.message + '</p>';
    })
    .then(function() {
      btn.disabled = false;
      icon.style.animation = '';
      icon.textContent = '\u2726';
    });
}

function aiAnalyzeCoin(coinId, coinName) {
  var modal   = document.getElementById('ai-modal');
  var content = document.getElementById('modal-content');

  content.innerHTML = '<div class="modal-loading"><div class="spinner"></div><p>Analyzing <strong style="color:var(--text-primary)">' + coinName + '</strong>...</p></div>';

  modal.classList.add('open');
  document.body.style.overflow = 'hidden';

  fetch('/api/ai/analyze/' + encodeURIComponent(coinId), { method: 'GET' })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (!data.success && data.error) {
        content.innerHTML = '<h3 style="color:var(--red);margin-bottom:12px">Analysis Error</h3><p style="font-family:var(--font-data);font-size:12px;color:var(--text-secondary)">' + (data.error || '').replace(/</g, '&lt;') + '</p>';
        return;
      }
      if (data.error && !data.signal) {
        content.innerHTML = '<h3 style="color:var(--red);margin-bottom:12px">Analysis Error</h3><p style="font-family:var(--font-data);font-size:12px;color:var(--text-secondary)">' + (data.error || '').replace(/</g, '&lt;') + '</p>';
        return;
      }
      displayAIAnalysis(data);
      var idx = allCoins.findIndex(function(c) { return c.id === (data.coin_id || coinId); });
      if (idx >= 0 && data.coin_id) {
        allCoins[idx].ai_recommendation = data;
      }
    })
    .catch(function(e) {
      content.innerHTML = '<h3 style="color:var(--red);margin-bottom:12px">Error</h3><p style="font-family:var(--font-data);font-size:12px;color:var(--text-secondary)">' + e.message + '</p>';
    });
}

function showAIAnalysis(coinId, coinName) {
  if (!coinId && !coinName) return;
  // Find the cached AI recommendation: by id, then symbol, then name (case-insensitive)
  var coin = allCoins.find(function(c) { return (c.id && String(c.id) === String(coinId)); });
  if (!coin && coinId) coin = allCoins.find(function(c) { return (c.symbol || '').toLowerCase() === String(coinId).toLowerCase(); });
  if (!coin && coinName) coin = allCoins.find(function(c) { return (c.name || '').toLowerCase() === String(coinName).toLowerCase(); });
  if (!coin || !coin.ai_recommendation) {
    alert('Analysis not available for this coin. Try clicking "🤖 Analyze" to generate it.');
    return;
  }

  var modal   = document.getElementById('ai-modal');
  var content = document.getElementById('modal-content');
  
  modal.classList.add('open');
  document.body.style.overflow = 'hidden';

  displayAIAnalysis(coin.ai_recommendation, coin);
}

function displayAIAnalysis(data, coin) {
  var content = document.getElementById('modal-content');
  var cons = data.consensus || {};
  var quant = data.quant || {};
  var regime = (data.regime && data.regime.regime) ? data.regime.regime : '—';
  var hypothesis = data.hypothesis || '';
  var keyRisks = data.key_risks || [];
  var entryStrategy = data.entry_strategy || '—';
  var timeframe = data.timeframe || '—';
  var dt = data.dynamic_targets || (coin && coin.dynamic_targets) || {};
  var risk = (coin && coin.risk) || {};
  var stopPct = (dt && dt.stop_loss_pct != null) ? dt.stop_loss_pct : (data.stop_loss_pct != null ? data.stop_loss_pct : (coin && coin.stop_loss_pct != null ? coin.stop_loss_pct : (risk.stop_loss_pct != null ? risk.stop_loss_pct : null)));
  var targetPct = (dt && dt.take_profit_pct != null) ? dt.take_profit_pct : (data.take_profit_pct != null ? data.take_profit_pct : (coin && coin.take_profit_pct != null ? coin.take_profit_pct : (risk.take_profit_pct != null ? risk.take_profit_pct : (risk.target_pct != null ? risk.target_pct : null))));
  var stopLoss = (stopPct != null && stopPct !== '') ? (Number(stopPct) + '%') : (data.stop_loss || '—');
  var takeProfit = (targetPct != null && targetPct !== '') ? (Number(targetPct) + '%') : (data.take_profit || '—');
  var riskReward = data.risk_reward || '—';
  var summary = data.summary || '—';
  var macroImpact = data.macro_impact || '';
  var watchFor = data.watch_for || '';
  var alternative = data.alternative || '';

  var signal = (cons.consensus_signal || data.signal || 'HOLD').replace(/_/g, ' ');
  var quantTier = quant.quant_tier || cons.quant_tier || null;
  var quantTierDisplay = quantTier ? (quantTier + '').replace(/_/g, ' ') : null;
  var vetoReason = (cons && cons.veto_reason) || data.veto_reason || null;
  var coinName = (coin && coin.name) || data.coin_name || '—';
  var coinSymbol = (coin && coin.symbol) || data.coin_symbol || '—';
  var coinPrice = (coin && coin.current_price) != null ? coin.current_price : data.current_price;
  var posSizing = data.position_sizing || {};
  var pctVal = posSizing.position_size_pct != null ? posSizing.position_size_pct : posSizing.pct_of_account;
  var positionSize = pctVal != null ? pctVal + '%' : (data.position_size || '—');

  var sigCls = (cons.consensus_signal || data.signal || 'HOLD').replace(/_/g, ' ');
  var quantSpan = quantTierDisplay ? '<span style="font-size:11px;color:var(--cyan)">Quant: ' + quantTierDisplay + '</span>' : '';
  var confSpan = cons.confidence != null ? '<span class="confidence-badge">' + cons.confidence + '%</span>' : (data.confidence ? '<span class="confidence-badge">' + data.confidence + '</span>' : '');
  var badgeList = (quant.signal_badges && Array.isArray(quant.signal_badges)) ? quant.signal_badges : [];
  var badgeLabels = { blood_in_the_street: 'Blood in street', dip: 'Dip', pullback_in_trend: 'Pullback in trend', volatility_harvest: 'Volatility harvest', rally: 'Rally', pump_cap: 'Pump cap', capitulation: 'Capitulation', bull_participant: 'Bull participant', momentum_continuation: 'Momentum continuation', intra_swing_sniper: 'SNIPER ENTRY', vol_contraction: 'Vol contraction', dynamic_prob: 'Dynamic prob', light_sniper_entry: 'Sniper', narrative_spike: 'Narrative', light_whale_accum: 'Whale accum', volume_accum_shift: 'Volume accum', cycle_extension_confirm: 'Cycle extension', debasement_hedge: 'Debasement hedge', flow_volume_confirm: 'Flow/volume', data_cycle_confirm: 'Data cycle', liquidity_expansion: 'Liquidity', weekly_rsi_oversold: 'Weekly RSI oversold' };
  var strategyBadgesHtml = badgeList.length ? '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px"><span style="font-size:10px;color:var(--text-dim);letter-spacing:0.5px">Strategy:</span>' + badgeList.map(function(b){ var lbl = badgeLabels[b] || b.replace(/_/g,' '); return '<span class="strategy-badge ' + b + '" title="' + (lbl || '').replace(/"/g,'&quot;') + '">' + lbl + '</span>'; }).join('') + '</div>' : '';
  var vetoBlock = vetoReason ? '<div style="background:rgba(255,184,0,0.08);border:1px solid rgba(255,184,0,0.3);border-radius:var(--radius);padding:10px 12px;margin-bottom:12px"><div style="font-size:10px;color:var(--amber);letter-spacing:1px;margin-bottom:4px">\u26A0 Rule Veto</div><div style="font-size:12px;color:var(--text-secondary)">' + (vetoReason + '').replace(/</g, '&lt;') + '</div></div>' : '';

  var quantScore = quant.score != null ? quant.score : '—';
  var quantPath = (quant.quant_path || 'default') + '';
  var quantPathDisplay = quantPath.replace(/_/g, ' ');
  var quantReasoning = quant.reasoning || '—';
  var edgeScore = quant.edge_score != null ? quant.edge_score : null;
  var edgeProb = quant.edge_probability != null ? quant.edge_probability : null;
  var histWinProb = quant.historical_win_prob != null ? quant.historical_win_prob : null;
  var quantBlock = '<div style="background:var(--bg-card);border:1px solid var(--border-dim);border-radius:var(--radius);padding:14px 16px;margin-bottom:16px">' +
    '<div style="font-family:var(--font-data);font-size:10px;color:var(--cyan);letter-spacing:1px;margin-bottom:8px">QUANT SCORING (gate + path)</div>' +
    '<div style="display:grid;grid-template-columns:auto 1fr;gap:6px 16px;font-size:12px;margin-bottom:10px">' +
    '<span style="color:var(--text-dim)">Score</span><span style="color:var(--text-primary)">' + quantScore + '</span>' +
    '<span style="color:var(--text-dim)">Tier</span><span style="color:var(--cyan)">' + (quantTierDisplay || '—') + (quantScore !== '—' ? ' (score ' + quantScore + ')' : '') + '</span>' +
    '<span style="color:var(--text-dim)">Path</span><span style="color:var(--text-secondary)">' + quantPathDisplay + '</span>' +
    (edgeScore != null ? '<span style="color:var(--text-dim)">Edge score</span><span>' + edgeScore + '</span>' : '') +
    (edgeProb != null && edgeProb > 0 ? '<span style="color:var(--text-dim)">Edge probability</span><span>' + (Number(edgeProb) * 100).toFixed(2) + '%</span>' : '') +
    (histWinProb != null ? '<span style="color:var(--text-dim)">Historical win prob</span><span>' + (Number(histWinProb) * 100).toFixed(1) + '%</span>' : '') +
    '</div>' +
    '<div style="font-family:var(--font-data);font-size:10px;color:var(--text-dim);letter-spacing:0.5px;margin-bottom:4px">Full reasoning</div>' +
    '<div style="font-size:11px;color:var(--text-secondary);line-height:1.6">' + (quantReasoning + '').replace(/</g, '&lt;') + '</div></div>';

  var consensusLine = 'Regime: ' + regime + '. Final signal from Quant + Veto.';

  var risksHtml = keyRisks.length ? keyRisks.map(function(k) { return '<span class="risk-tag">' + (k + '').replace(/</g, '&lt;') + '</span>'; }).join('') : '';
  var risksBlock = risksHtml ? '<div style="margin-bottom:16px"><div class="pick-risks-label">KEY RISKS</div><div class="risk-tags">' + risksHtml + '</div></div>' : '';
  var macroBlock = macroImpact ? '<div style="background:var(--bg-card);border:1px solid var(--border-dim);border-radius:var(--radius);padding:12px 14px;margin-bottom:14px"><div style="font-family:var(--font-data);font-size:10px;color:var(--text-dim);letter-spacing:1px;margin-bottom:4px">MACRO CONTEXT</div><div style="font-family:var(--font-data);font-size:12px;color:var(--text-secondary)">' + (macroImpact + '').replace(/</g, '&lt;') + '</div></div>' : '';
  var watchBlock = watchFor ? '<div style="background:rgba(255,184,0,0.05);border:1px solid rgba(255,184,0,0.15);border-radius:var(--radius);padding:12px 14px;margin-bottom:14px"><div style="font-family:var(--font-data);font-size:10px;color:var(--amber);letter-spacing:1px;margin-bottom:4px">\u26A0 WATCH FOR (INVALIDATION)</div><div style="font-family:var(--font-data);font-size:12px;color:var(--text-secondary)">' + (watchFor + '').replace(/</g, '&lt;') + '</div></div>' : '';
  var altBlock = alternative ? '<div style="font-family:var(--font-data);font-size:11px;color:var(--text-dim);margin-bottom:12px">\u2190 If setup fails: <span style="color:var(--amber)">' + (alternative + '').replace(/</g, '&lt;') + '</span></div>' : '';
  var coinPriceStr = coinPrice != null ? Number(coinPrice).toLocaleString() : '—';
  var analyzedStr = 'Analyzed at ' + (data.analyzed_at || '—') + (data.from_cache ? ' (cached)' : ' (fresh)');

  content.innerHTML = '<div style="margin-bottom:20px">' +
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">' +
      '<div><div style="font-size:20px;font-weight:800;color:var(--text-primary)">' + (coinName + '').replace(/</g, '&lt;') + '</div>' +
      '<div style="font-family:var(--font-data);font-size:12px;color:var(--text-dim)">' + (coinSymbol + '').toUpperCase() + ' · $' + coinPriceStr + '</div></div>' +
      '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">' +
        '<span class="signal-badge ' + sigCls + '">' + signal + '</span>' + quantSpan + confSpan +
      '</div>' + strategyBadgesHtml + '</div></div>' +
    vetoBlock +
    quantBlock +
    '<div style="font-family:var(--font-data);font-size:10px;color:var(--text-dim);margin-bottom:16px">' + (consensusLine + '').replace(/</g, '&lt;') + '</div>' +
    '<div class="pick-grid" style="margin-bottom:16px">' +
      '<div class="pick-stat"><div class="pick-stat-label">Entry Strategy</div><div class="pick-stat-value" style="font-size:11px;color:var(--text-secondary)">' + (entryStrategy + '').replace(/</g, '&lt;') + '</div></div>' +
      '<div class="pick-stat"><div class="pick-stat-label">Timeframe</div><div class="pick-stat-value" style="color:var(--cyan)">' + (timeframe + '').replace(/</g, '&lt;') + '</div></div>' +
      '<div class="pick-stat"><div class="pick-stat-label">Stop Loss</div><div class="pick-stat-value" style="color:var(--red)">' + (stopLoss + '').replace(/</g, '&lt;') + '</div></div>' +
      '<div class="pick-stat"><div class="pick-stat-label">Take Profit</div><div class="pick-stat-value" style="color:var(--green)">' + (takeProfit + '').replace(/</g, '&lt;') + '</div></div>' +
      '<div class="pick-stat"><div class="pick-stat-label">Risk:Reward</div><div class="pick-stat-value" style="color:var(--cyan)">' + (riskReward + '').replace(/</g, '&lt;') + '</div></div>' +
      '<div class="pick-stat"><div class="pick-stat-label">Position Size</div><div class="pick-stat-value">' + (positionSize + '').replace(/</g, '&lt;') + '</div></div>' +
    '</div>' +
    risksBlock + macroBlock + watchBlock + altBlock +
    '<div style="background:rgba(0,200,232,0.05);border:1px solid rgba(0,200,232,0.15);border-radius:var(--radius);padding:12px 14px">' +
      '<div style="font-family:var(--font-data);font-size:11px;color:var(--cyan);font-style:italic">\uD83D\uDCA1 ' + (summary + '').replace(/</g, '&lt;') + '</div></div>' +
    '<div style="font-family:var(--font-data);font-size:9px;color:var(--text-dim);margin-top:12px">' + (analyzedStr + '').replace(/</g, '&lt;') + '</div>';
}

function closeModal(e) {
  if (e.target.id === 'ai-modal') closeModalDirect();
}

function closeModalDirect() {
  document.getElementById('ai-modal').classList.remove('open');
  document.body.style.overflow = '';
}

document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    closeModalDirect();
    hideJournal();
  }
});

// ── Trade Journal ─────────────────────────────────────────────

function showJournal() {
  document.getElementById('journal-modal').classList.add('open');
  document.body.style.overflow = 'hidden';
  loadJournal();
}

function hideJournal() {
  document.getElementById('journal-modal').classList.remove('open');
  document.body.style.overflow = '';
}

function loadJournal() {
  fetch('/api/journal/positions')
    .then(function(res) { return res.json(); })
    .then(function(data) {
      renderStats(data.stats || {});
      renderPositions(data.positions || []);
    })
    .catch(function(e) {
      console.error('Error loading journal:', e);
      document.getElementById('trades-tbody').innerHTML =
        '<tr><td colspan="14" class="no-trades" style="color:var(--red)">Error loading positions: ' + e.message + '</td></tr>';
    });
}

function renderStats(s) {
  var pnl = (s.total_pnl_usd != null ? s.total_pnl_usd : 0);
  var exp = (s.expectancy_usd != null ? s.expectancy_usd : 0);
  var wr  = (s.win_rate_pct != null ? s.win_rate_pct : 0);
  var aiHit = (s.ai_target_hit_rate != null ? s.ai_target_hit_rate : 0);

  document.getElementById('js-total').textContent   = (s.total_positions != null ? s.total_positions : '—');
  document.getElementById('js-open').textContent    = (s.open_positions != null ? s.open_positions : '—');
  document.getElementById('js-wins').textContent    = (s.wins != null ? s.wins : '—');
  document.getElementById('js-losses').textContent  = (s.losses != null ? s.losses : '—');
  document.getElementById('js-winrate').textContent = s.closed_positions ? wr + '%' : '—';
  document.getElementById('js-pnl').textContent     = s.closed_positions ? (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2) : '—';
  document.getElementById('js-avgwin').textContent  = s.avg_win_pct  ? '+' + s.avg_win_pct + '%'  : '—';
  document.getElementById('js-avgloss').textContent = s.avg_loss_pct ? s.avg_loss_pct + '%'  : '—';
  document.getElementById('js-pf').textContent      = (s.profit_factor != null ? s.profit_factor : '—');
  document.getElementById('js-expect').textContent  = s.closed_positions ? (exp >= 0 ? '+' : '') + '$' + exp.toFixed(2) : '—';
  document.getElementById('js-best').textContent    = s.best_trade_pct ? '+' + s.best_trade_pct + '%' : '—';
  
  // AI target hit rate
  var aiHitEl = document.getElementById('js-ai-hit');
  if (aiHitEl) {
    aiHitEl.textContent = s.closed_positions ? aiHit + '%' : '—';
    aiHitEl.style.color = aiHit >= 50 ? 'var(--green)' : aiHit >= 30 ? 'var(--amber)' : 'var(--red)';
  }

  // Streak
  var stEl = document.getElementById('js-streak');
  if (s.current_streak > 0) {
    var isWin = s.streak_type === 'win';
    stEl.textContent = s.current_streak + ' ' + (isWin ? 'W' : 'L');
    stEl.style.color = isWin ? 'var(--green)' : 'var(--red)';
  } else {
    stEl.textContent = '—';
    stEl.style.color = 'var(--text-dim)';
  }

  // Color P&L
  var pnlEl = document.getElementById('js-pnl');
  pnlEl.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';
  var expEl = document.getElementById('js-expect');
  expEl.style.color = exp >= 0 ? 'var(--green)' : 'var(--red)';
  var wrEl = document.getElementById('js-winrate');
  wrEl.style.color = wr >= 55 ? 'var(--green)' : wr >= 40 ? 'var(--amber)' : 'var(--red)';
}

function renderPositions(positions) {
  var tbody = document.getElementById('trades-tbody');
  if (!positions.length) {
    tbody.innerHTML = '<tr><td colspan="14" class="no-trades">No positions logged yet — use the form above to log your first position.</td></tr>';
    return;
  }

  // Show newest first
  var sorted = positions.slice().reverse();

  tbody.innerHTML = sorted.map(function(p) {
    var isOpen   = p.status === 'OPEN';
    var signal   = p.signal || 'BUY';
    var unreal   = p.unrealized_pct;
    var pnlUsd   = isOpen ? (p.unrealized_usd != null ? p.unrealized_usd : null) : p.pnl_usd;
    var pnlPct   = isOpen ? unreal : p.pnl_pct;
    var outcome  = p.outcome || (isOpen ? 'open' : '—');

    var pnlCls   = pnlPct == null ? '' : pnlPct >= 0 ? 'outcome-win' : 'outcome-loss';
    var pnlUsdPart = pnlUsd != null ? ' ($' + Number(pnlUsd).toFixed(2) + ')' : '';
    var pnlStr   = pnlPct != null
      ? (pnlPct >= 0 ? '+' : '') + Number(pnlPct).toFixed(2) + '%' + pnlUsdPart
      : '—';

    var outCls = outcome === 'win' ? 'outcome-win' : outcome === 'loss' ? 'outcome-loss' : outcome === 'breakeven' ? 'outcome-be' : 'status-open';
    var outStr = outcome === 'win' ? '\u2713 WIN' : outcome === 'loss' ? '\u2717 LOSS' : outcome === 'breakeven' ? '\u2248 EVEN' : '\u25CF OPEN';

    var currentOrExit = isOpen
      ? (p.current_price ? '$' + Number(p.current_price).toLocaleString() : '—')
      : (p.exit_price ? '$' + Number(p.exit_price).toLocaleString() : '—');

    var aiTargetStr = p.ai_target_price
      ? '$' + Number(p.ai_target_price).toLocaleString() + ' (' + p.ai_target_pct + '%)'
      : '—';

    var aiStopStr = p.ai_stop_price
      ? '$' + Number(p.ai_stop_price).toLocaleString() + ' (' + p.ai_stop_pct + '%)'
      : '—';

    var aiHitStr = '—';
    if (!isOpen && p.hit_ai_target !== null) {
      aiHitStr = p.hit_ai_target
        ? '<span style="color:var(--green);font-weight:600">\u2713 YES</span>'
        : '<span style="color:var(--red)">\u2717 NO</span>';
    }

    var closeBtn = isOpen ? '<button class="btn-close-trade" onclick="promptClosePosition(' + p.id + ', \'' + (p.coin_symbol || '').replace(/'/g, "\\'") + '\', ' + p.entry_price + ')">Close</button> ' : '';

    return '<tr>' +
      '<td style="color:var(--text-dim)">#' + p.id + '</td>' +
      '<td style="font-family:var(--font-data);font-size:11px;color:var(--text-secondary)">' + (p.trade_id || '—') + '</td>' +
      '<td><div style="font-weight:600;color:var(--text-primary)">' + (p.coin_name || '—') + '</div><div style="font-size:10px;color:var(--text-dim)">' + (p.coin_symbol || '') + '</div></td>' +
      '<td><span class="signal-badge ' + signal + '" style="font-size:10px;padding:2px 8px">' + signal + '</span></td>' +
      '<td>$' + Number(p.entry_price || 0).toLocaleString() + '</td>' +
      '<td>' + currentOrExit + '</td>' +
      '<td style="color:var(--green);font-size:11px">' + aiTargetStr + '</td>' +
      '<td style="color:var(--red);font-size:11px">' + aiStopStr + '</td>' +
      '<td class="' + (isOpen ? pnlCls : '') + '">' + (isOpen ? pnlStr : '—') + '</td>' +
      '<td class="' + (!isOpen ? pnlCls : '') + '">' + (!isOpen ? pnlStr : '—') + '</td>' +
      '<td class="' + outCls + '">' + outStr + '</td>' +
      '<td style="text-align:center;font-size:11px">' + aiHitStr + '</td>' +
      '<td style="color:var(--text-dim);font-size:10px">' + (p.entry_time || '').slice(0, 10) + '</td>' +
      '<td>' + closeBtn + '<button class="btn-delete-trade" onclick="deletePosition(' + p.id + ')" title="Delete (mistakes only)">\u2715</button></td>' +
    '</tr>';
  }).join('');
}

function fillQtyFromPositionSizer() {
  var entry = parseFloat(document.getElementById('f-entry').value);
  var stop = parseFloat(document.getElementById('f-stop').value) || 4.0;
  var coinId = document.getElementById('f-coin-id').value;
  var consensusSignal = 'MODERATE_BUY';
  var volatility = 50;
  if (coinId && typeof allCoins !== 'undefined') {
    var match = allCoins.find(function(c) { return c.id === coinId; });
    if (match) {
      var aiRec = match.ai_recommendation;
      var consensus = (aiRec && aiRec.consensus) || {};
      consensusSignal = consensus.consensus_signal || 'MODERATE_BUY';
      if (!['ULTRA_BUY','STRONG_BUY','MODERATE_BUY','WEAK_BUY'].includes(consensusSignal)) consensusSignal = 'MODERATE_BUY';
      var ind = match.indicators || {};
      if (ind.atr_pct != null) volatility = Math.min(200, Math.max(0, ind.atr_pct * 20));
    }
  }
  if (!entry || entry <= 0) {
    alert('Enter a valid Entry price first.');
    return;
  }
  var btn = document.getElementById('btn-fill-qty');
  if (btn) btn.disabled = true;
  fetch('/api/position-size/calculate', {
    method: 'POST',
    headers: mergeAdminHeaders({'Content-Type': 'application/json'}),
    body: JSON.stringify({
      entry_price: entry,
      stop_loss_pct: stop,
      consensus_signal: consensusSignal,
      volatility: volatility,
    }),
  })
    .then(function(r) { return r.json(); })
    .then(function(res) {
      if (btn) btn.disabled = false;
      if (res.error) {
        alert('Position sizer: ' + res.error);
        return;
      }
      var qty = res.quantity;
      var posUsd = res.position_size_usd;
      if (qty > 0 && posUsd > 0) {
        document.getElementById('f-qty').value = qty.toFixed(8).replace(/\.?0+$/, '') || qty;
      } else {
        alert('Position sizer returned $0 (no cash available or SKIP). Check Account Overview.');
      }
    })
    .catch(function(e) {
      if (btn) btn.disabled = false;
      alert('Failed: ' + (e.message || 'Network error'));
    });
}

function bookToCoinbase() {
  var symbol = (document.getElementById('f-coin-symbol').value || '').trim().toUpperCase();
  var name = (document.getElementById('f-coin-name').value || '').trim();
  var entry = parseFloat(document.getElementById('f-entry').value);
  var qty = parseFloat(document.getElementById('f-qty').value);
  var stop = parseFloat(document.getElementById('f-stop').value) || 4.0;
  var target = parseFloat(document.getElementById('f-target').value) || 6.0;
  var coinId = document.getElementById('f-coin-id').value || symbol.toLowerCase();

  if (!symbol) {
    alert('Missing Symbol');
    return;
  }
  if (isNaN(entry) || entry <= 0) {
    alert('Entry Price must be greater than 0');
    return;
  }
  if (isNaN(qty) || qty <= 0) {
    alert('Quantity must be greater than 0 (use position sizer to autofill)');
    return;
  }

  var totalUsd = entry * qty;
  var btn = document.getElementById('btn-book-coinbase');
  if (btn) btn.disabled = true;

  fetch('/api/execute/manual-book', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      symbol: symbol,
      total_usd: totalUsd,
      entry_price: entry,
      stop_pct: stop,
      target_pct: target,
      coin_name: name || symbol,
      coin_id: coinId,
    }),
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (btn) btn.disabled = false;
      if (data.error) {
        alert('Error: ' + data.error);
        return;
      }
      alert(data.message || 'TWAP buy started. Position will be logged when complete (~45 min).');
      loadJournal();
    })
    .catch(function(e) {
      if (btn) btn.disabled = false;
      alert('Failed: ' + (e.message || 'Network error'));
    });
}

function logPosition() {
  console.log('=== logPosition called ===');
  
  // Get raw values first
  var nameRaw   = document.getElementById('f-coin-name').value;
  var symbolRaw = document.getElementById('f-coin-symbol').value;
  var entryRaw  = document.getElementById('f-entry').value;
  var qtyRaw    = document.getElementById('f-qty').value;
  
  console.log('Raw field values:');
  console.log('  f-coin-name:', nameRaw);
  console.log('  f-coin-symbol:', symbolRaw);
  console.log('  f-entry:', entryRaw);
  console.log('  f-qty:', qtyRaw);
  console.log('  f-qty field exists?', !!document.getElementById('f-qty'));
  
  var name   = nameRaw.trim();
  var symbol = symbolRaw.trim();
  var entry  = parseFloat(entryRaw);
  var qty    = parseFloat(qtyRaw);
  var stop   = parseFloat(document.getElementById('f-stop').value) || 4.0;
  var target = parseFloat(document.getElementById('f-target').value) || 6.0;

  console.log('Parsed values:', {
    name,
    symbol,
    entry,
    qty,
    stop,
    target,
    'name check': !name,
    'entry NaN': isNaN(entry),
    'entry <= 0': entry <= 0,
    'qty NaN': isNaN(qty),
    'qty <= 0': qty <= 0,
  });

  // Better validation: check for actual invalid values
  if (!name) {
    alert('Missing Coin Name');
    return;
  }
  if (isNaN(entry) || entry <= 0) {
    alert('Entry Price must be a valid number greater than 0');
    return;
  }
  if (isNaN(qty) || qty <= 0) {
    alert('Quantity must be a valid number greater than 0');
    return;
  }

  console.log('Validation passed, creating payload...');

  // Try to match coin_id from form or live data
  var coinId = document.getElementById('f-coin-id').value || null;
  var match  = coinId ? allCoins.find(function(c) { return c.id === coinId; }) :
                 allCoins.find(function(c) {
                   var s = (c.symbol != null ? c.symbol : '').toLowerCase();
                   var n = (c.name != null ? c.name : '').toLowerCase();
                   return s === symbol.toLowerCase() || n === name.toLowerCase();
                 });

  var rec = (match && match.ai_recommendation) || {};
  var consensus = rec.consensus || {};
  var ai = consensus.ai_analyses || {};
  var payload = {
    coin_id:         (match && match.id) || symbol.toLowerCase(),
    coin_name:       (match && match.name) || name,
    coin_symbol:     symbol || (match && match.symbol) || '',
    signal:          document.getElementById('f-signal').value,
    entry_price:     entry,
    quantity:        qty,
    
    // AI recommendations (locked)
    ai_target_pct:   target,
    ai_stop_pct:     stop,
    ai_timeframe:    document.getElementById('f-timeframe').value,
    ai_confidence:   document.getElementById('f-confidence').value,
    ai_hypothesis:   document.getElementById('f-notes').value,
    
    notes:           document.getElementById('f-notes').value,
    capital_usd:     entry * qty,
  };
  if (consensus && Object.keys(consensus).length) {
    payload.specialist_veto = consensus.veto_reason ? 'VETO' : 'CONFIRM';
    payload.quant_tier = consensus.quant_tier || null;
    payload.market_regime = (match && match.regime) || null;
    var q = ai.quant || {};
    payload.quant_path = q.quant_path || consensus.quant_path || null;
    var badges = q.signal_badges || [];
    var sniper = badges.indexOf('light_sniper_entry') >= 0 || badges.indexOf('intra_swing_sniper') >= 0;
    payload.trading_scenario = sniper ? 'sniper' : (q.quant_path || consensus.quant_path || null);
  }

  console.log('Payload:', payload);

  fetch('/api/account/overview')
    .then(function(r) { return r.json(); })
    .catch(function() { return null; })
    .then(function(acc) {
      if (acc && acc.risk_pct != null) payload.risk_pct_used = acc.risk_pct;
      console.log('Sending to /api/journal/open...');
      return adminFetchJson('/api/journal/open', {
        method: 'POST',
        body: payload,
      });
    })
    .then(function(data) {
      console.log('Response:', data);
      if (data.error) {
        alert('Error: ' + data.error);
        return;
      }
      console.log('Position logged successfully, clearing form...');
      ['f-coin-id','f-coin-name','f-coin-symbol','f-entry','f-qty','f-timeframe','f-confidence','f-notes'].forEach(function(id) {
        document.getElementById(id).value = '';
      });
      document.getElementById('f-stop').value   = '4.0';
      document.getElementById('f-target').value = '6.0';
      var levelsEl = document.getElementById('quick-log-levels-comment');
      if (levelsEl) levelsEl.textContent = '';
      loadJournal();
    })
    .catch(function(e) {
      console.error('Error logging position:', e);
      alert('Failed to log position: ' + (e.message || 'Network error'));
    });
}

function promptClosePosition(positionId, symbol, entryPrice) {
  var exitPrice = prompt(
    'Close position #' + positionId + ' (' + symbol + ')\n\nEntry was: $' + entryPrice + '\nEnter exit price:',
    entryPrice
  );
  if (!exitPrice) return;

  var reason = prompt('Exit reason? (target / stop / manual / time)', 'manual') || 'manual';
  closePosition(positionId, parseFloat(exitPrice), reason);
}

function refreshJournalAndPositionsViews() {
  loadJournal();
  if (typeof loadPositionsTab === 'function') loadPositionsTab();
}

function closePosition(positionId, exitPrice, exitReason) {
  adminFetchJson('/api/journal/close/' + positionId, {
    method: 'POST',
    body: { exit_price: exitPrice, exit_reason: exitReason },
  })
    .then(function() { refreshJournalAndPositionsViews(); })
    .catch(function(e) {
      alert('Failed to close position: ' + (e.message || 'Network error'));
    });
}

function deletePosition(positionId) {
  if (!confirm('Delete position #' + positionId + '? This cannot be undone.')) return;
  adminFetchJson('/api/journal/delete/' + positionId, { method: 'DELETE' })
    .then(function() { refreshJournalAndPositionsViews(); })
    .catch(function(e) {
      alert('Failed to delete position: ' + (e.message || 'Network error'));
    });
}

// ── Quick Log (auto-fill journal from dashboard) ────────────

function quickLogTrade(coinId) {
  // Find coin in current data
  var coin = allCoins.find(function(c) { return c.id === coinId; });
  if (!coin) {
    alert('Coin not found in current data — try refreshing the dashboard first.');
    return;
  }

  console.log('Quick Log - Coin found:', coin.name, coin);

  // OPEN JOURNAL FIRST
  showJournal();

  // Small delay to ensure journal is fully rendered
  new Promise(function(resolve) { setTimeout(resolve, 100); });

  var risk = coin.risk || {};
  var ind  = coin.indicators || {};

  // Get AI recommendation if available (from auto-analysis or manual analysis)
  var aiRec = coin.ai_recommendation;
  var aiStop   = null;
  var aiTarget = null;
  var aiHypothesis = '';
  var aiTimeframe  = '';
  var aiConfidence = '';

  if (aiRec && aiRec.signal) {
    // AI data available - use it
    console.log('Using AI recommendation:', aiRec);
    var stopStr = aiRec.stop_loss || '';
    if (stopStr.includes('%')) {
      aiStop = parseFloat(stopStr);
    }
    var targetStr = aiRec.take_profit || '';
    if (targetStr.includes('%')) {
      aiTarget = parseFloat(targetStr);
    }
    aiHypothesis = aiRec.hypothesis || '';
    aiTimeframe  = aiRec.timeframe || '';
    aiConfidence = aiRec.confidence || '';
  } else {
    console.log('No AI recommendation - using dashboard values');
  }

  // Prefer dashboard dynamic targets (same source as Book to Coinbase / auto-exec), then AI, then defaults
  var fromDynamic = (risk.stop_loss_pct != null && risk.stop_loss_pct > 0) || (risk.take_profit_pct != null && risk.take_profit_pct > 0) || (coin.dynamic_targets && (coin.dynamic_targets.stop_loss_pct || coin.dynamic_targets.take_profit_pct));
  var stopPct   = (risk.stop_loss_pct != null && risk.stop_loss_pct > 0) ? risk.stop_loss_pct : ((coin.dynamic_targets && coin.dynamic_targets.stop_loss_pct) ? coin.dynamic_targets.stop_loss_pct : (aiStop || 4.0));
  var targetPct = (risk.take_profit_pct != null && risk.take_profit_pct > 0) ? risk.take_profit_pct : ((risk.target_pct != null && risk.target_pct > 0) ? risk.target_pct : ((coin.dynamic_targets && coin.dynamic_targets.take_profit_pct) ? coin.dynamic_targets.take_profit_pct : (aiTarget || 6.0)));
  // Notes: momentum, RSI, score, and actual Stop/Target applied (dynamic or default)
  var momentumStr = (ind.momentum != null && ind.momentum !== '') ? ((parseFloat(ind.momentum) >= 0 ? '+' : '') + Number(ind.momentum).toFixed(1) + '%') : '—';
  var rsiVal = (ind.rsi != null && ind.rsi !== '') ? Number(ind.rsi).toFixed(2) : '—';
  var rsiLabel = (rsiVal !== '—') ? (parseFloat(ind.rsi) >= 70 ? ' (overbought)' : ' (not overbought)') : '';
  var scoreStr = (coin.score != null && coin.score !== '') ? coin.score : '—';
  var levelsSource = fromDynamic ? 'Dynamic (volatility + regime)' : (aiStop || aiTarget ? 'Quant' : 'Default');

  var statsLine = 'Momentum=' + momentumStr + ' | RSI=' + rsiVal + rsiLabel + ' | Score=' + scoreStr + '/100 | ' + levelsSource + ' Stop=' + stopPct.toFixed(1) + '% | Target=' + targetPct.toFixed(1) + '%';
  var notes = aiHypothesis ? (aiHypothesis + '\n' + statsLine) : statsLine;
  var timeframe = aiTimeframe || '3-14 days';
  var confidence = aiConfidence || '';

  // Ensure we definitely have the coin name
  var coinName = coin.name || coin.coin_name || coin.id || 'Unknown';
  console.log('Pre-filling form with coin name:', coinName);

  // NOW fill the form (after journal is open and rendered)
  // Quantity is auto-filled from position sizer below (same logic as auto-execute sizing)
  console.log('Filling form fields...');
  document.getElementById('f-coin-name').value   = coinName;
  document.getElementById('f-coin-symbol').value = (coin.symbol || '').toUpperCase();
  document.getElementById('f-signal').value      = coin.signal || 'BUY';
  document.getElementById('f-entry').value       = coin.current_price || 0;
  document.getElementById('f-qty').value         = '';  // User enters quantity
  document.getElementById('f-stop').value        = stopPct.toFixed(2);
  document.getElementById('f-target').value      = targetPct.toFixed(2);
  document.getElementById('f-timeframe').value   = timeframe;
  document.getElementById('f-confidence').value  = confidence;
  document.getElementById('f-notes').value       = notes;
  
  // Store coin_id for later use
  document.getElementById('f-coin-id').value     = coin.id;

  // Update visible comment: dynamic (volatility + regime) vs static/default
  var levelsEl = document.getElementById('quick-log-levels-comment');
  if (levelsEl) levelsEl.textContent = fromDynamic
    ? ('Dynamic levels (volatility + regime): Stop ' + stopPct.toFixed(2) + '% · Target ' + targetPct.toFixed(2) + '% — same as Book to Coinbase')
    : ('Levels: Stop ' + stopPct.toFixed(2) + '% · Target ' + targetPct.toFixed(2) + '% (Quant or default — refresh dashboard for dynamic)');

  console.log('Form filled - checking values:');
  console.log('  Coin Name:', document.getElementById('f-coin-name').value);
  console.log('  Symbol:', document.getElementById('f-coin-symbol').value);
  console.log('  Signal:', document.getElementById('f-signal').value);
  console.log('  Entry:', document.getElementById('f-entry').value);
  console.log('  Stop:', document.getElementById('f-stop').value);
  console.log('  Target:', document.getElementById('f-target').value);
  console.log('  Coin ID:', document.getElementById('f-coin-id').value);

  // Autopopulate quantity from position sizer (based on available cash)
  var entryPrice = parseFloat(coin.current_price) || 0;
  if (entryPrice > 0) {
    var consensus = (aiRec && aiRec.consensus) || {};
    var consensusSignal = consensus.consensus_signal || 'MODERATE_BUY';
    if (!['ULTRA_BUY','STRONG_BUY','MODERATE_BUY','WEAK_BUY'].includes(consensusSignal)) consensusSignal = 'MODERATE_BUY';
    var volatility = (ind && ind.atr_pct != null) ? Math.min(200, Math.max(0, ind.atr_pct * 20)) : 50;
    setTimeout(function() {
      fetch('/api/position-size/calculate', {
        method: 'POST',
        headers: mergeAdminHeaders({'Content-Type': 'application/json'}),
        body: JSON.stringify({
          entry_price: entryPrice,
          stop_loss_pct: stopPct,
          consensus_signal: consensusSignal,
          volatility: volatility,
        }),
      })
        .then(function(r) { return r.json(); })
        .then(function(res) {
          if (res.error) return;
          var qty = res.quantity;
          var posUsd = res.position_size_usd;
          if (qty > 0 && posUsd > 0) {
            var el = document.getElementById('f-qty');
            if (el) el.value = qty.toFixed(8).replace(/\.?0+$/, '') || qty;
            console.log('Quick Log: Position sizer autofilled qty=' + qty + ' ($' + posUsd + ')');
          }
        })
        .catch(function(e) { console.warn('Position sizer autofill failed:', e); });
    }, 150);
  }

  // Scroll to top of journal and highlight the quantity field
  setTimeout(function() {
    var qtyField = document.getElementById('f-qty');
    qtyField.focus();
    qtyField.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, 200);

  // Show notification
  var source = aiStop ? '📊 Quant/analysis' : '📊 Dashboard';
  console.log('Quick Log: ' + coinName + ' pre-filled with ' + source + ' values (Stop: ' + stopPct + '%, Target: ' + targetPct + '%)');
}

// ── Tab Switching ─────────────────────────────────────────────

function switchTab(name) {
  document.querySelectorAll('.tab-panel').forEach(function(p) { p.classList.remove('active'); });
  document.querySelectorAll('.main-tab').forEach(function(t) { t.classList.remove('active'); });
  var panel = document.getElementById('tab-' + name);
  var tabBtn = document.querySelector('.main-tab[data-tab="' + name + '"]');
  if (panel) panel.classList.add('active');
  if (tabBtn) tabBtn.classList.add('active');
  
  if (name === 'positions') {
    loadPositionsTab();
  } else if (name === 'xrpl-ledger') {
    loadXrplTab();
  } else if (name === 'performance') {
    loadPerformanceTab();
  } else if (name === 'roadmap') {
    loadRoadmapTab();
  } else if (name === 'funding') {
    loadFundingTab();
  } else if (name === 'data-sources') {
    loadDataSourcesTab();
  }
}

function loadRoadmapTab() {
  Promise.all([
    fetch('/api/optimizer/lean-status').then(function(r) { return r.ok ? r.json() : {}; }),
    fetch('/api/monthly-review?days=30').then(function(r) { return r.ok ? r.json() : {}; }),
    fetch('/api/roadmap/doc').then(function(r) { return r.ok ? r.json() : {}; }),
    fetch('/api/roadmap/automation').then(function(r) { return r.ok ? r.json() : {}; })
  ]).then(function(results) {
    var lean = results[0] || {};
    var review = results[1] || {};
    var roadmapDoc = results[2] || {};
    var autoStatus = results[3] || {};
    var perf = review.performance || {};
    var closedTrades = Number(lean.closed_trades || 0);
    var attrPct = Number(lean.attr_completeness_pct || 0);
    var expectancy = perf.expectancy_pct != null ? Number(perf.expectancy_pct) : null;
    var maxdd = perf.max_drawdown_pct != null ? Number(perf.max_drawdown_pct) : null;
    var winRate = perf.win_rate_pct != null ? Number(perf.win_rate_pct) : null;
    var tradesWeek = review.closed_trades_count_7d != null ? Number(review.closed_trades_count_7d) : null;
    var ready = !!lean.ready_for_advanced;
    var th = lean.thresholds || {};
    var minClosed = Number(th.min_closed_trades || 100);
    var minAttr = Number(th.min_attr_completeness_pct || 95);
    var minExp = Number(th.min_expectancy_pct_30d || 0);

    var setLabel = function(id, txt) {
      var el = document.getElementById(id);
      if (el) el.textContent = txt;
    };
    setLabel('roadmap-kpi-trades-label', 'Trades / Week (target 2-6)');
    setLabel('roadmap-kpi-winrate-label', 'Win Rate 30d (target >=45%)');
    setLabel('roadmap-kpi-expectancy-label', 'Expectancy 30d (target >=' + minExp + '%)');
    setLabel('roadmap-kpi-maxdd-label', 'Max DD 30d (target <=12%)');
    setLabel('roadmap-ms-closed-label', 'Closed Trades (>=' + minClosed + ')');
    setLabel('roadmap-ms-attr-label', 'Attribution Completeness (>=' + minAttr + '%)');

    var elTrades = document.getElementById('roadmap-kpi-trades');
    var elWr = document.getElementById('roadmap-kpi-winrate');
    var elExp = document.getElementById('roadmap-kpi-expectancy');
    var elDd = document.getElementById('roadmap-kpi-maxdd');
    if (elTrades) {
      elTrades.textContent = tradesWeek != null ? String(tradesWeek) : '—';
      elTrades.className = 'perf-metric-value ' + ((tradesWeek != null && tradesWeek >= 2 && tradesWeek <= 6) ? 'text-success' : 'text-danger');
    }
    if (elWr) {
      elWr.textContent = winRate != null ? winRate.toFixed(1) + '%' : '—';
      elWr.className = 'perf-metric-value ' + ((winRate != null && winRate >= 45) ? 'text-success' : 'text-danger');
    }
    if (elExp) {
      elExp.textContent = expectancy != null ? expectancy.toFixed(2) + '%' : '—';
      elExp.className = 'perf-metric-value ' + ((expectancy != null && expectancy >= 0) ? 'text-success' : 'text-danger');
    }
    if (elDd) {
      elDd.textContent = maxdd != null ? maxdd.toFixed(1) + '%' : '—';
      elDd.className = 'perf-metric-value ' + ((maxdd != null && maxdd <= 12) ? 'text-success' : 'text-danger');
    }

    var elClosed = document.getElementById('roadmap-ms-closed');
    var elAttr = document.getElementById('roadmap-ms-attr');
    var elGate = document.getElementById('roadmap-ms-gate');
    if (elClosed) {
      elClosed.textContent = closedTrades + ' / ' + minClosed;
      elClosed.className = 'perf-metric-value ' + (closedTrades >= minClosed ? 'text-success' : 'text-danger');
    }
    if (elAttr) {
      elAttr.textContent = attrPct.toFixed(1) + '%';
      elAttr.className = 'perf-metric-value ' + (attrPct >= minAttr ? 'text-success' : 'text-danger');
    }
    if (elGate) {
      elGate.textContent = ready ? 'READY' : 'GATED';
      elGate.className = 'perf-metric-value ' + (ready ? 'text-success' : 'text-danger');
    }

    var actionEl = document.getElementById('roadmap-next-action');
    if (actionEl) {
      actionEl.innerHTML = ready
        ? '<strong style="color:var(--green);">Next action:</strong> Start staged reactivation (SHAP → VectorBT → Optuna), one stage per week with rollback checks.'
        : '<strong style="color:var(--amber);">Next action:</strong> Stay core-only. Focus on clean attribution and profitable, disciplined trade flow until milestones are satisfied.';
    }

    var chipsEl = document.getElementById('roadmap-stage-chips');
    var noteEl = document.getElementById('roadmap-stage-note');
    if (chipsEl) {
      var stage1 = ready ? 'active' : 'locked';
      var stage2 = ready ? 'locked' : 'locked';
      var stage3 = ready ? 'locked' : 'locked';
      chipsEl.innerHTML =
        '<span class="roadmap-stage-chip active">Core Runtime (active)</span>' +
        '<span class="roadmap-stage-chip ' + stage1 + '">SHAP Weekly Diagnostics</span>' +
        '<span class="roadmap-stage-chip ' + stage2 + '">VectorBT Weekly Exit Calibration</span>' +
        '<span class="roadmap-stage-chip ' + stage3 + '">Optuna Weekly Threshold Tuning</span>';
    }
    if (noteEl) {
      noteEl.textContent = ready
        ? 'Milestones passed. Reactivate one feature stage at a time to control risk and attribution quality.'
        : 'Advanced stages remain intentionally paused to avoid overfitting and noise-driven churn.';
    }

    var updatedEl = document.getElementById('roadmap-last-updated');
    if (updatedEl) {
      updatedEl.textContent = 'Updated: ' + new Date().toLocaleString();
    }
    var autoSummaryEl = document.getElementById('roadmap-auto-summary');
    var autoPendingEl = document.getElementById('roadmap-auto-pending');
    if (autoSummaryEl) {
      var next = autoStatus.next_decisions || [];
      var promoteCount = next.filter(function(d) { return d.decision === 'promote'; }).length;
      var activeCount = Object.values(autoStatus.feature_states || {}).filter(function(s) { return s && s.status === 'active'; }).length;
      autoSummaryEl.textContent = 'Active features: ' + activeCount + ' | Eligible for promotion: ' + promoteCount;
    }
    if (autoPendingEl) {
      var pending = autoStatus.pending_approvals || [];
      if (pending.length) {
        autoPendingEl.innerHTML = 'Pending approvals (high-risk): ' + pending.map(function(p) {
          var fid = p.feature_id || '';
          return '<div style="margin:6px 0;">' +
            '<strong>' + fid + '</strong> ' +
            '<button type="button" class="schedule-save-btn" style="margin-left:8px;padding:4px 8px;" onclick="roadmapDecision(&quot;' + fid + '&quot;, &quot;approve&quot;)">Approve</button>' +
            '<button type="button" class="schedule-save-btn" style="margin-left:4px;padding:4px 8px;background:var(--bg-card);color:var(--text-secondary);border:1px solid var(--border-dim);" onclick="roadmapDecision(&quot;' + fid + '&quot;, &quot;reject&quot;)">Reject</button>' +
            '</div>';
        }).join('');
      } else {
        autoPendingEl.textContent = 'Pending approvals (high-risk): none';
      }
    }
    var docEl = document.getElementById('roadmap-doc-content');
    if (docEl) {
      if (roadmapDoc && roadmapDoc.markdown) {
        var escaped = roadmapDoc.markdown
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;');
        docEl.innerHTML = '<pre style="margin:0; white-space: pre-wrap; font-family: var(--font-mono); font-size: 12px; color: var(--text-secondary);">' + escaped + '</pre>';
      } else {
        docEl.innerHTML = '<span style="color:var(--red);">Roadmap doc unavailable.</span>';
      }
    }
  }).catch(function(err) {
    console.error('Load roadmap tab:', err);
    var actionEl = document.getElementById('roadmap-next-action');
    if (actionEl) actionEl.innerHTML = '<span style="color:var(--red);">Failed to load roadmap data.</span>';
    var autoSummaryEl = document.getElementById('roadmap-auto-summary');
    if (autoSummaryEl) autoSummaryEl.innerHTML = '<span style="color:var(--red);">Failed to load automation state.</span>';
    var docEl = document.getElementById('roadmap-doc-content');
    if (docEl) docEl.innerHTML = '<span style="color:var(--red);">Failed to load canonical roadmap doc.</span>';
  });
}

function roadmapDecision(featureId, decision) {
  var rationale = window.prompt('Enter rationale for ' + decision + ' (' + featureId + '):');
  if (!rationale || !rationale.trim()) {
    alert('Rationale is required.');
    return;
  }
  fetch('/api/roadmap/automation/decision', {
    method: 'POST',
    headers: mergeAdminHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ feature_id: featureId, decision: decision, rationale: rationale.trim() })
  })
  .then(function(r) {
    return r.json().then(function(resp) {
      if (!r.ok) throw new Error((resp && resp.error) || r.statusText || String(r.status));
      return resp;
    });
  })
  .then(function(resp) {
    if (resp && resp.success) {
      loadRoadmapTab();
    } else {
      alert('Roadmap decision failed: ' + ((resp && (resp.error || resp.message)) || 'unknown error'));
    }
  })
  .catch(function(err) {
    alert('Roadmap decision error: ' + err);
  });
}

function loadOptimizerTab() {
  document.getElementById('opt-last-updated').textContent = 'Loading...';
  fetch('/api/optimizer/status')
    .then(function(r) {
      if (!r.ok) {
        return r.json().then(function(j) {
          throw new Error((j && j.error) || r.statusText || String(r.status));
        });
      }
      return r.json();
    })
    .then(function(data) {
      document.getElementById('opt-last-updated').textContent = 'Updated: ' + new Date().toLocaleTimeString();

      // Last cycle
      var cycle = data.last_cycle || {};
      document.getElementById('opt-cycle-ts').textContent = cycle.timestamp || '—';
      document.getElementById('opt-applied-count').textContent = (cycle.applied || []).length;
      document.getElementById('opt-reverted-count').textContent = (cycle.reverted || []).length;
      var recs = cycle.recommendations || [];
      if (recs.length) {
        document.getElementById('opt-recommendations').innerHTML = '<strong>Recommendations:</strong><ul style="margin:4px 0 0 16px;">' +
          recs.map(function(r) { return '<li>' + (r.parameter || r) + ': ' + (r.direction || '') + ' (' + (r.reason || '') + ')</li>'; }).join('') + '</ul>';
      } else {
        document.getElementById('opt-recommendations').textContent = 'No recommendations from last cycle.';
      }

      // Model calibration
      var cal = data.model_calibration || {};
      var calEl = document.getElementById('opt-calibration-content');
      if (cal.accuracy) {
        var acc = cal.accuracy;
        var html = 'Win accuracy: <strong>' + (acc.win_accuracy_pct != null ? acc.win_accuracy_pct + '%' : '—') + '</strong> | ' +
          'Overall: <strong>' + (acc.overall_accuracy_pct != null ? acc.overall_accuracy_pct + '%' : '—') + '</strong> | ' +
          'Trades: ' + (acc.total || 0);
        var drifts = cal.drift_warnings || [];
        if (drifts.length) {
          html += '<div style="margin-top:8px;color:#ff9800;">Drift warnings: ' + drifts.length + ' bin(s) with >10pp divergence</div>';
        }
        calEl.innerHTML = html;
      } else {
        calEl.textContent = 'No calibration data available yet.';
      }

      // Veto health
      var veto = data.veto_accuracy || {};
      var vetoLog = data.veto_log_30d || {};
      document.getElementById('opt-veto-total').textContent = vetoLog.total_vetoes != null ? vetoLog.total_vetoes : (veto.total || '—');
      document.getElementById('opt-veto-correct').textContent = veto.correct_veto != null ? veto.correct_veto : '—';
      document.getElementById('opt-veto-missed').textContent = veto.missed_by_veto != null ? veto.missed_by_veto : '—';
      var byRegime = vetoLog.by_regime || {};
      var regimeKeys = Object.keys(byRegime);
      if (regimeKeys.length) {
        document.getElementById('opt-veto-by-regime').innerHTML = '<strong>By regime:</strong> ' +
          regimeKeys.map(function(r) { return r + ': ' + byRegime[r]; }).join(' | ');
      } else {
        document.getElementById('opt-veto-by-regime').textContent = 'No veto log entries in last 30 days.';
      }

      // Roadmap features
      var features = data.roadmap_features || {};
      var tbody = document.getElementById('opt-roadmap-tbody');
      var fids = Object.keys(features);
      if (fids.length) {
        tbody.innerHTML = fids.map(function(fid) {
          var f = features[fid];
          var activeClass = f.active ? 'text-success' : 'text-danger';
          var activeText = f.active ? 'Active' : 'Inactive';
          return '<tr><td>' + fid.replace(/_/g, ' ') + '</td><td>' + (f.stage || '—') + '</td>' +
            '<td><span class="' + activeClass + '">' + activeText + '</span></td>' +
            '<td>' + (f.consecutive_passes || 0) + '</td></tr>';
        }).join('');
      } else {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-dim);">No roadmap state loaded yet.</td></tr>';
      }
    })
    .catch(function(err) {
      document.getElementById('opt-last-updated').textContent = 'Error: ' + err.message;
    });
}

function loadDataSourcesTab() {
  fetch('/api/data/sources')
    .then(function(r) {
      if (!r.ok) return r.json().then(function(j) { throw new Error(j.error || r.statusText); });
      return r.json();
    })
    .then(function(data) {
      var tbody = document.getElementById('data-sources-body');
      if (!tbody) return;
      var noteEl = document.getElementById('data-sources-note');
      if (noteEl) {
        var bits = [];
        if (data.pipeline_last_refresh) bits.push('Last pipeline refresh: ' + data.pipeline_last_refresh);
        if (data.note) bits.push(data.note);
        noteEl.textContent = bits.join(' ');
      }
      tbody.innerHTML = '';
      function feedClass(f) {
        if (f === 'Yes') return 'text-success';
        if (f === 'Standby') return 'text-info';
        if (f === 'N/A') return 'text-dim';
        if (f === 'Degraded') return 'text-warning';
        if (f === 'Unknown') return 'text-warning';
        return 'text-danger';
      }
      data.sources.forEach(function(s) {
        var row = '<tr>' +
          '<td><strong>' + s.source + '</strong></td>' +
          '<td>' + s.status + '</td>' +
          '<td>' + s.last_updated + '</td>' +
          '<td><span class="' + feedClass(s.feed_ok) + '">' + s.feed_ok + '</span></td>' +
          '<td>' + s.what_it_provides + '</td>' +
          '<td><strong>' + s.edge_contribution + '</strong></td>' +
          '<td>' + s.impact + '</td>' +
        '</tr>';
        tbody.innerHTML += row;
      });
    })
    .catch(function(e) {
      var tbody = document.getElementById('data-sources-body');
      if (tbody) tbody.innerHTML = '<tr><td colspan="7" class="text-danger">Failed to load: ' + (e && e.message ? e.message : e) + '</td></tr>';
    });
}

// ── XRPL Ledger Tab ─────────────────────────────────────────────
var xrplEventsMarker = null;

function loadXrplTab() {
  document.getElementById('xrpl-snapshot-loading').style.display = 'block';
  document.getElementById('xrpl-snapshot-content').style.display = 'none';
  document.getElementById('xrpl-snapshot-error').style.display = 'none';
  document.getElementById('xrpl-events-loading').style.display = 'block';
  document.getElementById('xrpl-events-content').style.display = 'none';
  document.getElementById('xrpl-events-error').style.display = 'none';

  Promise.all([
    fetch('/api/xrpl/snapshot').then(function(r) {
      return r.json().then(function(j) { if (!r.ok && !j.error) throw new Error(j.error || r.statusText); return j; });
    }),
    fetch('/api/xrpl/events?limit=100').then(function(r) {
      return r.json().then(function(j) { if (!r.ok && !j.error) throw new Error(j.error || r.statusText); return j; });
    })
  ]).then(function(results) {
    var snap = results[0];
    var ev = results[1];

    if (snap.error) {
      document.getElementById('xrpl-snapshot-loading').style.display = 'none';
      document.getElementById('xrpl-snapshot-error').textContent = snap.error;
      document.getElementById('xrpl-snapshot-error').style.display = 'block';
    } else {
      document.getElementById('xrpl-snapshot-loading').style.display = 'none';
      document.getElementById('xrpl-snapshot-content').style.display = 'block';
      document.getElementById('xrpl-account-label').textContent = snap.account ? ' — ' + snap.account : '';
      var fmt = function(v) { return v != null ? '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'; };
      document.getElementById('xrpl-value-now').textContent = fmt(snap.value_usd);
      document.getElementById('xrpl-value-1d').textContent = fmt(snap.value_1d);
      document.getElementById('xrpl-value-1w').textContent = fmt(snap.value_1w);
      document.getElementById('xrpl-value-30d').textContent = fmt(snap.value_30d);
      document.getElementById('xrpl-value-90d').textContent = fmt(snap.value_90d);
      document.getElementById('xrpl-value-1y').textContent = fmt(snap.value_1y);
      document.getElementById('xrpl-xrp-price').textContent = snap.xrp_price_usd != null ? '$' + Number(snap.xrp_price_usd).toFixed(4) : '—';
      var posRows = [];
      var assets = snap.assets || [];
      if (!assets.length && snap.xrp_balance != null) {
        assets = [{ token_name: 'XRP', value: snap.xrp_balance, usd_per_token: snap.xrp_price_usd, value_usd: snap.xrp_price_usd != null ? snap.xrp_balance * snap.xrp_price_usd : null }];
      }
      assets.forEach(function(a) {
        var balance = (a.value != null) ? Number(a.value).toLocaleString(undefined, { maximumFractionDigits: 6 }) : '—';
        var valueCell = (a.value_usd != null) ? '$' + Number(a.value_usd).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
        var usdPerToken = (a.usd_per_token != null) ? '$' + Number(a.usd_per_token).toFixed(4) : '—';
        posRows.push('<tr><td>' + (a.token_name || a.currency || '') + '</td><td>' + balance + '</td><td>' + valueCell + '</td><td class="text-dim">' + usdPerToken + '</td></tr>');
      });
      document.getElementById('xrpl-positions-tbody').innerHTML = posRows.length ? posRows.join('') : '<tr><td colspan="4" style="text-align:center;color:var(--text-dim);">No positions</td></tr>';
    }

    if (ev.error) {
      document.getElementById('xrpl-events-loading').style.display = 'none';
      document.getElementById('xrpl-events-error').textContent = ev.error;
      document.getElementById('xrpl-events-error').style.display = 'block';
    } else {
      document.getElementById('xrpl-events-loading').style.display = 'none';
      document.getElementById('xrpl-events-content').style.display = 'block';
      xrplEventsMarker = ev.marker || null;
      document.getElementById('xrpl-load-more-btn').style.display = xrplEventsMarker ? 'inline-block' : 'none';
      renderXrplEvents(ev.events || []);
    }
  }).catch(function(e) {
    document.getElementById('xrpl-snapshot-loading').style.display = 'none';
    document.getElementById('xrpl-snapshot-error').textContent = 'Failed to load snapshot: ' + (e && e.message);
    document.getElementById('xrpl-snapshot-error').style.display = 'block';
    document.getElementById('xrpl-events-loading').style.display = 'none';
    document.getElementById('xrpl-events-error').textContent = 'Failed to load events: ' + (e && e.message);
    document.getElementById('xrpl-events-error').style.display = 'block';
  });
}

function renderXrplEvents(events, append) {
  var tbody = document.getElementById('xrpl-events-tbody');
  if (!append) tbody.innerHTML = '';
  if (!events.length && !append) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-dim);">No transactions</td></tr>';
    return;
  }
  var rows = events.map(function(e) {
    var time = (e.time || '').replace('T', ' ').slice(0, 19) || '—';
    var hash = (e.hash || '—').slice(0, 16) + '…';
    var amtXrp = e.amount_xrp != null ? e.amount_xrp.toLocaleString() : (e.amount_currency || '—');
    var amtOther = e.amount_currency || '—';
    var fee = e.fee_drops != null ? (Number(e.fee_drops) / 1000000).toFixed(6) : '—';
    return '<tr><td>' + time + '</td><td>' + (e.type || '—') + '</td><td>' + amtXrp + '</td><td>' + amtOther + '</td><td>' + fee + '</td><td class="text-dim" title="' + (e.hash || '') + '">' + hash + '</td></tr>';
  });
  if (append) tbody.insertAdjacentHTML('beforeend', rows.join('')); else tbody.innerHTML = rows.join('');
}

function loadXrplEventsMore() {
  if (!xrplEventsMarker) return;
  var btn = document.getElementById('xrpl-load-more-btn');
  btn.disabled = true;
  fetch('/api/xrpl/events?limit=100&marker=' + encodeURIComponent(xrplEventsMarker))
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) {
        btn.disabled = false;
        return;
      }
      xrplEventsMarker = data.marker || null;
      btn.style.display = xrplEventsMarker ? 'inline-block' : 'none';
      renderXrplEvents(data.events || [], true);
      btn.disabled = false;
    })
    .catch(function() { btn.disabled = false; });
}

// ── Positions Tab ─────────────────────────────────────────────

/** All OPEN rows from API (edit modal / lookups). */
var openPositionsAllCache = [];
/** Live OPEN rows only: shown on Open Positions table (excludes paper + test). */
var openPositionsCache = [];
/** Full closed list for Positions tab (used with Live-only filter). */
var closedPositionsFullCache = [];
// Default ON: Closed Positions History only shows trades that were live on Coinbase.
// Toggle off in the UI to inspect paper / test rows for diagnostics.
var showClosedLiveOnly = true;

/** Live Positions tab + live-only stats: not test, not paper, not paper notes if tags missing. */
function isLiveJournalPosition(p) {
  if (p.is_test || p.exit_reason === 'test_cleanup') return false;
  if (p.source === 'paper_trade') return false;
  var n = (p.notes || '').toString();
  if (n.indexOf('paper_trade simulation') !== -1) return false;
  return true;
}

/** Escape for HTML attribute (e.g. title tooltip). */
function escapeHtmlAttr(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;');
}

function tradeModeLabel(p) {
  if (p.source === 'paper_trade') return 'Paper';
  if (p.is_test) return 'Test';
  return 'Live';
}

function toggleClosedLiveOnly() {
  var el = document.getElementById('closed-live-only-checkbox');
  showClosedLiveOnly = !!(el && el.checked);
  renderClosedPositions(closedPositionsFullCache);
}

function loadPositionsTab() {
  fetch('/api/journal/positions')
    .then(function(res) { return res.json(); })
    .then(function(data) {
      var positions = data.positions || [];
      var stats = data.stats || {};
      var pendingTwap = data.pending_twap_orders || [];

      openPositionsAllCache = positions.filter(function(p) { return p.status === 'OPEN'; });
      openPositionsCache = openPositionsAllCache.filter(isLiveJournalPosition);
      closedPositionsFullCache = positions.filter(function(p) { return p.status === 'CLOSED'; });
      renderPositionsStats(stats, positions);
      renderPendingTwap(pendingTwap);
      renderOpenPositions(openPositionsCache);
      renderClosedPositions(closedPositionsFullCache);
      if (typeof loadAccountOverview === 'function') loadAccountOverview();
    })
    .catch(function(e) {
      console.error('Error loading positions:', e);
    });
}

function placeSlTpForPosition(positionId) {
  if (!positionId) return;
  adminFetchJson('/api/journal/position/' + positionId + '/place-sl-tp', { method: 'POST' })
    .then(function(data) {
      if (data.error) { alert(data.error); return; }
      if (typeof loadPositionsTab === 'function') loadPositionsTab();
      var slStr = data.sl_ok ? 'placed' : 'failed';
      var tpStr = data.tp_ok ? 'placed' : (data.tp_attempted ? 'failed' : 'not attempted');
      var msg = 'SL: ' + slStr + ', TP: ' + tpStr;
      if (data.sl_error || data.tp_error) {
        var err = (data.sl_error || '').toString();
        if (data.tp_error && data.tp_error !== err) err += (err ? '; ' : '') + (data.tp_error || '').toString();
        if (err) {
          if (err.indexOf('INSUFFICIENT_FUND') !== -1) msg += '\n\nReason: Insufficient balance on Coinbase. Sync positions or ensure NEAR is in your trading account and not locked by another order.';
          else msg += '\n\n' + (err.length > 200 ? err.substring(0, 200) + '…' : err);
        }
      }
      alert(msg);
    })
    .catch(function(e) { console.error(e); alert('Request failed'); });
}

function unregisterPendingOrder(symbol) {
  if (!symbol) return;
  fetch('/api/execution/unregister-pending', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbol: symbol })
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) { alert(data.error); return; }
      if (data.was_pending) {
        if (typeof loadPositionsTab === 'function') loadPositionsTab();
        if (typeof loadAccountOverview === 'function') loadAccountOverview();
      }
    })
    .catch(function(e) { console.error(e); alert('Request failed'); });
}

function addFromExchangeClearPending(symbol) {
  if (!symbol) return;
  fetch('/api/journal/add-from-exchange-clear-pending', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbol: symbol })
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) { alert(data.error); return; }
      if (typeof loadPositionsTab === 'function') loadPositionsTab();
      if (typeof loadAccountOverview === 'function') loadAccountOverview();
      if (data.message) { alert(data.message); }
    })
    .catch(function(e) { console.error(e); alert('Request failed'); });
}

function renderPendingTwap(pending) {
  var section = document.getElementById('pending-twap-section');
  var tbody = document.getElementById('pending-twap-tbody');
  if (!section || !tbody) return;
  if (!pending || pending.length === 0) {
    section.style.display = 'none';
    return;
  }
  section.style.display = 'block';
  var now = Date.now() / 1000;
  tbody.innerHTML = pending.map(function(o) {
    var sym = (o.symbol || o.coin_name || '—').toString().replace(/</g, '&lt;');
    var symbolRaw = (o.symbol || '').toString();
    var usd = Number(o.total_usd || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    var mode = (o.mode || 'twap').toUpperCase() === 'MARKET' ? 'Market buy' : 'TWAP';
    var startTs = o.start_ts || 0;
    var elapsed = startTs ? Math.floor(now - startTs) : 0;
    var elapsedStr = elapsed < 60 ? elapsed + 's ago' : elapsed < 3600 ? Math.floor(elapsed / 60) + 'm ago' : Math.floor(elapsed / 3600) + 'h ago';
    var esc = symbolRaw.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    var btnRemove = '<button type="button" class="btn-small" onclick="unregisterPendingOrder(\'' + esc + '\')" title="Remove from pending and free reserved cash.">Remove</button>';
    var btnAdd = '<button type="button" class="btn-small btn-primary" onclick="addFromExchangeClearPending(\'' + esc + '\')" title="Add to journal using quantity and price from Coinbase, remove from pending, place SL/TP.">Add to journal from Coinbase</button>';
    return '<tr><td>' + sym + '</td><td>$' + usd + '</td><td>' + mode + '</td><td>' + elapsedStr + '</td><td>' + btnAdd + ' ' + btnRemove + '</td></tr>';
  }).join('');
}

function openEditPositionModal(positionId) {
  var p = openPositionsAllCache.find(function(x) { return x.id === positionId; });
  if (!p) {
    alert('Position not found. Try refreshing the list.');
    return;
  }
  document.getElementById('edit-position-id').value = p.id;
  document.getElementById('edit-position-title').textContent = 'Edit #' + p.id + ' ' + p.coin_symbol;
  document.getElementById('edit-position-notes').value = p.notes || '';
  document.getElementById('edit-position-entry-price').value = (p.entry_price != null ? p.entry_price : '');
  document.getElementById('edit-position-quantity').value = p.quantity;
  document.getElementById('edit-position-target-pct').value = (p.current_target_pct != null ? p.current_target_pct : (p.ai_target_pct != null ? p.ai_target_pct : 6));
  document.getElementById('edit-position-stop-pct').value = (p.current_stop_pct != null ? p.current_stop_pct : (p.ai_stop_pct != null ? p.ai_stop_pct : 4));
  document.getElementById('edit-position-modal').classList.add('open');
}

function hideEditPositionModal() {
  document.getElementById('edit-position-modal').classList.remove('open');
}

function saveEditPosition() {
  var id = document.getElementById('edit-position-id').value;
  if (!id) return;
  var payload = {};
  var notes = document.getElementById('edit-position-notes').value;
  var entryPrice = document.getElementById('edit-position-entry-price').value;
  var qty = document.getElementById('edit-position-quantity').value;
  var targetPct = document.getElementById('edit-position-target-pct').value;
  var stopPct = document.getElementById('edit-position-stop-pct').value;
  if (notes !== undefined) payload.notes = notes;
  if (entryPrice !== '' && entryPrice !== undefined) payload.entry_price = parseFloat(entryPrice);
  if (qty !== '' && qty !== undefined) payload.quantity = parseFloat(qty);
  if (targetPct !== '' && targetPct !== undefined) payload.target_pct = parseFloat(targetPct);
  if (stopPct !== '' && stopPct !== undefined) payload.stop_pct = parseFloat(stopPct);
  adminFetchJson('/api/journal/position/' + id, { method: 'PATCH', body: payload })
    .then(function() {
      hideEditPositionModal();
      loadPositionsTab();
    })
    .catch(function(e) {
      console.error('Error saving position:', e);
      alert('Failed to update position: ' + e.message);
    });
}

function renderPositionsStats(stats, positions) {
  var openPos = positions.filter(function(p) { return p.status === 'OPEN'; });
  var openLive = openPos.filter(isLiveJournalPosition);
  var closedPos = positions.filter(function(p) { return p.status === 'CLOSED'; });
  
  // Calculate total unrealized P&L (live opens only — matches Open Positions table)
  var totalUnrealized = openLive.reduce(function(sum, p) { return sum + (p.unrealized_usd || 0); }, 0);
  
  // Calculate total realized P&L
  var totalRealized = closedPos.reduce(function(sum, p) { return sum + (p.pnl_usd || 0); }, 0);
  
  var winRate = stats.win_rate_pct || 0;
  var aiHitRate = stats.ai_target_hit_rate || 0;
  
  document.getElementById('pstat-open').textContent = openLive.length;
  
  var unrealEl = document.getElementById('pstat-unrealized');
  unrealEl.textContent = totalUnrealized >= 0 ? '+$' + totalUnrealized.toFixed(2) : '-$' + Math.abs(totalUnrealized).toFixed(2);
  unrealEl.style.color = totalUnrealized >= 0 ? 'var(--green)' : 'var(--red)';
  
  document.getElementById('pstat-closed').textContent = closedPos.length;
  
  var realEl = document.getElementById('pstat-realized');
  realEl.textContent = totalRealized >= 0 ? '+$' + totalRealized.toFixed(2) : '-$' + Math.abs(totalRealized).toFixed(2);
  realEl.style.color = totalRealized >= 0 ? 'var(--green)' : 'var(--red)';
  
  var wrEl = document.getElementById('pstat-winrate');
  wrEl.textContent = closedPos.length > 0 ? winRate + '%' : '—';
  wrEl.style.color = winRate >= 55 ? 'var(--green)' : winRate >= 40 ? 'var(--amber)' : 'var(--red)';
  
  var aiEl = document.getElementById('pstat-aihit');
  aiEl.textContent = closedPos.length > 0 ? aiHitRate + '%' : '—';
  aiEl.style.color = aiHitRate >= 50 ? 'var(--green)' : aiHitRate >= 30 ? 'var(--amber)' : 'var(--red)';
}

function renderOpenPositions(positions) {
  var tbody = document.getElementById('open-positions-tbody');
  
  if (!positions.length) {
    tbody.innerHTML = '<tr><td colspan="16" class="no-positions">No live open positions. Paper and test opens are hidden here — see the Trade Journal for all opens. Click 📒 Quick Log to add a live trade.</td></tr>';
    return;
  }
  
  // Sort newest first
  var sorted = positions.slice().sort(function(a, b) { return b.id - a.id; });
  
  tbody.innerHTML = sorted.map(function(p, idx) {
    var pnlPct = p.unrealized_pct || 0;
    var pnlUsd = p.unrealized_usd || 0;
    var pnlCls = pnlPct >= 0 ? 'pos-pnl-positive' : 'pos-pnl-negative';
    var pnlStr = (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(2) + '% (' + (pnlUsd >= 0 ? '+' : '') + '$' + pnlUsd.toFixed(2) + ')';
    
    var targetDist = p.target_distance || 0;
    var stopDist   = p.stop_distance || 0;
    var targetCls  = targetDist >= 0 ? 'pos-distance-positive' : 'pos-distance-negative';
    var stopCls    = stopDist >= 0 ? 'pos-distance-positive' : 'pos-distance-negative';
    var trailingActive = pnlPct >= 3;
    var trailingBadge = trailingActive ? '<span class="pos-trailing-badge" title="Trailing stop active (3% below peak)">Trailing</span>' : '';
    var tradeDate  = (p.entry_time || '').slice(0, 10);
    var entryMs    = p.entry_time ? new Date(p.entry_time).getTime() : Date.now();
    var daysOpen   = Math.floor((Date.now() - entryMs) / (1000 * 60 * 60 * 24));
    var daysStr    = daysOpen > 0 ? daysOpen + 'd' : (daysOpen === 0 ? '<1d' : '0d');
    var riskDenom = _globalTotalCash > 0 ? _globalTotalCash : _globalAccountSize;
    var posBudgetPct = (riskDenom > 0) ? ((p.capital_usd || 0) / riskDenom * 100) : 0;
    var posBudgetColor = (posBudgetPct > _globalRiskCapPct * 0.3) ? 'var(--amber)' : 'var(--text-secondary)';
    var currPriceStr = p.current_price ? Number(p.current_price).toLocaleString() : '—';
    var hasSl = p.sl_order_id != null && p.sl_order_id !== '';
    // Winners always run: no TP order is ever placed, so only the stop-loss matters here.
    var needsSlTp = !hasSl;
    var placeSlTpBtn = needsSlTp ? '<button type="button" class="btn-place-sl-tp" onclick="placeSlTpForPosition(' + p.id + ')" title="Place stop-loss and take-profit orders on Coinbase">Place SL/TP</button> ' : '';
    var qtyNum = p.quantity != null ? Number(p.quantity) : 0;
    var entryNum = p.entry_price != null ? Number(p.entry_price) : 0;
    var usdVal = (qtyNum && entryNum) ? (qtyNum * entryNum) : (p.capital_usd != null ? Number(p.capital_usd) : null);
    var qtyDisp = p.quantity != null ? Number(p.quantity).toLocaleString(undefined, {maximumFractionDigits: 6}) : '—';
    var usdDisp = usdVal != null ? '<span style="color:var(--text-dim);font-size:11px"> $' + Number(usdVal).toLocaleString(undefined, {maximumFractionDigits: 0}) + '</span>' : '';
    var liveOrdinal = idx + 1;
    var journalTip = 'Journal id ' + p.id + (p.trade_id ? ' · ' + p.trade_id : '') + ' (internal ids include paper/test slots)';
    
    return '<tr>' +
      '<td style="color:var(--text-dim);font-weight:600;cursor:help" title="' + escapeHtmlAttr(journalTip) + '">' + liveOrdinal + '</td>' +
      '<td><div style="font-weight:600;color:var(--text-primary)">' + p.coin_name + '</div><div style="font-size:10px;color:var(--text-dim)">' + p.coin_symbol + '</div></td>' +
      '<td><span class="signal-badge ' + p.signal + '" style="font-size:10px;padding:2px 8px">' + p.signal + '</span></td>' +
      '<td>$' + Number(p.entry_price).toLocaleString() + '</td>' +
      '<td style="font-weight:600">$' + currPriceStr + '</td>' +
      '<td><span style="font-family:var(--font-data)">' + qtyDisp + '</span></td>' +
      '<td style="font-family:var(--font-data)">' + (usdVal != null ? '$' + Number(usdVal).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}) : '—') + '</td>' +
      '<td class="' + pnlCls + '">' + pnlStr + ' ' + trailingBadge + '</td>' +
      '<td style="color:var(--green);font-size:11px">$' + Number(p.ai_target_price).toLocaleString() + '<br><span style="color:var(--text-dim)">(' + p.ai_target_pct + '%)</span></td>' +
      '<td class="' + targetCls + '">' + (targetDist >= 0 ? '+' : '') + targetDist.toFixed(2) + '%</td>' +
      '<td style="color:var(--red);font-size:11px">$' + Number(p.ai_stop_price).toLocaleString() + '<br><span style="color:var(--text-dim)">(' + p.ai_stop_pct + '%)</span></td>' +
      '<td class="' + stopCls + '">' + (stopDist >= 0 ? '+' : '') + stopDist.toFixed(2) + '%</td>' +
      '<td style="color:var(--text-dim);font-size:11px">' + (tradeDate || '—') + '</td>' +
      '<td style="color:var(--text-dim);font-size:11px">' + daysStr + '</td>' +
      '<td style="font-size:11px;color:' + posBudgetColor + ';text-align:center">' + posBudgetPct.toFixed(1) + '%</td>' +
      '<td>' + placeSlTpBtn + '<button class="btn-edit-trade" onclick="openEditPositionModal(' + p.id + ')" title="Edit trade details">Edit</button> ' +
      '<button class="btn-close-trade" onclick="promptClosePosition(' + p.id + ', \'' + (p.coin_symbol || '').replace(/'/g, "\\'") + '\', ' + p.entry_price + ')">Close</button></td>' +
    '</tr>';
  }).join('');
}

function renderClosedPositions(positions) {
  var tbody = document.getElementById('closed-positions-tbody');
  var list = positions || [];
  if (showClosedLiveOnly) {
    list = list.filter(isLiveJournalPosition);
  }

  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="14" class="no-positions">' +
      (showClosedLiveOnly ? 'No live closed trades match this filter.' : 'No closed positions yet.') +
      '</td></tr>';
    return;
  }
  
  // Sort newest exit first (then journal id) so a just-closed row appears at the top
  var sorted = list.slice().sort(function(a, b) {
    var ta = Date.parse(String(a.exit_time || '').replace(' ', 'T')) || 0;
    var tb = Date.parse(String(b.exit_time || '').replace(' ', 'T')) || 0;
    if (tb !== ta) return tb - ta;
    return (b.id || 0) - (a.id || 0);
  });

  tbody.innerHTML = sorted.map(function(p, idx) {
    var pnlPct = p.pnl_pct || 0;
    var pnlUsd = p.pnl_usd || 0;
    var pnlCls = pnlPct >= 0 ? 'pos-pnl-positive' : 'pos-pnl-negative';
    var pnlStr = (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(2) + '% (' + (pnlUsd >= 0 ? '+' : '') + '$' + pnlUsd.toFixed(2) + ')';
    
    var outcome = p.outcome || '—';
    var outCls = outcome === 'win' ? 'pos-pnl-positive' : outcome === 'loss' ? 'pos-pnl-negative' : '';
    var outStr = outcome === 'win' ? '\u2713 WIN' : outcome === 'loss' ? '\u2717 LOSS' : '\u2248 EVEN';
    
    var hitAI = p.hit_ai_target === true ? '<span class="pos-ai-hit-yes">\u2713 YES</span>' : 
                p.hit_ai_target === false ? '<span class="pos-ai-hit-no">\u2717 NO</span>' : '—';
    
    var entryMs = Date.parse(String(p.entry_time || '').replace(' ', 'T')) || 0;
    var exitMs = Date.parse(String(p.exit_time || '').replace(' ', 'T')) || 0;
    var duration = entryMs && exitMs ? Math.floor((exitMs - entryMs) / (1000 * 60 * 60 * 24)) : 0;
    var durationStr = duration > 0 ? duration + 'd' : '<1d';
    var entrySlice = (p.entry_time || '').slice(0, 16).replace(' ', '<br>');
    var exitSlice = (p.exit_time || '').slice(0, 16).replace(' ', '<br>');
    var qtyNum = p.quantity != null ? Number(p.quantity) : 0;
    var entryNum = p.entry_price != null ? Number(p.entry_price) : 0;
    var usdVal = (qtyNum && entryNum) ? (qtyNum * entryNum) : (p.capital_usd != null ? Number(p.capital_usd) : null);
    var qtyDisp = p.quantity != null ? Number(p.quantity).toLocaleString(undefined, {maximumFractionDigits: 6}) : '—';
    var usdDisp = usdVal != null ? ' <span style="color:var(--text-dim);font-size:11px">$' + Number(usdVal).toLocaleString(undefined, {maximumFractionDigits: 0}) + '</span>' : '';
    var liveOrdinal = idx + 1;
    var journalTip = 'Journal id ' + p.id + (p.trade_id ? ' · ' + p.trade_id : '') + ' · closed rank #' + liveOrdinal + ' (newest exits first)';

    return '<tr data-journal-id="' + p.id + '">' +
      '<td style="color:var(--text-dim);font-weight:600;cursor:help" title="' + escapeHtmlAttr(journalTip) + '">' +
        '<span style="color:var(--text-primary)">#' + p.id + '</span>' +
        '<span style="display:block;font-size:10px;opacity:0.7">(' + liveOrdinal + ')</span></td>' +
      '<td style="font-size:11px;color:var(--text-dim)">' + tradeModeLabel(p) + '</td>' +
      '<td><div style="font-weight:600;color:var(--text-primary)">' + p.coin_name + '</div><div style="font-size:10px;color:var(--text-dim)">' + p.coin_symbol + '</div></td>' +
      '<td><span class="signal-badge ' + p.signal + '" style="font-size:10px;padding:2px 8px">' + p.signal + '</span></td>' +
      '<td>$' + Number(p.entry_price).toLocaleString() + '</td>' +
      '<td>$' + Number(p.exit_price).toLocaleString() + '</td>' +
      '<td><span style="font-family:var(--font-data)">' + qtyDisp + '</span>' + usdDisp + '</td>' +
      '<td class="' + pnlCls + '">' + pnlStr + '</td>' +
      '<td style="color:var(--green);font-size:11px">$' + Number(p.ai_target_price).toLocaleString() + '<br><span style="color:var(--text-dim)">(' + p.ai_target_pct + '%)</span></td>' +
      '<td style="text-align:center">' + hitAI + '</td>' +
      '<td class="' + outCls + '">' + outStr + '</td>' +
      '<td style="color:var(--text-dim);font-size:11px">' + entrySlice + '</td>' +
      '<td style="color:var(--text-dim);font-size:11px">' + exitSlice + '</td>' +
      '<td style="color:var(--text-dim)">' + durationStr + '</td>' +
    '</tr>';
  }).join('');
}

function exportCSV() {
  window.location.href = '/api/journal/export';
}

// ============================================================
// ACCOUNT OVERVIEW WIDGET
// ============================================================
var accountOverviewDirty = false;
var aoSharpePeriod = 30;

function loadAccountOverviewSharpe() {
  var days = aoSharpePeriod || 30;
  fetch('/api/monthly-review?days=' + days)
.then(function(res) { return res.json(); })
.then(function(d) {
if (d.error) return;
var perf = d.performance || {};
var sharpe = perf.sharpe_ratio;
var el = document.getElementById('ao-sharpe-val');
if (el) {
  el.textContent = sharpe != null ? Number(sharpe).toFixed(2) : '—';
  el.className = 'ao-sharpe-val ' + sharpeColorClass(sharpe);
}
document.querySelectorAll('.ao-sharpe-pill').forEach(function(p) {
  p.classList.toggle('active', parseInt(p.getAttribute('data-days')) === days);
});
})
.catch(function(e) { console.error('Load AO Sharpe:', e); });
}

function setAoSharpePeriod(days) {
  aoSharpePeriod = days;
  loadAccountOverviewSharpe();
}

function loadAccountOverview() {
  // Reconcile uses server-side 5-min cache (no ?force=1), avoids hitting exchange API on every load
  fetch('/api/account/reconcile').then(function(r) { return r.json(); }).catch(function() { return null; })
  .then(function(reconcileData) {
return fetch('/api/account/overview').then(function(r) { return r.json(); }).then(function(d) { return [reconcileData, d]; });
  })
  .then(function(results) {
var reconcileData = results[0];
var d = results[1];
if (d.error) return;
_globalAccountSize = Number(d.total_account_value) || 0;
var tc = Number(d.total_cash) || 0;
if (tc > 0) _globalTotalCash = tc;
// Total Account Value
document.getElementById('ao-total-value').textContent = '$' + Number(d.total_account_value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
// Cash Balance (read-only, merged field)
var cashBalEl = document.getElementById('ao-cash-balance');
var totalCash = Number(d.total_cash) || 0;
var cashAvail = Number(d.cash_available_to_trade) || 0;
if (cashBalEl) cashBalEl.textContent = '$' + totalCash.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
// Pending TWAP hint (show under cash balance when orders reserve cash)
var pendingHint = document.getElementById('ao-pending-twap-hint');
if (pendingHint) {
  var pendingUsd = Number(d.pending_twap_usd) || 0;
  if (pendingUsd > 0) {
    pendingHint.textContent = '$' + pendingUsd.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' reserved \u2192 $' + cashAvail.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' available';
    pendingHint.style.display = 'block';
  } else {
    pendingHint.style.display = 'none';
  }
}
// Editable fields
document.getElementById('ao-risk-pct').value = d.risk_pct;
document.getElementById('ao-max-cash-pct').value = d.max_cash_usage_pct != null ? d.max_cash_usage_pct : 40;
document.getElementById('ao-dust-threshold').value = d.dust_threshold_usd != null ? d.dust_threshold_usd : 1;
// HELOC indicator badge
var helocIndicator = document.getElementById('ao-heloc-indicator');
var helocBadge = document.getElementById('ao-heloc-badge');
if (helocIndicator && helocBadge) {
  if (d.heloc_mode) {
    var drawnStr = '$' + Number(d.heloc_drawn || 0).toLocaleString('en-US', { maximumFractionDigits: 0 });
    var notionalStr = '$' + Number(d.heloc_notional || 0).toLocaleString('en-US', { maximumFractionDigits: 0 });
    helocBadge.textContent = 'HELOC: ' + drawnStr + ' / ' + notionalStr + ' drawn';
    helocIndicator.style.display = '';
  } else {
    helocIndicator.style.display = 'none';
  }
}
// Risk per Trade
document.getElementById('ao-risk-per-trade').textContent = '$' + Number(d.max_risk_usd_per_trade || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' (' + d.risk_pct + '%)';
// Est. Next Trade Size (NEW)
var nextTradeEl = document.getElementById('ao-next-trade-size');
if (nextTradeEl) {
  var refSize = Number(d.reference_cost_basis_per_trade) || 0;
  var refStop = Number(d.reference_stop_pct) || 0;
  nextTradeEl.textContent = '$' + refSize.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + (refStop > 0 ? ' (at ' + refStop.toFixed(1) + '% stop)' : '');
}
// Open Positions with capacity (NEW)
var openPosEl = document.getElementById('ao-open-positions');
if (openPosEl) {
  var count = Number(d.open_positions_count) || 0;
  var maxOpen = Number(d.max_open_total) || 8;
  openPosEl.textContent = count + ' / ' + maxOpen + ' max';
  openPosEl.className = 'account-overview-value' + (count >= maxOpen ? ' ao-positions-at-capacity' : (count >= maxOpen - 1 ? ' ao-positions-near-capacity' : ''));
}
var deployedEl = document.getElementById('ao-deployed-capital');
if (deployedEl) {
  var mv = Number(d.open_positions_market_value) || 0;
  var tav = Number(d.total_account_value) || 1;
  var pct = tav > 0 ? (mv / tav * 100) : 0;
  deployedEl.textContent = '$' + mv.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' deployed (' + pct.toFixed(0) + '%)';
}
// Low cash banner
var helocMode = !!d.heloc_mode;
var helocNotional = Number(d.heloc_notional) || 0;
var lowCashThreshold = (helocMode && helocNotional > 0) ? 0.30 * helocNotional : 0.30 * totalCash;
var lowCashBanner = document.getElementById('ao-low-cash-banner');
var lowCashMsg = document.getElementById('ao-low-cash-msg');
if (lowCashBanner && lowCashMsg && totalCash > 0 && cashAvail < lowCashThreshold) {
  var pendingUsd2 = Number(d.pending_twap_usd) || 0;
  var hint = pendingUsd2 > 0
    ? ' Cash is reserved for pending TWAP/market buys on the Positions tab.'
    : (helocMode ? ' Consider drawing more HELOC to maintain dry powder.' : ' Consider adding more funds or use Sync cash.');
  var msgPrefix = (helocMode && helocNotional > 0)
    ? 'Low Cash ($' + cashAvail.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' is below 30% of notional $' + helocNotional.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 }) + ')'
    : 'Low Cash ($' + cashAvail.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' of $' + totalCash.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' total)';
  lowCashMsg.textContent = msgPrefix + hint;
  lowCashBanner.style.display = 'flex';
} else if (lowCashBanner) {
  lowCashBanner.style.display = 'none';
}
if (typeof loadAccountOverviewSharpe === 'function') { loadAccountOverviewSharpe(); }
fetch('/api/position-size/settings').then(function(r) { return r.json(); }).then(function(ps) {
  var sumEl = document.getElementById('ao-position-sizer-summary');
  if (!sumEl || !ps || ps.error) {
    if (sumEl && ps && ps.error) sumEl.textContent = 'Error: ' + ps.error;
    return;
  }
  sumEl.textContent =
    'Sizer module — account_size $' + Number(ps.account_size || 0).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 }) +
    ' · max_risk_pct ' + (ps.max_risk_pct != null ? ps.max_risk_pct : '—') + '%' +
    (ps.max_cash_usage_pct != null ? ' · max_cash_usage_pct ' + ps.max_cash_usage_pct + '%' : '');
}).catch(function() {
  var sumEl = document.getElementById('ao-position-sizer-summary');
  if (sumEl) sumEl.textContent = '—';
});
var syncBtn = document.getElementById('ao-sync-balance-btn');
if (syncBtn) syncBtn.disabled = !isSyncBalanceAvailable(d);
var lastSyncEl = document.getElementById('ao-last-sync-indicator');
if (lastSyncEl) {
  if (d.last_auto_sync_at) {
    var then = new Date(d.last_auto_sync_at).getTime();
    var minAgo = Math.floor((Date.now() - then) / 60000);
    lastSyncEl.textContent = minAgo < 1 ? 'Synced just now' : (minAgo === 1 ? 'Synced 1 min ago' : 'Synced ' + minAgo + ' min ago');
    if (Date.now() - then < 30000) triggerSyncHighlight();
  } else {
    lastSyncEl.textContent = '';
  }
}
// Reconcile status
var reconcileForceBtn = document.getElementById('ao-reconcile-force-btn');
if (reconcileData && !reconcileData.error) {
  lastReconcileData = reconcileData;
  var status = reconcileData.status || 'clean';
  var disc = reconcileData.discrepancies || [];
  var extCount = disc.filter(function(d) { return d.type === 'external'; }).length;
  var statusMsg = status === 'clean' ? 'Clean' : (extCount === disc.length && extCount > 0 ? extCount + ' external position(s) detected' : disc.length + ' discrepancy(ies) detected');
  var statusEl = document.getElementById('ao-reconcile-status');
  var btnEl = document.getElementById('ao-review-discrepancies-btn');
  if (statusEl) {
    statusEl.textContent = statusMsg;
    statusEl.className = 'account-overview-reconcile-status ' + (status === 'clean' ? 'clean' : 'discrepancy');
  }
  if (btnEl) btnEl.style.display = disc.length > 0 ? 'inline-block' : 'none';
  if (reconcileForceBtn) reconcileForceBtn.style.display = 'inline-block';
} else {
  lastReconcileData = null;
  var statusEl = document.getElementById('ao-reconcile-status');
  var btnEl = document.getElementById('ao-review-discrepancies-btn');
  if (statusEl) statusEl.textContent = '';
  if (btnEl) btnEl.style.display = 'none';
  if (reconcileForceBtn) reconcileForceBtn.style.display = 'none';
}
accountOverviewDirty = false;
  }).catch(function(e) {
console.error('Account overview load failed:', e);
  });
}

function triggerSyncHighlight() {
  var cashBalEl = document.getElementById('ao-cash-balance');
  if (cashBalEl) cashBalEl.classList.add('account-overview-sync-highlight');
  setTimeout(function() {
if (cashBalEl) cashBalEl.classList.remove('account-overview-sync-highlight');
  }, 2000);
}

function forceReconcile() {
  var btn = document.getElementById('ao-reconcile-force-btn');
  if (btn) btn.disabled = true;
  fetch('/api/account/reconcile?force=1').then(function(r) { return r.json(); })
.then(function(data) {
  if (btn) btn.disabled = false;
  if (data && !data.error) {
    lastReconcileData = data;
    loadAccountOverview();
  }
})
.catch(function() { if (btn) btn.disabled = false; });
}

function toggleAoAdvanced() {
  var section = document.getElementById('ao-advanced-section');
  var arrow = document.getElementById('ao-advanced-arrow');
  if (section.style.display === 'none') {
section.style.display = '';
arrow.classList.add('open');
  } else {
section.style.display = 'none';
arrow.classList.remove('open');
  }
}

var lastReconcileData = null;

function updateReconcileStatus() {
  var statusEl = document.getElementById('ao-reconcile-status');
  var btnEl = document.getElementById('ao-review-discrepancies-btn');
  fetch('/api/account/reconcile')
.then(function(res) { return res.json(); })
.then(function(data) {
  if (data.error) {
    if (statusEl) statusEl.textContent = '';
    if (btnEl) { btnEl.style.display = 'none'; }
    lastReconcileData = null;
    return;
  }
  lastReconcileData = data;
  var status = data.status || 'clean';
  var disc = data.discrepancies || [];
  var extCount = disc.filter(function(d) { return d.type === 'external'; }).length;
  var statusMsg = status === 'clean' ? 'Clean' : (extCount === disc.length && extCount > 0 ? extCount + ' external position(s) detected' : disc.length + ' discrepancy(ies) detected');
  if (statusEl) {
    statusEl.textContent = statusMsg;
    statusEl.className = 'account-overview-reconcile-status ' + (status === 'clean' ? 'clean' : 'discrepancy');
  }
  if (btnEl) {
    btnEl.style.display = disc.length > 0 ? 'inline-block' : 'none';
  }
})
.catch(function(e) {
  if (statusEl) statusEl.textContent = '';
  if (btnEl) btnEl.style.display = 'none';
  lastReconcileData = null;
});
}

function openReconcileModal() {
  var content = document.getElementById('reconcile-modal-content');
  if (!content || !lastReconcileData || !lastReconcileData.discrepancies || lastReconcileData.discrepancies.length === 0) {
if (content) content.innerHTML = '<p>No discrepancies.</p>';
  } else {
var html = '<p style="margin-bottom:10px;font-size:12px;color:var(--text-secondary);">Add exchange-only holdings to the journal so they appear on the Positions tab.</p><ul style="list-style:none;padding:0;margin:0;">';
lastReconcileData.discrepancies.forEach(function(d) {
  if (d.type === 'external') {
    var sym = (d.symbol || '').toString().replace(/</g, '&lt;');
    var qty = d.exchange_quantity != null ? d.exchange_quantity : '';
    html += '<li style="margin-bottom:10px;color:var(--amber);display:flex;align-items:center;gap:10px;flex-wrap:wrap;">';
    html += '<span>On exchange only: <strong>' + sym + '</strong> qty ' + qty + '</span>';
    html += '<button type="button" class="account-overview-sync-btn" onclick="addExternalToJournal(\'' + sym.replace(/'/g, "\\'") + '\', ' + parseFloat(qty) + ', this)">Add to journal</button>';
    html += '</li>';
  } else if (d.type === 'quantity_mismatch') {
    html += '<li style="margin-bottom:8px;color:var(--amber);">Quantity mismatch <strong>' + (d.symbol || '') + '</strong>: journal ' + (d.journal_quantity != null ? d.journal_quantity : '') + ' vs exchange ' + (d.exchange_quantity != null ? d.exchange_quantity : '') + ' — update journal or use Sync cash and refresh.</li>';
  } else {
    html += '<li style="margin-bottom:8px;color:var(--text-secondary);">In journal only: <strong>' + (d.symbol || '') + '</strong> qty ' + (d.journal_quantity != null ? d.journal_quantity : '') + ' — if you just booked this trade, try Sync cash then refresh.</li>';
  }
});
html += '</ul>';
content.innerHTML = html;
  }
  document.getElementById('reconcile-modal').classList.add('open');
}

function closeReconcileModal() {
  document.getElementById('reconcile-modal').classList.remove('open');
}

function syncPositionQuantitiesFromExchange() {
  var btnModal = document.getElementById('reconcile-sync-qty-btn');
  var btnOverview = document.getElementById('ao-sync-positions-btn');
  if (btnModal) btnModal.disabled = true;
  if (btnOverview) btnOverview.disabled = true;
  adminFetchJson('/api/account/sync-position-quantities', { method: 'POST' })
.then(function(data) {
  if (btnModal) btnModal.disabled = false;
  if (btnOverview) btnOverview.disabled = false;
  if (data.error) {
    alert('Sync failed: ' + (data.error || 'Unknown error'));
    return;
  }
  var n = (data.updated || []).length;
  if (n > 0) {
    if (typeof loadAccountOverview === 'function') loadAccountOverview();
    if (typeof loadPositionsTab === 'function') loadPositionsTab();
    fetch('/api/account/reconcile').then(function(r) { return r.json(); }).then(function(r) {
      lastReconcileData = r;
      if (document.getElementById('reconcile-modal').classList.contains('open')) openReconcileModal();
    });
    alert('Updated ' + n + ' position(s) to match exchange.');
  } else {
    var skipped = data.skipped || [];
    var errs = (data.errors || []).length;
    if (errs > 0) alert('Sync had errors. See console.');
    else if (skipped.length > 0) {
      var parts = skipped.map(function(s) {
        var r = s.reason;
        if (r === 'no_open_position_in_journal') return (s.symbol || '?') + ': no open position in journal';
        if (r === 'below_dust_threshold') return (s.symbol || '?') + ': below dust threshold (skipped)';
        return (s.symbol || '?') + ': ' + r;
      });
      alert('No positions updated for: ' + parts.join('; ') + '.');
    } else alert('Quantities already match the exchange.');
  }
})
.catch(function(e) {
  if (btnModal) btnModal.disabled = false;
  if (btnOverview) btnOverview.disabled = false;
  console.error('Sync position quantities failed:', e);
  alert('Sync failed. See console.');
});
}

function addExternalToJournal(symbol, quantity, btnEl) {
  if (!symbol || !quantity || quantity <= 0) return;
  if (btnEl) btnEl.disabled = true;
  adminFetchJson('/api/journal/open-from-exchange', {
    method: 'POST',
    body: { symbol: symbol, quantity: quantity },
  })
.then(function(data) {
  if (data.error) {
    alert(data.error);
    if (btnEl) btnEl.disabled = false;
    return;
  }
  if (typeof loadPositionsTab === 'function') loadPositionsTab();
  if (typeof loadAccountOverview === 'function') loadAccountOverview();
  closeReconcileModal();
  fetch('/api/account/reconcile').then(function(r) { return r.json(); }).then(function(r) { lastReconcileData = r; });
})
.catch(function(e) {
  console.error('Add to journal failed:', e);
  alert('Failed to add position. See console.');
  if (btnEl) btnEl.disabled = false;
});
}

function syncBalanceFromExchange() {
  var btn = document.getElementById('ao-sync-balance-btn');
  var msgEl = document.getElementById('ao-sync-msg');
  if (btn) btn.disabled = true;
  if (msgEl) { msgEl.textContent = ''; msgEl.className = 'account-overview-sync-msg'; }
  fetch('/api/account/sync-balance')
.then(function(res) { return res.json(); })
.then(function(data) {
  if (data.success) {
    var cashBalEl = document.getElementById('ao-cash-balance');
    if (cashBalEl) cashBalEl.textContent = '$' + Number(data.total_cash).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (msgEl) {
      msgEl.textContent = (data.message || 'Synced.') + ' Total cash: $' + Number(data.total_cash).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      msgEl.className = 'account-overview-sync-msg success';
    }
    setTimeout(function() { if (msgEl) msgEl.textContent = ''; }, 6000);
    loadAccountOverview();
    triggerSyncHighlight();
  } else {
    if (msgEl) {
      msgEl.textContent = data.error || 'Sync failed';
      msgEl.className = 'account-overview-sync-msg error';
    }
  }
  if (btn) btn.disabled = !isSyncBalanceAvailable(data);
})
.catch(function(e) {
  if (msgEl) {
    msgEl.textContent = 'Request failed: ' + (e.message || 'Network error');
    msgEl.className = 'account-overview-sync-msg error';
  }
  if (btn) btn.disabled = false;
});
}

function isSyncBalanceAvailable(d) {
  if (!d) return false;
  var ex = (d.auto_execution_exchange || 'coinbase').toLowerCase();
  return (ex === 'coinbase' && d.coinbase_configured) || (ex === 'kraken' && d.kraken_configured);
}

function markAccountOverviewDirty() {
  accountOverviewDirty = true;
  document.getElementById('account-overview-save-btn').style.display = 'inline-block';
}

// ── Funding Tab (HELOC) ─────────────────────────────────────────────
var fundingDirty = false;
var fundingPeriod = 30;

function markFundingDirty() { fundingDirty = true; }

function loadFundingTab() {
  fetch('/api/funding').then(function(r) { return r.json(); }).then(function(d) {
if (d.error) return;
var modeEl = document.getElementById('funding-heloc-mode');
if (modeEl) modeEl.checked = !!d.heloc_mode;
var notionalEl = document.getElementById('funding-notional');
if (notionalEl) notionalEl.value = d.heloc_notional || '';
var rateEl = document.getElementById('funding-rate');
if (rateEl) rateEl.value = d.heloc_rate_pct || 8.0;
var drawnEl = document.getElementById('funding-drawn');
if (drawnEl) drawnEl.value = d.heloc_drawn || '';
var ddEl = document.getElementById('funding-dd-limit');
if (ddEl) ddEl.value = d.max_drawn_drawdown_pct || 30;
var hurdleEl = document.getElementById('funding-hurdle-mode');
if (hurdleEl) hurdleEl.value = d.hurdle_mode || 'per_trade';
// Cost of Capital metrics
var m = d.metrics || {};
setText('funding-usdc-yield', (d.usdc_yield_pct || 0).toFixed(1) + '% APY');
var yieldUpdated = document.getElementById('funding-yield-updated');
if (yieldUpdated) yieldUpdated.textContent = d.usdc_yield_last_updated ? 'Updated ' + d.usdc_yield_last_updated : '';
setText('funding-net-idle-cost', (m.net_idle_cost_pct || 0).toFixed(2) + '%');
setText('funding-deployed-cost', (m.deployed_cost_pct || 0).toFixed(2) + '%');
setText('funding-hurdle', (m.hurdle_per_trade_pct || 0).toFixed(3) + '%');
setText('funding-avg-hold', (m.avg_hold_days || 0).toFixed(1) + ' days');
setText('funding-monthly-carry', '$' + (m.monthly_carry_est || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
setText('funding-deployed-idle', '$' + (m.deployed_usd || 0).toLocaleString('en-US', { maximumFractionDigits: 0 }) + ' / $' + (m.idle_usd || 0).toLocaleString('en-US', { maximumFractionDigits: 0 }));
var ddPct = m.drawn_drawdown_pct || 0;
var ddText = ddPct.toFixed(1) + '%';
var ddEl2 = document.getElementById('funding-drawdown');
if (ddEl2) {
  ddEl2.textContent = ddText;
  ddEl2.style.color = m.drawn_drawdown_alert ? 'var(--red)' : '';
}
fundingDirty = false;
  }).catch(function(e) { console.error('Load Funding:', e); });
  loadFundingPerformance(fundingPeriod);
}

function setText(id, text) {
  var el = document.getElementById(id);
  if (el) el.textContent = text;
}

function saveFundingSettings() {
  var payload = {
heloc_mode: document.getElementById('funding-heloc-mode').checked,
heloc_notional: parseFloat(document.getElementById('funding-notional').value) || 0,
heloc_rate_pct: parseFloat(document.getElementById('funding-rate').value) || 8.0,
heloc_drawn: parseFloat(document.getElementById('funding-drawn').value) || 0,
max_drawn_drawdown_pct: parseFloat(document.getElementById('funding-dd-limit').value) || 30,
hurdle_mode: document.getElementById('funding-hurdle-mode').value || 'per_trade',
  };
  if (payload.heloc_notional < 0) { alert('Notional amount cannot be negative.'); return; }
  if (payload.heloc_drawn < 0) { alert('Drawn amount cannot be negative.'); return; }
  fetch('/api/funding/update', {
method: 'POST',
headers: { 'Content-Type': 'application/json' },
body: JSON.stringify(payload),
  })
  .then(function(res) { return res.json(); })
  .then(function(data) {
if (data.error) { alert('Error: ' + data.error); return; }
fundingDirty = false;
var savedMsg = document.getElementById('funding-saved-msg');
if (data.saved_at) {
  savedMsg.textContent = 'Saved at ' + data.saved_at;
  setTimeout(function() { savedMsg.textContent = ''; }, 5000);
}
loadFundingTab();
loadAccountOverview(); // refresh HELOC badge in account overview
  })
  .catch(function(e) { alert('Save error: ' + e); });
}

function refreshUsdcYield() {
  fetch('/api/funding/refresh-yield', { method: 'POST' })
  .then(function(r) { return r.json(); })
  .then(function(d) {
if (d.warning) { alert(d.warning); }
loadFundingTab();
  })
  .catch(function(e) { alert('Refresh error: ' + e); });
}

function setFundingPeriod(period) {
  fundingPeriod = period;
  document.querySelectorAll('[data-funding-period]').forEach(function(btn) {
btn.classList.toggle('active', String(btn.getAttribute('data-funding-period')) === String(period));
  });
  loadFundingPerformance(period);
}

function loadFundingPerformance(period) {
  fetch('/api/funding/performance?period=' + period).then(function(r) { return r.json(); }).then(function(d) {
if (d.error) return;
var pnlEl = document.getElementById('funding-perf-pnl');
if (pnlEl) {
  pnlEl.textContent = '$' + Number(d.trading_pnl || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  pnlEl.style.color = (d.trading_pnl || 0) >= 0 ? 'var(--green)' : 'var(--red)';
}
var carryEl = document.getElementById('funding-perf-carry');
if (carryEl) {
  carryEl.textContent = '-$' + Math.abs(d.carry_cost || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  carryEl.style.color = 'var(--red)';
}
var netEl = document.getElementById('funding-perf-net');
if (netEl) {
  netEl.textContent = '$' + Number(d.net_after_carry || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  netEl.style.color = (d.net_after_carry || 0) >= 0 ? 'var(--green)' : 'var(--red)';
}
var rocEl = document.getElementById('funding-perf-roc');
if (rocEl) {
  rocEl.textContent = (d.annualized_roc_pct || 0).toFixed(1) + '%';
  rocEl.style.color = (d.annualized_roc_pct || 0) >= 0 ? 'var(--green)' : 'var(--red)';
}
setText('funding-perf-trades', d.trades_closed || 0);
setText('funding-perf-period', (d.period_days || 0) + ' days');
  }).catch(function(e) { console.error('Load Funding Performance:', e); });
}

function saveAccountOverview() {
  var riskPct = parseFloat(document.getElementById('ao-risk-pct').value);
  var maxCashPct = parseFloat(document.getElementById('ao-max-cash-pct').value);
  if (isNaN(riskPct) || riskPct < 0.1 || riskPct > 5) {
alert('Risk % must be between 0.1 and 5.0.');
return;
  }
  if (isNaN(maxCashPct) || maxCashPct < 20 || maxCashPct > 65) {
alert('Max Cash Per Trade % must be between 20 and 65.');
return;
  }
  var dustThreshold = parseFloat(document.getElementById('ao-dust-threshold').value);
  if (isNaN(dustThreshold) || dustThreshold < 0.01) {
alert('Dust Threshold must be at least 0.01.');
return;
  }
  var payload = { risk_pct: riskPct, max_cash_usage_pct: maxCashPct, dust_threshold_usd: dustThreshold };
  adminFetchJson('/api/account/update', { method: 'POST', body: payload })
.then(function(data) {
  if (data.error) {
    alert('Error: ' + data.error);
    return;
  }
  accountOverviewDirty = false;
  var savedMsg = document.getElementById('ao-saved-msg');
  if (data.saved_at) {
    savedMsg.textContent = 'Saved at ' + data.saved_at;
    setTimeout(function() { savedMsg.textContent = ''; }, 5000);
  }
  loadAccountOverview();
})
.catch(function(e) {
  alert('Failed to save: ' + e.message);
});
}

// ============================================================
// DATA SOURCE HEALTH TRACKING
// ============================================================

function updateHealth() {
var statusEl = document.getElementById('health-status');
var iconEl = document.getElementById('health-icon');
fetch('/api/health')
    .then(function(response) { return response.json(); })
    .then(function(data) {
        var sources = data.sources || {};
        var summary = data.summary || {};
        statusEl.textContent = summary.ok + '/' + summary.total + ' OK';
        if (summary.errors === 0) {
            statusEl.style.color = 'var(--green)';
            iconEl.textContent = '\u2705';
        } else if (summary.errors > 2) {
            statusEl.style.color = 'var(--red)';
            iconEl.textContent = '\u274C';
        } else {
            statusEl.style.color = 'var(--amber)';
            iconEl.textContent = '\u26A0\uFE0F';
        }
        var sourceNames = {
            coingecko: 'CoinGecko (Price Data)',
            fear_greed: 'F&G',
            fred: 'FRED',
            alpha_vantage: 'News',
            btc_correlation: 'BTC',
            onchain: 'On-Chain',
            indicators: 'Indicators',
            scoring: 'Scoring',
            dynamic_targets: 'Targets',
            quant_veto: 'Quant'
        };
        var html = '';
        for (var key in sources) {
            if (!sources.hasOwnProperty(key)) continue;
            var status = sources[key];
            var icon = status.status === 'ok' ? '\u2713' :
                        status.status === 'error' ? '\u2717' :
                        status.status === 'warning' ? '\u26A0' : '\u25CB';
            var cls = 'health-' + status.status;
            html += '<div class="health-source ' + cls + '">' + icon + ' ' + (sourceNames[key] || key) + '</div>';
        }
        document.getElementById('health-sources').innerHTML = html;
    })
    .catch(function(e) {
        console.error('Health check failed:', e);
        if (statusEl) statusEl.textContent = 'Error';
        if (statusEl) statusEl.style.color = 'var(--red)';
        if (iconEl) iconEl.textContent = '\u274C';
    });
}

// ============================================================
// GLOBAL MARKET CAP FETCHER
// ============================================================

function fetchGlobalMarketCap() {
  fetch('/api/global-market-cap')
.then(function(response) { return response.json(); })
.then(function(data) {
if (data && data.data) {
  var gmc = data.data;
  
  // Format market cap (in trillions)
  var mcapUSD = (gmc.total_market_cap && gmc.total_market_cap.usd);
  var mcapT = mcapUSD ? (mcapUSD / 1e12).toFixed(2) : '—';
  document.getElementById('total-mcap').textContent = mcapT !== '—' ? '$' + mcapT + 'T' : '—';
  
  // Format and color performance metrics
  function updatePerf(id, value) {
    var elem = document.getElementById(id);
    if (value != null) {
      var sign = value >= 0 ? '+' : '';
      elem.textContent = sign + value.toFixed(2) + '%';
      elem.className = 'mcap-perf-value ' + (value > 0 ? 'positive' : value < 0 ? 'negative' : 'neutral');
    } else {
      elem.textContent = '—';
      elem.className = 'mcap-perf-value neutral';
    }
  }
  
  updatePerf('mcap-1d', gmc.market_cap_change_percentage_24h_usd);
  updatePerf('mcap-1w', gmc.market_cap_change_percentage_7d_usd || null);
  updatePerf('mcap-1m', gmc.market_cap_change_percentage_30d_usd || null);
}
})
.catch(function(e) {
  console.error('Error fetching global market cap:', e);
  document.getElementById('total-mcap').textContent = '\u2014';
  document.getElementById('mcap-1d').textContent = '\u2014';
  document.getElementById('mcap-1w').textContent = '\u2014';
  document.getElementById('mcap-1m').textContent = '\u2014';
});
}

function showHealthDetails() {
fetch('/api/health')
    .then(function(r) { return r.json(); })
    .then(function(data) {
        var sourceNames = {
            coingecko: 'CoinGecko API',
            fear_greed: 'Fear & Greed Index',
            fred: 'FRED Economic Data',
            alpha_vantage: 'News Sentiment (Alpha Vantage)',
            btc_correlation: 'BTC Correlation Calculator',
            onchain: 'On-Chain (CryptoQuant)',
            indicators: 'Technical Indicators Engine',
            scoring: 'Multi-Factor Scoring System',
            dynamic_targets: 'Dynamic Target Calculator',
            quant_veto: 'Quant + Veto Engine'
        };
        
        var details = Object.keys(data.sources).map(function(name) {
                var status = data.sources[name];
                var icon = status.status === 'ok' ? '\u2705' : status.status === 'error' ? '\u274C' : status.status === 'warning' ? '\u26A0\uFE0F' : '\u26AA';
                var time = status.time || 'never';
                var error = status.error ? '\n   Error: ' + status.error : '';
                return icon + ' ' + (sourceNames[name] || name) + '\n   Status: ' + status.status.toUpperCase() + '\n   Last Check: ' + time + error;
            }).join('\n\n');
        
        var eq = (function(){ var s=''; for(var i=0;i<50;i++) s+='='; return s; })();
        var msg = 'DATA SOURCE HEALTH DIAGNOSTICS\n' + eq + '\n\nSummary: ' + data.summary.ok + ' OK, ' + data.summary.errors + ' Errors, ' + data.summary.warnings + ' Warnings\nLast Updated: ' + data.last_updated + '\n\n' + details + '\n\n' + eq + '\nClick health bar again to refresh';
        
        alert(msg);
    })
    .catch(function(error) {
        alert('Failed to load health details:\n' + error.message);
    });
}

// Update health every 30 seconds
setInterval(updateHealth, 30000);

// Update health and account overview when page loads
document.addEventListener('DOMContentLoaded', function() {
updateHealth();
if (typeof loadAccountOverview === 'function') loadAccountOverview();
});

// Update health after manual refresh (manualRefresh in head does full reload; health updates on DOMContentLoaded)


// ============================================================
// SCHEDULE & COSTS MANAGEMENT
// ============================================================

var currentSchedule = 'off';

// Load schedule status on page load
function loadScheduleStatus() {
fetch('/api/schedule/status')
    .then(function(response) { return response.json(); })
    .then(function(data) {
    // Update dropdown
    document.getElementById('schedule-dropdown').value = data.interval;
    currentSchedule = data.interval;
    
    // Update status indicator
    var indicator = document.getElementById('status-indicator');
    var statusText = document.getElementById('status-text');
    
    if (data.is_running) {
        indicator.className = 'status-indicator running';
        if (statusText) statusText.textContent = 'Running';
    } else {
        indicator.className = 'status-indicator stopped';
        if (statusText) statusText.textContent = 'Stopped';
    }
    
    // Update counters
    document.getElementById('count-today').textContent = data.refresh_count_today;
    document.getElementById('count-month').textContent = data.refresh_count_month;
    document.getElementById('last-refresh').textContent = 
        data.last_refresh ? new Date(data.last_refresh).toLocaleTimeString() : 'Never';
    
    // Update next refresh
    var nextEl = document.getElementById('next-refresh');
    if (nextEl) {
        if (data.next_refresh_in_seconds) {
            var minutes = Math.floor(data.next_refresh_in_seconds / 60);
            nextEl.textContent = 'Next in ' + minutes + ' min';
        } else {
            nextEl.textContent = '';
        }
    }
    var countMonth = document.getElementById('count-month');
    if (countMonth) countMonth.textContent = data.refresh_count_month;
    // Status email (included in status response) — populate Alerts tab row
    if (data.status_email) loadAlertsStatusEmailFromData(data.status_email);
    })
    .catch(function(e) {
        console.error('Error loading schedule status:', e);
    });
loadScheduleLearningStatus();
fetch('/api/schedule/status-email').then(function(r) { return r.json(); }).then(function(d) { if (!d.error) loadAlertsStatusEmailFromData(d); }).catch(function() {});
}

// Cost breakdown removed (AI costs not applicable); loadCosts no longer used

// Load monthly ML learning status
function loadScheduleLearningStatus() {
fetch('/api/schedule/learning')
    .then(function(response) { return response.json(); })
    .then(function(data) {
        if (data.error) {
            document.getElementById('schedule-learning-last-run').textContent = 'Error';
            return;
        }
        document.getElementById('schedule-learning-enabled').checked = !!data.enabled;
        var daySelect = document.getElementById('schedule-learning-day');
        var day = Math.max(1, Math.min(28, parseInt(data.day_of_month, 10) || 1));
        if (daySelect) {
            // Populate 1-28 if not already
            if (daySelect.options.length <= 1) {
                daySelect.innerHTML = '';
                for (var d = 1; d <= 28; d++) {
                    var opt = document.createElement('option');
                    opt.value = String(d);
                    opt.textContent = String(d);
                    daySelect.appendChild(opt);
                }
            }
            daySelect.value = String(day);
        }
        document.getElementById('schedule-learning-last-run').textContent =
            data.last_learning_run ? new Date(data.last_learning_run).toLocaleString() : 'Never';
    })
    .catch(function(e) {
        console.error('Error loading schedule learning:', e);
        document.getElementById('schedule-learning-last-run').textContent = '—';
    });
}

// Save monthly ML learning settings
function saveMonthlyLearning() {
var enabled = document.getElementById('schedule-learning-enabled').checked;
var day = Math.max(1, Math.min(28, parseInt(document.getElementById('schedule-learning-day').value, 10) || 1));
var btn = document.getElementById('schedule-learning-save-btn');
btn.disabled = true;
btn.textContent = '⏳ Saving...';
fetch('/api/schedule/learning', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ enabled: enabled, day_of_month: day })
})
    .then(function(response) { return response.json(); })
    .then(function(data) {
        btn.disabled = false;
        btn.textContent = '💾 Save';
        if (data.error) {
            alert('Failed to save: ' + data.error);
            return;
        }
        document.getElementById('schedule-learning-last-run').textContent =
            data.last_learning_run ? new Date(data.last_learning_run).toLocaleString() : 'Never';
    })
    .catch(function(e) {
        btn.disabled = false;
        btn.textContent = '💾 Save';
        console.error('Error saving schedule learning:', e);
    });
}

// Status email presets: value -> hours_et (ET)
var STATUS_EMAIL_PRESETS = { '3x': [9, 13, 17], '2x': [9, 17], '6h': [0, 6, 12, 18], '1x': [9] };

function loadScheduleStatusEmailFromData(data) {
var enabled = document.getElementById('schedule-status-email-enabled');
var preset = document.getElementById('schedule-status-email-preset');
var customRow = document.getElementById('schedule-status-email-custom-row');
var hoursInput = document.getElementById('schedule-status-email-hours');
if (!enabled || !preset) return;
enabled.checked = !!data.enabled;
var hours = (data.hours_et || []).slice().sort(function(a,b){ return a - b; });
var presetKey = 'custom';
for (var k in STATUS_EMAIL_PRESETS) {
    var p = STATUS_EMAIL_PRESETS[k];
    if (p.length === hours.length && p.every(function(h, i) { return h === hours[i]; })) {
        presetKey = k;
        break;
    }
}
preset.value = presetKey;
hoursInput.value = hours.join(', ');
customRow.style.display = presetKey === 'custom' ? 'block' : 'none';
}

function onStatusEmailPresetChange() {
var preset = document.getElementById('schedule-status-email-preset');
var customRow = document.getElementById('schedule-status-email-custom-row');
customRow.style.display = preset.value === 'custom' ? 'block' : 'none';
}

function saveScheduleStatusEmail() {
var enabled = document.getElementById('schedule-status-email-enabled').checked;
var preset = document.getElementById('schedule-status-email-preset').value;
var hoursInput = document.getElementById('schedule-status-email-hours').value;
var hours_et = preset === 'custom'
    ? hoursInput.replace(/,/g, ' ').split(/\s+/).filter(function(s) { return s.trim(); }).map(function(s) { return Math.max(0, Math.min(23, parseInt(s, 10) || 0)); })
    : (STATUS_EMAIL_PRESETS[preset] || [9, 13, 17]);
var btn = document.getElementById('schedule-status-email-save-btn');
btn.disabled = true;
btn.textContent = '⏳ Saving...';
fetch('/api/schedule/status-email', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ enabled: enabled, hours_et: hours_et })
})
    .then(function(response) { return response.json(); })
    .then(function(data) {
        btn.disabled = false;
        btn.textContent = '💾 Save';
        if (data.error) { alert('Failed to save: ' + data.error); return; }
        loadScheduleStatusEmailFromData(data);
    })
    .catch(function(e) {
        btn.disabled = false;
        btn.textContent = '💾 Save';
        console.error('Error saving status email:', e);
    });
}

// Alerts tab: daily status email (same API as schedule)
function loadAlertsStatusEmailFromData(data) {
var enabled = document.getElementById('alerts-status-email-enabled');
var preset = document.getElementById('alerts-status-email-preset');
var customRow = document.getElementById('alerts-status-email-custom-row');
var hoursInput = document.getElementById('alerts-status-email-hours');
if (!enabled || !preset) return;
enabled.checked = !!data.enabled;
var hours = (data.hours_et || []).slice().sort(function(a,b){ return a - b; });
var presetKey = 'custom';
for (var k in STATUS_EMAIL_PRESETS) {
    var p = STATUS_EMAIL_PRESETS[k];
    if (p.length === hours.length && p.every(function(h, i) { return h === hours[i]; })) {
        presetKey = k;
        break;
    }
}
preset.value = presetKey;
if (hoursInput) hoursInput.value = hours.join(', ');
if (customRow) customRow.style.display = presetKey === 'custom' ? 'block' : 'none';
}

function onAlertsStatusEmailPresetChange() {
var preset = document.getElementById('alerts-status-email-preset');
var customRow = document.getElementById('alerts-status-email-custom-row');
if (customRow) customRow.style.display = preset && preset.value === 'custom' ? 'block' : 'none';
}

function saveAlertsStatusEmail() {
var enabled = document.getElementById('alerts-status-email-enabled').checked;
var presetEl = document.getElementById('alerts-status-email-preset');
var preset = presetEl ? presetEl.value : '3x';
var hoursInput = document.getElementById('alerts-status-email-hours');
var hoursInputVal = hoursInput ? hoursInput.value : '';
var hours_et = preset === 'custom'
    ? hoursInputVal.replace(/,/g, ' ').split(/\s+/).filter(function(s) { return s.trim(); }).map(function(s) { return Math.max(0, Math.min(23, parseInt(s, 10) || 0)); })
    : (STATUS_EMAIL_PRESETS[preset] || [9, 13, 17]);
var btn = document.getElementById('alerts-status-email-save-btn');
if (btn) { btn.disabled = true; btn.textContent = '⏳ Saving...'; }
fetch('/api/schedule/status-email', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ enabled: enabled, hours_et: hours_et })
})
    .then(function(response) { return response.json(); })
    .then(function(data) {
        if (btn) { btn.disabled = false; btn.textContent = '💾 Save'; }
        if (data.error) { alert('Failed to save: ' + data.error); return; }
        loadAlertsStatusEmailFromData(data);
    })
    .catch(function(e) {
        if (btn) { btn.disabled = false; btn.textContent = '💾 Save'; }
        console.error('Error saving status email:', e);
    });
}

// Save schedule
function saveSchedule() {
var dropdown = document.getElementById('schedule-dropdown');
var btn = document.getElementById('schedule-save-btn');
var newInterval = dropdown.value;

btn.disabled = true;
btn.textContent = '⏳ Saving...';

fetch('/api/schedule/set', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({interval: newInterval})
})
    .then(function(response) { return response.json(); })
    .then(function(data) {
    
    if (data.success) {
        btn.textContent = '✅ Saved!';
        currentSchedule = newInterval;
        
        // Reload status
        setTimeout(function() {
            loadScheduleStatus();
            btn.textContent = '💾 Save';
            btn.disabled = false;
        }, 1000);
    } else {
        btn.textContent = '\u274C Error';
        setTimeout(function() {
            btn.textContent = '\uD83D\uDCBE Save';
            btn.disabled = false;
        }, 2000);
    }
    })
    .catch(function(e) {
        console.error('Error saving schedule:', e);
        btn.textContent = '\u274C Error';
        setTimeout(function() {
            btn.textContent = '\uD83D\uDCBE Save';
            btn.disabled = false;
        }, 2000);
    });
}

// Update schedule/refresh status every 30s when Alerts/Refreshes tab is active
setInterval(function() {
var alertsTab = document.getElementById('tab-alerts');
if (alertsTab && alertsTab.classList.contains('active')) {
    loadScheduleStatus();
}
}, 30000);

// Load data when tab is first opened
document.addEventListener('DOMContentLoaded', function() {
var originalSwitchTab = window.switchTab;
window.switchTab = function(tabName) {
    originalSwitchTab(tabName);
    if (tabName === 'alerts') {
        loadAlertsTab();
        loadScheduleStatus();
    }
};
});

// ============================================================
// ALERTS TAB
// ============================================================

function loadAlertsTab() {
fetch('/api/alerts/settings')
    .then(function(res) { return res.json(); })
    .then(function(d) {
    if (d.error) return;
    document.getElementById('alerts-email-enabled').checked = !!d.email_enabled;
    document.getElementById('alerts-telegram-enabled').checked = !!d.telegram_enabled;
    document.getElementById('alerts-email-status').textContent = d.email_configured ? 'Configured' : 'Set SMTP in .env + add emails below';
    document.getElementById('alerts-telegram-status').textContent = d.telegram_configured ? 'Configured' : 'Set TELEGRAM_BOT_TOKEN + CHAT_ID in .env';
    var types = d.alert_type_enabled || {};
    var typeEmail = d.alert_type_email || {};
    var typeTelegram = d.alert_type_telegram || {};
    ['ai_signal', 'technical_signal', 'stop_loss', 'take_profit', 'trailing_stop', 'insufficient_cash', 'low_cash', 'drift', 'stress_report', 'sync_balance_failure'].forEach(function(id) {
        var el = document.getElementById('alerts-type-' + id);
        if (el) el.checked = types[id] !== false;
        var emailEl = document.getElementById('alerts-type-' + id + '-email');
        if (emailEl) emailEl.checked = typeEmail[id] !== false;
        var tgEl = document.getElementById('alerts-type-' + id + '-telegram');
        if (tgEl) tgEl.checked = typeTelegram[id] !== false;
    });
    var list = document.getElementById('alerts-email-list');
    var recs = d.email_recipients || [];
    list.innerHTML = recs.length ? recs.map(function(email) {
        var safe = escapeHtml(email);
        var attr = safe.replace(/"/g, '&quot;');
        return '<li><span>' + safe + '</span><button type="button" data-email="' + attr + '" onclick="removeAlertEmail(this.getAttribute(\'data-email\'))">Remove</button></li>';
    }).join('') : '<li class="alerts-no-emails">No addresses added. Add one above or use ALERT_EMAIL_TO in .env.</li>';
    })
    .catch(function(e) { console.error('Load alerts failed:', e); });
fetch('/api/schedule/status-email')
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (!data.error) loadAlertsStatusEmailFromData(data);
    })
    .catch(function(e) { console.error('Load status email failed:', e); });
}

function escapeHtml(s) {
var div = document.createElement('div');
div.textContent = s;
return div.innerHTML;
}

function saveAlertsChannels() {
var payload = {
    email_enabled: document.getElementById('alerts-email-enabled').checked,
    telegram_enabled: document.getElementById('alerts-telegram-enabled').checked
};
fetch('/api/alerts/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    .then(function(r) { return r.json(); }).then(function() {}).catch(function(e) { console.error(e); });
}

function saveAlertsTypes() {
var ids = ['ai_signal', 'technical_signal', 'stop_loss', 'take_profit', 'trailing_stop', 'insufficient_cash', 'low_cash', 'drift', 'stress_report', 'sync_balance_failure'];
var payload = {
    alert_type_enabled: {},
    alert_type_email: {},
    alert_type_telegram: {}
};
ids.forEach(function(id) {
    var el = document.getElementById('alerts-type-' + id);
    payload.alert_type_enabled[id] = el ? el.checked : true;
    var emailEl = document.getElementById('alerts-type-' + id + '-email');
    payload.alert_type_email[id] = emailEl ? emailEl.checked : true;
    var tgEl = document.getElementById('alerts-type-' + id + '-telegram');
    payload.alert_type_telegram[id] = tgEl ? tgEl.checked : true;
});
fetch('/api/alerts/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    .then(function(r) { return r.json(); }).then(function() {}).catch(function(e) { console.error(e); });
}

function addAlertEmail() {
var input = document.getElementById('alerts-email-input');
var email = (input.value || '').trim();
if (!email || !email.includes('@')) {
    alert('Enter a valid email address.');
    return;
}
fetch('/api/alerts/email/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: email }) })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.error) {
            alert('Error: ' + data.error);
            return;
        }
        input.value = '';
        loadAlertsTab();
    })
    .catch(function(e) {
        alert('Failed: ' + e.message);
    });
}

function removeAlertEmail(email) {
if (!email) return;
if (!confirm('Remove ' + email + '?')) return;
fetch('/api/alerts/email/remove', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: email }) })
    .then(function(r) { return r.json(); })
    .then(function(data) {
    if (data.error) {
        alert('Error: ' + data.error);
        return;
    }
    loadAlertsTab();
    })
    .catch(function(e) {
        alert('Failed: ' + e.message);
    });
}

function sendTestAlert() {
var resultEl = document.getElementById('alerts-test-result');
resultEl.textContent = 'Sending...';
fetch('/api/alerts/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.status === 'sent' && data.channels) {
            var parts = [];
            if (data.channels.email) parts.push('Email: ' + data.channels.email);
            if (data.channels.telegram) parts.push('Telegram: ' + data.channels.telegram);
            resultEl.textContent = parts.length ? parts.join('; ') : 'Sent';
        } else {
            resultEl.textContent = data.message || data.error || 'Check console';
        }
    })
    .catch(function(e) {
        resultEl.textContent = 'Failed: ' + e.message;
    });
setTimeout(function() { resultEl.textContent = ''; }, 8000);
}


// ============================================================
// OVERVIEW GROUP COLLAPSE/EXPAND
// ============================================================

function toggleOverviewGroup(groupName) {
  var group = document.getElementById('overview-group-' + groupName);
  if (!group) return;
  group.classList.toggle('collapsed');
  var collapsed = {};
  try { collapsed = JSON.parse(localStorage.getItem('overviewCollapsed') || '{}'); } catch(e) {}
  collapsed[groupName] = group.classList.contains('collapsed');
  localStorage.setItem('overviewCollapsed', JSON.stringify(collapsed));
}

function restoreOverviewGroups() {
  var collapsed = {};
  try { collapsed = JSON.parse(localStorage.getItem('overviewCollapsed') || '{}'); } catch(e) {}
  Object.keys(collapsed).forEach(function(name) {
    if (collapsed[name]) {
      var group = document.getElementById('overview-group-' + name);
      if (group) group.classList.add('collapsed');
    }
  });
}

// ============================================================
// CHART.JS — Equity Curve + P&L Bar + Risk Budget Bullet
// ============================================================

var _equityCurveChart = null;
var _pnlBarChart = null;
var _riskBulletChart = null;

var CHART_COLORS = {
  cyan: '#00c8e8',
  cyanFill: 'rgba(0, 200, 232, 0.1)',
  green: '#00e87a',
  red: '#ff3a6e',
  amber: '#ffb800',
  gridLine: '#1e3048',
  tickLabel: '#3d5a78',
  axisLabel: '#7a9ab8'
};

function renderEquityCurve(labels, values) {
  var ctx = document.getElementById('equity-curve-chart');
  if (!ctx) return;
  if (_equityCurveChart) _equityCurveChart.destroy();

  // Determine line color: green if final value >= 0, red otherwise
  var finalVal = values.length ? values[values.length - 1] : 0;
  var lineColor = finalVal >= 0 ? CHART_COLORS.green : CHART_COLORS.red;
  var fillColor = finalVal >= 0 ? 'rgba(0, 232, 122, 0.08)' : 'rgba(255, 58, 110, 0.08)';

  _equityCurveChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        label: 'Cumulative P&L ($)',
        data: values,
        borderColor: lineColor,
        backgroundColor: fillColor,
        fill: true,
        tension: 0.3,
        pointRadius: values.length > 30 ? 0 : 3,
        pointHoverRadius: 5,
        borderWidth: 2
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#0d1520',
          borderColor: '#1e3048',
          borderWidth: 1,
          titleColor: '#e8f0f8',
          bodyColor: '#c5daf0',
          callbacks: {
            label: function(ctx) { return '$' + ctx.parsed.y.toFixed(2); }
          }
        }
      },
      scales: {
        x: {
          ticks: { color: CHART_COLORS.tickLabel, maxTicksLimit: 10, font: { size: 10 } },
          grid: { color: CHART_COLORS.gridLine, lineWidth: 0.5 }
        },
        y: {
          ticks: {
            color: CHART_COLORS.axisLabel,
            font: { size: 10 },
            callback: function(v) { return '$' + v.toFixed(0); }
          },
          grid: { color: CHART_COLORS.gridLine, lineWidth: 0.5 }
        }
      }
    }
  });
}

function renderPnlBar(labels, pnlValues) {
  var ctx = document.getElementById('pnl-bar-chart');
  if (!ctx) return;
  if (_pnlBarChart) _pnlBarChart.destroy();

  var colors = pnlValues.map(function(v) { return v >= 0 ? CHART_COLORS.green : CHART_COLORS.red; });

  _pnlBarChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: 'P&L ($)',
        data: pnlValues,
        backgroundColor: colors,
        borderRadius: 2
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#0d1520',
          borderColor: '#1e3048',
          borderWidth: 1,
          titleColor: '#e8f0f8',
          bodyColor: '#c5daf0',
          callbacks: {
            label: function(ctx) {
              var v = ctx.parsed.y;
              return (v >= 0 ? '+$' : '-$') + Math.abs(v).toFixed(2);
            }
          }
        }
      },
      scales: {
        x: {
          ticks: { color: CHART_COLORS.tickLabel, maxTicksLimit: 15, font: { size: 9 }, maxRotation: 45 },
          grid: { display: false }
        },
        y: {
          ticks: {
            color: CHART_COLORS.axisLabel,
            font: { size: 10 },
            callback: function(v) { return '$' + v.toFixed(0); }
          },
          grid: { color: CHART_COLORS.gridLine, lineWidth: 0.5 }
        }
      }
    }
  });
}

function renderRiskBullet(usedPct, riskCap, currentExposure) {
  var ctx = document.getElementById('risk-budget-bullet');
  if (!ctx) return;
  if (_riskBulletChart) _riskBulletChart.destroy();

  var barColor = usedPct >= 100 ? CHART_COLORS.red : usedPct >= 80 ? CHART_COLORS.amber : CHART_COLORS.green;
  var remaining = Math.max(0, riskCap - currentExposure);

  _riskBulletChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: [''],
      datasets: [
        { label: 'Used', data: [currentExposure], backgroundColor: barColor, barPercentage: 0.5 },
        { label: 'Remaining', data: [remaining], backgroundColor: 'rgba(30,48,72,0.4)', barPercentage: 0.5 }
      ]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { stacked: true, display: false, max: Math.max(riskCap, currentExposure) },
        y: { stacked: true, display: false }
      }
    }
  });
}

function loadPerformanceCharts() {
  fetch('/api/journal/positions?_=' + Date.now(), { cache: 'no-store' })
    .then(function(r) { return r.ok ? r.json() : null; })
    .then(function(data) {
      if (!data || !data.positions) return;
      var closed = data.positions
        .filter(function(p) { return p.status === 'CLOSED' && p.realized_pnl_usd != null && p.exit_time; })
        .sort(function(a, b) { return new Date(a.exit_time) - new Date(b.exit_time); });
      if (!closed.length) return;

      // Equity curve: cumulative P&L
      var cumulative = 0;
      var eqLabels = [];
      var eqValues = [];
      closed.forEach(function(p) {
        cumulative += Number(p.realized_pnl_usd);
        eqLabels.push(new Date(p.exit_time).toLocaleDateString());
        eqValues.push(Math.round(cumulative * 100) / 100);
      });
      renderEquityCurve(eqLabels, eqValues);

      // P&L per trade bar chart
      var barLabels = [];
      var barValues = [];
      closed.forEach(function(p) {
        var sym = (p.symbol || p.coin_id || '').toUpperCase().replace(/-.*/, '');
        barLabels.push(sym);
        barValues.push(Math.round(Number(p.realized_pnl_usd) * 100) / 100);
      });
      renderPnlBar(barLabels, barValues);
    })
    .catch(function(err) {
      console.debug('[charts] Failed to load performance charts:', err);
    });
}
