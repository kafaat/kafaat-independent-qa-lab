"""JetStream file-storage restart acceptance, executed by GitHub CI.

Uses an isolated Docker container with a persistent named volume. Publish,
stop/remove server, recreate it with the same volume, then consume the event.
"""
import asyncio
import json
import os
import subprocess
import time
import unittest
import uuid

@unittest.skipUnless(os.environ.get('QA_NATS_RESTART_TEST')=='1','requires dedicated Docker restart CI')
class NATSRestartTests(unittest.TestCase):
    def test_file_storage_survives_container_recreation(self):
        asyncio.run(self.scenario())
    async def scenario(self):
        import nats
        from nats.js.api import StreamConfig,StorageType
        name='qa-restart-'+uuid.uuid4().hex[:10]
        volume=name+'-data'
        subject='qa.restart.'+uuid.uuid4().hex
        stream='QA_RESTART_'+uuid.uuid4().hex[:10].upper()
        def docker(*args):
            return subprocess.run(['docker',*args],capture_output=True,text=True,check=True,timeout=30)
        async def connect():
            for _ in range(40):
                try:return await nats.connect('nats://127.0.0.1:4223',connect_timeout=1)
                except Exception:await asyncio.sleep(.25)
            raise RuntimeError('NATS not ready')
        docker('volume','create',volume)
        try:
            docker('run','-d','--name',name,'-p','127.0.0.1:4223:4222','-v',f'{volume}:/data','nats:2.10-alpine','-js','-sd','/data')
            nc=await connect()
            js=nc.jetstream()
            await js.add_stream(config=StreamConfig(name=stream,subjects=[subject],storage=StorageType.FILE))
            await js.publish(subject,json.dumps({'event_id':str(uuid.uuid4())}).encode())
            await nc.drain()
            docker('rm','-f',name)
            docker('run','-d','--name',name,'-p','127.0.0.1:4223:4222','-v',f'{volume}:/data','nats:2.10-alpine','-js','-sd','/data')
            nc=await connect()
            js=nc.jetstream()
            sub=await js.pull_subscribe(subject,durable='restart-test',stream=stream)
            messages=await sub.fetch(batch=1,timeout=5)
            self.assertEqual(len(messages),1)
            await messages[0].ack()
            await nc.drain()
        finally:
            subprocess.run(['docker','rm','-f',name],capture_output=True)
            subprocess.run(['docker','volume','rm',volume],capture_output=True)
