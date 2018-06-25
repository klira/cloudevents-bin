import cloudevents
import asyncio
import aiohttp
import aioredis
import ujson
from sanic import Sanic
from sanic.response import json, text
from sanic_cors import CORS

app = Sanic("cloudevents-bin", load_env="CE_BIN_")
cors = CORS(app, resources={r"/api/*": {"origins": "*"}})

stop_ws = None

INFO_STR = """CloudEvents is works like but JSON-bin but for CloudEvents. To use it simply start sending webhooks to `/ce/<namespace>` where namespace is an arbitrary string. You can then list your events using /api/<namespace>/events.  Its probably best to use a random ID for you namespace to avoid others using your namespace.  We don't offer any security, your data is visible to anyone who knows your namespace."""

@app.route("/")
async def info(request):
    return json({
        "cloudevents-bin": "0.1",
        "info": INFO_STR
    })


async def ping_approval_url(url):
    try:
        async with app.http.get(url) as resp:
            if not resp.status > 400:
                print("Response not OK pinging Webhook-Request-Callback at URL '{}'".format(url))
    except:
        print("Something went wrong pining Webhook-Request-Callback at URL '{}'".format(url))


async def handle_options(request):
    if 'WebHook-Request-Callback' in request.headers:
        url = request.headers["Webhook-Request-Callback"]
        app.add_task(ping_approval_url(url))

    resp_headers = {
        'WebHook-Allowed-Origin': '*',
        'Allow': 'OPTIONS,POST',
    }
    return text("Allowed!", status=200, headers=resp_headers)


async def handle_post(request, namespace):
    if request.json is None:
        return json({"err": "Only JSON bodies are supported"}, status=400)
    ce = cloudevents.parse(request.json)
    data = dict(namespace=namespace, event=ce.to_dict())
    list_key = key_name("events/" + namespace)
    push_f = app.redis.lpush(list_key, ujson.dumps(ce.to_dict()))
    pub_f = app.redis.publish(key_name("feed"), ujson.dumps(data))
    await asyncio.gather(push_f, pub_f)
    app.add_task(asyncio.ensure_future(app.redis.ltrim(list_key, 0, 200)))
    return json({"msg": "Got webhook!"}, status=202)


@app.route("/ce/<namespace>/", methods=["POST", "OPTIONS"])
async def receive_webhook(request, namespace):
    if request.method == "OPTIONS":
        return await handle_options(request)
    elif request.method == "POST":
        return await handle_post(request, namespace)
    else:
        return json({"err": "method not allowed"}, status=405)


@app.route("/api/<namespace>", methods=["GET"])
async def about_namespace(request, namespace):
    url = app.url_for("receive_webhook", namespace=namespace, _external=True)
    return json(
        dict(cloudevents_webhook_url=url),
        headers={"Link": "<{}>; rel=cloudevents-webhook".format(url)}
    )


@app.route("/api/<namespace>/events", methods=["GET"])
async def get_events(request, namespace):
    events = await app.redis.lrange(key_name("events/" + namespace), 0, 100)
    objects = list(ujson.loads(x) for x in events)
    return json(dict(events=objects))


@app.websocket('/api/<namespace>/feed')
async def event_feed(request, ws, namespace):
    sender_fn = ws.send
    try:
        if namespace not in app.subs:
            app.subs[namespace] = []
        app.subs[namespace].append(sender_fn)
        await stop_ws
    finally:
        if namespace in app.subs:
            app.subs[namespace].remove(sender_fn)


async def redis_reader(app, ch):
    while (await ch.wait_message()):
        msg = await ch.get_json()
        if "namespace" not in msg:
            print("BUG: Message missing namespace - {}".format(msg))
            continue

        if "event" not in msg:
            print("BUG: Message missing event - {}".format(msg))
            continue

        ns = msg["namespace"]
        for fns in app.subs[ns]:
            await fns(ujson.dumps(msg["event"]))


@app.listener('before_server_stop')
async def notify_server_stopping(app, loop):
    stop_ws.set_result(True)
    await asyncio.sleep(1)
    app.redis_sub.close()

    app.redis.close()
    await app.redis_sub.wait_closed()
    await app.redis.wait_closed()

    await app.http.close()


def key_name(x):
    prefix = app.config.REDIS_PREFIX if "REDIS_PREFIX" in app.config else "CLOUDEVENTS-BIN-"
    full = prefix + 'CLOUDEVENTS-BIN/'
    return full + x

@app.listener('before_server_start')
async def setup_something(app, loop):
    global stop_ws
    stop_ws = asyncio.Future(loop=loop)
    app.subs = dict()
    redis_sub = await aioredis.create_redis(
        app.config.REDIS_URL,
        loop=loop,
        password=app.config.REDIS_PASSWORD if 'REDIS_PASSWORD' in app.config else None
    )
    app.redis_sub = redis_sub
    app.redis = await aioredis.create_redis(
        app.config.REDIS_URL,
        loop=loop,
        password=app.config.REDIS_PASSWORD if 'REDIS_PASSWORD' in app.config else None
    )

    async def sub(r):
        subs = await r.subscribe(key_name("feed"))
        chan = subs[0]
        app.add_task(asyncio.ensure_future(redis_reader(app, chan)))

    app.add_task(asyncio.ensure_future(sub(redis_sub)))

    app.http = aiohttp.ClientSession(loop=loop)

if __name__ == "__main__":
    port = int(app.config.PORT) if "PORT" in app.config else 8080
    app.run(host="0.0.0.0", port=port)
