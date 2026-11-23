"""Launch the installed OpenCode CLI with the project Python and working directory."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit
from labcore.io import write_json
from labcore.project import ROOT
from rsi.runner import find_cli


def configure(root=ROOT, *, provider='lab-deepseek', model='deepseek-flash',
              base_url='https://api.deepseek.com/v1', key_env='DEEPSEEK_API_KEY', replace=False):
    root=Path(root).resolve()
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',provider):raise ValueError('invalid provider ID')
    if not re.fullmatch(r'[A-Z][A-Z0-9_]*',key_env):raise ValueError('use an environment variable name, not an API key')
    url=urlsplit(base_url)
    if url.scheme not in {'http','https'} or not url.netloc or url.username or url.password or url.query:
        raise ValueError('base URL must be an API endpoint without embedded credentials or a query')
    if not isinstance(model,str) or not model.strip():raise ValueError('upstream model ID is required')
    path=root/'.opencode/opencode.jsonc'
    if path.exists() and not replace:raise FileExistsError('local config exists; edit it or explicitly use --replace-local')
    identifier=f'{provider}/analysis'
    cfg={'$schema':'https://opencode.ai/config.json','model':identifier,'default_agent':'lab-assistant',
        'providers':{provider:{'name':'Lab model provider','env':[key_env],
           'package':'@opencode/ai/providers/openai-compatible','settings':{'baseURL':base_url},
           'models':{'analysis':{'name':model,'modelID':model}}}}}
    rsi_path=root/'rsi/config.local.json'
    rsi_cfg=json.loads(rsi_path.read_text(encoding='utf-8')) if rsi_path.exists() else {}
    if not isinstance(rsi_cfg,dict):raise ValueError('invalid local RSI config')
    rsi_cfg['model']=identifier
    write_json(path,cfg);write_json(rsi_path,rsi_cfg)
    return {'config':str(path),'model':identifier,'upstream_model':model,'key_environment':key_env,
            'credentials_written':False}


def launch(arguments):
    exe=find_cli()
    if not exe:raise FileNotFoundError('OpenCode CLI not found; set LAB_OPENCODE_EXE')
    env=os.environ.copy();env['PATH']=str(Path(sys.executable).parent)+os.pathsep+env.get('PATH','')
    # A private server inherits this terminal's API key and numerical environment.
    # Only the TUI and inference commands accept --standalone in OpenCode v2.
    standalone = not arguments or arguments[0].startswith('-') or arguments[0] in {'run','mini'}
    flags = ['--standalone'] if standalone and '--standalone' not in arguments else []
    return subprocess.call([exe,*flags,*arguments],cwd=ROOT,env=env)


def main():
    args=sys.argv[1:]
    if args and args[0]=='configure':
        p=argparse.ArgumentParser(description='Write ignored local provider settings; never a credential')
        p.add_argument('--provider',default='lab-deepseek');p.add_argument('--model',default='deepseek-flash')
        p.add_argument('--base-url',default='https://api.deepseek.com/v1');p.add_argument('--key-env',default='DEEPSEEK_API_KEY')
        p.add_argument('--replace-local',action='store_true');a=p.parse_args(args[1:])
        print(json.dumps(configure(provider=a.provider,model=a.model,base_url=a.base_url,key_env=a.key_env,replace=a.replace_local),ensure_ascii=False,indent=2))
    else:raise SystemExit(launch(args))


if __name__=='__main__':main()
