"""Read-only next-session certification; never equates monitoring with entry."""
from datetime import datetime, timedelta
import json
import hashlib
import os
import platform
from pathlib import Path
import plistlib
import subprocess

from . import configuration as C
from .broker import connect, TokenFile
from .calendar import Calendar
from .doctor import read_db, kv
from .host_health import require_host
from .models import UTC, NY, Unsafe, dt, iso
from .oauth_refresh import credential_health
from .risk import reconcile, TERMINAL


def order_history_digest(orders):
    """Bind complete returned terminal history without storing broker identifiers."""
    if not isinstance(orders,list):raise Unsafe('Order history unavailable')
    canonical=sorted(json.dumps(o,sort_keys=True,separators=(',',':')) for o in orders)
    return hashlib.sha256(json.dumps(canonical,separators=(',',':')).encode()).hexdigest()


def next_session(config, now):
    calendar=Calendar(config)
    # If today's opening is still ahead, certify today. Otherwise next opening.
    for n in range(15):
        probe=now.astimezone(NY)+timedelta(days=n)
        session=calendar.session(probe)
        if session and session.open>now:return session
    raise Unsafe('No next exchange session within calendar horizon')


def launchagent_running(pid):
    for path in (Path.home()/'Library/LaunchAgents').glob('*.plist'):
        try:
            spec=plistlib.loads(path.read_bytes());label=spec['Label']
            result=subprocess.run(['launchctl','print',f'gui/{os.getuid()}/{label}'],capture_output=True,text=True,timeout=3)
            if result.returncode==0 and f'pid = {pid}\n' in result.stdout and 'state = running' in result.stdout:return True
        except (OSError,ValueError,KeyError,subprocess.TimeoutExpired):continue
    return False


