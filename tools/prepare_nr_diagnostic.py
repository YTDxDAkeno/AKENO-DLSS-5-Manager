"""CLI for the same pinned instrumentation shipped in the graphical local builder."""
from pathlib import Path
import sys,argparse,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from akeno.diagnostics.source_build import prepare,BASE,BUILD
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source');args=p.parse_args()
    print(json.dumps(prepare(args.source),indent=2))
