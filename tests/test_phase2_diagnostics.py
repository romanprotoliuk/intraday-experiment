from trader.multistrategy.diagnose import matched_cost, bucket
import pytest


@pytest.mark.parametrize('reason,spread_applied',[('STOP',False),('TARGET',False),('TARGET_GAP',False),('TIME',True),('STOP_GAP',True),('VWAP',True)])
def test_cost_attribution_respects_bid_side_barriers(reason,spread_applied):
    costs=dict(slippage_bps=10,spread_bps=20,fee_per_side=.01)
    trade=dict(exit=99.9,exit_reason=reason,quantity='2',estimated_entry_cost='.03')
    expected=.04+2*(100/(.999 if spread_applied else 1)-99.9)
    assert matched_cost(trade,costs)==pytest.approx(expected)


def test_missing_bucket_is_not_low_volatility():
    assert bucket(None,[.2,.8])=='UNAVAILABLE'
    assert bucket(.2,[.2,.8])=='<0.8'
