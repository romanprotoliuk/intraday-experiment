"""Offline minute replay with explicit open/close chronology and unchanged risk veto.

    completed prior bars -> opening exits -> pending fills -> reservations
    -> current-bar protective exits -> close mark

No new reservation may use cash released by a later intrabar exit. Barrier time
is reported as an interval, realized at its conservative upper bound.
"""
from collections import Counter,defaultdict
from dataclasses import asdict
from datetime import timedelta

from trader.models import D,Unsafe,dt,iso,down
from trader.configuration import DEFAULTS,fingerprint
from trader.multistrategy.backtest import Costs,quote,size_signal,exit_decision,metrics
from trader.multistrategy.diagnose import matched_cost
from .regimes import classify

VERSION='research-replay-2'


def run(days,tape,strategy,costs=Costs(),*,run_id='phase2',code_commit='uncommitted',miss_every=None):
    cash=D(50);net=D(0);peak=cash;drawdown=D(0);trades=[];daily=[];signals_audit={}
    counts=Counter();rejected=defaultdict(set);reserved=set();filled=set();used=set();decisions=[];fill_number=0
    def reject(reason,sig):counts[reason]+=1;rejected[reason].add(sig.setup_id)
    for day in days:
        session=day.session;start=cash;daynet=D(0);losses=D(0);attempts=0;positions={};pending={};signals={};frames={}

        def close(sym,result,index,intrabar=False):
            nonlocal cash,daynet,losses
            price,reason=result;p=positions.pop(sym);q=D(p['quantity']);fee=D(str(costs.fee_per_side))
            pnl=q*price-D(p['cost'])-D(p['entry_fee'])-fee
            cash+=q*price-fee;daynet+=pnl;losses+=max(D(0),-pnl)
            when=session.open+timedelta(minutes=index+int(intrabar))
            record=dict(p,exit=float(price),exit_at=iso(when),exit_reason=reason,pnl=float(pnl),
                r_multiple=float(pnl/D(p['planned_risk'])),hold_minutes=index+int(intrabar)-p['entry_index'],
                exit_time_lower=iso(session.open+timedelta(minutes=index)),exit_time_upper=iso(when),
                exit_time_exact=not intrabar,estimated_cost=0,estimated_slippage=0)
            record['estimated_cost']=matched_cost(record,asdict(costs))
            slip=D(str(costs.slippage_bps))/10000
            record['estimated_slippage']=float(q*(D(str(p['entry']))-D(str(p['entry']))/(1+slip)+price/(1-slip)-price))
            record['gross_pnl_same_fills']=record['pnl']+record['estimated_cost'];trades.append(record)

        def allocation(now,index):
            exposure=sum(max(D(p['cost']),D(p['quantity'])*D(quote(day.minutes[s][index]['open_price'],now,costs)['ask_price'])) for s,p in positions.items())
            exposure+=sum(D(p['proposal']['notional']) for p in pending.values())
            risk=sum(D(p['planned_risk']) for p in positions.values())+sum(D(p['proposal']['planned_risk']) for p in pending.values())
            power=cash-sum(D(p['proposal']['notional']) for p in pending.values())
            return dict(exposure=exposure,allocated_risk=risk,gross_losses=losses,net_to_date=net+daynet,cash=power)

        for index in range(5,len(day.minutes['SPY'])):
            now=session.open+timedelta(minutes=index)
            if index in tape[session.day]:
                frames=tape[session.day][index]
                for sym,f in sorted(frames.items()):
                    e=strategy.evaluate(f,session);signals[sym]=e.signal
                    counts['SIGNAL' if e.signal else 'STRATEGY:'+e.reason]+=1
                    if e.signal and e.signal.setup_id not in signals_audit:
                        signals_audit[e.signal.setup_id]=e.signal.audit()
            # Opening-only exits cannot consult this minute's high/low/close.
            for sym,p in list(positions.items()):
                bar=dict(day.minutes[sym][index]);op=bar['open_price']
                bar.update(high_price=op,low_price=op,close_price=op)
                result=exit_decision(p,bar,index,session,frames.get(sym),costs)
                marked=allocation(now,index)['exposure']
                if marked>D(30) or losses>=D('1.5'):
                    result=(D(quote(op,now,costs)['bid_price'])*(1-D(str(costs.slippage_bps))/10000),'EXPOSURE_OR_DAILY_LOSS')
                if result:close(sym,result,index)
            for sym,item in list(pending.items()):
                if index<item['due']:continue
                sig=item['signal'];del pending[sym]
                if now>=dt(sig.valid_until) or not session.entry_window(now):reject('FILL:EXPIRED',sig);continue
                if any(float(b['low_price'])<=sig.stop for b in day.minutes[sym][item['index']:index]):reject('FILL:STOP_WHILE_PENDING',sig);continue
                fill_number+=1
                if miss_every and fill_number%miss_every==0:reject('FILL:DECLARED_MISSED_TRADE',sig);continue
                try:
                    proposed=size_signal(sig,day.minutes[sym][index]['open_price'],now,costs,**allocation(now,index))
                    dollars=min(D(proposed['notional']),D(item['proposal']['notional']))
                    q=down(dollars/D(proposed['entry'])*D(str(costs.fill_fraction)))
                    if q<=0:reject('FILL:DECLARED_NO_FILL',sig);continue
                    entry=D(proposed['entry']);fee=D(str(costs.fee_per_side));cost=q*entry
                    risk=q*(entry-D(str(sig.stop))+entry*D(str(costs.slippage_bps))/10000)+2*max(D('.01'),fee)
                    if risk>D('.15') or risk>D(item['proposal']['planned_risk']):raise Unsafe('Fill exceeds reserved risk')
                    cash-=cost+fee;filled.add(sig.setup_id)
                    positions[sym]=dict(run_id=run_id,code_commit=code_commit,replay_version=VERSION,config_fingerprint=fingerprint(DEFAULTS),
                        strategy_id=strategy.id,strategy_version=strategy.version,strategy_fingerprint=strategy.fingerprint,
                        feature_engine_version=frames[sym].version,regime_version=classify(frames[sym]).version,
                        symbol=sym,direction='LONG',day=session.day,setup_id=sig.setup_id,signal_at=sig.signal_at,decision_at=iso(now),
                        regime=list(classify(frames[sym]).labels),entry_reason=sig.reason,features=sig.features,
                        entry_at=iso(now),entry_index=index,proposed_entry=sig.entry,entry=float(entry),quantity=str(q),cost=str(cost),entry_fee=str(fee),
                        stop=str(sig.stop),target=str(entry+D(str(sig.target_r))*(entry-D(str(sig.stop)))),
                        planned_risk=str(risk),deadline=iso(now+timedelta(minutes=sig.max_hold_minutes)),vwap_exit=sig.vwap_exit,
                        estimated_entry_cost=str(q*(entry-D(str(day.minutes[sym][index]['open_price'])))+fee),fill_fraction=costs.fill_fraction,recovery_events=[])
                except Unsafe as exc:reject('FILL:'+str(exc),sig)
            for sym,sig in sorted(signals.items()):
                if not sig or sig.setup_id in used:continue
                if sig.direction!='LONG':reject('CAPABILITY:SHORT_UNSUPPORTED',sig);continue
                if sym in positions or sym in pending:reject('CAPACITY:SYMBOL_OCCUPIED',sig);continue
                if not session.entry_window(now):reject('CAPABILITY:PRODUCTION_ENTRY_WINDOW',sig);continue
                if now>=dt(sig.valid_until):reject('SIGNAL:EXPIRED',sig);continue
                if attempts>=5 or losses>=D('1.5') or len(positions)+len(pending)>=3:reject('RISK:SESSION_CAPACITY',sig);continue
                try:
                    proposed=size_signal(sig,day.minutes[sym][index]['open_price'],now,costs,**allocation(now,index))
                    used.add(sig.setup_id);reserved.add(sig.setup_id);attempts+=1
                    pending[sym]=dict(signal=sig,proposal=proposed,due=index+1+costs.latency_minutes,index=index)
                    decisions.append(dict(day=session.day,symbol=sym,at=iso(now),setup_id=sig.setup_id,reserved_risk=proposed['planned_risk'],reserved_notional=proposed['notional']))
                except Unsafe as exc:reject('RISK:'+str(exc),sig)
            # Only now may this minute's range affect protective exits.
            for sym,p in list(positions.items()):
                result=exit_decision(p,day.minutes[sym][index],index,session,frames.get(sym),costs)
                if result:close(sym,result,index,intrabar=True)
            equity=cash+sum(D(p['quantity'])*D(quote(day.minutes[s][index]['close_price'],now,costs)['bid_price']) for s,p in positions.items())
            peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
            if cash<D(20):raise AssertionError('Buying power reserve violated')
        if positions:raise Unsafe('No data-supported liquidation before session end')
        net+=daynet;daily.append(dict(day=session.day,pnl=float(cash-start),gross_losses=float(losses),attempts=attempts))
    return dict(strategy=strategy.id,fingerprint=strategy.fingerprint,costs=asdict(costs),trades=trades,daily=daily,
        metrics=metrics(trades,daily,float(drawdown)),diagnostics=dict(counts),
        funnel=dict(unique_signals=len(signals_audit),reserved=len(reserved),filled=len(filled),
                    unique_rejections={k:len(v) for k,v in sorted(rejected.items())}),
        signals=list(signals_audit.values()),reservations=decisions,
        limitations=['Synthetic execution; minute data cannot establish quote latency or NBBO',
                    'Intrabar barrier time is interval-censored, realized at minute end',
                    'Independent books are not an executable combined portfolio',
                    'Reused chronological replication is not an untouched test'])
