import importlib.util
from pathlib import Path
import subprocess

spec=importlib.util.spec_from_file_location('repository_check',Path(__file__).parents[1]/'ops/repository_check.py')
scanner=importlib.util.module_from_spec(spec);spec.loader.exec_module(scanner)


def git(root,*args):
    subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True)


def test_index_scan_detects_secret_removed_only_from_worktree(tmp_path):
    git(tmp_path,'init','-q')
    p=tmp_path/'settings.txt';p.write_text('Bearer '+('a'*40))
    git(tmp_path,'add','settings.txt');p.write_text('redacted')
    assert scanner.audit(tmp_path)['passed']
    assert not scanner.audit(tmp_path,staged=True)['passed']


def test_forced_private_file_is_rejected(tmp_path):
    git(tmp_path,'init','-q');(tmp_path/'.gitignore').write_text('runtime/\n')
    (tmp_path/'runtime').mkdir();(tmp_path/'runtime/state.json').write_text('{}')
    git(tmp_path,'add','-f','runtime/state.json')
    report=scanner.audit(tmp_path,staged=True)
    assert not report['passed']
    assert any(f['category']=='ignored_path_in_index' for f in report['findings'])


def test_binary_and_symlink_are_not_silently_skipped(tmp_path):
    (tmp_path/'blob').write_bytes(bytes([255,254]))
    (tmp_path/'link').symlink_to(tmp_path/'blob')
    report=scanner.audit(tmp_path)
    assert {f['category'] for f in report['findings']}=={'unreviewed_binary','unreviewed_symlink'}
