"""Ephemeral browser-acceptance server. Engineering data never leaves tempdir."""
import argparse
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

PROJECT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PROJECT/'src'))
sys.path.insert(0,str(PROJECT))
from tests.mvp.helpers import setup_offline_project
from xquant_assistant.web.server import make_server
from xquant_assistant.web.service import WebService

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8766)
    args=parser.parse_args()
    with TemporaryDirectory(prefix='xquant-web-engineering-') as directory:
        root=Path(directory)
        setup_offline_project(root,PROJECT)
        service=WebService(root,clock=lambda:datetime.fromisoformat('2026-09-28T16:10:00+08:00'))
        with make_server(root,args.port,service=service) as server:
            print('ISOLATED ENGINEERING BROWSER FIXTURE',flush=True)
            server.serve_forever()

