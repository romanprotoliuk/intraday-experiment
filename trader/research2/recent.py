"""Frozen default-only recent-period replication; no holdout or promotion claim."""
import json
from pathlib import Path
from trader.multistrategy.data import load_vendor
from trader.multistrategy.strategies import STRATEGIES
from .features import build_tape
from .hypotheses import NEW,Hypothesis
from .replay import run,Costs
from .study import save


def study(raw,output):
    output=Path(output);protocol=json.loads((output/'frozen-protocol.json').read_text())
    days,audit=load_vendor(raw,protocol['start'],protocol['end_exclusive'])
    audit.update(source='FirstRate recent public sample',adjustment_basis='Vendor describes split-adjusted; not independently verified',
                 repairs=['Added explicit column header; no OHLCV rows changed'],holdout_untouched=False)
    save(output/'data-audit.json',audit)
    tape=build_tape(days);out={}
    for s in [c() for c in STRATEGIES.values()]+[Hypothesis(k) for k in NEW]:
        out[s.id]={}
        for scenario,cost in [('base',Costs()),('stress',Costs(spread_bps=5,slippage_bps=2,fee_per_side=.01,latency_minutes=1))]:
            r=run(days,tape,s,cost,run_id='recent-replication')
            save(output/(s.id+'-'+scenario+'.json'),r)
            out[s.id][scenario]=dict(metrics=r['metrics'],funnel=r['funnel'])
    save(output/'results.json',dict(protocol=protocol,data=audit,strategies=out))
    print('Recent replication complete; no evidence gate or enablement changed',flush=True)


if __name__=='__main__':study('research/phase2-recent/raw','reports/strategy-phase-2/recent')
