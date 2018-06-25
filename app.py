import cloudevents
import asyncio
import aiohttp
import ujson
from db import DB
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
    await app.db.register_event(namespace, ce)

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
    objects = await app.db.get_events(namespace)
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


def send_event(ns, event):
    data = ujson.dumps(event)
    return asyncio.gather(*(
        fn(data)
        for fn in app.subs.get(ns, [])
    ))


@app.listener('before_server_stop')
async def notify_server_stopping(app, loop):
    stop_ws.set_result(True)
    await asyncio.sleep(1)


@app.listener('after_server_stop')
async def close_db(app, loop):
    await asyncio.gather(
        app.db.close(),
        app.http.close()
    )


@app.listener('before_server_start')
async def setup_something(app, loop):
    global stop_ws
    stop_ws = asyncio.Future(loop=loop)
    app.subs = dict()
    app.db = DB(app.config, loop, send_event)
    await app.db.start()
    app.http = aiohttp.ClientSession(loop=loop)

if __name__ == "__main__":
    port = int(app.config.PORT) if "PORT" in app.config else 8080
    app.run(host="0.0.0.0", port=port)
