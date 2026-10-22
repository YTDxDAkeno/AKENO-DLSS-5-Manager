"""Matched-image guidance and portable local HTML; no invented image-quality metrics."""
from pathlib import Path
import html
import json
import shutil
import uuid
from akeno.paths import app_home
from akeno.core.db import now_iso
from akeno.security.downloads import sha256_file
from akeno.diagnostics.privacy import redact


class VisualComparisons:
    def __init__(self,services):self.s=services

    def create(self,game_id,paths,conditions,notes='',contexts=None):
        if set(paths)!={'off','on'}:raise ValueError('Choose both real NR-off and NR-on screenshots.')
        if not all(conditions.get(k,'').strip() for k in ('viewpoint','resolution','settings','renderer')):raise ValueError('Record matching camera, resolution, settings and renderer before comparing.')
        from PySide6.QtGui import QImageReader
        images={};sizes=[]
        for side,name in paths.items():
            path=Path(name)
            if path.is_symlink() or not path.is_file() or path.stat().st_size>64*1024*1024:raise ValueError('Select a regular screenshot below 64 MiB.')
            if path.suffix.casefold() not in ('.png','.jpg','.jpeg','.bmp'):raise ValueError('Unsupported captured image type.')
            reader=QImageReader(str(path));size=reader.size()
            if not size.isValid() or size.width()*size.height()>64_000_000:raise ValueError('Invalid or oversized image dimensions.')
            images[side]=(path,sha256_file(path));sizes.append((size.width(),size.height()))
        if sizes[0]!=sizes[1]:raise ValueError('Matched screenshots must have the same dimensions.')
        if images['off'][1]==images['on'][1]:raise ValueError('The two images are identical; no A/B change is established.')
        game=self.s.library.get(game_id)
        if not game:raise ValueError('Selected game is unavailable.')
        root=app_home()/'visual-comparisons'/uuid.uuid4().hex;root.mkdir(parents=True)
        report={'schema':1,'id':root.name,'game_id':game_id,'created_at':now_iso(),'conditions':conditions,'notes':notes[:4000],
                'images':{},'contexts':contexts or {},'capture_attribution':'User-supplied screenshots and matching-condition attestation; capture time and NR off/on are not independently instrumented.',
                'limitations':['Still images cannot measure temporal flicker or ghosting. Use a matched video and repeatable camera path.',
                    'No reference image or defensible perceptual metric was supplied. No quantitative quality score is calculated.',
                    'Higher FPS is not proof of higher image quality.','Image labels do not establish Neural Rendering execution.']}
        for side,(path,pin) in images.items():
            dest=root/(side+path.suffix.lower());shutil.copyfile(path,dest)
            if sha256_file(dest)!=pin:raise ValueError('Captured image changed while importing.')
            report['images'][side]={'file':dest.name,'sha256':pin,'dimensions':list(sizes[0])}
        safe=redact(report);(root/'comparison.json').write_text(json.dumps(safe,indent=2,ensure_ascii=False),encoding='utf-8')
        off=html.escape(report['images']['off']['file'],quote=True);on=html.escape(report['images']['on']['file'],quote=True)
        page='''<!doctype html><meta charset="utf-8"><title>AKENO visual A/B</title>
<style>body{background:#10101c;color:#eee;font:16px system-ui;margin:2em} .compare{position:relative;display:inline-block;max-width:100%}img{max-width:100%;display:block}#on{position:absolute;left:0;top:0;clip-path:inset(0 50% 0 0)}input{width:100%}pre{white-space:pre-wrap}</style>
<h1>AKENO visual A/B — user-supplied captures</h1><p>NR off (base) / NR on (left). Labels are user declarations, not execution proof.</p>'''
        page+=f'<div class="compare"><img alt="NR off" src="{off}"><img id="on" alt="NR on" src="{on}"></div><input aria-label="Comparison split" type="range" min="0" max="100" value="50" oninput="document.getElementById(\'on\').style.clipPath=\'inset(0 \'+(100-this.value)+\'% 0 0)\'">'
        page+='<pre>'+html.escape(json.dumps(safe,indent=2,ensure_ascii=False))+'</pre>'
        (root/'comparison.html').write_text(page,encoding='utf-8')
        self.s.db.set_setting('visual-comparison:'+root.name,safe)
        return dict(safe,local_html=str(root/'comparison.html'))
