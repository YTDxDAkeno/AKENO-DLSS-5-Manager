"""Apply only independent counter instrumentation to the exact pinned GPL backend source.

No upstream installer or downloaded binary is executed. Build with upstream's normal MSBuild recipe.
The generated derivative source stays GPL; ship corresponding source/patch and upstream notices.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess

BASE='fc702f8ca22f97b01bd6a02f3386bffb3484fc35'
BUILD='akeno-fc702f8-v2'
ROOT=Path(__file__).resolve().parents[1]


def wrap_calls(text,name,wrapper):
    # Balanced lexical walk: expressions in this pinned source are ordinary C++ calls.
    matches=list(re.finditer(re.escape(name)+r'\s*\(',text))
    for match in reversed(matches):
        opening=text.index('(',match.start());depth=1;end=opening+1
        while depth:
            if text[end]=='(':depth+=1
            elif text[end]==')':depth-=1
            end+=1
        original=text[match.start():end]
        text=text[:match.start()]+f'AkenoNrDiagnostic::{wrapper}([&]() {{ return '+original+'; })'+text[end:]
    return text,len(matches)


def prepare(source):
    source=Path(source)
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()
    if commit!=BASE:raise ValueError('Diagnostic patch requires exact upstream source '+BASE)
    main=source/'OptiScaler/shaders/dlssnr/DlssNr_Dx12.cpp'
    proxy=source/'OptiScaler/dlssnr/DlssNr_Proxy.cpp'
    records=[]
    for path,include in [(main,'../../dlssnr/AkenoNrDiagnostic.h'),(proxy,'AkenoNrDiagnostic.h')]:
        original=path.read_text(encoding='utf-8-sig')
        if 'AkenoNrDiagnostic' in original:raise ValueError('Source is already instrumented.')
        text='#include "'+include+'"\n'+original
        if path==main:
            text,evaluates=wrap_calls(text,'g_nr.evaluate','Evaluate')
            text,creates=wrap_calls(text,'g_nr.create','Create')
            if evaluates<1 or creates<1:raise ValueError('Expected NR call sites missing.')
        else:
            # Existing driver-proxy call returns, no hook/loader/caller-check changes.
            call='NVNGXProxy::D3D12_EvaluateFeature()(cmdList, g_proxy.feature, params, nullptr);'
            if call not in text:raise ValueError('Expected NR proxy evaluate missing.')
            text=text.replace(call,call+'\n    AkenoNrDiagnostic::RecordEvaluate((unsigned int) result);',1)
            anchor='if (created != NVSDK_NGX_Result_Success || g_proxy.feature == nullptr)'
            text=text.replace(anchor,'AkenoNrDiagnostic::RecordCreate(created == NVSDK_NGX_Result_Success && g_proxy.feature != nullptr);\n        '+anchor,1)
        # Include after pch/Logger: LOG_INFO must be defined at template definition time.
        text=text.replace('#include "'+include+'"\n','',1)
        if '#include <Logger.h>' in text:text=text.replace('#include <Logger.h>','#include <Logger.h>\n#include "'+include+'"',1)
        else:
            position=text.index('\n',text.index('#include "pch.h"'))
            text=text[:position+1]+'#include <Logger.h>\n#include "'+include+'"\n'+text[position+1:]
        path.write_text(text,encoding='utf-8')
        records.append({'path':path.relative_to(source).as_posix(),'base_sha256':hashlib.sha256(original.encode()).hexdigest(),'patched_sha256':hashlib.sha256(text.encode()).hexdigest()})
    header=(ROOT/'diagnostics/optiscaler/AkenoNrDiagnostic.h').read_bytes()
    (source/'OptiScaler/dlssnr/AkenoNrDiagnostic.h').write_bytes(header)
    manifest={'schema':1,'build':BUILD,'upstream':'https://github.com/Skynizz/optiscaler-dlss5','source_commit':BASE,
              'license':'GPL-3.0','files':records,'counter_header_sha256':hashlib.sha256(header).hexdigest(),
              'limitations':['API success counts calls accepted by NGX, not visual quality or GPU completion.','Periodic summaries are observed lower bounds, not exact final totals.','Only selected NR backend call paths are instrumented.']}
    (source/'AKENO_DIAGNOSTIC_SOURCE.json').write_text(json.dumps(manifest,indent=2))
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source');args=p.parse_args()
    print(json.dumps(prepare(args.source),indent=2))
