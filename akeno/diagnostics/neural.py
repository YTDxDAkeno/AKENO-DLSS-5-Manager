"""Read-only NR evidence with pinned start hashes and bounded incremental log polling."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import configparser
import re
import time
from akeno.security.downloads import sha256_file

LOGS = ('OptiScaler.log', 'ReShade.log', 'dlss5-feed.log', 'host64/ReShade.log', 'host64/dlss5-feed-host.log')
LIMIT = 8 * 1024 * 1024  # hard total per-poll read bound
POLL_LIMIT = 256 * 1024
MAX_LOGS = 24


def log_clock_ns():
    """Use the Windows filesystem's shared system-clock precision, without a replay tolerance."""
    import sys
    if sys.platform!='win32':return time.time_ns()
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetSystemTimeAsFileTime.argtypes=[ctypes.POINTER(wintypes.FILETIME)]
    stamp=wintypes.FILETIME();kernel.GetSystemTimeAsFileTime(ctypes.byref(stamp))
    return (((stamp.dwHighDateTime<<32)|stamp.dwLowDateTime)-116444736000000000)*100


def identity(path):
    p = Path(path)
    for parent in (p, *p.parents):
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError('Linked diagnostic paths are not permitted.')
    st = p.stat()
    if not p.is_file():
        raise ValueError('A regular diagnostic file is required.')
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _anchor(path, size):
    """At most 256 bytes, independent of EXE/model/log size; not a cryptographic file verification."""
    with Path(path).open('rb') as stream:
        first = stream.read(min(size, 128))
        stream.seek(max(0, size - 128))
        last = stream.read(min(size, 128))
    return hashlib.sha256(first + last).hexdigest()


def discover_logs(target):
    root = Path(target).resolve()
    found = []
    configured = []
    stems = ['optiscaler','reshade','dlss5','ngx']
    try:
        ini = root / 'OptiScaler.ini'
        if ini.is_file() and not ini.is_symlink() and ini.stat().st_size <= 64*1024:
            config = configparser.ConfigParser(interpolation=None, strict=False)
            config.read_string(ini.read_text(encoding='utf-8-sig', errors='replace'))
            value = config.get('Log', 'LogFileName', fallback='').strip()
            if value and value.casefold() != 'auto':
                candidate = Path(value)
                candidate = candidate if candidate.is_absolute() else root / candidate
                if candidate.resolve().is_relative_to(root):
                    configured.append(candidate.parent.relative_to(root).as_posix())
                    stems.append(candidate.stem.casefold())
    except (OSError, ValueError, configparser.Error): pass
    # Restrict discovery to backend-relevant locations; never traverse game assets/library trees.
    for relative in ('', 'logs', 'Logs', 'OptiScaler', 'OptiScaler/logs', 'host64', 'ReShade', 'ReShade/Logs', *configured):
        folder = root / relative
        if not folder.is_dir() or folder.is_symlink() or not folder.resolve().is_relative_to(root):
            continue
        try:
            for p in sorted(folder.iterdir())[:512]:
                low = p.name.casefold()
                if p.suffix.casefold() not in ('.log', '.txt') or not any(x in low for x in stems):
                    continue
                try:
                    identity(p)
                except (OSError, ValueError):
                    continue
                if p.resolve().is_relative_to(root) and p not in found:
                    found.append(p)
                if len(found) >= MAX_LOGS:
                    return found
        except OSError:
            continue
    return found


@dataclass(frozen=True)
class LogCursor:
    size: int
    prefix_sha256: str
    file_identity: tuple = ()


@dataclass(frozen=True)
class Observation:
    game_id: str
    target: str
    executable: str
    artifacts: tuple[tuple[str, str], ...]
    cursors: tuple[tuple[str, LogCursor], ...]
    started_at: float
    executable_sha256: str
    file_identities: tuple[tuple[str, tuple], ...] = ()
    diagnostic_builds: tuple[str, ...] = ()
    log_clock_ns: int = 0
    diagnostic_modules: tuple[str, ...] = ()


@dataclass
class NeuralEvidence:
    state: str = 'none'
    lines: list[dict] = field(default_factory=list)
    detail: str = ''
    stages: dict[str, bool] = field(default_factory=dict)
    logs: list[dict] = field(default_factory=list)
    raw_excerpts: list[dict] = field(default_factory=list)
    error_codes: list[str] = field(default_factory=list)
    integrity: str = 'not_checked'
    overhead: dict = field(default_factory=dict)
    counters: dict = field(default_factory=dict)
    execution: dict = field(default_factory=dict)


