"""Only synthetic test/audit evidence; no actual build is certified by fixtures."""
import hashlib,json
import pytest
from trader import build_verification as V

@pytest.fixture
def evidence(tmp_path,monkeypatch):
    report=tmp_path/'reports';report.mkdir()
    test=report/'suite.xml';test.write_text('<testsuites><testsuite tests="2" failures="0" errors="0" skipped="0"/></testsuites>')
    audit=report/'audit.json';audit.write_text(json.dumps(dict(commit='synthetic',secret_audit='PASS',runtime_audit='PASS',history_audit='PASS',findings=[])))
    def item(p):return dict(path='reports/'+p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    record=dict(commit='synthetic',tree='tree',exit_code=0,tests=item(test),audit=item(audit),private_remote_ref='refs/remotes/origin/engineering/synthetic',private_repository='xenonmatrix/intraday-experiment')
    path=report/'production-verification.json';path.write_text(json.dumps(record))
    for p in (test,audit,path):p.chmod(0o600)
    def git(args,**kwargs):
        if args[-2:]==['status','--porcelain']:return ''
        return 'tree' if args[-1]=='HEAD^{tree}' else 'synthetic'
    monkeypatch.setattr(V.subprocess,'check_output',git)
    return tmp_path,path,record,test,audit


def test_bound_clean_tested_build(evidence):
    root,_,_,_,_=evidence
    assert V.verify(root)['passed'] and V.verify(root)['tests']==2

@pytest.mark.parametrize('fault',['missing','permissions','commit','tree','exit','test_tamper','audit_tamper','public_repository','outside_path','symlink','skipped','failures','errors','empty','audit_fail'])
def test_missing_or_forged_verification_never_reports_engineering_pass(evidence,fault):
    root,path,record,test,audit=evidence
    if fault=='missing':path.unlink()
    elif fault=='permissions':path.chmod(0o644)
    elif fault=='test_tamper':test.write_text('changed')
    elif fault=='audit_tamper':audit.write_text('{}')
    elif fault=='symlink':path.unlink();path.symlink_to(test)
    else:
        if fault in ('commit','tree'):record[fault]='wrong'
        elif fault=='exit':record['exit_code']=1
        elif fault=='public_repository':record['private_repository']='xenonmatrix/intraday-research'
        elif fault=='outside_path':record['tests']['path']='../outside'
        elif fault in ('skipped','failures','errors','empty'):
            test.write_text('<testsuites><testsuite tests="'+('0' if fault=='empty' else '2')+'" '+('failures="0"' if fault=='empty' else fault+'="1"')+'/></testsuites>')
            record['tests']['sha256']=hashlib.sha256(test.read_bytes()).hexdigest()
        elif fault=='audit_fail':
            audit.write_text(json.dumps(dict(commit='synthetic',secret_audit='FAIL',runtime_audit='PASS',history_audit='PASS',findings=[])))
            record['audit']['sha256']=hashlib.sha256(audit.read_bytes()).hexdigest()
        path.write_text(json.dumps(record))
    assert not V.verify(root)['passed']
