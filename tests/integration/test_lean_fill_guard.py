# tests/integration/test_lean_fill_guard.py
# Production-path parity for Stage 1c LEAN mode: the fail-closed fill guard in
# trade_journal.open_position must block non-compliant automated entries when
# LEAN_ENTRY_MODE is active, while leaving legacy behavior and manual/import
# overrides untouched. Mirrors the leaked fills (#655-#662) that motivated it.

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# A non-compliant entry with the exact fingerprint of the leaked trades:
# contrarian path, risk_regime unknown, narrow legacy stop.
_LEAKED = dict(
    coin_id='bonk', coin_name='Bonk', coin_symbol='BONK', signal='BUY',
    entry_price=0.00002, quantity=1_000_000,
    ai_stop_pct=10.3, ai_target_pct=20.59,
    quant_path='blood_in_the_street', risk_regime=None, price_change_30d=-12.0,
    is_test=True,
)

# A compliant lean entry: momentum path that survived the prune (risk-on),
# carrying the wide fixed lean stop.
_COMPLIANT = dict(
    coin_id='solana', coin_name='Solana', coin_symbol='SOL', signal='BUY',
    entry_price=150.0, quantity=2.0,
    ai_stop_pct=24.0, ai_target_pct=150.0,
    quant_path='early_momentum', risk_regime='RISK_ON', price_change_30d=22.0,
    is_test=True,
)


@pytest.fixture
def lean_on():
    """Activate LEAN_ENTRY_MODE for the current thread (no live_thresholds write)."""
    from thresholds import threshold_override
    with threshold_override({'LEAN_ENTRY_MODE': 1.0}):
        yield


@pytest.fixture(autouse=True)
def _isolate_audit_log(tmp_path, monkeypatch):
    """Redirect the lean audit trail to a temp file so tests don't touch data/."""
    import lean_policy
    monkeypatch.setattr(lean_policy, '_LEAN_AUDIT_PATH', str(tmp_path / 'lean_audit.jsonl'))
    return tmp_path / 'lean_audit.jsonl'


class TestLeanFillGuard:
    def test_blocks_noncompliant_when_lean_active(self, temp_positions, lean_on):
        """Leaked-fingerprint entry is rejected (returns None) under lean mode."""
        from trade_journal import open_position, get_open_positions
        pos = open_position(dict(_LEAKED))
        assert pos is None
        assert get_open_positions() == []

    def test_allows_compliant_when_lean_active(self, temp_positions, lean_on):
        """A momentum/risk-on entry with the lean stop profile is journaled."""
        from trade_journal import open_position
        pos = open_position(dict(_COMPLIANT))
        assert pos is not None
        assert pos['status'] == 'OPEN'
        assert pos['quant_path'] == 'early_momentum'

    def test_override_bypasses_guard(self, temp_positions, lean_on):
        """Manual/import path (lean_override=True) is exempt even when non-compliant."""
        from trade_journal import open_position
        payload = dict(_LEAKED)
        payload['lean_override'] = True
        pos = open_position(payload)
        assert pos is not None
        assert pos['status'] == 'OPEN'

    def test_legacy_behavior_when_lean_off(self, temp_positions):
        """With lean off (default), the non-compliant entry opens — no regression."""
        from trade_journal import open_position
        pos = open_position(dict(_LEAKED))
        assert pos is not None
        assert pos['status'] == 'OPEN'

    def test_block_is_audited(self, temp_positions, lean_on, _isolate_audit_log):
        """A blocked fill writes a fill_blocked event with a reason to the audit log."""
        from trade_journal import open_position
        open_position(dict(_LEAKED))
        audit_path = _isolate_audit_log
        assert audit_path.exists()
        events = [json.loads(line) for line in audit_path.read_text().splitlines()]
        blocked = [e for e in events if e['event'] == 'fill_blocked']
        assert len(blocked) == 1
        assert blocked[0]['symbol'] == 'BONK'
        assert blocked[0]['reason']


