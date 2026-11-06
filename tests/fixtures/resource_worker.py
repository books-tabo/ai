"""Independent, local-only synthetic fault fixture (never an HTTP endpoint)."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from media_worker import set_limits
operation,directory,memory,cpu,output=sys.argv[1:]
set_limits(int(memory),int(cpu),int(output))
root=Path(directory)
options=json.loads((root/'options.json').read_bytes())
mode=options['mode']
if mode=='time':
    import os
    import time
    Path(options['pid_path']).write_text(str(os.getpid()))
    time.sleep(30)
elif mode=='cpu':
    while True:
        pass
elif mode=='memory':
    payload=bytearray(int(memory)*2)
elif mode=='disk':
    with (root/'output').open('wb') as handle:
        handle.write(b'x'*(int(output)+1))
elif mode=='output':
    (root/'output').write_bytes(b'12345')
    (root/'result.json').write_text('{"ok":true}')
elif mode=='environment':
    import os
    (root/'output').write_text('absent' if 'KASHIER_SECRET_KEY' not in os.environ else 'leaked')
    (root/'result.json').write_text('{"ok":true}')
