"""Strict structured counter protocol emitted only by the identified diagnostic backend."""
import re

BUILD='akeno-fc702f8-v3'
BUILDS=(BUILD,'akeno-fc702f8-v2')
FIELDS=('pid','birth','seq','create_attempts','create_success','create_fail','eval_attempts','eval_success','eval_fail')


def parse_counter(line):
    pos=line.find('AKENO_NR_V1 ')
    if pos<0 or len(line)>4096:return None
    text=line[pos+len('AKENO_NR_V1 '):]
    pairs=re.findall(r'(\w+)=([^\s]+)',text)
    values=dict(pairs)
    if len(values)!=len(pairs):return None
    if values.get('build') not in BUILDS:return None
    if any(not re.fullmatch(r'\d{1,20}',values.get(k,'')) for k in FIELDS):return None
    record={k:int(values[k]) for k in FIELDS}
    if any(v>2**64-1 for v in record.values()):return None
    if record['create_success']+record['create_fail']>record['create_attempts'] or record['eval_success']+record['eval_fail']>record['eval_attempts']:return None
    if record['pid']<=0 or record['birth']<=0 or record['seq']<=0:return None
    code=values.get('last_result','')
    if not re.fullmatch(r'0x[0-9a-fA-F]{1,8}',code):return None
    last_error=values.get('last_error')
    if last_error is not None and not re.fullmatch(r'0x[0-9a-fA-F]{1,8}',last_error):return None
    optional={}
    for key in ('eval_max_us','eval_stalls','forwarder_evals','proxy_evals'):
        if key not in values:continue
        if not re.fullmatch(r'\d{1,20}',values[key]) or int(values[key])>2**64-1:return None
        optional[key]=int(values[key])
    return record|optional|{'last_error':last_error,'build':values['build'],'last_result':code,'scope':'NR API return results; accepted calls do not prove visual quality or GPU completion.'}


def attributed(record,process,allowed_builds):
    return bool(record and process and record['build'] in allowed_builds and
                record['pid']==process.get('pid') and 'filetime:'+str(record['birth'])==process.get('created_at'))


def monotonic_records(records):
    """Validate one authorized stream. Totals are snapshots, never additive."""
    latest={}
    totals=FIELDS[3:]
    for record in records:
        if latest and (record['seq']<=latest['seq'] or any(record[k]<latest[k] for k in totals)):
            raise ValueError('Diagnostic counters reset or decreased; a fresh process session is required.')
        latest=record
    return latest
