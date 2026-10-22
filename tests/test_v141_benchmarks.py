"""PresentMon-shaped synthetic streams and historical attribution, not hardware measurements."""
from copy import deepcopy
from datetime import datetime, timezone
import pytest
from akeno.benchmark import stats
from akeno.benchmark.service import BenchmarkService


def csv_stream(rows, modern=True):
    header='Application,ProcessID,SwapChainAddress,PresentRuntime,FrameType,MsBetweenPresents,MsBetweenAppStart,DisplayedTime\n'
    return header + '\n'.join(','.join(map(str,r)) for r in rows)


def test_multi_swapchain_is_not_merged_or_selected_by_tiniest_present_rate():
    rows=[('game.exe',42,'main','DXGI','Application',16,16,16)]*100
    rows += [('game.exe',42,'ui','DXGI','Application',0.1,0.1,'NA')]*1000
    rows += [('dwm.exe',99,'dwm','DXGI','Application',4,4,4)]*100
    parsed=stats.parse_presentmon_csv(csv_stream(rows),'game.exe',42)
    assert parsed.quality['selected_stream']['swapchain']=='main'
    assert parsed.frametimes_ms==[16]*100
    assert not parsed.quality['reliable']  # auto-selection is disclosed as ambiguous
    explicit=stats.parse_presentmon_csv(csv_stream(rows),'game.exe',42,'main')
    assert explicit.quality['reliable'] and explicit.quality['rendered_fps_available']
    assert stats.compute(explicit.frametimes_ms).avg_fps==62.5


def test_frame_generation_rendered_and_displayed_are_distinct(db,tmp_path):
    rows=[]
    for _ in range(60):
        rows += [('game.exe',42,'main','DXGI','Application',8,16,8),
                 ('game.exe',42,'main','DXGI','NVIDIA DLSS-G',8,0,8)]
    parsed=stats.parse_presentmon_csv(csv_stream(rows),'game.exe',42,'main')
    assert parsed.frametimes_ms==[16]*60
    assert parsed.quality['generated_frame_share']==0.5
    assert parsed.displayed_frametimes_ms==[8]*120
    service=BenchmarkService(db,None)
    session=service.record('g','fixture',parsed.frametimes_ms,{},'fixture',None,parsed)
    assert session.stats.application_rendered_fps==62.5
    assert session.stats.displayed_fps==125.0
    assert session.stats.generated_frame_share==0.5


def test_legacy_present_intervals_do_not_claim_rendered_frame_generation_fps():
    text='Application,ProcessID,SwapChainAddress,FrameType,MsBetweenPresents\n'
    text+='game.exe,42,main,Application,8\n'*40+'game.exe,42,main,NVIDIA DLSS-G,8\n'*40
    parsed=stats.parse_presentmon_csv(text,'game.exe',42)
    assert not parsed.quality['rendered_fps_available']
    assert any('application-start' in w for w in parsed.quality['warnings'])


def test_pid_filter_and_missing_pid_are_explicit():
    text=csv_stream([('game.exe',42,'main','DXGI','Application',16,16,16)]*40 +
                    [('game.exe',43,'main','DXGI','Application',1,1,1)]*40)
    assert stats.parse_presentmon_csv(text,'game.exe',42).frametimes_ms==[16]*40
    with pytest.raises(stats.CsvError,match='ProcessID'): stats.parse_presentmon_csv('Application,MsBetweenPresents\ngame.exe,16\n','game.exe',42)
    with pytest.raises(stats.CsvError): stats.parse_presentmon_csv('Application,Unknown\ngame.exe,16\n')


