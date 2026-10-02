"""Loopback-only HTTP server with same-origin mutation protection."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import socket
from urllib.parse import parse_qs, unquote, urlsplit
from .service import BusyError, WebService
from ..domain import json_value

ASSETS = Path(__file__).parent/'assets'


class LocalHTTPServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR permits a second listener on the same port.
    # Exclusive binding ensures the launcher cannot create ambiguous servers.
    allow_reuse_address=False

    def server_bind(self):
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()


def make_server(root,port=8765,*,service=None):
    service=service or WebService(root)
    token=secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self,fmt,*args):
            pass

        def allowed_host(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}

        def response(self,status,payload,content_type='application/json; charset=utf-8',*,attachment=None,sandbox=False):
            data=bytes(payload) if isinstance(payload,(bytes,bytearray)) else json.dumps(json_value(payload),ensure_ascii=False,allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"sandbox; default-src 'none'; style-src 'unsafe-inline'" if sandbox else
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if attachment:
                self.send_header('Content-Disposition',f'attachment; filename="{attachment}"')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):
                pass

        def do_GET(self):
            if not self.allowed_host():
                return self.response(403,{'error':'仅接受本机访问'})
            try:
                url=urlsplit(self.path)
                path=unquote(url.path)
                query=parse_qs(url.query)
                if path in {'/','/index.html'}:
                    return self.response(200,(ASSETS/'index.html').read_bytes(),'text/html; charset=utf-8')
                if path in {'/app.js','/style.css'}:
                    kind='text/javascript' if path.endswith('.js') else 'text/css'
                    return self.response(200,(ASSETS/path[1:]).read_bytes(),kind+'; charset=utf-8')
                if path=='/favicon.ico':
                    return self.response(204,b'','image/x-icon')
                if path=='/api/state':
                    return self.response(200,{**service.state(),'csrf_token':token})
                if path=='/api/jobs':
                    return self.response(200,{'jobs':service.job_list()})
                if path=='/api/market':
                    return self.response(200,service.market(query.get('symbol',[''])[0],int(query.get('limit',['120'])[0])))
                if path=='/api/report':
                    return self.response(200,service.report(query.get('id',[''])[0]))
                if path.startswith('/artifact/'):
                    pieces=path.split('/')
                    if len(pieces)!=4:
                        raise ValueError('无效文件路径')
                    folder=service.run_path(pieces[2])
                    name=pieces[3]
                    target=(folder/name).resolve()
                    if Path(name).name!=name or not target.is_relative_to(folder) or target.suffix not in {'.json','.csv','.html','.parquet'} or not target.is_file():
                        raise ValueError('文件不存在或不可导出')
                    kind=mimetypes.guess_type(name)[0] or 'application/octet-stream'
                    return self.response(200,target.read_bytes(),kind,attachment=None if target.suffix=='.html' else name,sandbox=target.suffix=='.html')
                return self.response(404,{'error':'页面不存在'})
            except (ValueError,OSError,KeyError) as exc:
                self.response(400,{'error':str(exc)})
            except Exception as exc:
                self.response(500,{'error':'读取失败：'+str(exc)})

        def do_POST(self):
            origin=self.headers.get('Origin')
            expected={f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}
            if not self.allowed_host() or self.headers.get('X-Local-Token')!=token or (origin and origin not in expected):
                # Drain bounded request bodies before closing to deliver a reliable
                # 403 on Windows rather than resetting a socket with unread bytes.
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if 0<length<=65536:
                        self.rfile.read(length)
                except (ValueError,OSError):
                    pass
                return self.response(403,{'error':'操作须从本地网页提交，请刷新页面'})
            try:
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    raise ValueError('仅接受 JSON 请求')
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=65536:
                    raise ValueError('请求体大小无效')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):
                    raise ValueError('请求必须是对象')
                if self.path=='/api/jobs':
                    return self.response(202,service.submit(body))
                if self.path=='/api/config':
                    return self.response(200,service.update_config(body))
                return self.response(404,{'error':'操作不存在'})
            except BusyError as exc:
                self.response(409,{'error':str(exc)})
            except (ValueError,KeyError,TypeError) as exc:
                self.response(400,{'error':str(exc)})
            except Exception as exc:
                self.response(500,{'error':str(exc)})

    server=LocalHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    server.web_service=service
    return server


def serve(root,port=8765):
    with make_server(root,port) as server:
        print(f'XQuant 本地操作台：http://127.0.0.1:{server.server_port} / 仅本地模拟',flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
