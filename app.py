import cloudevents
import aiohttp
from sanic import Sanic
from sanic.response import json, text
from sanic_cors import CORS

app = Sanic("cloudevents-bin", load_env="CE_BIN_")
cors = CORS(app, resources={r"/api/*": {"origins": "*"}})

class EventDB(object):
    def __init__(self):
        self.db = dict()

    def _get_list(self, k):
        if k in self.db:
            return self.db[k]
        else:
            l = list()
            self.db[k] = l
            return l

    def _add_to_list(self, k, v):
        self._get_list(k).append(v)

    def log_event(self, namespace, event):
        key = (namespace, event.event_type)
        self._add_to_list(key, event)
        self._add_to_list(namespace, event)

    def get_events_for_type(self, namespace, event_type):
        key = (namespace, event_type)
        return self._get_list(key)

    def get_events(self, namespace):
        return self._get_list(namespace)

    def all_keys(self):
        return self.db.keys()


db = EventDB()

INFO_STR = """CloudEvents is works like but JSON-bin but for CloudEvents. To use it simply start sending webhooks to `/ce/<namespace>` where namespace is an arbitrary string. You can then list your events using /api/<namespace>/events.  Its probably best to use a random ID for you namespace to avoid others using your namespace.  We don't offer any security, your data is visible to anyone who knows your namespace."""

@app.route("/")
async def info(request):
    return json({
        "cloudevents-bin": "0.1",
        "info": INFO_STR
    })


async def ping_approval_url(url):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.github.com/events') as resp:
                if not resp.status > 400:
                    print("Response not OK pinging Webhook-Request-Callback at URL '{}'".format(url))
    except:
        print("Something went wrong pining Webhook-Request-Callback at URL '{}'".format(url))


async def handle_options(request):
    if 'WebHook-Request-Callback' in request.headers:
        url = request.headers["Webhook-Request-Callback"]
        ping_approval_url(url)

    resp_headers = {
        'WebHook-Allowed-Origin': '*',
        'Allow': 'OPTIONS,POST',
    }
    return text("Allowed!", status=200, headers=resp_headers)


def handle_post(request, namespace):
    if request.json is None:
        return json({"err": "Only JSON bodies are supported"}, status=400)
    ce = cloudevents.parse(request.json)
    db.log_event(namespace, ce)
    return json({"msg": "Got webhook!"}, status=202)


@app.route("/ce/<namespace>/", methods=["POST", "OPTIONS"])
async def receive_webhook(request, namespace):
    if request.method == "OPTIONS":
        return await handle_options(request)
    elif request.method == "POST":
        return handle_post(request, namespace)
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
    events = db.get_events(namespace)
    objects = (e.to_dict() for e in events)
    return json(dict(events=objects))

if __name__ == "__main__":
    port = int(app.config.PORT) if "PORT" in app.config else 8080
    app.run(host="0.0.0.0", port=port)
