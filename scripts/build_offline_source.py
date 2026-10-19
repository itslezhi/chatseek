#!/usr/bin/env python3
"""Build an allowlisted source archive with verified, public embedding weights."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chatseek_models import REVISION
from scripts.prepare_public_source import snapshot


def model_files(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((ROOT/'models/bge-small-zh-v1.5.manifest.json').read_text())
    if manifest['revision'] != REVISION:
        raise RuntimeError('Model revision does not match runtime')
    result = []
    for item in manifest['files']:
        path = directory/item['path']
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise RuntimeError('Model checksum mismatch: '+item['path'])
        result.append((item['path'], path))
    return result


def build(directory, output):
    # Only audited public sources and model files enter the distribution.
    sources = snapshot()
    weights = model_files(directory)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for item in sources:
            archive.write(ROOT/item['path'], 'ChatSeek/'+item['path'])
        for name, path in weights:
            archive.write(path, 'ChatSeek/models/bge-small-zh-v1.5/'+name)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.zip.sha256').write_text(digest+'  '+output.name+'\n')
    return {'archive':str(output),'bytes':output.stat().st_size,'sha256':digest,
            'model_files':len(weights),'public_source_files':len(sources)}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'dist/ChatSeek-source-with-model.zip')
    args=parser.parse_args()
    print(json.dumps(build(args.model_dir,args.output)))
