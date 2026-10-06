"""Read-only ledger reconstruction. Observation time is not exchange event time."""
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
import sqlite3
from pathlib import Path

from .calendar import Calendar
from .models import UTC, NY, D, dt, iso


def reconstruct(root, day, now=None):
    now = now or datetime.now(UTC)
    start = datetime.fromisoformat(day).replace(tzinfo=NY)
    end = start + timedelta(days=1)
    db = sqlite3.connect(f'file:{Path(root)/"runtime/persistent.sqlite3"}?mode=ro', uri=True)
    try:
        db.execute('BEGIN')
        watermark = db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
        row = db.execute('SELECT data FROM sessions WHERE day=?', (day,)).fetchone()
        daily = json.loads(row[0]) if row else {}
        result = dict(day=day, as_of=iso(now), event_watermark=watermark,
                      scope='INTERIM' if now < end else 'HISTORICAL',
                      actual_trades=db.execute('SELECT COUNT(*) FROM trades WHERE day=?', (day,)).fetchone()[0],
                      realized_pnl=daily.get('net'), attempts=daily.get('attempts'),
                      opening_health=dict(passed=daily.get('opening_healthy'), at=daily.get('opening_checked_at')),
                      symbols={}, control_events=[], recovery_events=[])
        totals = {s: Counter() for s in ('SPY','QQQ')}
        digest = hashlib.sha256()
        for eid, at, kind, text in db.execute('SELECT id,at,kind,data FROM events WHERE id<=? ORDER BY id',(watermark,)):
            when = dt(at)
            if not start <= when < end: continue
            digest.update(json.dumps([eid,at,kind,text],separators=(',',':')).encode())
            d = json.loads(text)
            if kind in ('PRODUCTION_TRANSITION','SYSTEM_HOLD_CREATED','SYSTEM_HOLD_PRESERVED',
                        'SYSTEM_RECOVERY_CLEARED','LEGACY_INCIDENT_ACKNOWLEDGED'):
                result['control_events'].append(dict(at=at,kind=kind))
            if kind in ('HEALTH_FAILURE','CONNECTIVITY_RECOVERED','CONNECTIVITY_HALT_RECOVERED'):
                result['recovery_events'].append(dict(at=at,kind=kind,
                    failure_class=d.get('diagnostic',{}).get('failure_class')))
            if kind != 'CANDIDATE_ANALYSIS' or d.get('symbol') not in totals: continue
            symbol=d['symbol']; c=d.get('signal') or {}
            info=result['symbols'].setdefault(symbol,dict(first_observation=at,states=[],
                 first_missed=None,first_observed_nonnegative_extension=None,sized_candidates_blocked_by_kill=[],
                 exact_exchange_trigger_time='NOT_RECORDED',exact_exchange_invalidation_time='NOT_RECORDED'))
            state=c.get('state')
            if not info['states'] or info['states'][-1]['state']!=state:
                info['states'].append(dict(at=at,state=state))
            if state=='MISSED' and info['first_missed'] is None:info['first_missed']=at
            extension=((d.get('features') or {}).get('breakout_extension') or {}).get('dollars')
            if extension is not None and D(extension)>=0 and info['first_observed_nonnegative_extension'] is None:
                info['first_observed_nonnegative_extension']=at
            for key in ('opening_high','opening_low','trigger','invalidation','target','setup_id'):
                if c.get(key) is not None:info[key]=c[key]
            totals[symbol][d.get('final_reason') or 'NO_RECORDED_REASON']+=1
            if d.get('entry') is not None and d.get('final_reason')=='Entry block: KILL':
                info['sized_candidates_blocked_by_kill'].append(dict(at=at,entry=d['entry'],planned_risk=d.get('planned_risk'),capital=d.get('capital')))
            info['last_observation']=at
        for symbol,info in result['symbols'].items():
            info['rejection_reasons']=dict(totals[symbol])
            info['all_other_gates_passed']='NOT_PROVEN: first-failure rejection is not a complete independent gate vector'
            info['kill_only_at_each_candidate']='NOT_RECORDED: runtime snapshots are overwritten; full per-candidate control vectors are absent'
        result['ledger_rows_sha256']=digest.hexdigest()
        result['kill_continuously_present']='NOT_PROVEN: ledger alone does not record every filesystem control observation'
        result['counterfactual']='NOT_COMPUTED: no complete timestamp-aligned executable quotes, risk decisions and fills; no hindsight fill assumptions'
        result['timestamp_note']='Times are durable observation timestamps, not exact crossing times. OHLC cannot establish within-bar event order.'
        return result
    finally:db.close()
