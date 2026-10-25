"""Build a shared-hosting upload without runtime data, secrets or Node on Plesk."""
from __future__ import annotations

import argparse
from hashlib import sha256
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED


def package(frontend: Path,output: Path):
    root=Path(__file__).resolve().parents[1]
    output=output.resolve();output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="immoalert-plesk-") as temporary:
        stage=Path(temporary)
        environment={**os.environ,"VITE_API_TRANSPORT":"php"}
        subprocess.run([str(frontend/"node_modules/.bin/tsc"),"--noEmit"],cwd=frontend,env=environment,check=True)
        subprocess.run([str(frontend/"node_modules/.bin/vite"),"build","--outDir",str(stage/"httpdocs")],cwd=frontend,env=environment,check=True)
        shutil.copytree(root/"deploy/plesk/httpdocs",stage/"httpdocs",dirs_exist_ok=True)
        shutil.copytree(root/"deploy/plesk/private",stage/"private")
        shutil.copyfile(root/"docs/PLESK.md",stage/"INSTALLATION-PLESK.md")
        files=sorted(path for path in stage.rglob("*") if path.is_file())
        (stage/"SHA256SUMS").write_text("".join(sha256(path.read_bytes()).hexdigest()+"  "+path.relative_to(stage).as_posix()+"\n" for path in files))
        with ZipFile(output,"w",ZIP_DEFLATED) as archive:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    name=path.relative_to(stage).as_posix()
                    if path.suffix in {".db",".sqlite",".sqlite3",".log"} or path.name==".env" or name=="private/immoalert.php":
                        raise RuntimeError("Runtime file unexpectedly entered the upload")
                    archive.write(path,name)
    digest=sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(digest+"  "+output.name+"\n")
    print("Plesk upload:",output)
    print("SHA-256:",digest)


if __name__=="__main__":
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser()
    parser.add_argument("--frontend",type=Path,default=root/"web")
    parser.add_argument("--output",type=Path,default=root/"dist/immoalert-plesk.zip")
    args=parser.parse_args()
    package(args.frontend.resolve(),args.output)