async def control_snapshot(root, *, now=None, broker_check=True):
    """Flat maintenance evidence without quotes/opening gates. No control writes."""
    clock=now or (lambda:datetime.now(UTC));root=Path(root);r=root/'runtime'
    checks={};result={'checks':checks,'at':iso(clock()),'read_only':True}
    def check(name,value):checks[name]=bool(value)
    try:
        config=C.load(root);result['config_fingerprint']=C.fingerprint(config)
        result['strategy_fingerprint']=C.strategy_fingerprint(config)
        check('platform',platform.system()=='Darwin')
        check('config_permissions',C.path(root).stat().st_mode&0o077==0)
        check('runtime_permissions',r.stat().st_mode&0o077==0)
        db=read_db(root)
        try:
            check('database',db.execute('PRAGMA quick_check').fetchone()[0]=='ok')
            check('configuration',kv(db,'production_config')==config and kv(db,'config_fingerprint')==C.fingerprint(config))
            check('strategy',kv(db,'strategy_fingerprint')==C.strategy_fingerprint(config))
            settings=kv(db,'settings',{});result['live_enabled']=settings.get('live_enabled') is True
            check('risk',all(settings.get(k)==v for k,v in C.settings_values(config).items()))
            positions=db.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'").fetchone()[0]
            orders=db.execute("SELECT COUNT(*) FROM orders WHERE status NOT IN ('FILLED','CANCELLED','REJECTED','ABORTED')").fetchone()[0]
            result.update(local_positions=positions,unresolved_orders=orders)
            check('local_flat',positions==0 and orders==0)
            account=kv(db,'account_number');worker=kv(db,'worker_identity',{})
            h=json.loads((r/'heartbeat.json').read_text());w=json.loads((r/'supervisor-health.json').read_text());checked=clock()
            result['generation']=worker.get('generation')
            check('ownership',bool(worker.get('generation')) and worker.get('generation')==w.get('worker_generation') and
                  worker.get('pid')==h.get('pid')==w.get('worker_pid') and worker.get('supervisor_pid')==w.get('pid'))
            check('worker_stable',0<=(checked-dt(worker['started_at'])).total_seconds() and
                  (checked-dt(worker['started_at'])).total_seconds()>=config['connectivity']['recovery_min_seconds'])
            check('heartbeat',0<=checked.timestamp()-h['at']<=config['watchdog']['heartbeat_stale_seconds'] and h.get('state') in ('POLLING','HEALTHY','ENTRIES_BLOCKED'))
            check('watchdog',w.get('state')=='RUNNING' and 0<=checked.timestamp()-w['at']<=config['host']['health_max_age_seconds'] and w.get('config_fingerprint')==C.fingerprint(config))
            os.kill(h['pid'],0);os.kill(w['pid'],0);check('processes',True)
            check('launchagent',launchagent_running(w['pid']))
            require_host(r,clock);check('host_clock',True)
            host=json.loads((r/'host-health.json').read_text())
            result['host']={k:host.get(k) for k in ('ac_power','lid_open','sleep_prevention')}
            result['clock']=dict(at=host['clock']['at'],
                combined_bound_ms=1000*(abs(float(host['clock']['offset_seconds']))+float(host['clock']['uncertainty_seconds'])),
                threshold_ms=config['clock']['max_combined_bound_ms'])
            check('ac_power',host.get('ac_power') is True)
            result['controls']=[name for name in ('KILL','MAINTENANCE','STOP','TRANSITION','LIQUIDATE','READ_ONLY','ENTRIES_DISABLED') if (r/name).exists()]
            check('no_other_control',not set(result['controls'])-{'KILL'})
            result['current_health']=kv(db,'health',{})
            # Only a safe subset; no account, order identifiers or exception text.
            result['current_health']={k:result['current_health'].get(k) for k in ('at','auth','reconciled','data_healthy')}
            health=result['current_health']
            check('worker_auth',health.get('auth')=='AUTHENTICATED_READ_VERIFIED' and health.get('reconciled') is True and
                  bool(health.get('at')) and 0<=(clock()-dt(health['at'])).total_seconds()<=config['connectivity']['account_max_age_seconds'])
            row=db.execute('SELECT data FROM sessions WHERE day=?',(checked.astimezone(NY).date().isoformat(),)).fetchone()
            day=json.loads(row[0]) if row else {}
            result['daily']={k:day.get(k) for k in ('day','attempts','loss_budget','gross_losses','loss_latched','closed','entry_cutoff_latched','opening_healthy')}
            result['recovery']={k:day.get(k) for k in ('recovery_active','connectivity_halt','outage_active','outages')}
            since=day.get('last_failure') or day.get('stable_since') or worker.get('started_at')
            check('quiet_period',since is not None and (checked-dt(since)).total_seconds()>=config['connectivity']['recovery_min_seconds'])
            if not broker_check:check('broker',False)
            else:
                async with connect(r/'oauth.json') as broker:
                    if broker.live_guard is not None or broker.cancel_guard is not None:raise Unsafe('Read-only broker capability required')
                    accounts=await broker.call('get_accounts',{})
                    matches=[a for a in accounts['accounts'] if a and a.get('agentic_allowed') and a['account_number'].endswith(config['broker']['account_suffix'])]
                    if not account or len(matches)!=1 or matches[0]['account_number']!=account:raise Unsafe('Expected account mismatch')
                    snap=await broker.snapshot(account)
                    power=reconcile(snap,account,clock(),C.market_config(config),{})
                    if snap['positions'] or any(o.get('state') not in TERMINAL for o in snap['orders']):raise Unsafe('Broker flat required')
                    result['broker']=dict(authenticated=True,expected_account=True,reconciled=True,positions=0,orders=0,
                                          buying_power=str(power),observed_at=snap['observed_at'],order_history_digest=order_history_digest(snap['orders']))
                    check('broker',True)
            # Re-read after awaits. This snapshot is NOT itself clearance authority.
            check('local_flat',not db.execute("SELECT 1 FROM trades WHERE status='OPEN'").fetchone() and
                  not db.execute("SELECT 1 FROM orders WHERE status NOT IN ('FILLED','CANCELLED','REJECTED','ABORTED')").fetchone())
            check('configuration',C.load(root)==config and kv(db,'production_config')==config)
        finally:db.close()
    except Exception as exc:
        check('evidence_complete',False);result['error_type']=type(exc).__name__
    result['ended_at']=iso(clock());result['passed']=bool(checks) and all(checks.values())
    return result


