"""Versioned Feeder/ReShade configuration; no imaginary consumer settings."""
from pathlib import Path
from akeno.backends.base import FileOp
from akeno.backends import ini
from akeno.security.downloads import sha256_file


def feeder_configuration(services,game,motion,target=None):
    # Older expert-import workflows retain their manual shader setup. Automatic
    # recipes require the explicit reviewed provider/technique contract.
    if 'provider_id' not in motion or not motion.get('technique'):return []
    if motion['provider_id'] not in (0,1,2,3,4):raise ValueError('Unsupported Feeder motion-vector provider.')
    target=Path(target or game.target_dir)
    provider=motion['technique'];shader=Path(motion.get('destination',motion['path'])).name
    technique=provider+'@'+shader
    preset='Techniques='+technique+',DLSS5_Feed@DLSS5_Feed.fx\nTechniqueSorting='+technique+',DLSS5_Feed@DLSS5_Feed.fx\n'
    reshade=target/'ReShade.ini'
    current=reshade.read_text(encoding='utf-8-sig') if reshade.is_file() else ''
    values={'GENERAL':{'EffectSearchPaths':'.\\reshade-shaders\\Shaders\\**','TextureSearchPaths':'.\\reshade-shaders\\Textures\\**',
        'PresetPath':'.\\AKENO-Feeder.ini','PreprocessorDefinitions':'DLSS5_MV_PROVIDER='+str(motion['provider_id'])},
        'ADDON':{'AddonPath':'.\\'},'DEPTH':{'DrawStatsHeuristic':'1'},'INPUT':{'KeyOverlay':'36,0,0,0'}}
    existing=ini.get_values(current).get('GENERAL',{})
    definitions=[v for v in existing.get('PreprocessorDefinitions','').split(',') if v and not v.startswith('DLSS5_MV_PROVIDER=')]
    values['GENERAL']['PreprocessorDefinitions']=','.join([*definitions,'DLSS5_MV_PROVIDER='+str(motion['provider_id'])])
    for key in ('EffectSearchPaths','TextureSearchPaths'):
        old=existing.get(key,'')
        if old:values['GENERAL'][key]=old+';'+values['GENERAL'][key] if values['GENERAL'][key] not in old else old
    text=ini.set_values(current,values)
    # Feeder's preset is an SR render-preset hint, not an NR quality setting.
    cfg='enabled=1\nmode=2\nhdr=-1\ndepth_inverted=-1\nwork_resolution=100\nwork_upscale=0\npreset=0\ngpu_timeout_ms=2000\n'
    return [FileOp('ReShade.ini',content=text.encode('utf-8'),kind='config',description='Merge supported ReShade/Feeder settings, preserving unrelated sections'),
        FileOp('AKENO-Feeder.ini',content=preset.encode('utf-8'),kind='config',description='Enable the selected motion provider before Feeder'),
        FileOp('dlss5-feed.cfg',content=cfg.encode('utf-8'),kind='config',description='Pinned Feeder 1.19 input/output configuration')]
