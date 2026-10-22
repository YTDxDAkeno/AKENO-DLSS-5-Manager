"""Assemble personal offline inputs; never uploads or changes the public runtime directory."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from akeno.community.profiles import local_profile
from akeno.community.runtime import Category, RuntimeManager, regular
from akeno.security.catalog import Catalog
from akeno.security.downloads import sha256_file
from akeno.security import archive


def build(runtime, backend_archive, destination, source='', permitted=False, manager=None, sr_runtime=None, include_presentmon=True, feeder_archive=None, pipeline_manifest=None):
    if not permitted:
        raise PermissionError('Confirm rights for personal local reuse. This does not grant redistribution rights.')
    destination = Path(destination)
    # Refuse mixing with a previous package or writing proprietary inputs inside tracked trees.
    repo = Path(__file__).resolve().parents[1]
    if destination.resolve().is_relative_to(repo) and not destination.resolve().is_relative_to(repo / 'dist-private'):
        raise ValueError('Use dist-private or an output directory outside the repository.')
    if destination.exists():
        raise ValueError('Choose a new empty output directory for this private bundle.')
    manager = manager or RuntimeManager(None)
    evaluation = manager.inspect(str(runtime))
    if evaluation.category == Category.REJECTED or not evaluation.signature.get('checked'):
        raise ValueError(evaluation.detail)
    profile = local_profile(evaluation, source, True)
    evaluated = manager.inspect(str(runtime), profile)
    if evaluated.category not in (Category.OFFICIAL, Category.COMMUNITY, Category.ADVANCED):
        raise ValueError(evaluated.detail)
    catalog = Catalog.load_bundled()
    package = catalog.best_package('optiscaler_nr')
    if backend_archive is None:
        from akeno.security.downloads import DownloadManager
        backend_archive = DownloadManager(catalog.allowed_hosts).fetch(package.url, package.sha256,
            Path(package.url).name, package.size).path
    regular(Path(backend_archive))
    if sha256_file(backend_archive) != package.sha256:
        raise ValueError('Backend archive differs from the pinned public catalog.')
    # Validate archive paths, exact members and pins without running upstream installers.
    import tempfile
    with tempfile.TemporaryDirectory() as temporary:
        archive.extract(Path(backend_archive), Path(temporary), package.files, package.root)
        for name, pin in package.files.items():
            if sha256_file(Path(temporary) / name) != pin:
                raise ValueError('Backend member hash mismatch: ' + name)
    if feeder_archive is not None:
        feeder=catalog.package('dlss5-feeder-1.19.0')
        regular(Path(feeder_archive))
        if not feeder or sha256_file(feeder_archive)!=feeder.sha256:raise ValueError('Feeder archive differs from the pinned catalog.')
        with tempfile.TemporaryDirectory() as temporary:archive.extract(Path(feeder_archive),Path(temporary),feeder.files,feeder.root)
    if pipeline_manifest is not None:
        # Reuse the exact local component validator; no scripts/components are executed.
        from akeno.core.db import Database
        from akeno.pipeline.manifests import ComponentStore
        with tempfile.TemporaryDirectory() as temporary:
            database=Database(Path(temporary)/'validate.sqlite')
            try:ComponentStore(database).import_manifest(pipeline_manifest,True)
            finally:database.close()
    destination.mkdir(parents=True)
    components = []
    for path, relative, kind, identity, extra in [
        (Path(runtime), 'community/' + evaluation.sha256 + '/nvngx_dlssnr.dll', 'neural_runtime',
            'nr-' + evaluation.sha256, {'profile': profile}),
        (Path(backend_archive), 'backends/optiscaler.zip', 'backend_archive', package.id, {})]:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        before = sha256_file(path)
        shutil.copyfile(path, target)
        if sha256_file(target) != before or before != (evaluation.sha256 if kind == 'neural_runtime' else package.sha256):
            raise ValueError('An input changed during private packaging.')
        components.append(dict(id=identity, kind=kind, path=relative, sha256=before, **extra))
    if feeder_archive is not None:
        target=destination/'backends'/'feeder.zip';shutil.copyfile(feeder_archive,target)
        if sha256_file(target)!=feeder.sha256:raise ValueError('Feeder changed during packaging.')
        components.append({'id':feeder.id,'kind':'backend_archive','path':'backends/feeder.zip','sha256':feeder.sha256})
    if pipeline_manifest is not None:
        from akeno.pipeline.manifests import safe_relative
        manifest_path=Path(pipeline_manifest)
        data=json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        folder=destination/'pipeline';folder.mkdir()
        for component in data['components']:
            for item in [component,*component.get('assets',[])]:
                relative=safe_relative(item['path']);source_path=manifest_path.parent/relative;regular(source_path)
                target=folder/relative;target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source_path,target)
                if sha256_file(target)!=item['sha256']:raise ValueError('Pipeline dependency changed during packaging.')
        target=folder/'components.json';target.write_text(json.dumps(data,indent=2),encoding='utf-8')
        components.append({'id':'private-pipeline-components','kind':'pipeline_manifest','path':'pipeline/components.json','sha256':sha256_file(target)})
    if sr_runtime is not None:
        from akeno.backends import runtimes
        sr = runtimes.evaluate(str(sr_runtime), runtimes.DLSS_RUNTIME, verifier=manager.verifier)
        if sr.trust.value != 'verified':
            raise ValueError('The optional SR runtime must independently pass official NVIDIA verification.')
        target = destination / 'official' / sr.sha256 / runtimes.DLSS_RUNTIME
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sr_runtime, target)
        if sha256_file(target) != sr.sha256:
            raise ValueError('SR runtime changed during packaging.')
        components.append({'id': 'sr-' + sr.sha256, 'kind': 'official_runtime',
            'runtime_name': runtimes.DLSS_RUNTIME, 'path': target.relative_to(destination).as_posix(), 'sha256': sr.sha256})
    if include_presentmon:
        from akeno.security.downloads import DownloadManager
        tool = catalog.tool('presentmon')
        if tool is None:
            raise ValueError('Pinned PresentMon catalog entry missing.')
        local = DownloadManager(catalog.allowed_hosts).fetch(tool.url, tool.sha256, tool.file_name, tool.size).path
        target = destination / 'tools' / tool.file_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, target)
        if sha256_file(target) != tool.sha256:
            raise ValueError('PresentMon changed during packaging.')
        components.append({'id': tool.id, 'kind': 'tool', 'path': target.relative_to(destination).as_posix(), 'sha256': tool.sha256})
    manifest = {'schema': 1, 'distribution': 'private-local-use', 'components': components,
        'limitations': ['No redistribution rights inferred. No real GPU execution verified.',
            'Games without their own DLSS SR require an independently NVIDIA-verified local SR runtime.',
            'PresentMon capture requires Windows ETW permissions. Missing sensors are not inferred.']}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--backend-archive', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source', default='')
    parser.add_argument('--sr-runtime', type=Path)
    parser.add_argument('--feeder-archive', type=Path)
    parser.add_argument('--pipeline-manifest', type=Path)
    parser.add_argument('--without-presentmon', action='store_true')
    parser.add_argument('--local-use-permitted', action='store_true')
    args = parser.parse_args()
    build(args.runtime, args.backend_archive, args.output, args.source, args.local_use_permitted, sr_runtime=args.sr_runtime, include_presentmon=not args.without_presentmon,feeder_archive=args.feeder_archive,pipeline_manifest=args.pipeline_manifest)
