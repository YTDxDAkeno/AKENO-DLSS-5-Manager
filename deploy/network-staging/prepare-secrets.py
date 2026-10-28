#!/usr/bin/env python3
"""Future activation step only. Creates new staging secrets, refuses an existing runtime directory."""
import os,secrets,json
from pathlib import Path

def main():
    if os.geteuid()!=0:
        raise SystemExit("Run as root only after staging activation approval.")
    root=Path(__file__).resolve().parent/"runtime"
    if root.exists() or root.is_symlink():
        raise SystemExit("REFUSED: runtime already exists; nothing overwritten")
    os.umask(0o077)
    root.mkdir(mode=0o700)
    directory=root/"secrets";directory.mkdir(mode=0o700)
    password=secrets.token_urlsafe(48)
    values={"token":secrets.token_urlsafe(48),"pepper":secrets.token_urlsafe(48),
            "db-app":password,"db-pg":password}
    for name,value in values.items():
        fd=os.open(directory/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
        with os.fdopen(fd,"w") as f:
            f.write(value+"\n");f.flush();os.fsync(f.fileno())
        # postgres:16-alpine uses uid 70; recheck before activation if image changes.
        os.chown(directory/name,70 if name=="db-pg" else 10001,70 if name=="db-pg" else 10001)
    (root/"STAGING_ONLY.json").write_text(json.dumps({"project":"akeno-network-staging","production_data_imported":False})+"\n")
    print("STAGING_SECRETS_CREATED_NO_VALUES_PRINTED")

if __name__=="__main__":main()
