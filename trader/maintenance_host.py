"""Bounded, independently owned host evidence for disarmed code replacement.

Not imported by entry, recovery, readiness or legacy acknowledgment. Never
writes host-health.json, controls, settings or broker state. Caller must retain
the configuration lock and separately prove broker truth and service lifecycle.
"""
import asyncio
from datetime import datetime
import hashlib
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import time

from . import configuration as C
from .doctor import read_db, kv
from .host_health import command, parse_ntp, parse_power, problems, sleep_assertions, require_host
from .legacy_recovery import hold_identity
from .models import UTC, Unsafe, iso
from .store import process_lock


class MaintenanceHost:
    # 15 minutes bounds a controlled stop/start plus the existing five-minute
    # stability check and fresh clock/broker proofs. Not a trading threshold.
    def __init__(self, root, *, lifetime=900):
        if type(lifetime) is not int or not 1 <= lifetime <= 900:
            raise Unsafe('Maintenance assertion requires bounded lifetime')
        self.root=Path(root);self.runtime=self.root/'runtime';self.lifetime=lifetime
        self.process=None;self.lock=None;self.handlers={};self.active=False

    def state(self):
        config=C.load(self.root)
        for name in ('READ_ONLY','MAINTENANCE'):
            p=self.runtime/name
            if not p.is_file() or p.is_symlink():raise Unsafe('Disarmed maintenance controls required')
        if any((self.runtime/n).exists() for n in ('STOP','TRANSITION','LIQUIDATE','ENTRIES_DISABLED')):
            raise Unsafe('Conflicting maintenance control')
        hold=hold_identity(self.runtime)
        db=read_db(self.root)
        try:
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise Unsafe('Database integrity failed')
            settings=kv(db,'settings',{})
            if settings.get('live_enabled') is not False:raise Unsafe('Persistent LIVE must remain disabled')
            if (kv(db,'production_config')!=config or
                kv(db,'config_fingerprint')!=C.fingerprint(config) or
                kv(db,'strategy_fingerprint')!=C.strategy_fingerprint(config) or
                any(settings.get(k)!=v for k,v in C.settings_values(config).items())):
                raise Unsafe('Applied maintenance identity mismatch')
            if (db.execute("SELECT 1 FROM trades WHERE status='OPEN'").fetchone() or
                db.execute("SELECT 1 FROM orders WHERE status NOT IN ('FILLED','CANCELLED','REJECTED','ABORTED')").fetchone()):
                raise Unsafe('Flat resolved local state required')
            account=kv(db,'account_number')
            if not isinstance(account,str) or not config['broker']['account_suffix'] or not account.endswith(config['broker']['account_suffix']):
                raise Unsafe('Pinned account required')
            return config,dict(hold=hold,config=C.fingerprint(config),strategy=C.strategy_fingerprint(config),
                               account_digest=hashlib.sha256(account.encode()).hexdigest())
        finally:db.close()

    def __enter__(self):
        if self.active or self.process is not None:raise Unsafe('Maintenance host context cannot be reused')
        if platform.system()!='Darwin':raise Unsafe('Supported macOS assertion required')
        self.lock=process_lock(self.runtime/'maintenance-host.lock');self.lock.__enter__()
        try:
            self.config,self.binding=self.state();self.owner=os.getpid();self.started=time.monotonic()
            self.process=subprocess.Popen(['/usr/bin/caffeinate','-is','-w',str(self.owner),'-t',str(self.lifetime)],
                                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            # Normal termination cleans up immediately. SIGKILL is covered by
            # caffeinate -w; a hung owner is bounded independently by -t.
            for sig in (signal.SIGTERM,signal.SIGINT):
                self.handlers[sig]=signal.getsignal(sig);signal.signal(sig,self.interrupted)
            self.active=True
            return self
        except BaseException:
            self.close();raise

    def interrupted(self, *_):
        raise Unsafe('Maintenance controller interrupted')

    def alive(self):
        elapsed=time.monotonic()-self.started
        if (not self.active or os.getpid()!=self.owner or not 0<=elapsed<self.lifetime or
            self.process is None or self.process.poll() is not None):
            raise Unsafe('Maintenance assertion expired or owner unavailable')

    async def verify(self):
        """Collect actual observations; never substitute an old worker's proof."""
        self.alive();config,binding=self.state()
        if binding!=self.binding or config!=self.config:raise Unsafe('Maintenance binding changed')
        start=datetime.now(UTC);mono=time.monotonic()
        power,lid,assertions,parent=await asyncio.gather(
            command('/usr/bin/pmset','-g','batt'),
            command('/usr/sbin/ioreg','-r','-k','AppleClamshellState','-d','4'),
            command('/usr/bin/pmset','-g','assertions'),
            command('/bin/ps','-p',str(self.process.pid),'-o','ppid='))
        clock_start=datetime.now(UTC)
        output=await command('/usr/bin/sntp','-t',str(config['clock']['sample_timeout_seconds']),
                             'time.apple.com',timeout=max(10,config['clock']['sample_timeout_seconds']+5))
        end=datetime.now(UTC);elapsed=time.monotonic()-mono
        bound=float(config['clock']['max_combined_bound_ms'])/1000
        record=dict(at=iso(start),scope='DISARMED_MAINTENANCE_ONLY',owner_pid=self.owner,
                    inhibitor_pid=self.process.pid,**parse_power(power),
                    lid_open=bool(re.search(r'"AppleClamshellState"\s*=\s*No\b',lid)),
                    sleep_prevention=parent.strip()==str(self.owner) and sleep_assertions(assertions,self.process.pid,self.owner),
                    collection_valid=0<=elapsed<=config['host']['health_max_age_seconds'] and
                        abs((end-start).total_seconds()-elapsed)<=bound,
                    clock=dict(at=iso(clock_start),**parse_ntp(output)))
        errors=problems(record,end,config)
        if record['ac_power'] is not True:errors.append('Maintenance AC required')
        self.alive()
        if self.state()!=(config,binding):errors.append('Maintenance state changed during collection')
        if errors:raise Unsafe('; '.join(errors))
        return record

    def close(self):
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=3)
        finally:
            self.active=False
            for sig,handler in self.handlers.items():signal.signal(sig,handler)
            self.handlers={}
            if self.lock is not None:
                lock,self.lock=self.lock,None;lock.__exit__(None,None,None)

    def __exit__(self,*_):self.close()


async def guarded_replacement(root, *, preflight, stop, install, start, certify):
    """Caller supplies the existing broker/build and controlled service actions.

    A fresh independent host proof surrounds each destructive lifecycle step.
    Ordinary watchdog-owned host certification is mandatory before handoff.
    On failure no later action runs; the temporary assertion always expires or
    is cleaned up. Hard maintenance controls are never removed by this helper.
    """
    with MaintenanceHost(root) as host:
        first=await host.verify()
        await preflight();await host.verify()
        await stop();await host.verify()
        await preflight();await host.verify()
        await install();await host.verify()
        await start();await host.verify()
        service=await certify()
        await host.verify();require_host(Path(root)/'runtime')
        return dict(maintenance_host=first,service=service)