def begin(game_id, target, executable, artifacts, expected_hashes=None, diagnostic_builds=(), diagnostic_modules=()):
    target = Path(target).resolve()
    pins, metadata = [], []
    for name in artifacts:
        p = target / name
        before = identity(p)
        if not p.resolve().is_relative_to(target):
            raise ValueError('Diagnostic artifacts must be inside the selected target.')
        pin = sha256_file(p)
        if before != identity(p) or (expected_hashes is not None and name in expected_hashes and expected_hashes[name] != pin):
            raise ValueError('An installed artifact differs from its transaction hash.')
        pins.append((name, pin)); metadata.append((str(p.resolve()), before))
    exe = Path(executable)
    before = identity(exe); exe_hash = sha256_file(exe)
    if before != identity(exe):
        raise ValueError('The executable changed during session verification.')
    metadata.append((str(exe.resolve()), before))
    cursors = []
    for p in discover_logs(target):
        stat = identity(p)
        cursors.append((p.relative_to(target).as_posix(), LogCursor(stat[2], _anchor(p, stat[2]), stat)))
    return Observation(game_id, str(target), str(exe.resolve()), tuple(pins), tuple(cursors),
        time.time(), exe_hash, tuple(metadata), tuple(diagnostic_builds), log_clock_ns(), tuple(diagnostic_modules))


def classify(line):
    low = line.casefold()
    # NR context is mandatory; ordinary NGX/SR/FG messages are deliberately excluded.
    nr = bool(re.search(r'dlss[- _]nr|neural (?:model|rendering)|feature\s*(?:id\s*[:=]?\s*)?(?:18|0x12)\b', low))
    if 'dlss5-feeder initialized' in low or 'dlss-nr forwarder loaded from' in low:
        return 'loaded'
    if not nr or ('nvngx_dlssnr.dll' in low and 'dlss-nr' not in low and 'inline feature 18' not in low):
        return None
    if 'probe:' in low or 'proxy probe' in low:
        return 'capability_probe'
    if re.search(r'fail(?:ed|ure)?|\[error\]|not created|0xbad\w*', low):
        return 'failed'
    if re.search(r'evaluat(?:e|ion|ed|ing)', low):
        # 'first evaluated next frame' in a creation message is future intent, not evaluation.
        if 'next frame' not in low:
            if re.search(r'succeed|success|\bok\b|(?:result|returned)\s*[:=]?\s*0x1\b', low):
                return 'running'
            return 'evaluation_attempted'
    if re.search(r'pass \d+ built at|feature\s*18\s*(?:was )?created|dlss-nr \(proxy\): feature created at|dlss-nr vulkan: feature up at', low):
        return 'initialized'
    if re.search(r'creat(?:e|ion|ing).*feature\s*18|feature\s*18.*creat(?:e|ion|ing)', low):
        return 'creation_attempted'
    if 'dlss-nr' in low and 'the model initialised on this device' in low:
        return 'ngx_initialized'
    if 'ngx' in low and re.search(r'initializ|initialis|\binit\b', low):
        if 'not initialis' in low or 'not initializ' in low: return 'ngx_attempted'
        return 'ngx_initialized' if re.search(r'succeed|success|initialized|initialised|returned 0x1\b|\bok\b', low) else 'ngx_attempted'
    if 'dlss-nr (proxy): re-init' in low:
        return 'ngx_initialized' if 'returned 0x1 ' in low else 'ngx_attempted'
    if re.search(r'forwarder.*loaded|runtime.*loaded', low):
        return 'loaded'
    return None


