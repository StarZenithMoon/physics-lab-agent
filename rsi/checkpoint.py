"""Durable, atomic evaluator records, readable while a task is running."""
import json
import os
from pathlib import Path
import tempfile
import time


def checkpoint(path, value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                                         prefix='.'+path.name+'-',suffix='.tmp',delete=False) as stream:
            temporary=Path(stream.name)
            json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        for attempt in range(6):
            try:
                os.replace(temporary,path);return
            except PermissionError:
                if attempt==5:raise
                time.sleep(.05)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
