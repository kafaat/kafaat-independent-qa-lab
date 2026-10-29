import argparse
import json
from .core import Lab
from .api import serve

def main():
    p=argparse.ArgumentParser(description="Independent QA Lab")
    p.add_argument("--data-dir",default=".qa-lab-data")
    sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("recipes")
    s=sub.add_parser("submit");s.add_argument("request_json")
    s=sub.add_parser("run");s.add_argument("run_id");s.add_argument("--workspace",required=True)
    s=sub.add_parser("get");s.add_argument("run_id")
    s=sub.add_parser("verify");s.add_argument("run_id")
    s=sub.add_parser("serve");s.add_argument("--port",type=int,default=8765)
    a=p.parse_args()
    if a.command=="serve":return serve(a.data_dir,host=__import__("os").environ.get("QA_BIND_HOST","127.0.0.1"),port=a.port)
    lab=Lab(a.data_dir)
    if a.command=="recipes":from .core import RECIPES; result=sorted(RECIPES)
    elif a.command=="submit":result=lab.submit(json.loads(open(a.request_json,encoding="utf-8").read()))
    elif a.command=="run":result=lab.execute(a.run_id,a.workspace)
    elif a.command=="get":result=lab.get(a.run_id)
    else:result={"valid":lab.verify(a.run_id)}
    print(json.dumps(result,indent=2,ensure_ascii=False))
if __name__=="__main__":main()
