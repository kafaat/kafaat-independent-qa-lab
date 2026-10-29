"""Separate polling worker; API never executes test commands."""
import argparse
import os
import time
from contextlib import closing
from .core import Lab

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--data-dir",default=os.environ.get("QA_DATA_DIR","/data"))
    parser.add_argument("--workspace",default=os.environ.get("QA_WORKSPACE","/workspace"))
    parser.add_argument("--interval",type=float,default=2)
    args=parser.parse_args()
    lab=Lab(args.data_dir)
    while True:
        with closing(lab.connect()) as db:
            row=db.execute("SELECT id FROM runs WHERE status='PENDING' ORDER BY created LIMIT 1").fetchone()
        if row:
            try: lab.execute(row[0],args.workspace)
            except (ValueError,KeyError,OSError,RuntimeError) as exc:
                print(f"worker rejected {row[0]}: {exc}",flush=True)
                try: lab.reject(row[0], str(exc))
                except (ValueError, KeyError): pass
                time.sleep(args.interval)
        else: time.sleep(args.interval)
if __name__=="__main__":main()