class NeuralMonitor:
    """One observation, one stream parser. Full hashes are calculated only by begin()."""
    def __init__(self, observation):
        self.observation = observation
        self.streams = {}
        self.events = []
        self.excerpts = []
        self.bytes_read = 0
        self.stale = None
        self.counters = {}
        from akeno.diagnostics.execution import diagnose
        self.health = diagnose('')
        for name, cursor in observation.cursors:
            self.streams[name] = {'offset': cursor.size, 'identity': cursor.file_identity,
                'anchor': cursor.prefix_sha256, 'partial': b'', 'state': 'none', 'created': False,
                'evaluated': False, 'stages': set(), 'baseline': cursor.size}

    def poll(self, process_paths=(), attribution_allowed=True, process_identity=None, diagnostic_loaded=False):
        started = time.perf_counter()
        o = self.observation
        result = NeuralEvidence(state='installed' if o.artifacts else 'none', integrity='cached_start_hashes_metadata_unchanged')
        for name, expected in o.file_identities:
            try:
                if identity(name) != expected:
                    self.stale = 'A watched file changed; start a new session for full cryptographic revalidation.'
            except (OSError, ValueError):
                self.stale = 'A watched file is unavailable or linked; start a new verification session.'
        if self.stale:
            return NeuralEvidence('stale', detail=self.stale, integrity='invalidated')
        read_bytes = 0
        for path in discover_logs(o.target):
            name = path.relative_to(o.target).as_posix()
            info = identity(path)
            stream = self.streams.get(name)
            if stream is None:
                # Files predating observation but discovered late are historical; skip their old content.
                offset = info[2] if info[3] < (o.log_clock_ns or int(o.started_at*1_000_000_000)) else 0
                stream = {'offset': offset, 'identity': info, 'anchor': _anchor(path, offset),
                    'partial': b'', 'state': 'none', 'created': False, 'evaluated': False,
                    'stages': set(), 'baseline': offset}
                self.streams[name] = stream
            old = stream['identity']
            replaced = bool(old and old[:2] != info[:2])
            if replaced or info[2] < stream['offset'] or _anchor(path, stream['offset']) != stream['anchor']:
                self.stale = 'A log was replaced or truncated; start a new observation.'
                return NeuralEvidence('stale', detail=self.stale, integrity=result.integrity,
                    logs=[{'name': name, 'activity': 'rotated_or_truncated', 'size': info[2]}])
            budget = min(POLL_LIMIT, LIMIT - read_bytes)
            with path.open('rb') as source:
                source.seek(stream['offset']); content = source.read(budget)
            read_bytes += len(content)
            stream['offset'] += len(content)
            stream['anchor'] = _anchor(path, stream['offset'])
            stream['identity'] = info
            # Keep partial trailing lines for the next append; never turn a partial failure into success.
            data = stream['partial'] + content
            lines = data.split(b'\n')
            stream['partial'] = lines.pop()[-8192:] if lines else b''
            for raw in (lines if content else []):
                line = raw.decode('utf-8', errors='replace')[:1000]
                self.excerpts.append({'file': name, 'line': line})
                from akeno.diagnostics.execution import diagnose
                health=diagnose(line)
                for key in ('secure_loader','feature_18_requirements_query','consumer_fallback'):
                    if health[key] not in ('not_observed','unknown'):self.health[key]=health[key]
                for key in ('query_observations','error_codes','stability_incidents','dispatch_observations'):
                    for item in health[key]:
                        if item not in self.health[key]:self.health[key].append(item)
                    self.health[key]=self.health[key][-24:]
                self.health['dred_observed'] |= health['dred_observed']
                if health['ngx_evaluate_max_ms'] is not None:self.health['ngx_evaluate_max_ms']=max(self.health['ngx_evaluate_max_ms'] or 0,health['ngx_evaluate_max_ms'])
                from akeno.diagnostics.counters import parse_counter, attributed
                counter=parse_counter(line)
                if 'AKENO_NR_V1 ' in line and counter is None:continue
                if counter:
                    if not diagnostic_loaded or not attributed(counter,process_identity,o.diagnostic_builds):continue
                    previous=self.counters
                    if previous and (counter['seq']<=previous['seq'] or any(counter[k]<previous[k] for k in ('create_attempts','create_success','create_fail','eval_attempts','eval_success','eval_fail'))):continue
                    self.counters=counter
                    stream['created']=counter['create_success']>0
                    stream['evaluated']=counter['eval_success']>0
                    stream['stages'].update({'creation_attempted','evaluation_attempted'})
                    if stream['created']:stream['stages'].add('initialized')
                    if stream['evaluated']:stream['stages'].add('running')
                    stream['state']='failed' if counter['eval_fail'] or counter['create_fail'] else ('running' if stream['evaluated'] else 'creation_attempted')
                    self.events.append({'file':name,'stage':'counter_summary','counter':counter,'line':line})
                    continue
                stage = classify(line)
                if not stage:
                    continue
                self.events.append({'file': name, 'line': line[:500], 'stage': stage})
                stream['stages'].add(stage)
                if stage == 'failed':
                    stream.update(state='failed', created=False, evaluated=False)
                elif stage == 'initialized':
                    stream.update(state='initialized', created=True, evaluated=False)
                elif stage == 'running':
                    stream.update(state='running', evaluated=True)
                elif stream['state'] != 'failed':
                    stream['state'] = stage
            result.logs.append({'name': name, 'size': info[2], 'observation_offset': stream['baseline'],
                'read_offset': stream['offset'], 'appended_bytes': len(content),
                'activity': 'appended' if content else 'no_new_content', 'backlog_bytes': max(0, info[2]-stream['offset'])})
            if read_bytes >= LIMIT:
                break
        self.events = self.events[-500:]; self.excerpts = self.excerpts[-80:]
        stages = set().union(*(s['stages'] for s in self.streams.values())) if self.streams else set()
        created = any(s['created'] for s in self.streams.values())
        evaluated = any(s['evaluated'] for s in self.streams.values())
        live = o.executable in {str(Path(p).resolve()) for p in process_paths if p}
        states = [s['state'] for s in self.streams.values()]
        if self.health['stability_incidents'] or 'failed' in states:
            result.state = 'failed'
        elif o.artifacts and any(s['created'] and s['evaluated'] for s in self.streams.values()) and live and attribution_allowed:
            result.state = 'verified'
        else:
            for state in ('running', 'initialized', 'creation_attempted', 'ngx_initialized', 'ngx_attempted', 'loaded', 'evaluation_attempted'):
                if state in states:
                    result.state = state; break
        result.stages = {'runtime_present': (Path(o.target)/'nvngx_dlssnr.dll').is_file(),
            'backend_loaded': 'loaded' in stages,
            'runtime_loaded': any(e['stage'] == 'loaded' and 'runtime' in e['line'].casefold() and 'forwarder' not in e['line'].casefold() for e in self.events), 'ngx_initialization_attempted': bool(stages & {'ngx_attempted','ngx_initialized'}),
            'ngx_initialized': 'ngx_initialized' in stages, 'feature_18_creation_attempted': bool(stages & {'creation_attempted','initialized'}),
            'feature_18_created': created, 'frame_evaluation_attempted': bool(stages & {'evaluation_attempted','running'}),
            'feature_evaluated': evaluated, 'evaluations_attributed_to_game': result.state == 'verified'}
        result.counters = dict(self.counters)
        result.lines = list(self.events); result.raw_excerpts = list(self.excerpts)
        result.error_codes = sorted({code for e in self.events if e['stage'] == 'failed' for code in re.findall(r'0x[0-9a-fA-F]+', e['line'])})
        result.error_codes=sorted(set(result.error_codes+self.health['error_codes']))
        if self.counters.get('eval_fail') and self.counters.get('last_error'):
            result.error_codes=sorted(set(result.error_codes+[self.counters['last_error']]))
        if not result.logs:
            result.detail = 'No backend log found in the bounded game/backend log locations.'
        elif not self.events and all(item['activity'] == 'no_new_content' for item in result.logs):
            result.detail = 'Existing backend logs have no fresh content since the observation cursor. Launch or restart the game to capture initialization.'
        elif not self.events:
            result.detail = 'Fresh backend log content was read, but no recognized Neural Rendering events were found. Ordinary SR/FG events do not verify Feature 18.'
        else:
            result.detail = 'Fresh backend log evidence; module presence, initialization and Feature 18 evaluation remain separate.'
        self.bytes_read += read_bytes
        result.overhead = {'poll_ms': round((time.perf_counter()-started)*1000, 3), 'log_bytes_read': read_bytes,
            'total_log_bytes_read': self.bytes_read, 'full_file_hashes_this_poll': 0,
            'integrity_limitation': 'Metadata checks reuse hashes verified at session start; they are not fresh cryptographic verification.'}
        from akeno.diagnostics.execution import contract
        result.execution=contract(process=process_identity,stages=dict(result.stages,evaluations_attributed_to_game=bool(live and attribution_allowed and (self.counters or result.state=='verified'))),counters=result.counters,
            diagnostics=dict(self.health),duration_s=time.time()-o.started_at)
        return result


def inspect(target, observation=None, game_id=None, process_paths=()):
    if observation:
        if str(Path(target).resolve()) != observation.target or game_id != observation.game_id:
            return NeuralEvidence('stale', detail='The selected game or target changed.')
        return NeuralMonitor(observation).poll(process_paths)
    # Read-only historical inspection, explicitly never attributed to a current process/session.
    root = Path(target).resolve()
    if not root.is_dir():
        return NeuralEvidence()
    o = Observation(game_id or '', str(root), '', (), (), time.time()+1, '', ())
    monitor = NeuralMonitor(o)
    for p in discover_logs(root):
        st = identity(p)
        monitor.streams[p.relative_to(root).as_posix()] = {'offset': max(0, st[2]-POLL_LIMIT), 'identity': st,
            'anchor': _anchor(p, max(0, st[2]-POLL_LIMIT)), 'partial': b'', 'state': 'none', 'created': False,
            'evaluated': False, 'stages': set(), 'baseline': 0}
    result = monitor.poll(attribution_allowed=False)
    if result.state == 'none' and (root/'nvngx_dlssnr.dll').is_file():
        result.state = 'installed'
    return result
