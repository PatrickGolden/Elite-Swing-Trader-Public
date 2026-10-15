# =============================================================================
# alert_system.py — Trading Alert System
# Sends email and Telegram notifications for AI signals and buy/sell alerts
# =============================================================================

import os
import json
import logging
import smtplib
import threading
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

# =============================================================================
# Configuration
# =============================================================================

# Email settings (SMTP)
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USERNAME = os.getenv('SMTP_USERNAME', '')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
ALERT_EMAIL_FROM = os.getenv('ALERT_EMAIL_FROM', SMTP_USERNAME)
ALERT_EMAIL_TO_RAW = os.getenv('ALERT_EMAIL_TO', '')


def _parse_email_to(raw: str):
    """Parse ALERT_EMAIL_TO: comma-separated list, or JSON array, or single address."""
    if not raw or not raw.strip():
        return []
    s = raw.strip()
    if s.startswith('['):
        try:
            return [x.strip() for x in json.loads(s) if isinstance(x, str) and x.strip()]
        except (json.JSONDecodeError, TypeError):
            pass
    return [x.strip() for x in s.split(',') if x.strip()]


# Resolved list for sending; To header uses comma-joined string
ALERT_EMAIL_TO_LIST = _parse_email_to(ALERT_EMAIL_TO_RAW)
ALERT_EMAIL_TO = ', '.join(ALERT_EMAIL_TO_LIST) if ALERT_EMAIL_TO_LIST else ALERT_EMAIL_TO_RAW

# Primary owner (critical alerts and daily reports go ONLY to this address + Telegram)
# Falls back to the first ALERT_EMAIL_TO address so no personal address is hardcoded.
ALERT_PRIMARY_EMAIL_RAW = os.getenv('ALERT_PRIMARY_EMAIL', '').strip()
ALERT_PRIMARY_EMAIL = ALERT_PRIMARY_EMAIL_RAW or (ALERT_EMAIL_TO_LIST[0] if ALERT_EMAIL_TO_LIST else '')

# Telegram settings
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

# User settings file (can be replaced with database later)
USER_SETTINGS_FILE = Path('user_alert_settings.json')

# Alert tracking (prevents duplicates)
ALERT_TRACKING_FILE = Path('alert_history.json')

# Critical-alert short-term dedupe: same subject not sent twice within cooldown window
_CRITICAL_ALERT_COOLDOWN_MINUTES = 2
_critical_alert_recent = {}  # key -> datetime (UTC)
_critical_alert_lock = threading.Lock()
_CRITICAL_ALERT_DEDUP_FILE = Path('alert_dedup_cache.json')


def _load_dedup_cache():
    """Load dedup cache from disk. Called once at module import."""
    global _critical_alert_recent
    try:
        if _CRITICAL_ALERT_DEDUP_FILE.exists():
            raw = json.loads(_CRITICAL_ALERT_DEDUP_FILE.read_text(encoding='utf-8'))
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(minutes=_CRITICAL_ALERT_COOLDOWN_MINUTES)
            for k, ts_str in raw.items():
                ts = datetime.fromisoformat(ts_str)
                if ts >= cutoff:
                    _critical_alert_recent[k] = ts
    except Exception:
        _critical_alert_recent = {}


def _save_dedup_cache():
    """Persist dedup cache to disk. Called under _critical_alert_lock."""
    try:
        serialized = {k: v.isoformat() for k, v in _critical_alert_recent.items()}
        _CRITICAL_ALERT_DEDUP_FILE.write_text(
            json.dumps(serialized, indent=2), encoding='utf-8'
        )
    except Exception:
        pass


_load_dedup_cache()
# Lock for alert_history file (system alert cooldown) — prevents race when multiple threads trigger same alert.
# RLock allows same thread to re-enter (e.g. send_sync_balance_failure_alert holds lock, calls _save_alert_history).
_alert_history_lock = threading.RLock()

# Legal disclaimer appended to all alert emails and Telegram messages
LEGAL_DISCLAIMER_PLAIN = """

───────────────────────────────────────────────
DISCLAIMER
───────────────────────────────────────────────
This communication is for educational and informational purposes only. It does not constitute financial, investment, or trading advice. I am not responsible for any actions taken by recipients based on this message or for any losses incurred. Trading involves substantial risk of loss. Past performance is not indicative of future results. You are solely responsible for your own trading decisions.
───────────────────────────────────────────────
"""

LEGAL_DISCLAIMER_HTML = """
<div style="margin-top: 24px; padding: 16px; border-top: 1px solid #e2e8f0; color: #a0aec0; font-size: 11px; line-height: 1.5;">
<strong>DISCLAIMER:</strong> This communication is for educational and informational purposes only. It does not constitute financial, investment, or trading advice. I am not responsible for any actions taken by recipients based on this message or for any losses incurred. Trading involves substantial risk of loss. Past performance is not indicative of future results. You are solely responsible for your own trading decisions.
</div>
"""

# Badge display labels (mirrors frontend badgeLabels)
BADGE_LABELS = {
    'blood_in_the_street': 'Blood in Street',
    'dip': 'Dip',
    'pullback_in_trend': 'Pullback in Trend',
    'volatility_harvest': 'Vol Harvest',
    'rally': 'Rally',
    'pump_cap': 'Pump Cap',
    'capitulation': 'Capitulation',
    'bull_participant': 'Bull Participant',
    'momentum_continuation': 'Momentum Cont.',
    'momentum_breakout': 'Momentum Breakout',
    'early_momentum': 'Early Momentum',
    'vol_contraction': 'Vol Contraction',
    'light_sniper_entry': 'Sniper',
    'intra_swing_sniper': 'Sniper Entry',
    'light_whale_accum': 'Whale Accum',
    'narrative_spike': 'Narrative',
    'dynamic_prob': 'Dynamic Prob',
    'path_killed': 'Path Killed',
    'portfolio_gate': 'Portfolio Gate',
}

# Badge colors for HTML emails (background, text, border)
BADGE_COLORS = {
    'blood_in_the_street': ('#fde8e8', '#c85050', '#e8a0a0'),
    'dip':                 ('#fef3c7', '#d97706', '#fcd34d'),
    'pullback_in_trend':   ('#dbeafe', '#2563eb', '#93c5fd'),
    'volatility_harvest':  ('#ede9fe', '#7c3aed', '#c4b5fd'),
    'rally':               ('#d1fae5', '#059669', '#6ee7b7'),
    'pump_cap':            ('#fee2e2', '#dc2626', '#fca5a5'),
    'capitulation':        ('#fde8e8', '#991b1b', '#e8a0a0'),
    'bull_participant':    ('#d1fae5', '#059669', '#6ee7b7'),
    'momentum_continuation': ('#dbeafe', '#0284c7', '#7dd3fc'),
    'momentum_breakout':   ('#d1fae5', '#16a34a', '#86efac'),
    'early_momentum':      ('#dbeafe', '#2563eb', '#93c5fd'),
    'vol_contraction':     ('#f3f4f6', '#6b7280', '#d1d5db'),
    'light_sniper_entry':  ('#d1fae5', '#059669', '#6ee7b7'),
    'intra_swing_sniper':  ('#d1fae5', '#059669', '#6ee7b7'),
    'light_whale_accum':   ('#ede9fe', '#7c3aed', '#c4b5fd'),
    'narrative_spike':     ('#fef3c7', '#d97706', '#fcd34d'),
    'dynamic_prob':        ('#f3f4f6', '#6b7280', '#d1d5db'),
    'path_killed':         ('#fee2e2', '#dc2626', '#fca5a5'),
    'portfolio_gate':      ('#fee2e2', '#dc2626', '#fca5a5'),
}

REGIME_COLORS = {
    'BULL': '#16a34a',
    'BEAR': '#dc2626',
    'VOLATILE': '#d97706',
    'SIDEWAYS': '#6b7280',
}

def _fg_label(value):
    """Return Fear & Greed sentiment label for a numeric value."""
    if value is None:
        return ''
    v = float(value)
    if v <= 25:
        return 'Extreme Fear'
    if v <= 45:
        return 'Fear'
    if v <= 55:
        return 'Neutral'
    if v <= 75:
        return 'Greed'
    return 'Extreme Greed'

def _format_badges_plain(badges):
    """Format badge list for plain-text emails."""
    if not badges:
        return ''
    labels = [BADGE_LABELS.get(b, b.replace('_', ' ').title()) for b in badges]
    return ', '.join(labels)

def _format_badges_html(badges):
    """Format badge list as inline HTML pills."""
    if not badges:
        return ''
    pills = []
    for b in badges:
        label = BADGE_LABELS.get(b, b.replace('_', ' ').title())
        bg, text, border = BADGE_COLORS.get(b, ('#f3f4f6', '#6b7280', '#d1d5db'))
        pills.append(
            f'<span style="display:inline-block;padding:2px 8px;margin:2px;border-radius:12px;'
            f'font-size:11px;font-weight:600;background:{bg};color:{text};border:1px solid {border};">'
            f'{label}</span>'
        )
    return ' '.join(pills)

def _regime_pill_html(regime):
    """Render a color-coded regime pill for HTML emails."""
    if not regime:
        return ''
    color = REGIME_COLORS.get(regime.upper(), '#6b7280')
    return (
        f'<span style="display:inline-block;padding:2px 10px;border-radius:12px;'
        f'font-size:12px;font-weight:700;color:#fff;background:{color};">'
        f'{regime.upper()}</span>'
    )

