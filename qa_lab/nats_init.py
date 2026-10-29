"""Initialize JetStream stream with durable file storage."""
import asyncio
import os
import nats

async def main():
    nc=await nats.connect(os.environ.get('QA_NATS_URL','nats://nats:4222'))
    js=nc.jetstream()
    from nats.js.api import StreamConfig,StorageType,RetentionPolicy
    await js.add_stream(config=StreamConfig(name='QA_JOBS',subjects=['qa.jobs.*'],storage=StorageType.FILE,retention=RetentionPolicy.LIMITS))
    await nc.drain()

if __name__=='__main__':asyncio.run(main())
