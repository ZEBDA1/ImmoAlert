"""Check an exported public snapshot, without printing secret contents."""
from pathlib import Path
import argparse
import json
import re

PRIVATE_PARTS={'.git','.aws','.codex','.agents','.venv','venv','node_modules','storage','__pycache__'}
PRIVATE_SUFFIXES={'.db','.sqlite','.sqlite3','.log','.har','.pem','.key','.p12','.pfx','.dump','.backup'}
PATTERNS={
    'private-key':re.compile(rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----'),
    'github-token':re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})'),
    'telegram-token':re.compile(rb'(?<![0-9])[0-9]{7,12}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])'),
    'aws-access-key':re.compile(rb'AKIA[A-Z0-9]{16}'),
}

def inspect(root):
    if not root.is_dir():return {"files_checked":0,"findings":[{"file":".","kind":"missing-snapshot"}]}
    findings=[];count=0
    for path in sorted(root.rglob('*')):
        if not path.is_file():continue
        relative=path.relative_to(root);name=relative.as_posix();count+=1
        private=(bool(set(relative.parts)&PRIVATE_PARTS) or path.suffix.lower() in PRIVATE_SUFFIXES
            or (path.name.startswith('.env') and path.name!='.env.example')
            or path.name=='immoalert.php' or name.startswith('docs/diagnostics/'))
        if private:findings.append({'file':name,'kind':'private-file'});continue
        for kind,pattern in PATTERNS.items():
            if pattern.search(path.read_bytes()):findings.append({'file':name,'kind':kind})
    if not count:findings.append({'file':'.','kind':'empty-snapshot'})
    return {'files_checked':count,'findings':findings}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True,type=Path);args=parser.parse_args()
    result=inspect(args.root.resolve());print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(bool(result['findings']))
