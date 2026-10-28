#!/usr/bin/env python3
"""Read-only local monitor for the future isolated stack; no startup, login or repair."""
import json,subprocess,urllib.request,datetime
from zoneinfo import ZoneInfo
result={'at_berlin':datetime.datetime.now(ZoneInfo('Europe/Berlin')).isoformat(),'project':'akeno-network-staging'}
r=subprocess.run(['docker','ps','-a','--filter','label=com.docker.compose.project=akeno-network-staging','--format','{{.Names}}|{{.Status}}|{{.Ports}}'],capture_output=True,text=True)
result['containers']=r.stdout.strip().splitlines();result['docker_query_ok']=r.returncode==0
for endpoint in ['health','ready']:
    try:
        with urllib.request.urlopen('http://127.0.0.1:18510/api/v1/system/'+endpoint,timeout=4) as response:
            result[endpoint]={'http':response.status,'result':json.load(response)}
    except Exception as e:result[endpoint]={'error_type':type(e).__name__}
print(json.dumps(result,ensure_ascii=False,indent=2))
raise SystemExit(0 if result.get('ready',{}).get('http')==200 else 1)
