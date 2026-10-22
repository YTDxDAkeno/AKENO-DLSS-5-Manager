"""Evidence contract: API acceptance, GPU completion and presentation are separate facts.

Only the caller may supply process/module-authorized counters. Free text remains a
log observation and never supplies GPU fences or pixel provenance.
"""
from __future__ import annotations
import re
from copy import deepcopy
from akeno.i18n import msg

FAILURES = {
    '0x887A0006': 'device_hung', '0x887A0005': 'device_removed',
    '0x887A0007': 'device_reset', '0x887A0020': 'driver_internal_error',
}


def diagnose(text):
    """Bounded upstream log diagnostics, including errors outside NR-labelled lines."""
    text = text[-4*1024*1024:]
    codes = sorted({c.upper().replace('0X','0x') for c in re.findall(r'0x[0-9a-fA-F]{8}\b', text) if int(c,16)&0x80000000})
    low = text.casefold()
    secure = bool(re.search(r'nvloadsignedlibraryw\(\).*fail|failed to verify.*(?:signature|nvngx_dlssnr)|signature.*(?:fail|invalid)|digitale signatur.*(?:nicht|fehl)', low))
    query_lines = [s[:1000] for s in text.splitlines() if re.search(r'(?:feature\s*(?:=|id\s*[:=])?\s*18|dlss.?nr)',s,re.I) and re.search(r'quer|requirements|capabilit|probe',s,re.I)]
    query = 'not_implemented' if any('0xbad00012' in s.casefold() for s in query_lines) else 'not_observed'
    # A presence test called "model created" by an upstream backend is not an NGX return.
    stalls = [float(v) for v in re.findall(r'NGX evaluate\s+(\d+(?:\.\d+)?)\s*ms', text, re.I)]
    incidents = [{'kind':FAILURES[c], 'code':c} for c in codes if c in FAILURES]
    if re.search(r'(?:did not retire allocator|gpu.*timed? ?out|gpu timeout)',low):incidents.append({'kind':'gpu_timeout_or_allocator_stall'})
    if stalls and max(stalls)>=2000:incidents.append({'kind':'evaluate_stall','duration_ms':max(stalls)})
    fallback = 'reported' if re.search(r'(?:dlss.?nr|neural|feature\s*18).*\bfallback\b|\bfallback\b.*(?:dlss.?nr|neural|feature\s*18)',low) else 'unknown'
    dispatch = []
    if 'dlss-nr forwarder loaded from' in low:dispatch.append('forwarder_module_observed')
    if 'dlss-nr (proxy):' in low:dispatch.append('driver_proxy_log_observed')
    if re.search(r'dlss-nr.*(?:direct.runtime|direct runtime)',low):dispatch.append('direct_runtime_log_observed')
    gpu_lines=[s[:1000] for s in text.splitlines() if re.search(r'DRED|STALL frame|did not retire allocator',s,re.I)][-24:]
    return {'gpu_diagnostics':gpu_lines,'secure_loader':'signature_rejected' if secure else 'not_observed',
            'feature_18_requirements_query':query, 'query_observations':query_lines[-12:],
            'error_codes':codes, 'stability_incidents':incidents, 'consumer_fallback':fallback,
            'dispatch_observations':dispatch, 'dred_observed':'DRED' in text,
            'ngx_evaluate_max_ms':max(stalls) if stalls else None}


def contract(*, process=None, runtime=None, consumer=None, gpu=None, stages=None,
             counters=None, diagnostics=None, duration_s=0, shutdown='unknown', scope='game'):
    stages=stages or {}; counters=counters or {}; diagnostics=diagnostics or diagnose('')
    attributed=bool(stages.get('evaluations_attributed_to_game'))
    created=attributed and (counters.get('create_success',0)>0 or stages.get('feature_18_created',False))
    evaluated=attributed and (counters.get('eval_success',0)>0 or stages.get('feature_evaluated',False))
    diagnostics=deepcopy(diagnostics)
    incidents=diagnostics['stability_incidents']
    if counters.get('eval_stalls'):
        incidents.append({'kind':'diagnostic_evaluate_stall','count':counters['eval_stalls'],'max_us':counters.get('eval_max_us')})
    if incidents:next_action=msg('Stop this test and restore the previous configuration. Keep the exact log, DRED and component hashes; do not change Windows TDR settings.')
    elif diagnostics['secure_loader']=='signature_rejected':next_action=msg('Import a genuinely NVIDIA-authenticated NR runtime from a permitted local installation and repeat the same isolated test. A separate backend result cannot authenticate this rejected library.')
    elif diagnostics['feature_18_requirements_query']=='not_implemented':next_action=msg('The queried dispatcher does not implement Feature 18 on this path. Check the exact runtime, consumer, GPU and driver contract; do not force feature support.')
    elif not counters:next_action=msg('Build or import the pinned optional diagnostic consumer, then start a fresh process observation for exact Feature 18 API results.')
    elif not evaluated:next_action=msg('Inspect the recorded create/evaluate errors for this exact consumer and runtime before changing components.')
    else:next_action=msg('Run repeated offline gameplay tests with this exact configuration and collect matched NR-off/on images. GPU completion and display contribution still need independent measurement.')
    return {'schema':2, 'scope':scope, 'process':process or {}, 'runtime':runtime or {},
            'consumer':consumer or {}, 'gpu':gpu or {}, 'session_duration_s':round(max(0,duration_s),3),
            'runtime_shutdown':shutdown, 'stages':{
                'runtime_module_loaded':bool(stages.get('runtime_loaded')),
                'ngx_initialized':bool(stages.get('ngx_initialized')),
                'feature_created':bool(created), 'evaluation_api_succeeded':bool(evaluated),
                'gpu_execution_completed':False, 'display_contribution_verified':False},
            'api_counters':counters, 'api_evidence_source':'authorized_diagnostic_counters' if counters else ('fresh_process_bound_log_events' if evaluated else 'none'),
            'diagnostics':diagnostics, 'stability':{'state':'failure_observed' if incidents else 'unconfirmed',
                'note':'Elapsed time or one successful evaluate does not establish stable gameplay. No GPU completion or output provenance measurement is provided.'},
            'next_action':next_action}
