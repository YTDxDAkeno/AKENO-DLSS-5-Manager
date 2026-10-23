"""Experiment eligibility is distinct from expert review and execution verification."""
from __future__ import annotations
import json
import re
from urllib.parse import urlsplit
from akeno.community import candidates

GENERATIONS = ('RTX 20', 'RTX 30', 'RTX 40', 'RTX 50')


def validate_profile(data):
    if not isinstance(data, dict) or data.get('schema') != 3:
        raise ValueError('Unsupported experimental profile schema.')
    if not re.fullmatch(r'[a-f0-9]{64}', data.get('sha256', '')) or not data.get('version'):
        raise ValueError('An exact runtime hash and version are required.')
    source = urlsplit(data.get('runtime_source', ''))
    if source.scheme != 'https' or not source.hostname or source.username or source.password or not source.path.strip('/'):
        raise ValueError('Record an explicit HTTPS source page; no file is downloaded.')
    if data.get('local_use_permitted') is not True:
        raise ValueError('Local reuse permission must be explicitly confirmed.')
    named = candidates.by_hash(data['sha256'])
    if named and (data.get('candidate_id') != named.id or data['runtime_source'] != named.runtime_source):
        raise ValueError('The known modified runtime must retain its exact source identity.')
    for key in ('claimed_generations', 'validated_generations', 'incompatible_generations'):
        values = data.get(key, [])
        if not isinstance(values, list) or any(x not in GENERATIONS for x in values):
            raise ValueError('Invalid GPU architecture inventory.')
    if data.get('validated_generations') and not data.get('execution_evidence'):
        raise ValueError('Validated older-GPU profiles require independent execution evidence.')
    if not data.get('backends') or any(x not in ('optiscaler_nr', 'dlss5_feeder', 'renodx_nr') for x in data['backends']):
        raise ValueError('Declare applicable neural backends.')
    if not data.get('apis') or any(x not in ('d3d11', 'd3d12', 'vulkan') for x in data['apis']):
        raise ValueError('Declare applicable rendering APIs.')
    minimum = data.get('minimum_driver')
    if minimum and not re.fullmatch(r'\d+\.\d+', minimum):
        raise ValueError('Invalid minimum driver version.')
    return json.loads(json.dumps(data))


def local_profile(evaluation, source, permitted):
    named = candidates.by_hash(evaluation.sha256)
    return validate_profile({'schema': 3, 'sha256': evaluation.sha256, 'version': evaluation.version,
        'candidate_id': named.id if named else None,
        'runtime_source': named.runtime_source if named else source,
        'local_use_permitted': permitted, 'claimed_generations': ['RTX 40'] if named else [],
        'validated_generations': [], 'incompatible_generations': [], 'tested_gpus': [],
        'execution_evidence': [], 'minimum_driver': None,
        'backends': ['optiscaler_nr', 'dlss5_feeder', 'renodx_nr'], 'apis': ['d3d11', 'd3d12', 'vulkan'],
        'status': 'unverified', 'limitations': ['No malware audit or successful GPU execution is claimed.',
            'Modified/invalid signatures are not NVIDIA authentication. Keep Windows protections enabled.']})


def eligibility(profile, gpu, backend, api):
    p = validate_profile(profile)
    if gpu.generation in p.get('incompatible_generations', []) or gpu.device_id in p.get('incompatible_device_ids', []):
        raise PermissionError('This runtime profile records this GPU as incompatible.')
    if gpu.generation not in GENERATIONS:
        raise PermissionError('A recognized RTX generation is required.')
    if gpu.generation in ('RTX 20', 'RTX 30') and gpu.generation not in p.get('validated_generations', []):
        raise PermissionError('RTX 20/30 require an independently validated applicable community profile.')
    if backend not in p['backends'] or api not in p['apis']:
        raise PermissionError('The selected backend/renderer is outside this runtime profile.')
    minimum = p.get('minimum_driver')
    if minimum and tuple(map(int, gpu.driver_version.split('.'))) < tuple(map(int, minimum.split('.'))):
        raise PermissionError('The selected runtime requires driver ' + minimum + ' or newer.')
    return 'Experimental — execution not yet verified on this configuration'