def summarize(snapshot, *, controls, auth, reset_valid, next_session_info, recovery_class):
    """Pure readiness policy; no mutation capability and no broker entry permission."""
    checks=snapshot['checks']
    infra=('platform','config_permissions','runtime_permissions','database','configuration','strategy','risk','ownership','heartbeat','watchdog','processes','launchagent','host_clock','ac_power')
    infrastructure=all(checks.get(k) is True for k in infra)
    monitoring=infrastructure and checks.get('broker') is True and checks.get('worker_auth') is True
    reasons=[k+' failed/unavailable' for k,v in checks.items() if v is not True]
    for k in (*infra,'broker','worker_auth','local_flat','worker_stable','quiet_period','no_other_control'):
        if k not in checks:reasons.append(k+' evidence missing')
    if not snapshot.get('live_enabled'):reasons.append('Persistent LIVE disabled')
    manual_controls=[control for control in controls if not (control=='KILL' and recovery_class=='B')]
    if manual_controls:reasons.append('Persistent control requires resolution: '+','.join(manual_controls))
    if not auth.get('expiry_known') or not (auth.get('covers_session') or auth.get('refresh_proven')):
        reasons.append('Authentication lifetime/refresh through next session not proven')
    if not all(auth.get(k) for k in ('refresh_present','client_present','refresh_proven')) or auth.get('refresh_blocked'):
        reasons.append('Unattended credential refresh not operationally proven')
    if not reset_valid:reasons.append('Next-session initial state inconsistent')
    if recovery_class not in ('NORMAL','B'):reasons.append('Recovery requires operator investigation')
    unattended=monitoring and not reasons
    return dict(INFRASTRUCTURE_READY=infrastructure,MONITORING_READY=monitoring,
                UNATTENDED_LIVE_READY=unattended,operator_action_required=reasons,
                next_session=next_session_info,token_refresh_health=auth,recovery_class=recovery_class,
                automatic_recovery_pending=recovery_class=='B' and 'KILL' in controls,
                entry_eligible_now=False,
                note='Readiness describes known prerequisites, not permission, a trade guarantee, or future health certification')


async def certify(root, *, broker_check=True, now=None):
    clock=now or (lambda:datetime.now(UTC));root=Path(root)
    snapshot=await control_snapshot(root,now=clock,broker_check=broker_check)
    try:
        config=C.load(root);session=next_session(config,clock())
        auth=credential_health(TokenFile(root/'runtime/oauth.json'),session.close.timestamp())
        # A refresh token's existence is not evidence that unattended refresh works.
        # Current access lifetime can certify one session; long-term proof is separate.
        db=read_db(root)
        try:
            row=db.execute('SELECT data FROM sessions WHERE day=?',(session.day,)).fetchone()
            day=json.loads(row[0]) if row else None
            reset=day is None or (day.get('day')==session.day and day.get('attempts')==0 and
                not any(day.get(k) for k in ('loss_latched','closed','entry_cutoff_latched','opening_healthy','liquidation_due')) and
                day.get('gross_losses')=='0' and day.get('recovery_active') is True)
            from .legacy_recovery import classify_hold
            classification=classify_hold(root,db,clock())
        finally:db.close()
        info=dict(day=session.day,open=iso(session.open),opening_complete=iso(session.open+timedelta(minutes=session.opening_minutes)),
                  entry_cutoff=iso(session.close-timedelta(minutes=session.entry_cutoff_minutes)),
                  liquidation=iso(session.close-timedelta(minutes=session.liquidation_minutes)),close=iso(session.close))
        result=summarize(snapshot,controls=snapshot.get('controls',['UNKNOWN']),auth=auth,reset_valid=reset,
                         next_session_info=info,recovery_class=classification)
        result['snapshot']=snapshot;result['next_session_reset']='PENDING_NORMAL_CREATION' if day is None else 'VERIFIED' if reset else 'INVALID'
        return result
    except Exception as exc:
        return dict(INFRASTRUCTURE_READY=False,MONITORING_READY=False,UNATTENDED_LIVE_READY=False,
                    operator_action_required=['Next-session evidence unavailable'],error_type=type(exc).__name__,snapshot=snapshot)