def _fmt_pct(val, plus=True):
    """Format a percentage value with optional + sign."""
    if val is None:
        return 'N/A'
    v = float(val)
    sign = '+' if plus and v > 0 else ''
    return f'{sign}{v:.1f}%'


# =============================================================================
# Alert System Class
# =============================================================================

class AlertSystem:
    """
    Manages trading alerts across multiple channels (Email, Telegram).
    
    Features:
    - AI recommendation alerts (priority)
    - Technical buy/sell signal alerts
    - User preferences (toggles per channel)
    - Duplicate prevention with rate limiting
    - Graceful error handling
    - Simple logging
    """
    
    # Alert type keys used in settings['alert_type_enabled']
    ALERT_TYPES = [
        'ai_signal', 'technical_signal',
        'stop_loss', 'take_profit', 'trailing_stop',  # Exit monitoring
        'insufficient_cash', 'low_cash', 'drift',
        'sync_balance_failure',  # Auto-sync from exchange failed
        'stress_report',  # Monthly buy-signal stress report (30d/60d summary)
        'buy_filled',  # System booked a buy (TWAP fill or manual book)
    ]

    def __init__(self):
        self.settings = self._load_settings()
        self.alert_history = self._load_alert_history()
        
        # Validate configuration (email: SMTP + at least one recipient from settings or env)
        self._email_recipients_list = self._get_email_recipients_list()
        self.email_configured = bool(SMTP_USERNAME and SMTP_PASSWORD and self._email_recipients_list)
        self.telegram_configured = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
        
        # Log configuration status
        self._log_config_status()
    
    def _log_config_status(self):
        """Log alert system configuration status."""
        logger.info("Alert System Configuration:")
        
        if self.email_configured:
            logger.info(f"   Email: Configured ({ALERT_EMAIL_TO})")
        else:
            logger.warning("   Email: Not configured (missing SMTP credentials)")
        
        if self.telegram_configured:
            logger.info(f"   Telegram: Configured (Chat ID: {TELEGRAM_CHAT_ID})")
        else:
            logger.warning("   Telegram: Not configured (missing bot token/chat ID)")
        
        if self.settings.get('email_globally_disabled', False):
            logger.warning("   Email alerts: GLOBALLY DISABLED (master kill-switch ON)")
        else:
            logger.info(f"   Email alerts: {'ON' if self.settings['email_enabled'] else 'OFF'}")
        logger.info(f"   Telegram alerts: {'ON' if self.settings['telegram_enabled'] else 'OFF'}")
    
    
    # =========================================================================
    # Settings Management
    # =========================================================================
    
    def _load_settings(self):
        """Load user alert preferences from file."""
        type_defaults = {
            'ai_signal': True, 'technical_signal': True,
            'stop_loss': True, 'take_profit': True, 'trailing_stop': True,
            'insufficient_cash': True, 'low_cash': True, 'drift': True,
            'sync_balance_failure': True,
            'buy_filled': True,
        }
        defaults = {
            'email_enabled': False,
            'telegram_enabled': False,
            # Master kill-switch: when True, NO email is sent on any path
            # (including daily status / optimizer / stage1b arm reports that
            # normally bypass email_enabled). Reversible via enable_all_email().
            'email_globally_disabled': False,
            'alert_type_enabled': dict(type_defaults),
            'alert_type_email': dict(type_defaults),
            'alert_type_telegram': dict(type_defaults),
            'email_recipients': [],  # from UI; if empty, fall back to ALERT_EMAIL_TO from env
        }
        import safe_json
        data = safe_json.load(str(USER_SETTINGS_FILE))
        if data:
            try:
                for k, v in defaults.items():
                    if k not in data:
                        data[k] = v
                if 'alert_type_enabled' in data and isinstance(data['alert_type_enabled'], dict):
                    for t in self.ALERT_TYPES:
                        if t not in data['alert_type_enabled']:
                            data['alert_type_enabled'][t] = True
                for key in ('alert_type_email', 'alert_type_telegram'):
                    if key in data and isinstance(data[key], dict):
                        for t in self.ALERT_TYPES:
                            if t not in data[key]:
                                data[key][t] = True
                    else:
                        data[key] = dict(type_defaults)
                if 'email_recipients' not in data or not isinstance(data['email_recipients'], list):
                    data['email_recipients'] = list(defaults['email_recipients'])
                return data
            except Exception as e:
                logger.warning(f"Error loading alert settings: {e}")
        return defaults

    def _get_email_recipients_list(self):
        """Return list of email addresses to use (from settings or env)."""
        recs = self.settings.get('email_recipients') or []
        recs = [str(e).strip() for e in recs if e and str(e).strip()]
        if recs:
            return recs
        return list(ALERT_EMAIL_TO_LIST)
    
    def _save_settings(self):
        """Save user alert preferences to file."""
        import safe_json
        safe_json.save(str(USER_SETTINGS_FILE), self.settings)
    
    def enable_email(self):
        """Enable email alerts."""
        if not self.email_configured:
            logger.error("Cannot enable email: SMTP not configured in .env")
            return False
        
        self.settings['email_enabled'] = True
        self._save_settings()
        logger.info("Email alerts enabled")
        return True
    
    def disable_email(self):
        """Disable email alerts."""
        self.settings['email_enabled'] = False
        self._save_settings()
        logger.info("Email alerts disabled")
        return True

    def disable_all_email(self):
        """Master kill-switch: suppress ALL email on every path (trade alerts,
        critical alerts, and the daily/optimizer/stage1b reports that otherwise
        bypass email_enabled). Reversible via enable_all_email()."""
        self.settings['email_globally_disabled'] = True
        self._save_settings()
        logger.info("ALL email alerts disabled (master kill-switch ON)")
        return True

    def enable_all_email(self):
        """Lift the master email kill-switch. Individual paths still respect
        email_enabled / per-type toggles as before."""
        self.settings['email_globally_disabled'] = False
        self._save_settings()
        logger.info("Master email kill-switch lifted (email_globally_disabled=False)")
        return True
    
    def enable_telegram(self):
        """Enable Telegram alerts."""
        if not self.telegram_configured:
            logger.error("Cannot enable Telegram: Bot not configured in .env")
            return False
        
        self.settings['telegram_enabled'] = True
        self._save_settings()
        logger.info("Telegram alerts enabled")
        return True
    
    def disable_telegram(self):
        """Disable Telegram alerts."""
        self.settings['telegram_enabled'] = False
        self._save_settings()
        logger.info("Telegram alerts disabled")
        return True
    
    def get_settings(self):
        """Get current alert settings."""
        return {
            'email_enabled': self.settings['email_enabled'],
            'email_globally_disabled': self.settings.get('email_globally_disabled', False),
            'telegram_enabled': self.settings['telegram_enabled'],
            'email_configured': self.email_configured,
            'telegram_configured': self.telegram_configured,
            'email_to': ALERT_EMAIL_TO if self.email_configured else None,
            'email_recipients': self._get_email_recipients_list(),
            'telegram_chat_id': TELEGRAM_CHAT_ID if self.telegram_configured else None,
            'alert_type_enabled': self.settings.get('alert_type_enabled', {t: True for t in self.ALERT_TYPES}),
            'alert_type_email': self.settings.get('alert_type_email', {t: True for t in self.ALERT_TYPES}),
            'alert_type_telegram': self.settings.get('alert_type_telegram', {t: True for t in self.ALERT_TYPES}),
        }

    def set_alert_type_enabled(self, alert_type: str, enabled: bool) -> bool:
        """Enable or disable a specific alert type. Returns True if saved."""
        if alert_type not in self.ALERT_TYPES:
            return False
        if 'alert_type_enabled' not in self.settings:
            self.settings['alert_type_enabled'] = {t: True for t in self.ALERT_TYPES}
        self.settings['alert_type_enabled'][alert_type] = bool(enabled)
        self._save_settings()
        return True

    def set_alert_types(self, enabled_map: dict, email_map: dict = None, telegram_map: dict = None) -> bool:
        """Set multiple alert types at once. enabled_map: { 'ai_signal': True, ... }. Optional email_map, telegram_map per type."""
        if 'alert_type_enabled' not in self.settings:
            self.settings['alert_type_enabled'] = {t: True for t in self.ALERT_TYPES}
        for k, v in enabled_map.items():
            if k in self.ALERT_TYPES:
                self.settings['alert_type_enabled'][k] = bool(v)
        if email_map is not None and isinstance(email_map, dict):
            if 'alert_type_email' not in self.settings:
                self.settings['alert_type_email'] = {t: True for t in self.ALERT_TYPES}
            for k, v in email_map.items():
                if k in self.ALERT_TYPES:
                    self.settings['alert_type_email'][k] = bool(v)
        if telegram_map is not None and isinstance(telegram_map, dict):
            if 'alert_type_telegram' not in self.settings:
                self.settings['alert_type_telegram'] = {t: True for t in self.ALERT_TYPES}
            for k, v in telegram_map.items():
                if k in self.ALERT_TYPES:
                    self.settings['alert_type_telegram'][k] = bool(v)
        self._save_settings()
        return True

    def set_email_recipients(self, emails: list) -> bool:
        """Set the list of email recipients (from UI). Persists and updates email_configured."""
        self.settings['email_recipients'] = [str(e).strip() for e in (emails or []) if e and str(e).strip()]
        self._save_settings()
        self._email_recipients_list = self._get_email_recipients_list()
        self.email_configured = bool(SMTP_USERNAME and SMTP_PASSWORD and self._email_recipients_list)
        return True

    def add_email_recipient(self, email: str) -> bool:
        """Add one email address. Returns True if added and saved."""
        email = (email or '').strip()
        if not email or '@' not in email:
            return False
        recs = self._get_email_recipients_list()
        if email.lower() in [e.lower() for e in recs]:
            return True
        recs.append(email)
        return self.set_email_recipients(recs)

    def remove_email_recipient(self, email: str) -> bool:
        """Remove one email address. Returns True if removed and saved."""
        email = (email or '').strip()
        recs = self._get_email_recipients_list()
        recs = [e for e in recs if e.lower() != email.lower()]
        return self.set_email_recipients(recs)
    
    
    # =========================================================================
    # Alert History (Duplicate Prevention & Rate Limiting)
    # =========================================================================
    
    def _load_alert_history(self):
        """Load alert history from file."""
        import safe_json
        return safe_json.load(str(ALERT_TRACKING_FILE), default=dict)

    def _save_alert_history(self):
        """Save alert history to file. Thread-safe."""
        import safe_json
        with _alert_history_lock:
            safe_json.save(str(ALERT_TRACKING_FILE), self.alert_history)
    
    def _alert_key(self, coin_id, alert_type, signal):
        """Generate unique key for alert tracking."""
        return f"{coin_id}_{alert_type}_{signal}"
    
    def _was_recently_alerted(self, coin_id, alert_type, signal, cooldown_minutes=60):
        """
        Check if this alert was sent recently (prevents duplicates).
        Rate limit: Max 1 alert per signal per hour.
        """
        key = self._alert_key(coin_id, alert_type, signal)
        with _alert_history_lock:
            if key not in self.alert_history:
                return False, 0
            try:
                last_sent = datetime.fromisoformat(self.alert_history[key])
                time_since = datetime.now() - last_sent
                if time_since < timedelta(minutes=cooldown_minutes):
                    return True, int((timedelta(minutes=cooldown_minutes) - time_since).total_seconds() / 60)
            except (ValueError, TypeError):
                pass
        return False, 0
    
    def _mark_alert_sent(self, coin_id, alert_type, signal):
        """Mark this alert as sent (with timestamp). Thread-safe."""
        key = self._alert_key(coin_id, alert_type, signal)
        with _alert_history_lock:
            self.alert_history[key] = datetime.now().isoformat()
            self._save_alert_history()

    def _was_recently_system_alert(self, key: str, cooldown_minutes: int = 1440) -> bool:
        """Check if a system alert (e.g. low_cash) was sent recently. Default 24h cooldown."""
        with _alert_history_lock:
            if key not in self.alert_history:
                return False
            try:
                last_sent = datetime.fromisoformat(self.alert_history[key])
                return (datetime.now() - last_sent) < timedelta(minutes=cooldown_minutes)
            except (ValueError, TypeError):
                return False

    def _mark_system_alert_sent(self, key: str):
        """Mark a system alert as sent."""
        with _alert_history_lock:
            self.alert_history[key] = datetime.now().isoformat()
            self._save_alert_history()
    
    
    # =========================================================================
    # Message Formatting
    # =========================================================================
    
    def _format_alert_message(self, coin_data, alert_type='AI'):
        """
        Format trade alert (screening recommendation) message content.

        Args:
            coin_data: Dictionary with coin info and AI recommendation
            alert_type: 'AI' (priority) or 'SIGNAL' (technical)

        Returns:
            (subject, body_text, body_html)
        """
        # Extract data
        symbol = coin_data.get('symbol', '').upper()
        name = coin_data.get('name', 'Unknown')
        signal = coin_data.get('signal', 'UNKNOWN')
        price = coin_data.get('current_price', 0)

        # AI recommendation data (if available)
        ai_rec = coin_data.get('ai_recommendation', {})
        consensus = ai_rec.get('consensus', {})
        quant = ai_rec.get('quant', {})
        consensus_signal = consensus.get('consensus_signal', signal)
        confidence = quant.get('quant_confidence', 0)

        # Dynamic targets
        stop_loss_pct = coin_data.get('stop_loss_pct', 0)
        take_profit_pct = coin_data.get('take_profit_pct', 0)
        stop_loss_price = coin_data.get('stop_loss_price', 0)
        take_profit_price = coin_data.get('take_profit_price', 0)

        # Position sizing
        position_sizing = ai_rec.get('position_sizing', {})
        risk_pct = position_sizing.get('pct_of_account', 0)
        position_size = position_sizing.get('position_size_usd', 0)
        rr_ratio = coin_data.get('risk_reward_ratio', 0)

        # Market context
        regime = consensus.get('market_regime') or quant.get('regime') or ''
        badges = quant.get('signal_badges') or []
        quant_path = quant.get('quant_path') or ''
        confirmation_score = quant.get('confirmation_score')
        edge_score = quant.get('edge_score')
        fear_greed = coin_data.get('fear_greed') or quant.get('fear_greed')
        indicators = coin_data.get('indicators') or {}
        enhanced = coin_data.get('enhanced') or {}
        ch24h = indicators.get('ch24h') or coin_data.get('price_change_percentage_24h')
        ch7d = coin_data.get('price_change_percentage_7d_in_currency')
        ch30d = coin_data.get('price_change_percentage_30d_in_currency')
        funding_raw = enhanced.get('funding_rate')
        funding_rate = funding_raw.get('current') if isinstance(funding_raw, dict) else funding_raw

        # Reasoning (max 2 lines)
        if alert_type == 'AI':
            reasoning = quant.get('reasoning') or consensus.get('reasoning', 'Quant analysis complete')
        else:
            reasoning = coin_data.get('hypothesis', 'Technical signal triggered')

        # Truncate to 2 lines (~160 chars)
        if len(reasoning) > 160:
            reasoning = reasoning[:157] + '...'

        # Timestamp
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

        # Subject line
        display_signal = consensus_signal if alert_type == 'AI' else signal
        subject = f"Quant Gate Alert - {symbol} - {display_signal}"

        # --- Plain text: Market Context section ---
        ctx_lines = []
        if regime:
            ctx_lines.append(f"🌐 Regime: {regime}")
        if fear_greed is not None:
            ctx_lines.append(f"😱 Fear & Greed: {int(float(fear_greed))} ({_fg_label(fear_greed)})")
        momentum_parts = []
        if ch24h is not None:
            momentum_parts.append(f"24h: {_fmt_pct(ch24h)}")
        if ch7d is not None:
            momentum_parts.append(f"7d: {_fmt_pct(ch7d)}")
        if ch30d is not None:
            momentum_parts.append(f"30d: {_fmt_pct(ch30d)}")
        if momentum_parts:
            ctx_lines.append(f"📈 {' | '.join(momentum_parts)}")
        if funding_rate is not None:
            ctx_lines.append(f"💲 Funding Rate: {float(funding_rate):.4f}%")
        if quant_path and quant_path != 'default':
            ctx_lines.append(f"🛤️ Path: {quant_path.replace('_', ' ').title()}")
        if edge_score is not None:
            ctx_lines.append(f"🎲 Edge Score: {float(edge_score):.2f}")
        if confirmation_score is not None:
            ctx_lines.append(f"✅ Confirmation: {int(float(confirmation_score))}/100")
        if badges:
            ctx_lines.append(f"🏷️ Badges: {_format_badges_plain(badges)}")
        ctx_plain = '\n'.join(ctx_lines)

        # Plain text body
        body_text = f"""
═══════════════════════════════════════════════
QUANT GATE ALERT
═══════════════════════════════════════════════

🪙 COIN: {name} ({symbol})
📊 PAIR: {symbol}-USD

───────────────────────────────────────────────
QUANT GATE RECOMMENDATION
───────────────────────────────────────────────
🎯 Signal: {display_signal}
📊 Confidence: {confidence}%
💭 {reasoning}

───────────────────────────────────────────────
MARKET CONTEXT
───────────────────────────────────────────────
{ctx_plain}

───────────────────────────────────────────────
ENTRY & TARGETS
───────────────────────────────────────────────
💰 Entry Price: ${price:,.8f}
🛑 Stop-Loss: ${stop_loss_price:,.8f} ({stop_loss_pct:.2f}%)
🎯 Take-Profit: ${take_profit_price:,.8f} ({take_profit_pct:.2f}%)

───────────────────────────────────────────────
RISK PARAMETERS
───────────────────────────────────────────────
📉 Risk: {risk_pct:.2f}% of account
📊 R:R Ratio: 1:{rr_ratio:.1f}
💵 Position Size: ${position_size:,.2f}

───────────────────────────────────────────────
⏰ {timestamp}
═══════════════════════════════════════════════
"""

        # --- HTML body ---
        signal_color = '#16a34a' if 'BUY' in display_signal else '#dc2626'
        regime_color = REGIME_COLORS.get(regime.upper(), '#6b7280') if regime else '#6b7280'

        # Build HTML market context rows
        ctx_html_rows = []
        if regime:
            ctx_html_rows.append(f'<tr><td style="padding:4px 0;color:#64748b;">🌐 Regime</td><td style="padding:4px 0;text-align:right;">{_regime_pill_html(regime)}</td></tr>')
        if fear_greed is not None:
            fg_val = int(float(fear_greed))
            fg_lbl = _fg_label(fear_greed)
            ctx_html_rows.append(f'<tr><td style="padding:4px 0;color:#64748b;">😱 Fear &amp; Greed</td><td style="padding:4px 0;text-align:right;font-weight:600;">{fg_val} ({fg_lbl})</td></tr>')
        if momentum_parts:
            ctx_html_rows.append(f'<tr><td style="padding:4px 0;color:#64748b;">📈 Momentum</td><td style="padding:4px 0;text-align:right;">{" &nbsp;|&nbsp; ".join(momentum_parts)}</td></tr>')
        if funding_rate is not None:
            ctx_html_rows.append(f'<tr><td style="padding:4px 0;color:#64748b;">💲 Funding</td><td style="padding:4px 0;text-align:right;">{float(funding_rate):.4f}%</td></tr>')
        if quant_path and quant_path != 'default':
            ctx_html_rows.append(f'<tr><td style="padding:4px 0;color:#64748b;">🛤️ Path</td><td style="padding:4px 0;text-align:right;">{quant_path.replace("_", " ").title()}</td></tr>')
        if edge_score is not None:
            ctx_html_rows.append(f'<tr><td style="padding:4px 0;color:#64748b;">🎲 Edge</td><td style="padding:4px 0;text-align:right;">{float(edge_score):.2f}</td></tr>')
        if confirmation_score is not None:
            ctx_html_rows.append(f'<tr><td style="padding:4px 0;color:#64748b;">✅ Confirmation</td><td style="padding:4px 0;text-align:right;">{int(float(confirmation_score))}/100</td></tr>')
        ctx_table = '\n        '.join(ctx_html_rows)

        badges_html = _format_badges_html(badges)
        badges_section = f'<div style="margin-top:8px;">{badges_html}</div>' if badges_html else ''

        body_html = f"""
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 0 auto; background: #f8fafc; color: #1e293b; padding: 20px;">
  <div style="background: linear-gradient(135deg, #0d9488 0%, #0f766e 100%); color: #fff; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
    <div style="font-weight: bold; font-size: 20px;">Quant Gate Alert</div>
    <div style="font-size: 18px; margin-top: 8px; color: {signal_color}; font-weight: 600;">{display_signal} — {name} ({symbol})</div>
  </div>
  <div style="background: #fff; padding: 20px; margin-top: 0; border: 1px solid #e2e8f0; border-top: none; border-radius: 0 0 8px 8px;">
    <div style="margin-bottom: 16px;">
      <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #64748b; margin-bottom: 8px;">Coin &amp; pair</div>
      <div style="font-size: 15px;">🪙 {name} ({symbol}) &nbsp;|&nbsp; 📊 {symbol}-USD</div>
    </div>
    <div style="margin-bottom: 16px; padding: 12px; background: #f0fdf4; border-left: 4px solid {signal_color}; border-radius: 4px;">
      <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #64748b; margin-bottom: 6px;">Quant Gate Recommendation</div>
      <div style="font-size: 18px; font-weight: 600; color: {signal_color};">🎯 {display_signal}</div>
      <div style="font-size: 14px; margin-top: 4px;">📊 Confidence: {confidence}%</div>
      <div style="font-size: 13px; color: #64748b; line-height: 1.5; margin-top: 8px; font-style: italic;">{reasoning}</div>
    </div>
    <div style="margin-bottom: 16px;">
      <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #64748b; margin-bottom: 8px;">Market Context</div>
      <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
        {ctx_table}
      </table>
      {badges_section}
    </div>
    <div style="margin-bottom: 16px;">
      <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #64748b; margin-bottom: 8px;">Entry &amp; Targets</div>
      <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
        <tr><td style="padding: 6px 0; color: #64748b;">💰 Entry</td><td style="padding: 6px 0; text-align: right; font-weight: 600;">${price:,.8f}</td></tr>
        <tr><td style="padding: 6px 0; color: #64748b;">🛑 Stop-Loss</td><td style="padding: 6px 0; text-align: right;">${stop_loss_price:,.8f} ({stop_loss_pct:.2f}%)</td></tr>
        <tr><td style="padding: 6px 0; color: #64748b;">🎯 Take-Profit</td><td style="padding: 6px 0; text-align: right;">${take_profit_price:,.8f} ({take_profit_pct:.2f}%)</td></tr>
      </table>
    </div>
    <div style="margin-bottom: 16px;">
      <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #64748b; margin-bottom: 8px;">Risk Parameters</div>
      <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
        <tr><td style="padding: 6px 0; color: #64748b;">📉 Risk</td><td style="padding: 6px 0; text-align: right;">{risk_pct:.2f}% of account</td></tr>
        <tr><td style="padding: 6px 0; color: #64748b;">📊 R:R</td><td style="padding: 6px 0; text-align: right;">1:{rr_ratio:.1f}</td></tr>
        <tr><td style="padding: 6px 0; color: #64748b;">💵 Position</td><td style="padding: 6px 0; text-align: right; font-weight: 600;">${position_size:,.2f}</td></tr>
      </table>
    </div>
    <div style="text-align: center; color: #64748b; font-size: 12px; padding-top: 12px; border-top: 1px solid #e2e8f0;">
      ⏰ {timestamp}
    </div>
  </div>
</div>
"""
        body_text = body_text.strip() + LEGAL_DISCLAIMER_PLAIN
        body_html = body_html.strip() + LEGAL_DISCLAIMER_HTML
        return subject, body_text, body_html
    
    
    # =========================================================================
    # Email Notifications
    # =========================================================================
    
    def _send_email(self, subject, body_text, body_html, recipients=None, force=False):
        """Send email notification via SMTP. recipients: optional list; if None uses saved/env list.

        Master kill-switch: when settings['email_globally_disabled'] is True, every
        email path is suppressed here (the single SMTP chokepoint), including reports
        that normally bypass the email_enabled toggle. Pass force=True only for the
        explicit user-initiated test-delivery path.
        """
        if not force and self.settings.get('email_globally_disabled', False):
            logger.info(f"   Email suppressed (all email alerts disabled): {subject}")
            return False, "All email alerts are disabled (email_globally_disabled)"
        to_list = recipients if recipients is not None else self._get_email_recipients_list()
        if not to_list:
            return False, "No email recipients configured"
        try:
            # Create message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = ALERT_EMAIL_FROM
            msg['To'] = ', '.join(to_list)
            
            # Attach both plain text and HTML versions
            part1 = MIMEText(body_text, 'plain', 'utf-8')
            part2 = MIMEText(body_html, 'html', 'utf-8')
            msg.attach(part1)
            msg.attach(part2)
            
            # Send via SMTP
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(msg)
            
            logger.info(f"   Email sent: {subject}")
            return True, None

        except smtplib.SMTPAuthenticationError:
            err = "Invalid SMTP credentials (check SMTP_USERNAME/SMTP_PASSWORD; Gmail requires an App Password)"
            logger.error(f"   Email failed: {err}")
            return False, err
        except smtplib.SMTPException as e:
            err = f"SMTP error: {e}"
            logger.error(f"   Email failed: {err}")
            return False, err
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            logger.error(f"   Email failed: {err}")
            return False, err
    
    
    # =========================================================================
    # Telegram Notifications
    # =========================================================================
    
    def _send_telegram(self, subject, body_text):
        """Send Telegram notification via Bot API."""
        try:
            # Format message (Telegram supports limited HTML)
            message = f"<b>{subject}</b>\n{body_text}"
            
            # Telegram message length limit is 4096
            if len(message) > 4096:
                message = message[:4093] + '...'
            
            # Send via Telegram Bot API
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                'chat_id': TELEGRAM_CHAT_ID,
                'text': message,
                'parse_mode': 'HTML',
            }
            
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            
            logger.info(f"   Telegram sent: {subject}")
            return True, None

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else '?'
            if status == 401:
                err = "Invalid bot token (check TELEGRAM_BOT_TOKEN)"
                logger.error(f"   Telegram failed: {err}")
            elif status == 400:
                err = "Invalid chat ID or message format (check TELEGRAM_CHAT_ID)"
                logger.error(f"   Telegram failed: {err}")
            else:
                err = f"HTTP {status}"
                logger.error(f"   Telegram failed: {err}")
            return False, err
        except requests.exceptions.Timeout:
            err = "Request timed out"
            logger.error(f"   Telegram failed: {err}")
            return False, err
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            logger.error(f"   Telegram failed: {err}")
            return False, err

    def _primary_recipients(self):
        """List of email addresses for critical alerts and daily reports (primary owner only)."""
        return [ALERT_PRIMARY_EMAIL] if ALERT_PRIMARY_EMAIL else []

    def send_critical_alert(self, subject: str, body: str, alert_type: str = None):
        """
        Send a critical system alert to primary owner only (ALERT_PRIMARY_EMAIL) + Telegram.
        Use for: drift, sync balance failure, rate limit errors, insufficient cash, etc.
        Does not check alert_type_enabled; critical alerts always go to primary.
        If alert_type is provided (e.g. 'drift', 'stop_loss'), respects per-type email/telegram toggles.
        Dedupe: same subject not sent again within 2 minutes (avoids double-send from concurrent refresh).
        Body content is ignored for dedup — prices/P&L change each cycle but the alert is logically the same.
        """
        # Drift/performance alerts are not deduped: worsening conditions must re-alert.
        # Other types use 2-minute dedup to prevent double-send from concurrent refreshes.
        _NODEDUPE_TYPES = ('drift',)
        skip_dedup = alert_type in _NODEDUPE_TYPES
        dedupe_key = subject
        with _critical_alert_lock:
            now = datetime.now(timezone.utc)
            # Prune old entries
            cutoff = now - timedelta(minutes=_CRITICAL_ALERT_COOLDOWN_MINUTES)
            for k in list(_critical_alert_recent.keys()):
                if _critical_alert_recent[k] < cutoff:
                    del _critical_alert_recent[k]
            if not skip_dedup:
                if dedupe_key in _critical_alert_recent:
                    logger.info(f"   Skipping duplicate critical alert (cooldown): {subject[:50]}...")
                    return
                _critical_alert_recent[dedupe_key] = now
                _save_dedup_cache()
        type_email = self.settings.get('alert_type_email', {}) or {}
        type_telegram = self.settings.get('alert_type_telegram', {}) or {}
        send_telegram = self.settings.get('telegram_enabled') and type_telegram.get(alert_type, True) and self.telegram_configured
        send_email = self.settings.get('email_enabled') and type_email.get(alert_type, True) and self.email_configured
        body_with_disclaimer = body.strip() + LEGAL_DISCLAIMER_PLAIN
        body_html = f"<p>{body.strip().replace(chr(10), '<br>')}</p>{LEGAL_DISCLAIMER_HTML}"
        recipients = self._primary_recipients()
        if send_telegram:
            self._send_telegram(subject, body_with_disclaimer)
        if send_email:
            self._send_email(subject, body_with_disclaimer, body_html, recipients=recipients)

    def send_daily_status_report(self, summary_html: str):
        """
        Send daily status report to primary owner only + Telegram.
        Always sends when configured — daily reports bypass the email_enabled toggle.
        """
        now = datetime.now(timezone.utc)
        try:
            from zoneinfo import ZoneInfo
            et = now.astimezone(ZoneInfo('America/New_York'))
            date_str = et.strftime('%Y-%m-%d')
        except Exception:
            date_str = now.strftime('%Y-%m-%d')
        subject = f"Elite Swing Trader — Status Report [{date_str}]"
        import re
        summary_plain = re.sub(r'<[^>]+>', ' ', summary_html).replace('&nbsp;', ' ').replace('\n', ' ')
        summary_plain = re.sub(r'\s+', ' ', summary_plain).strip()
        if len(summary_plain) > 3500:
            summary_plain = summary_plain[:3497] + '...'
        recipients = self._primary_recipients()
        if self.telegram_configured:
            self._send_telegram(subject, summary_plain)
        if self.email_configured:
            full_html = f"<div style='font-family: sans-serif; font-size: 14px;'>{summary_html}</div>{LEGAL_DISCLAIMER_HTML}"
            self._send_email(subject, summary_plain, full_html, recipients=recipients)

    def send_optimizer_report(self, subject: str, body_html: str):
        """Send recursive optimizer report to primary owner + Telegram.
        Always sends email when SMTP is configured — this is a daily system
        report, not a trade alert, so it bypasses the email_enabled toggle."""
        import re
        plain = re.sub(r'<[^>]+>', ' ', body_html).replace('&nbsp;', ' ').replace('\n', ' ')
        plain = re.sub(r'\s+', ' ', plain).strip()
        if len(plain) > 3500:
            plain = plain[:3497] + '...'
        recipients = self._primary_recipients()
        if self.telegram_configured:
            self._send_telegram(subject, plain)
        if self.email_configured:
            full_html = f"<div style='font-family: sans-serif; font-size: 14px;'>{body_html}</div>{LEGAL_DISCLAIMER_HTML}"
            self._send_email(subject, plain, full_html, recipients=recipients)

    def send_telegram_to_chat(self, chat_id, text: str) -> bool:
        """Send a Telegram message to a specific chat_id (e.g. for webhook reply). Returns True if sent."""
        if not TELEGRAM_BOT_TOKEN or not chat_id:
            return False
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {'chat_id': chat_id, 'text': text}
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return True
        except Exception:
            return False

    # =========================================================================
    # Main Alert Method
    # =========================================================================
    
    def send_alert(self, coin_data, alert_type='AI'):
        """
        Send trading alert across all enabled channels.
        
        Args:
            coin_data: Dictionary with coin info and AI recommendation
            alert_type: 'AI' (priority) or 'SIGNAL' (technical)
        
        Returns:
            dict with success status per channel
        """
        coin_id = coin_data.get('id', 'unknown')
        symbol = coin_data.get('symbol', 'UNKNOWN').upper()
        signal = coin_data.get('signal', 'UNKNOWN')
        
        # Get display signal (AI consensus or technical)
        if alert_type == 'AI':
            ai_rec = coin_data.get('ai_recommendation', {})
            consensus = ai_rec.get('consensus', {})
            display_signal = consensus.get('consensus_signal', signal)
        else:
            display_signal = signal
        
        # Check if alert was recently sent (rate limiting)
        was_sent, cooldown_remaining = self._was_recently_alerted(coin_id, alert_type, display_signal)
        if was_sent:
            logger.info(f"Skipping duplicate alert: {symbol} {alert_type} {display_signal} (cooldown: {cooldown_remaining}min)")
            try:
                with open('debug-71d417.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({'sessionId':'71d417','location':'alert_system.py:send_alert','message':'Trading alert SKIPPED (rate limit)','data':{'symbol':symbol,'alert_type':alert_type,'signal':display_signal},'timestamp':int(datetime.now(timezone.utc).timestamp()*1000),'hypothesisId':'dup'})+'\n')
            except Exception:
                pass
            return {'status': 'skipped', 'reason': 'rate_limited', 'cooldown_minutes': cooldown_remaining}
        
        # Check if this alert type is enabled
        type_key = 'ai_signal' if alert_type == 'AI' else 'technical_signal'
        if not self.settings.get('alert_type_enabled', {}).get(type_key, True):
            logger.info(f"Alert type {type_key} disabled for: {symbol}")
            return {'status': 'skipped', 'reason': 'alert_type_disabled'}
        
        # Per-type channel toggles (default True if not set)
        type_email = self.settings.get('alert_type_email', {}) or {}
        type_telegram = self.settings.get('alert_type_telegram', {}) or {}
        send_email = self.settings['email_enabled'] and type_email.get(type_key, True)
        send_telegram = self.settings['telegram_enabled'] and type_telegram.get(type_key, True)
        if not send_email and not send_telegram:
            logger.info(f"No alert channels enabled for: {symbol} {alert_type} {display_signal}")
            return {'status': 'skipped', 'reason': 'no_channels_enabled'}
        
        # Format message
        subject, body_text, body_html = self._format_alert_message(coin_data, alert_type)
        
        logger.info(f"Sending alert: {symbol} {alert_type} {display_signal}")
        
        results = {'status': 'sent', 'channels': {}}
        
        # Send Email (if enabled for this type and configured)
        if send_email:
            if self.email_configured:
                ok, _ = self._send_email(subject, body_text, body_html)
                results['channels']['email'] = 'success' if ok else 'failed'
            else:
                results['channels']['email'] = 'not_configured'
                logger.warning("   Email: Not configured (skipped)")
        else:
            results['channels']['email'] = 'disabled'
        
        # Send Telegram (if enabled for this type and configured)
        if send_telegram:
            if self.telegram_configured:
                ok, _ = self._send_telegram(subject, body_text)
                results['channels']['telegram'] = 'success' if ok else 'failed'
            else:
                results['channels']['telegram'] = 'not_configured'
                logger.warning("   Telegram: Not configured (skipped)")
        else:
            results['channels']['telegram'] = 'disabled'
        
        # Mark as sent (if at least one channel succeeded)
        if 'success' in results['channels'].values():
            self._mark_alert_sent(coin_id, alert_type, display_signal)
            logger.info(f"Alert logged: {coin_id}_{alert_type}_{display_signal}")
            try:
                with open('debug-71d417.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps({'sessionId':'71d417','location':'alert_system.py:send_alert','message':'Trading alert SENT','data':{'symbol':symbol,'alert_type':alert_type,'signal':display_signal,'channels':results.get('channels',{})},'timestamp':int(datetime.now(timezone.utc).timestamp()*1000),'hypothesisId':'dup'})+'\n')
            except Exception:
                pass
        else:
            logger.error(f"Alert failed on all channels")
            results['status'] = 'failed'
        
        return results
    
    
    # =========================================================================
    # Convenience Methods
    # =========================================================================
    
    def alert_ai_signal(self, coin_data):
        """
        Send AI recommendation alert (PRIORITY).
        This should be called when AI generates a new trading pick.
        """
        return self.send_alert(coin_data, alert_type='AI')
    
    def alert_technical_signal(self, coin_data):
        """
        Send technical buy/sell signal alert.
        This should be called when technical indicators generate a signal.
        """
        return self.send_alert(coin_data, alert_type='SIGNAL')

    def send_drift_alert(self, reason: str):
        """
        Send drift alert (critical). Goes to primary owner only + Telegram.
        """
        if not self.settings.get('alert_type_enabled', {}).get('drift', True):
            return
        subject = "Drift Alert – Swing Trader"
        body = f"Performance drift detected.\n\n{reason}\n\nReview dashboard and consider pausing auto-execution or disabling exchange API if needed."
        self.send_critical_alert(subject, body, alert_type='drift')

    def send_sync_balance_failure_alert(self, exchange_id: str, error: str, is_zero_cash: bool = False):
        """
        Send sync balance failure alert. Uses 60-min cooldown to prevent spam.
        Thread-safe: check+mark atomic so concurrent callers (e.g. refresh + TWAP) only send once.
        """
        SYSTEM_KEY = "system_sync_balance_failure"
        COOLDOWN_MINUTES = 60
        with _alert_history_lock:
            if SYSTEM_KEY in self.alert_history:
                try:
                    last_sent = datetime.fromisoformat(self.alert_history[SYSTEM_KEY])
                    if (datetime.now() - last_sent) < timedelta(minutes=COOLDOWN_MINUTES):
                        logger.info("   Skipping duplicate sync balance failure alert (60min cooldown)")
                        try:
                            with open('debug-71d417.log', 'a', encoding='utf-8') as f:
                                f.write(json.dumps({'sessionId':'71d417','location':'alert_system.py:send_sync_balance_failure_alert','message':'Sync balance alert SKIPPED (60min cooldown)','data':{'exchange_id':exchange_id},'timestamp':int(datetime.now(timezone.utc).timestamp()*1000),'hypothesisId':'sync'})+'\n')
                        except Exception:
                            pass
                        return
                except (ValueError, TypeError):
                    pass
            self.alert_history[SYSTEM_KEY] = datetime.now().isoformat()
            self._save_alert_history()
        if is_zero_cash:
            body = f"Exchange ({exchange_id}) reported no cash (USD/USDC/USDT). Check balance on exchange."
        else:
            body = f"Auto-sync balance from exchange failed.\n\nExchange: {exchange_id}\nError: {error}\n\nCheck API keys and network."
            err_lower = (error or "").lower()
            if any(x in err_lower for x in ("401", "403", "unauthorized", "forbidden")):
                body += "\n\nCheck exchange API keys and permissions."
        subject = "Sync Balance Failed – Swing Trader"
        self.send_critical_alert(subject, body, alert_type='sync_balance_failure')

    def send_repeated_sync_failure_alert(self, exchange_id: str, consecutive_count: int):
        """
        Alert when sync has failed N times in a row (exchange unreachable). No cooldown; sent once when threshold is hit.
        """
        if not self.settings.get('alert_type_enabled', {}).get('sync_balance_failure', True):
            return
        subject = "Repeated Sync Failure – Exchange Unreachable"
        body = (
            f"Auto-sync balance has failed {consecutive_count} times in a row.\n\n"
            f"Exchange: {exchange_id}\n\n"
            f"Check exchange status, API keys, and network. Consider pausing auto-execution until resolved."
        )
        self.send_critical_alert(subject, body, alert_type='sync_balance_failure')

    def send_balance_mismatch_alert(self, discrepancies: list):
        """
        Alert when reconciliation detects exchange vs journal discrepancies. Uses 60-min cooldown.
        """
        if not discrepancies:
            return
        key = "system_balance_mismatch"
        COOLDOWN_MINUTES = 60
        with _alert_history_lock:
            if key in self.alert_history:
                try:
                    last_sent = datetime.fromisoformat(self.alert_history[key])
                    if (datetime.now() - last_sent) < timedelta(minutes=COOLDOWN_MINUTES):
                        return
                except (ValueError, TypeError):
                    pass
            self.alert_history[key] = datetime.now().isoformat()
            self._save_alert_history()
        summary = "; ".join([f"{d.get('type', '')} {d.get('symbol', '')}" for d in discrepancies[:10]])
        if len(discrepancies) > 10:
            summary += f" ... (+{len(discrepancies) - 10} more)"
        subject = "Balance Mismatch – Reconcile"
        body = f"Reconciliation found {len(discrepancies)} discrepancy(ies) between exchange and journal.\n\n{summary}\n\nReview Account → Reconcile and sync if needed."
        self.send_critical_alert(subject, body, alert_type='drift')

    def send_insufficient_cash_alert(self, coin_symbol: str, position_usd: float, cash_available: float):
        """
        Alert when auto-execution was skipped (critical). Goes to primary owner only + Telegram.
        """
        if not self.settings.get('alert_type_enabled', {}).get('insufficient_cash', True):
            return
        subject = "Insufficient Cash – Auto-Exec Skipped"
        body = (
            f"Auto-execution skipped for {coin_symbol}: not enough cash.\n\n"
            f"  Trade size: ${position_usd:,.2f}\n"
            f"  Cash available: ${cash_available:,.2f}\n\n"
            f"Close positions or update Total Cash in Account Overview to allow new trades."
        )
        self.send_critical_alert(subject, body, alert_type='insufficient_cash')

    def send_low_cash_alert(self, cash_available: float, total_cash: float, pct: float, cooldown_minutes: int = 1440):
        """
        Alert when cash available is low (critical). Goes to primary owner only + Telegram. Uses cooldown to avoid spam.
        """
        if not self.settings.get('alert_type_enabled', {}).get('low_cash', True):
            return
        if self._was_recently_system_alert("system_low_cash", cooldown_minutes):
            return
        subject = "Low Cash Available – Swing Trader"
        body = (
            f"Cash available to trade is low: ${cash_available:,.2f} ({pct:.1f}% of total ${total_cash:,.2f}).\n\n"
            f"New trades may be skipped or capped until you close positions or add capital."
        )
        self.send_critical_alert(subject, body, alert_type='low_cash')
        self._mark_system_alert_sent("system_low_cash")

    def send_missed_exit_alert(self, position: dict, trigger_type: str, minutes_over: int):
        """Alert when position is still open past stop/target for N+ minutes (possible missed exit)."""
        if not self.settings.get('alert_type_enabled', {}).get('stop_loss', True):
            return
        symbol = position.get('coin_symbol', '') or position.get('symbol', '')
        subject = f"Possible Missed Exit – {symbol}"
        body = (
            f"Position {symbol} is still open but price has been past "
            f"{'stop-loss' if trigger_type == 'stop' else 'take-profit'} for {minutes_over}+ minutes.\n\n"
            f"Consider closing manually or check auto-execution."
        )
        self.send_critical_alert(subject, body, alert_type='stop_loss')

    def send_stop_loss_alert(self, position: dict, current_price: float):
        """Alert when stop-loss is hit (manual mode or execution failed)."""
        if not self.settings.get('alert_type_enabled', {}).get('stop_loss', True):
            return
        symbol = position.get('coin_symbol', '') or position.get('symbol', '')
        subject = f"Stop-Loss Hit – {symbol}"
        body = f"{symbol} hit stop-loss at ${current_price:.8f}. Close position #{position.get('id')} manually if not auto-executed."
        self.send_critical_alert(subject, body, alert_type='stop_loss')

    def _format_sell_confirmation_extras(self, position: dict, fill_price: float, closed: dict = None, exit_fee_usd: float = None) -> str:
        """Build buy/sell levels and realized PnL section for sell confirmation emails."""
        entry = float(position.get('entry_price', 0) or 0)
        quantity = float(position.get('quantity', 0) or 0)
        capital = float(position.get('capital_usd', 0) or 0) or (entry * quantity if entry and quantity else 0)
        pnl_usd = closed.get('pnl_usd') if closed else None
        pnl_pct = closed.get('pnl_pct') if closed else None
        if pnl_usd is None and entry and fill_price and quantity:
            gross = (fill_price - entry) * quantity
            entry_fee = float((position.get('entry_fee_usd') or 0) or 0)
            exit_fee = float(exit_fee_usd or 0) if exit_fee_usd is not None else 0
            pnl_usd = gross - entry_fee - exit_fee
        if pnl_pct is None and capital and pnl_usd is not None:
            pnl_pct = (pnl_usd / capital) * 100
        lines = [
            f"Buy: ${entry:,.4f}  →  Sell: ${fill_price:,.4f}",
        ]
        if pnl_usd is not None and pnl_pct is not None:
            sign = '+' if pnl_usd >= 0 else ''
            lines.append(f"Realized PnL: {sign}${pnl_usd:,.2f} ({sign}{pnl_pct:.2f}%)")
        return " | ".join(lines)

    def send_stop_loss_executed_alert(self, position: dict, result: dict, closed: dict = None):
        """Alert when stop-loss was auto-executed. Include buy/sell levels and realized PnL."""
        if not self.settings.get('alert_type_enabled', {}).get('stop_loss', True):
            return
        symbol = position.get('coin_symbol', '') or position.get('symbol', '')
        fill = result.get('fill_price') or result.get('average_price', 0)
        exit_fee = result.get('total_fee_usd') or result.get('fee_usd')
        subject = f"Stop-Loss Executed – {symbol}"
        extras = self._format_sell_confirmation_extras(position, fill, closed, exit_fee_usd=exit_fee)
        body = f"{symbol} stop-loss auto-executed at ${fill:.8f}. Position closed.\n\n{extras}"
        self.send_critical_alert(subject, body, alert_type='stop_loss')

    def send_take_profit_alert(self, position: dict, current_price: float):
        """Alert when take-profit is hit (manual mode or execution failed)."""
        if not self.settings.get('alert_type_enabled', {}).get('take_profit', True):
            return
        symbol = position.get('coin_symbol', '') or position.get('symbol', '')
        subject = f"Take-Profit Hit – {symbol}"
        body = f"{symbol} hit take-profit at ${current_price:.8f}. Close position #{position.get('id')} manually if not auto-executed."
        self.send_critical_alert(subject, body, alert_type='take_profit')

    def send_take_profit_executed_alert(self, position: dict, result: dict, closed: dict = None):
        """Alert when take-profit was auto-executed. Include buy/sell levels and realized PnL."""
        if not self.settings.get('alert_type_enabled', {}).get('take_profit', True):
            return
        symbol = position.get('coin_symbol', '') or position.get('symbol', '')
        fill = result.get('fill_price') or result.get('average_price', 0)
        exit_fee = result.get('total_fee_usd') or result.get('fee_usd')
        subject = f"Take-Profit Executed – {symbol}"
        extras = self._format_sell_confirmation_extras(position, fill, closed, exit_fee_usd=exit_fee)
        body = f"{symbol} take-profit auto-executed at ${fill:.8f}. Position closed.\n\n{extras}"
        self.send_critical_alert(subject, body, alert_type='take_profit')

    def send_trailing_stop_alert(self, position: dict, current_price: float, high_water_mark: float):
        """Alert when trailing stop is hit."""
        if not self.settings.get('alert_type_enabled', {}).get('trailing_stop', True):
            return
        symbol = position.get('coin_symbol', '') or position.get('symbol', '')
        subject = f"Trailing Stop Hit – {symbol}"
        body = f"{symbol} hit trailing stop at ${current_price:.8f} (high water ${high_water_mark:.8f}). Close position #{position.get('id')} manually if not auto-executed."
        self.send_critical_alert(subject, body, alert_type='trailing_stop')

    def send_buy_filled_alert(self, fill_data: dict):
        """
        Send rich email alert when the system books a buy (TWAP fill or manual book).
        Accepts the full fill_data dict with execution_context fields merged in.
        Primary + Telegram; respects alert_type_enabled/email/telegram for 'buy_filled'.
        """
        if not self.settings.get('alert_type_enabled', {}).get('buy_filled', True):
            return

        # --- Extract fields (all optional-safe) ---
        symbol = (fill_data.get('coin_symbol') or '').upper()
        coin_name = fill_data.get('coin_name') or symbol
        quantity = fill_data.get('quantity', 0)
        entry_price = float(fill_data.get('entry_price', 0) or 0)
        capital_usd = float(fill_data.get('capital_usd', 0) or 0)
        tier = (fill_data.get('quant_tier') or 'BUY').replace('_', ' ')

        # Trade targets
        stop_pct = fill_data.get('ai_stop_pct')
        target_pct = fill_data.get('ai_target_pct')
        stop_price = round(entry_price * (1 - float(stop_pct) / 100), 8) if stop_pct and entry_price else None
        target_price = round(entry_price * (1 + float(target_pct) / 100), 8) if target_pct and entry_price else None
        rr_ratio = float(target_pct) / float(stop_pct) if target_pct and stop_pct and float(stop_pct) > 0 else None

        # Signal context
        regime = fill_data.get('market_regime') or ''
        quant_path = fill_data.get('quant_path') or ''
        trading_scenario = fill_data.get('trading_scenario') or ''
        confirmation_score = fill_data.get('confirmation_score')
        edge_score = fill_data.get('edge_score')
        specialist_veto = fill_data.get('specialist_veto') or ''
        badges = fill_data.get('signal_badges') or []

        # Market data
        fear_greed = fill_data.get('fear_greed')
        funding_rate = fill_data.get('funding_rate')
        ch24h = fill_data.get('price_change_24h')
        ch7d = fill_data.get('price_change_7d')
        ch30d = fill_data.get('price_change_30d')

        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        signal_color = '#16a34a'  # green for BUY confirmations
        subject = f"Trade Booked – {symbol} – {tier}"

        # --- Plain text body ---
        lines = [
            f"═══════════════════════════════════════════════",
            f"TRADE BOOKED — {symbol}",
            f"═══════════════════════════════════════════════",
            f"",
            f"───────────────────────────────────────────────",
            f"TRADE DETAILS",
            f"───────────────────────────────────────────────",
            f"🪙 {coin_name} ({symbol})",
            f"📦 Quantity: {quantity}",
            f"💰 Entry: ${entry_price:,.4f}",
            f"💵 Capital: ${capital_usd:,.2f}",
        ]
        if stop_pct and stop_price:
            lines.append(f"🛑 Stop-Loss: ${stop_price:,.4f} ({float(stop_pct):.1f}%)")
        if target_pct and target_price:
            lines.append(f"🎯 Take-Profit: ${target_price:,.4f} ({float(target_pct):.1f}%)")
        if rr_ratio:
            lines.append(f"📊 R:R Ratio: 1:{rr_ratio:.1f}")

        lines += [
            f"",
            f"───────────────────────────────────────────────",
            f"WHY IT'S A BUY",
            f"───────────────────────────────────────────────",
            f"🎯 Signal: {tier}",
        ]
        if quant_path and quant_path != 'default':
            lines.append(f"🛤️ Path: {quant_path.replace('_', ' ').title()}")
        if trading_scenario and trading_scenario != quant_path:
            lines.append(f"🎮 Scenario: {trading_scenario.replace('_', ' ').title()}")
        if confirmation_score is not None:
            lines.append(f"✅ Confirmation: {int(float(confirmation_score))}/100")
        if edge_score is not None:
            lines.append(f"🎲 Edge Score: {float(edge_score):.2f}")
        if specialist_veto:
            veto_icon = '✅' if specialist_veto == 'CONFIRM' else '🚫'
            lines.append(f"{veto_icon} Specialist: {specialist_veto}")

        lines += [
            f"",
            f"───────────────────────────────────────────────",
            f"MARKET CONTEXT",
            f"───────────────────────────────────────────────",
        ]
        if regime:
            lines.append(f"🌐 Regime: {regime}")
        if fear_greed is not None:
            lines.append(f"😱 Fear & Greed: {int(float(fear_greed))} ({_fg_label(fear_greed)})")
        momentum_parts = []
        if ch24h is not None:
            momentum_parts.append(f"24h: {_fmt_pct(ch24h)}")
        if ch7d is not None:
            momentum_parts.append(f"7d: {_fmt_pct(ch7d)}")
        if ch30d is not None:
            momentum_parts.append(f"30d: {_fmt_pct(ch30d)}")
        if momentum_parts:
            lines.append(f"📈 {' | '.join(momentum_parts)}")
        if funding_rate is not None:
            lines.append(f"💲 Funding Rate: {float(funding_rate):.4f}%")
        if badges:
            lines.append(f"🏷️ Badges: {_format_badges_plain(badges)}")

        lines += [
            f"",
            f"───────────────────────────────────────────────",
            f"⏰ {timestamp}",
            f"═══════════════════════════════════════════════",
        ]
        body_text = '\n'.join(lines)

        # --- HTML body (teal card design matching trade alert) ---
        # Trade details rows
        trade_rows = [
            f'<tr><td style="padding:6px 0;color:#64748b;">📦 Quantity</td><td style="padding:6px 0;text-align:right;font-weight:600;">{quantity}</td></tr>',
            f'<tr><td style="padding:6px 0;color:#64748b;">💰 Entry</td><td style="padding:6px 0;text-align:right;font-weight:600;">${entry_price:,.4f}</td></tr>',
            f'<tr><td style="padding:6px 0;color:#64748b;">💵 Capital</td><td style="padding:6px 0;text-align:right;font-weight:600;">${capital_usd:,.2f}</td></tr>',
        ]
        if stop_pct and stop_price:
            trade_rows.append(f'<tr><td style="padding:6px 0;color:#64748b;">🛑 Stop-Loss</td><td style="padding:6px 0;text-align:right;">${stop_price:,.4f} ({float(stop_pct):.1f}%)</td></tr>')
        if target_pct and target_price:
            trade_rows.append(f'<tr><td style="padding:6px 0;color:#64748b;">🎯 Take-Profit</td><td style="padding:6px 0;text-align:right;">${target_price:,.4f} ({float(target_pct):.1f}%)</td></tr>')
        if rr_ratio:
            trade_rows.append(f'<tr><td style="padding:6px 0;color:#64748b;">📊 R:R</td><td style="padding:6px 0;text-align:right;">1:{rr_ratio:.1f}</td></tr>')
        trade_table = '\n        '.join(trade_rows)

        # Why it's a buy rows
        why_rows = [
            f'<tr><td style="padding:4px 0;color:#64748b;">🎯 Signal</td><td style="padding:4px 0;text-align:right;font-weight:600;color:{signal_color};">{tier}</td></tr>',
        ]
        if quant_path and quant_path != 'default':
            why_rows.append(f'<tr><td style="padding:4px 0;color:#64748b;">🛤️ Path</td><td style="padding:4px 0;text-align:right;">{quant_path.replace("_", " ").title()}</td></tr>')
        if trading_scenario and trading_scenario != quant_path:
            why_rows.append(f'<tr><td style="padding:4px 0;color:#64748b;">🎮 Scenario</td><td style="padding:4px 0;text-align:right;">{trading_scenario.replace("_", " ").title()}</td></tr>')
        if confirmation_score is not None:
            why_rows.append(f'<tr><td style="padding:4px 0;color:#64748b;">✅ Confirmation</td><td style="padding:4px 0;text-align:right;">{int(float(confirmation_score))}/100</td></tr>')
        if edge_score is not None:
            why_rows.append(f'<tr><td style="padding:4px 0;color:#64748b;">🎲 Edge</td><td style="padding:4px 0;text-align:right;">{float(edge_score):.2f}</td></tr>')
        if specialist_veto:
            veto_color = '#16a34a' if specialist_veto == 'CONFIRM' else '#dc2626'
            why_rows.append(f'<tr><td style="padding:4px 0;color:#64748b;">🔬 Specialist</td><td style="padding:4px 0;text-align:right;color:{veto_color};font-weight:600;">{specialist_veto}</td></tr>')
        why_table = '\n        '.join(why_rows)

        # Market context rows
        mkt_rows = []
        if regime:
            mkt_rows.append(f'<tr><td style="padding:4px 0;color:#64748b;">🌐 Regime</td><td style="padding:4px 0;text-align:right;">{_regime_pill_html(regime)}</td></tr>')
        if fear_greed is not None:
            fg_val = int(float(fear_greed))
            mkt_rows.append(f'<tr><td style="padding:4px 0;color:#64748b;">😱 Fear &amp; Greed</td><td style="padding:4px 0;text-align:right;font-weight:600;">{fg_val} ({_fg_label(fear_greed)})</td></tr>')
        if momentum_parts:
            mkt_rows.append(f'<tr><td style="padding:4px 0;color:#64748b;">📈 Momentum</td><td style="padding:4px 0;text-align:right;">{" &nbsp;|&nbsp; ".join(momentum_parts)}</td></tr>')
        if funding_rate is not None:
            mkt_rows.append(f'<tr><td style="padding:4px 0;color:#64748b;">💲 Funding</td><td style="padding:4px 0;text-align:right;">{float(funding_rate):.4f}%</td></tr>')
        mkt_table = '\n        '.join(mkt_rows)

        badges_html = _format_badges_html(badges)
        badges_section = f'<div style="margin-top:8px;">{badges_html}</div>' if badges_html else ''

        body_html = f"""
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 0 auto; background: #f8fafc; color: #1e293b; padding: 20px;">
  <div style="background: linear-gradient(135deg, #0d9488 0%, #0f766e 100%); color: #fff; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
    <div style="font-weight: bold; font-size: 20px;">Trade Booked</div>
    <div style="font-size: 22px; margin-top: 8px; font-weight: 700;">{coin_name} ({symbol})</div>
    <div style="font-size: 15px; margin-top: 4px; opacity: 0.9;">{tier}</div>
  </div>
  <div style="background: #fff; padding: 20px; margin-top: 0; border: 1px solid #e2e8f0; border-top: none; border-radius: 0 0 8px 8px;">
    <div style="margin-bottom: 16px;">
      <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #64748b; margin-bottom: 8px;">Trade Details</div>
      <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
        {trade_table}
      </table>
    </div>
    <div style="margin-bottom: 16px; padding: 12px; background: #f0fdf4; border-left: 4px solid {signal_color}; border-radius: 4px;">
      <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #64748b; margin-bottom: 6px;">Why It's a Buy</div>
      <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
        {why_table}
      </table>
    </div>
    <div style="margin-bottom: 16px;">
      <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #64748b; margin-bottom: 8px;">Market Context</div>
      <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
        {mkt_table}
      </table>
      {badges_section}
    </div>
    <div style="text-align: center; color: #64748b; font-size: 12px; padding-top: 12px; border-top: 1px solid #e2e8f0;">
      ⏰ {timestamp}
    </div>
  </div>
</div>
"""
        body_text = body_text.strip() + LEGAL_DISCLAIMER_PLAIN
        body_html = body_html.strip() + LEGAL_DISCLAIMER_HTML

        # Send via critical alert path (primary + telegram, respects per-type toggles)
        type_email = self.settings.get('alert_type_email', {}) or {}
        type_telegram = self.settings.get('alert_type_telegram', {}) or {}
        send_telegram = self.settings.get('telegram_enabled') and type_telegram.get('buy_filled', True) and self.telegram_configured
        send_email = self.settings.get('email_enabled') and type_email.get('buy_filled', True) and self.email_configured
        recipients = self._primary_recipients()
        if send_telegram:
            self._send_telegram(subject, body_text)
        if send_email:
            self._send_email(subject, body_text, body_html, recipients=recipients)

    def send_stress_report_alert(self, summary: dict):
        """Send notification with 30d/60d buy-signal stress report summary (blockers, missed opportunities)."""
        if not self.settings.get('alert_type_enabled', {}).get('stress_report', True):
            return
        ts = summary.get('report_timestamp', '')[:19].replace('T', ' ')
        s30 = summary.get('30d') or {}
        s60 = summary.get('60d') or {}
        subject = "Monthly Buy-Signal Stress Report"
        body = (
            f"Buy-signal stress report ({ts} UTC).\n\n"
            f"30d: recommendations={s30.get('recommendations_total', 0)}, screening_failed_with_buy={s30.get('screening_failed_with_buy_total', 0)}, missed_opportunity={s30.get('missed_opportunity_count', 0)}. Blockers: {s30.get('by_blocker', {})}\n\n"
            f"60d: recommendations={s60.get('recommendations_total', 0)}, screening_failed_with_buy={s60.get('screening_failed_with_buy_total', 0)}, missed_opportunity={s60.get('missed_opportunity_count', 0)}. Blockers: {s60.get('by_blocker', {})}\n\n"
            "See data/buy_signals_stress_30d.json and data/buy_signals_stress_60d.json for full detail."
        )
        self.send_critical_alert(subject, body, alert_type='stress_report')

    def send_test_to_all_configured(self, test_coin_data=None):
        """
        Send a test message to every configured channel (email and/or Telegram),
        regardless of enabled flags. Used to verify credentials and delivery.
        Skips rate limiting. Returns status per channel.
        """
        if test_coin_data is None:
            test_coin_data = {
                'id': 'test',
                'name': 'Test Coin',
                'symbol': 'BTC',
                'signal': 'BUY',
                'current_price': 45000.0,
                'stop_loss_pct': 2.5,
                'take_profit_pct': 7.5,
                'stop_loss_price': 43875.0,
                'take_profit_price': 48375.0,
                'risk_reward_ratio': 3.0,
                'fear_greed': 68,
                'indicators': {'ch24h': 3.2},
                'price_change_percentage_7d_in_currency': 8.1,
                'price_change_percentage_30d_in_currency': 15.4,
                'enhanced': {'funding_rate': {'current': 0.012}},
                'ai_recommendation': {
                    'consensus': {
                        'consensus_signal': 'STRONG_BUY',
                        'reasoning': 'TEST ALERT – This is a test from your Swing Trader alert system. If you received this, alerts are working.',
                        'market_regime': 'BULL',
                    },
                    'quant': {
                        'quant_confidence': 78,
                        'reasoning': 'TEST ALERT – This is a test from your Swing Trader alert system. If you received this, alerts are working.',
                        'signal_badges': ['pullback_in_trend', 'light_sniper_entry'],
                        'quant_path': 'pullback_in_trend',
                        'confirmation_score': 82,
                        'edge_score': 0.68,
                    },
                    'position_sizing': {'pct_of_account': 2.0, 'position_size_usd': 1000.0},
                },
            }
        subject, body_text, body_html = self._format_alert_message(test_coin_data, 'AI')
        results = {'status': 'sent', 'channels': {}, 'message': 'Test sent to all configured channels.'}
        if self.email_configured:
            # force=True: user-initiated delivery test must work even when the
            # master kill-switch (email_globally_disabled) is on.
            ok, err = self._send_email(subject, body_text, body_html, force=True)
            results['channels']['email'] = 'success' if ok else 'failed'
            if err:
                results['email_error'] = err
        else:
            results['channels']['email'] = 'not_configured'
        if self.telegram_configured:
            ok, err = self._send_telegram(subject, body_text)
            results['channels']['telegram'] = 'success' if ok else 'failed'
            if err:
                results['telegram_error'] = err
        else:
            results['channels']['telegram'] = 'not_configured'
        if 'success' not in results['channels'].values():
            results['status'] = 'failed'
            results['message'] = 'No configured channel succeeded.'
        return results


# =============================================================================
# Module Test
# =============================================================================

if __name__ == '__main__':
    # Test the alert system
    print("="*60)
    print("ALERT SYSTEM TEST")
    print("="*60)
    
    alerts = AlertSystem()
    
    print("\nCurrent Settings:")
    settings = alerts.get_settings()
    for key, value in settings.items():
        print(f"  {key}: {value}")
    
    # Test coin data
    test_coin = {
        'id': 'bitcoin',
        'name': 'Bitcoin',
        'symbol': 'BTC',
        'signal': 'BUY',
        'current_price': 45000.50,
        'stop_loss_pct': 2.5,
        'take_profit_pct': 7.5,
        'stop_loss_price': 43875.49,
        'take_profit_price': 48375.54,
        'risk_reward_ratio': 3.0,
        'ai_recommendation': {
            'consensus': {
                'consensus_signal': 'STRONG_BUY',
                'reasoning': 'Strong bullish momentum with oversold bounce potential and high volume support.',
                'market_regime': 'BULL',
            },
            'quant': {
                'quant_confidence': 78,
                'reasoning': 'Strong bullish momentum with oversold bounce potential and high volume support.',
            },
            'position_sizing': {
                'pct_of_account': 2.0,
                'position_size_usd': 1000.00,
            }
        }
    }
    
    print("\n" + "="*60)
    print("To send test alert:")
    print("="*60)
    print("1. Set environment variables in .env:")
    print("   SMTP_USERNAME=your-email@gmail.com")
    print("   SMTP_PASSWORD=your-app-password")
    print("   ALERT_EMAIL_TO=recipient@example.com")
    print("   TELEGRAM_BOT_TOKEN=your-bot-token")
    print("   TELEGRAM_CHAT_ID=your-chat-id")
    print("\n2. Enable channels:")
    print("   alerts.enable_email()")
    print("   alerts.enable_telegram()")
    print("\n3. Send test alert:")
    print("   results = alerts.alert_ai_signal(test_coin)")
    print("="*60)