def test_heterogeneous_tiny_intervals_withhold_gameplay_median(db,tmp_path):
    rows=[('game.exe',42,'main','DXGI','Application',0.8,0.8,0.8)]*75
    rows += [('game.exe',42,'main','DXGI','Application',16,16,16)]*25
    parsed=stats.parse_presentmon_csv(csv_stream(rows),'game.exe',42)
    assert not parsed.quality['reliable']
    session=BenchmarkService(db,None).record('g','fixture',parsed.frametimes_ms,{},'fixture',None,parsed)
    assert session.stats.median_fps is None
    assert session.stats.application_rendered_fps is None
    assert session.stats.frame_spikes==0  # heterogeneity is warned, not counted as 25 fake stutters


def test_stutter_episodes_have_absolute_floor_and_group_adjacent_spikes():
    data=[10]*100+[40,45,50]+[10]*100+[60]
    measured=stats.compute(data)
    assert measured.frame_spikes==4
    assert measured.stutter_events_per_minute==round(2*60000/sum(data),2)
    assert stats.compute([0.8,2.0]*100).frame_spikes==0


def transaction():
    return {'id':5,'created_at':'2026-10-21T23:27:23Z','params':{'_community_runtime':'a'*64},
        'package_id':'opti-1.3.0','package_version':'1.3.0','backend_id':'community:optiscaler_nr'}


def attributed():
    return {'id':10,'label':'experimental','started_at':'2026-10-21T23:28:00Z',
        'neural_config':{'attribution':{'confirmed':True,'end_configuration_matches':True,
            'transaction_id':5,'runtime_sha256':'a'*64,'package_id':'opti-1.3.0','package_version':'1.3.0',
            'backend_id':'community:optiscaler_nr','observation_id':'session-1',
            'process':{'pid':42,'created_at':'filetime:123','executable_sha256':'b'*64},
            'capture_started_at':'2026-10-21T23:28:00Z','capture_ended_at':'2026-10-21T23:28:30Z'}}}


def test_six_older_sessions_are_never_transaction_five_evidence():
    old=[{'id':i,'label':'performance','started_at':'2026-10-21T23:20:00Z', 'neural_config':{}} for i in range(3,9)]
    groups=BenchmarkService.attributed_groups(old,transaction(),'session-1')
    assert not groups['current_installation']
    assert len(groups['historical_installations'])==6
    assert old[0]['neural_config']=={}  # no retroactive identity stamping


@pytest.mark.parametrize('field,value', [('transaction_id',4),('runtime_sha256','c'*64),
    ('package_version','different'),('end_configuration_matches',False),('confirmed',False),
    ('process',{}),('capture_started_at','2026-10-21T23:20:00Z')])
def test_current_attribution_requires_all_capture_time_pins(field,value):
    sample=attributed();sample['neural_config']['attribution'][field]=value
    assert not BenchmarkService.attributed_groups([sample],transaction(),'session-1')['current_installation']


def test_current_installation_and_session_are_separate_from_baseline_and_unknown():
    good=attributed(); older=deepcopy(good);older['id']=9;older['neural_config']['attribution']['observation_id']='previous-session'
    baseline={'id':2,'label':'baseline','neural_config':{}}
    unknown={'id':1,'label':'quality','neural_config':{}}
    groups=BenchmarkService.attributed_groups([good,older,baseline,unknown],transaction(),'session-1')
    assert [s['id'] for s in groups['current_installation']]==[10,9]
    assert [s['id'] for s in groups['current_session']]==[10]
    assert groups['baseline']==[baseline] and groups['unconfirmed']==[unknown]


def test_legacy_metrics_are_not_represented_as_validated_gameplay_and_raw_record_is_preserved(db):
    service=BenchmarkService(db,None)
    session=service.record('game','old-measurement',[0.8]*75+[16]*25,{},'fixture',None)
    raw=db.benchmarks('game')[0]['stats']['median_fps']
    normalized=service.sessions('game')[0]
    assert normalized['stats']['median_fps'] is None
    assert not normalized['neural_config']['measurement']['reliable']
    assert normalized['neural_config']['measurement']['legacy_raw_statistics']['median_fps']==raw
    assert db.benchmarks('game')[0]['stats']['median_fps']==raw
