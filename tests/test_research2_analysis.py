from trader.research2.supplement import block_interval,feature_correlations,overlaps


def test_event_interval_retains_constant_daily_response_and_seed():
    days=[f'2023-01-{i:02}' for i in range(1,26)]
    rows=[dict(day=d,returns={'30':dict(net_bps=2)}) for d in days]
    a=block_interval(rows,days)
    assert a==block_interval(rows,days) and a['lower']==2 and a['upper']==2
    assert block_interval(rows[:19],days)['lower'] is None


def test_overlap_requires_same_symbol_and_positive_time_overlap():
    a=dict(symbol='SPY',entry_at='2023-01-03T15:00:00Z',exit_at='2023-01-03T15:05:00Z')
    b={**a,'entry_at':a['exit_at'],'exit_at':'2023-01-03T15:10:00Z'}
    c={**a,'symbol':'QQQ'}
    assert overlaps({'a':{'trades':[a]},'b':{'trades':[b,c]}})['a']['b']==0


def test_unknown_feature_correlations_remain_unavailable():
    protocol=dict(train=['2023-01-01','2023-02-01'],validation=['2023-02-01','2023-03-01'],replication_test=['2023-03-01','2023-04-01'])
    out=feature_correlations([],protocol)
    assert out['LONG']['premarket_range']['train']==dict(n=0,pearson=None)
