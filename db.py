import aioredis
import asyncio
import ujson


async def redis_worker(ch, fn):
    while (await ch.wait_message()):
        msg = await ch.get_json()
        ns = msg.get("namespace")
        if ns is None:
            print("BUG: Message missing namespace - {}".format(msg))
            continue
        event = msg.get("event")
        if "event" is None:
            print("BUG: Message missing event - {}".format(msg))
            continue

        await fn(ns, event)


class DB(object):
    def __init__(self, cfg, loop, fn):
        self.fn = fn
        prefix = (
            cfg.REDIS_PREFIX
            if "REDIS_PREFIX" in cfg
            else "CLOUDEVENTS-BIN"
        )
        self.full_prefix = "{}-CLOUDEVENTS-BIN".format(prefix)
        self.loop = loop
        self.redis_url = cfg.REDIS_URL
        self.password = (
            cfg.REDIS_PASSWORD
            if 'REDIS_PASSWORD' in cfg
            else None
        )
        self.redis_sub = None
        self.redis = None

    def key_name(self, x):
        return self.full_prefix + x

    async def _sub(self):
        k = self.key_name("feed")
        subs = await self.redis_sub.subscribe(k)
        chan = subs[0]
        await redis_worker(chan, self.fn)

    def _mk_redis(self):
        return aioredis.create_redis(
            self.redis_url,
            loop=self.loop,
            password=self.password
        )

    async def start(self):
        self.redis_sub = await self._mk_redis()
        self.redis = await self._mk_redis()
        self.loop.create_task(self._sub())

    async def close(self):
        connections = [self.redis, self.redis_sub]
        for c in connections:
            c.close()

        await asyncio.gather(
            *(c.wait_closed() for c in connections)
        )

    async def register_event(self, namespace, ce):
        redis = self.redis

        data = dict(namespace=namespace, event=ce.to_dict())
        list_key = self.key_name("events/" + namespace)
        push_f = redis.lpush(list_key, ujson.dumps(ce.to_dict()))
        pub_key = self.key_name("feed")
        pub_f = redis.publish(pub_key, ujson.dumps(data))
        await asyncio.gather(push_f, pub_f)
        self.loop.create_task(redis.ltrim(list_key, 0, 200))

    async def get_events(self, namespace):
        k = self.key_name("events/" + namespace)
        events = await self.redis.lrange(k, 0, 100)
        return list(ujson.loads(x) for x in events)
