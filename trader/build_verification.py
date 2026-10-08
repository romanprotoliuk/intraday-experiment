"""Read-only local test/audit evidence. Never an entry or control authority.

The operator's test runner writes this protected report after verification. The
reader validates artifacts and the exact clean source tree; missing evidence is
not a pass. No broker, token, ledger or production-control access.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
from .models import Unsafe


def verify(root):
    root=Path(root)
    try:
        def git(*args):return subprocess.check_output(['git','-C',str(root),*args],text=True,stderr=subprocess.DEVNULL).strip()
        commit=git('rev-parse','HEAD')
        if git('status','--porcelain'):raise Unsafe('Working tree not clean')
        path=root/'reports/production-verification.json'
        def protected(p):
            if p.is_symlink() or not p.is_file() or p.stat().st_uid!=os.getuid() or p.stat().st_mode&0o077:
                raise Unsafe('Protected verification artifact required')
            return p.read_bytes()
        record=json.loads(protected(path))
        if record['commit']!=commit or record['tree']!=git('rev-parse','HEAD^{tree}') or record['exit_code']!=0:
            raise Unsafe('Tested build mismatch')
        artifacts={}
        for name in ('tests','audit'):
            relative=Path(record[name]['path'])
            if relative.is_absolute() or '..' in relative.parts or relative.parts[0]!='reports':raise Unsafe('Invalid verification path')
            data=protected(root/relative)
            if hashlib.sha256(data).hexdigest()!=record[name]['sha256']:raise Unsafe('Verification artifact changed')
            artifacts[name]=data
        xml=ET.fromstring(artifacts['tests']);suites=[xml] if xml.tag=='testsuite' else list(xml.iter('testsuite'))
        count=sum(int(x.attrib['tests']) for x in suites)
        if count<=0 or any(int(x.attrib.get(k,0)) for x in suites for k in ('failures','errors','skipped')):
            raise Unsafe('Complete passing tests required')
        audit=json.loads(artifacts['audit'])
        if audit.get('commit')!=commit or audit.get('findings') or any(audit.get(k)!='PASS' for k in ('secret_audit','runtime_audit','history_audit')):
            raise Unsafe('Passing exact-build audits required')
        remote=record['private_remote_ref']
        if not remote.startswith('refs/remotes/origin/') or git('rev-parse',remote)!=commit or record.get('private_repository')!='xenonmatrix/intraday-experiment':
            raise Unsafe('Private remote verification mismatch')
        return dict(passed=True,commit=commit,tests=count,failures=0,scope='Local operator verification; not entry authority')
    except (OSError,ValueError,KeyError,TypeError,IndexError,Unsafe,ET.ParseError,subprocess.SubprocessError) as exc:
        return dict(passed=False,reason=str(exc) if isinstance(exc,Unsafe) else 'Build verification missing or malformed')
