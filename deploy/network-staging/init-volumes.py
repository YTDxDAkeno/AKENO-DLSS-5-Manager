"""Runs only in staging-init with three fresh named volumes; no production mounts."""
import os
from pathlib import Path
for name in ("keys","mail","network"):
    p=Path("/data")/name
    if p.is_symlink() or not p.is_dir():raise SystemExit("STAGING_VOLUME_REFUSED")
    if any(p.iterdir()):
        if p.stat().st_uid!=10001 or (p.stat().st_mode&0o777)!=0o700:
            raise SystemExit("NONEMPTY_VOLUME_OWNER_REFUSED")
    else:
        os.chown(p,10001,10001);os.chmod(p,0o700)
print("STAGING_VOLUME_OWNERSHIP_OK")