class TestExecBoundaryGate:
    """The execution-boundary gate in start_twap_buy_async must block non-lean
    entries BEFORE any order is placed — the authoritative gate ahead of the
    irreversible buy."""

    @pytest.fixture
    def _patch_engine(self, monkeypatch):
        """Stub the exchange-facing internals so the gate can be tested without
        network or threads; record any execution call."""
        import execution_engine as ee

        calls = []

        class _SyncThread:
            def __init__(self, target=None, args=(), daemon=None):
                self._target, self._args = target, args

            def start(self):
                if self._target:
                    self._target(*self._args)

        monkeypatch.setattr(ee.threading, 'Thread', _SyncThread)
        monkeypatch.setattr(ee, 'symbol_to_ccxt', lambda s: f"{s}/USD")
        monkeypatch.setattr(ee, '_register_active_twap', lambda *a, **k: None)
        monkeypatch.setattr(ee, '_twap_cooldown_ok', lambda *a, **k: True)
        monkeypatch.setattr(ee, 'run_twap_buy_coinbase', lambda *a, **k: calls.append('twap'))
        monkeypatch.setattr(ee, 'run_single_market_buy_coinbase', lambda *a, **k: calls.append('single'))
        return ee, calls

    _CTX_COMPLIANT = dict(quant_path='early_momentum', risk_regime='RISK_ON', price_change_30d=22.0)
    _CTX_BAD_PATH = dict(quant_path='momentum_context', risk_regime='RISK_ON', price_change_30d=22.0)
    _CTX_RISK_OFF = dict(quant_path='early_momentum', risk_regime='RISK_OFF', price_change_30d=22.0)
    _CTX_LOW_MOM = dict(quant_path='early_momentum', risk_regime='RISK_ON', price_change_30d=5.0)

    def _start(self, ee, ctx, **kw):
        return ee.start_twap_buy_async(
            symbol='SOL', total_usd=1000.0, twap_min_usd=500.0,
            is_test=True, execution_context=dict(ctx), **kw,
        )

    @pytest.mark.parametrize('ctx', [_CTX_BAD_PATH, _CTX_RISK_OFF, _CTX_LOW_MOM])
    def test_blocks_noncompliant_before_execution(self, _patch_engine, lean_on, ctx):
        ee, calls = _patch_engine
        started = self._start(ee, ctx)
        assert started is False
        assert calls == []  # no order ever placed

    def test_allows_compliant(self, _patch_engine, lean_on):
        ee, calls = _patch_engine
        started = self._start(ee, self._CTX_COMPLIANT)
        assert started is True
        assert calls == ['twap']

    def test_override_bypasses_gate(self, _patch_engine, lean_on):
        ee, calls = _patch_engine
        started = self._start(ee, self._CTX_BAD_PATH, lean_override=True)
        assert started is True
        assert calls == ['twap']

    def test_no_gate_when_lean_off(self, _patch_engine):
        ee, calls = _patch_engine
        started = self._start(ee, self._CTX_BAD_PATH)  # lean inactive → legacy behavior
        assert started is True
        assert calls == ['twap']

    def test_block_is_audited(self, _patch_engine, lean_on, _isolate_audit_log):
        ee, _ = _patch_engine
        self._start(ee, self._CTX_BAD_PATH)
        events = [json.loads(l) for l in _isolate_audit_log.read_text().splitlines()]
        blocked = [e for e in events if e['event'] == 'exec_blocked']
        assert len(blocked) == 1 and blocked[0]['symbol'] == 'SOL' and blocked[0]['reason']


class TestLeanCompliantTag:
    """open_position tags every entry with lean_compliant (re-derived), so a
    lean-compliant paper cohort can be measured forward."""

    def test_compliant_entry_tagged_true(self, temp_positions, lean_on):
        from trade_journal import open_position
        pos = open_position(dict(_COMPLIANT))
        assert pos['lean_compliant'] is True

    def test_override_noncompliant_tagged_false(self, temp_positions, lean_on):
        from trade_journal import open_position
        payload = dict(_LEAKED); payload['lean_override'] = True
        pos = open_position(payload)
        assert pos is not None and pos['lean_compliant'] is False

    def test_legacy_noncompliant_tagged_false(self, temp_positions):
        from trade_journal import open_position
        pos = open_position(dict(_LEAKED))
        assert pos is not None and pos['lean_compliant'] is False


class TestLeanPolicyContract:
    def test_exec_and_fill_share_entry_core(self):
        """assert_exec_compliant and assert_fill_compliant must agree on entry
        eligibility; fill adds only the stop-profile check on top."""
        from lean_policy import assert_exec_compliant, assert_fill_compliant
        # Entry-noncompliant: identical reason from both.
        assert assert_exec_compliant(dict(_LEAKED)) == assert_fill_compliant(dict(_LEAKED))
        # Entry-compliant but wrong stop: exec passes, fill fails on the stop only.
        bad_stop = dict(_COMPLIANT); bad_stop['ai_stop_pct'] = 8.0
        assert assert_exec_compliant(bad_stop) is None
        assert 'stop' in (assert_fill_compliant(bad_stop) or '')

    def test_entry_allowed_rejects_contrarian_path(self):
        from lean_policy import entry_allowed
        ok, reason = entry_allowed('blood_in_the_street', True, 30.0)
        assert ok is False and 'path' in reason

    def test_entry_allowed_rejects_risk_off(self):
        from lean_policy import entry_allowed
        ok, reason = entry_allowed('early_momentum', False, 30.0)
        assert ok is False and 'risk-on' in reason

    def test_entry_allowed_accepts_compliant(self):
        from lean_policy import entry_allowed
        ok, reason = entry_allowed('early_momentum', True, 30.0)
        assert ok is True and reason is None

    def test_allowed_paths_shared_with_quant_signal(self):
        """quant_signal must use the same path set as the fill guard (no drift)."""
        import lean_policy
        import quant_signal
        assert quant_signal.LEAN_ALLOWED_PATHS is lean_policy.LEAN_ALLOWED_PATHS
