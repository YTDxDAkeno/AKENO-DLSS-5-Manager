"""Expose bounded JUnit failure details in CI annotations when authenticated logs are unavailable."""
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def annotate(path):
    path = Path(path)
    if not path.is_file():
        return
    root = ET.parse(path).getroot()
    count = 0
    for case in root.iter('testcase'):
        for kind in ('failure', 'error'):
            failure = case.find(kind)
            if failure is None:
                continue
            message = (case.get('classname','') + '::' + case.get('name','') + '\n' + (failure.text or failure.get('message','')))[:6000]
            message = message.replace('%','%25').replace('\r','%0D').replace('\n','%0A')
            print('::error title=Pytest regression::' + message)
            count += 1
            if count >= 15:
                return


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')    # a Windows console is cp1252
    for argument in sys.argv[1:]:
        annotate(argument)
