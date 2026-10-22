"""Fail public packaging if a proprietary NVIDIA DLSS/Streamline component leaked in."""
from pathlib import Path
import argparse
import re
import zipfile


def check(path):
    path=Path(path)
    names=(zipfile.ZipFile(path).namelist() if path.suffix.lower()=='.zip'
           else [p.relative_to(path).as_posix() for p in path.rglob('*') if p.is_file()])
    forbidden=[name for name in names if re.fullmatch(r'(?:nvngx[^/]*\.(?:dll|bin)|sl\.[^/]*\.dll)',Path(name).name,re.I)]
    if forbidden:raise ValueError('Proprietary NVIDIA component in public payload: '+', '.join(forbidden[:20]))
    return len(names)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('path');args=parser.parse_args()
    print('Public payload component-name audit: '+str(check(args.path))+' files checked. No proprietary NVIDIA runtime names found.')
