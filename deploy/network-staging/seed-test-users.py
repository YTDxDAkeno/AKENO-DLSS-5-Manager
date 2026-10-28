"""Future one-time synthetic staging users. Receives passwords via stdin; never prints them."""
import json,sys
from datetime import timedelta
from sqlalchemy import select,func
from entrypoint import prepare_environment

def main():
    prepare_environment()
    from akeno_server.db import transaction
    from akeno_server.models import User,Role,LicenseType
    from akeno_server.auth.service import create_user
    from akeno_server.licensing.service import ensure_license
    from akeno_server.licensing.signing import store
    from akeno_server.clock import utcnow
    store().active()  # refuse until a new staging-only license key exists
    raw=sys.stdin.buffer.read(16385)
    if len(raw)>16384:raise SystemExit("STAGING_INPUT_REFUSED")
    values=json.loads(raw)
    if not isinstance(values,dict) or set(values)!={"pc_a_password","pc_b_password"}:
        raise SystemExit("STAGING_FIELDS_REFUSED")
    if any(not isinstance(x,str) or not 16<=len(x)<=128 for x in values.values()):
        raise SystemExit("STAGING_PASSWORD_LENGTH_REFUSED")
    with transaction() as db:
        if db.scalar(select(func.count()).select_from(User))!=0:
            raise SystemExit("STAGING_USERS_ALREADY_EXIST_NOTHING_CHANGED")
        for label,field in (("pc-a","pc_a_password"),("pc-b","pc_b_password")):
            user=create_user(db,label+"@akeno-staging.invalid",values[field],label,"de",role=Role.USER,verified=True)
            lic,_=ensure_license(db,user,LicenseType.MONTHLY)
            lic.expires_at=utcnow()+timedelta(days=7);lic.device_limit=1
    print("TWO_SYNTHETIC_STAGING_USERS_CREATED_SEVEN_DAY_LICENSES_NO_ADMIN")

if __name__=="__main__":
    try:main()
    except Exception:
        # Upstream exceptions may contain input values. Suppress traceback/error details.
        print("STAGING_SEED_FAILED_NO_DETAILS_PRINTED",file=sys.stderr)
        raise SystemExit(1)
