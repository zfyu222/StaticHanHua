"""Own a loopback-only portable Ollama subprocess. No installer, GUI or auto-start."""
import os
from pathlib import Path
import subprocess
import time
import requests


class Server:
    def __init__(self,root=None,port=11435):
        self.root=Path(root) if root else Path(__file__).resolve().parents[1]/'Ollama'
        self.endpoint=f'http://127.0.0.1:{port}'
        self.process=None

    def __enter__(self):
        self.http=requests.Session();self.http.trust_env=False
        try:
            self.http.get(self.endpoint+'/api/version',timeout=1)
        except requests.RequestException:pass
        else:raise RuntimeError('Dedicated local model port already in use; refusing to take over another service')
        executable=self.root/'runtime/ollama.exe'
        if not executable.exists():raise FileNotFoundError(executable)
        (self.root/'logs').mkdir(parents=True,exist_ok=True)
        self.log=(self.root/'logs/server.log').open('ab')
        env=dict(os.environ,OLLAMA_HOST=self.endpoint,OLLAMA_MODELS=str((self.root/'models').resolve()),
                 OLLAMA_NUM_PARALLEL='1',OLLAMA_MAX_LOADED_MODELS='1',OLLAMA_CONTEXT_LENGTH='8192',OLLAMA_KEEP_ALIVE='300',
                 OLLAMA_NO_CLOUD='1',OLLAMA_NOPRUNE='1')
        self.process=subprocess.Popen([str(executable),'serve'],env=env,stdout=self.log,stderr=self.log,
                                      creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            if self.process.poll() is not None:break
            try:
                response=self.http.get(self.endpoint+'/api/version',timeout=1)
                if response.ok:return self
            except requests.RequestException:pass
            time.sleep(.3)
        self.__exit__(None,None,None)
        raise RuntimeError('Local Ollama failed to start; see Tools/Ollama/logs/server.log')

    def pull(self,model):
        started=time.monotonic();last=0
        with self.http.post(self.endpoint+'/api/pull',json=dict(model=model,stream=True),stream=True,timeout=(10,300)) as response:
            response.raise_for_status()
            import json
            for line in response.iter_lines():
                if not line:continue
                row=json.loads(line)
                if row.get('error'):raise RuntimeError(row['error'])
                now=time.monotonic()
                if now-last>20 or row.get('status')=='success':
                    print(f'model pull: {row.get("status")}; {row.get("completed",0)//1048576}/{row.get("total",0)//1048576} MiB; elapsed={now-started:.0f}s',flush=True)
                    last=now

    def __exit__(self,*args):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        if hasattr(self,'http'):self.http.close()
        if hasattr(self,'log'):self.log.close()
