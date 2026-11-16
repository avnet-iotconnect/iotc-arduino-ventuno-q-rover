# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Avnet
# -----------------------------------------------------------------------------
# VENTUNO Q mecanum rover dashboard - API Lambda.
#
# Same shape as the iotc-mchp-dspic33ck-lvmcb dashboard: one Lambda behind an
# HTTP API serves the static page AND proxies /IOTCONNECT REST calls for it.
# It holds no /IOTCONNECT secret of its own: the browser's login form collects
# the solution key/environment along with the user's own email/password, and
# resends the solution key/environment with every request, so one shared
# deployment works for anyone with their own /IOTCONNECT account.
#
# Routes (HTTP API, payload v2):
#   GET  /  /dashboard.html          serves the page (same origin as the API,
#                                     so the browser's fetch() needs no CORS)
#   POST /api/dash/login             {email,password,solutionKey,env,pf} -> {token}
#   GET  /api/dash/bootstrap         devices + their command GUIDs
#   GET  /api/dash/telemetry?guid=&duid=   age of the newest rover telemetry message
#                                     (the page's "live" indicator)
#   POST /api/dash/command           {deviceGuid,commandGuid,value} -> send
# Every route past /login also needs the X-Iotc-Solution-Key/X-Iotc-Env/
# X-Iotc-Pf headers (see api() in dashboard.html).
# -----------------------------------------------------------------------------
import datetime
import json
import os
import traceback
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from iotc_client import Client, IoTConnectError

# Code of the template imported from templates/ventuno-rover-template.json.
# The code is the same in every account, unlike the template's GUID.
# Only devices on this template are listed. Set it to "" to list every device.
TEMPLATE_CODE = os.environ.get("TEMPLATE_CODE", "vqrover")

# discover() returns the same URLs for as long as a (solution key, env, pf)
# is valid, so it's cached per warm Lambda instance instead of being repeated
# on every request (which drew throttling errors in the lvmcb dashboard).
_discovery_cache = {}

# Every rover telemetry message carries all of its fields together (see
# send_telemetry() in app/python/main.py); "drv" marks a message as one.
TELEMETRY_MARKER = "drv"
HISTORY_LOOKBACK_MIN = 10
API_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"  # what /Telemetry/attribute-history expects
TEMPLATE_LOOKUP_WORKERS = 8


def iotc_client(solution_key, env, pf, user_token=None):
    if not (solution_key and env):
        raise IoTConnectError("missing solution key or environment")
    pf = pf or "aws"
    c = Client(solution_key=solution_key, env=env, pf=pf)
    cache_key = (solution_key, env, pf)
    if cache_key in _discovery_cache:
        c.urls = _discovery_cache[cache_key]
    else:
        c.discover()
        _discovery_cache[cache_key] = c.urls
    if user_token:
        c.token = user_token
    return c


def rover_template_guids(c):
    tpls = c.templates().get("data", [])
    guids = {t.get("guid") for t in tpls if (t.get("code") or "").lower() == TEMPLATE_CODE.lower()}
    if not guids:
        print("no template with code %r among %d templates (fields: %s)" % (
            TEMPLATE_CODE, len(tpls), sorted(tpls[0]) if tpls else []))
    return guids


def template_commands(c, tpl):
    try:
        cmds = c._req("GET", c.urls["deviceBaseUrl"] + "/template-command/%s/lookup" % tpl)
    except IoTConnectError as e:
        # A template with no commands returns an empty body, which lands here too.
        print("template-command lookup failed for %s: %s" % (tpl, e))
        return {}
    return {x.get("command"): x.get("guid") for x in cmds.get("data", []) if x.get("command")}


def telemetry_base(c):
    return c.urls.get("telemetryBaseUrl") or c.urls["deviceBaseUrl"].replace("device", "telemetry")


def resp(code, body, ctype="application/json"):
    return {"statusCode": code,
            "headers": {"Content-Type": ctype,
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Headers":
                            "Content-Type, Authorization, X-Iotc-Solution-Key, X-Iotc-Env, X-Iotc-Pf"},
            "body": body if isinstance(body, str) else json.dumps(body)}


def age_seconds(timestamp):
    try:
        t0 = datetime.datetime.strptime(timestamp[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None
    return (datetime.datetime.now(datetime.timezone.utc) - t0).total_seconds()


def newest_telemetry_time(c, guid, duid):
    # /Telemetry/device/{guid} only returns the latest value per attribute, and
    # the lvmcb dashboard caught it serving whole stale snapshots with fresh-looking
    # timestamps. The attribute-history endpoint returns each raw device message
    # as {"dTime": ..., "attr": {...}}, so its newest one is trustworthy. URL and
    # date format per Avnet's iotc-python-rest-api reference client.
    now = datetime.datetime.now(datetime.timezone.utc)
    from_str = (now - datetime.timedelta(minutes=HISTORY_LOOKBACK_MIN)).strftime(API_DATETIME_FORMAT)
    path = "/Telemetry/attribute-history/device/%s/from/%s/to/%s" % (
        urllib.parse.quote(duid, safe=""), urllib.parse.quote(from_str, safe=""),
        urllib.parse.quote(now.strftime(API_DATETIME_FORMAT), safe=""))
    raw = c._req("GET", telemetry_base(c) + path)
    feed = []
    if isinstance(raw, list):
        feed = raw
    elif isinstance(raw, dict):
        data = raw.get("data")
        for candidate in (raw.get("feed"), data.get("feed") if isinstance(data, dict) else None, data):
            if isinstance(candidate, list):
                feed = candidate
                break
    stamps = [item.get("dTime") or "" for item in feed
              if isinstance(item.get("attr"), dict) and TELEMETRY_MARKER in item["attr"]]
    if stamps:
        return max(stamps)
    # Nothing in the history window: fall back to the latest-value endpoint.
    rows = c._req("GET", telemetry_base(c) + "/Telemetry/device/" + guid).get("data", [])
    return max((r.get("deviceUpdatedDate") or "" for r in rows), default="") or None


def dash(event, path, headers, body_raw):
    token = headers.get("authorization", "")
    token = token[7:] if token.lower().startswith("bearer ") else ""
    solution_key = headers.get("x-iotc-solution-key", "")
    env = headers.get("x-iotc-env", "")
    pf = headers.get("x-iotc-pf", "")
    qs = event.get("queryStringParameters") or {}

    if path.endswith("/login"):
        try:
            b = json.loads(body_raw or "{}")
        except ValueError:
            return resp(400, {"error": "bad json"})
        email, password = (b.get("email") or "").strip(), b.get("password") or ""
        solution_key, env = (b.get("solutionKey") or "").strip(), (b.get("env") or "").strip()
        pf = (b.get("pf") or "aws").strip()
        if not email or not password:
            return resp(400, {"error": "email and password are required"})
        if not solution_key or not env:
            return resp(400, {"error": "solution key and environment are required"})
        try:
            data = iotc_client(solution_key, env, pf).login(email, password)
        except IoTConnectError:
            return resp(401, {"error": "Sign-in failed - check your /IOTCONNECT solution key, "
                                       "environment, email and password."})
        return resp(200, {"token": data.get("access_token") or data.get("accessToken")})

    if not token:
        return resp(401, {"error": "not signed in"})
    if not solution_key or not env:
        return resp(401, {"error": "missing solution key or environment - please sign in again"})

    c = iotc_client(solution_key, env, pf, token)
    if path.endswith("/bootstrap"):
        devs = c.devices().get("data", [])
        if TEMPLATE_CODE:
            rover_tpls = rover_template_guids(c)
            devs = [d for d in devs if d.get("deviceTemplateGuid") in rover_tpls]
        devs.sort(key=lambda d: (d.get("uniqueId") or "").lower())
        tpls = {d.get("deviceTemplateGuid") for d in devs if d.get("deviceTemplateGuid")}
        with ThreadPoolExecutor(max_workers=TEMPLATE_LOOKUP_WORKERS) as pool:
            cmds_by_tpl = dict(zip(tpls, pool.map(lambda t: template_commands(c, t), tpls)))
        return resp(200, {"devices": [{"guid": d["guid"], "uniqueId": d["uniqueId"],
                                       "templateGuid": d.get("deviceTemplateGuid")} for d in devs],
                          "commandsByTemplate": cmds_by_tpl})

    if path.endswith("/telemetry"):
        guid, duid = qs.get("guid") or "", qs.get("duid") or ""
        if not guid or not duid:
            return resp(400, {"error": "guid and duid are required"})
        stamp = newest_telemetry_time(c, guid, duid)
        return resp(200, {"age_s": age_seconds(stamp) if stamp else None})

    if path.endswith("/command"):
        b = json.loads(body_raw or "{}")
        src = ((event.get("requestContext") or {}).get("http") or {}).get("sourceIp", "?")
        print("dashboard command from %s: device=%s cmd=%s value=%r" % (
            src, (b.get("deviceGuid") or "")[-8:], (b.get("commandGuid") or "")[-8:], (b.get("value") or "")[:60]))
        c._req("POST", c.urls["deviceBaseUrl"] + "/template-command/device/%s/send" % b["deviceGuid"],
               body={"commandGuid": b["commandGuid"], "parameterValue": b.get("value", ""), "gatewayGuid": ""})
        return resp(200, {"sent": True})

    return resp(404, {"error": "unknown route"})


def lambda_handler(event, context):
    http = (event.get("requestContext") or {}).get("http") or {}
    method, path = http.get("method", ""), http.get("path", "")

    if method == "OPTIONS":
        return resp(200, "")
    if method == "GET" and path in ("/", "/dashboard", "/dashboard.html"):
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")) as f:
                return resp(200, f.read(), "text/html; charset=utf-8")
        except OSError:
            return resp(500, {"error": "dashboard.html missing from deployment"})
    if "/api/dash/" in path:
        hdrs = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        try:
            return dash(event, path, hdrs, event.get("body") or "")
        except IoTConnectError as e:
            return resp(502, {"error": str(e)[:300]})
        except KeyError as e:
            return resp(400, {"error": "missing field: %s" % e})
        except Exception as e:  # noqa: BLE001 - a short error in the browser beats API Gateway's bare 500
            traceback.print_exc()
            return resp(500, {"error": "%s: %s" % (type(e).__name__, str(e)[:300])})
    return resp(404, {"error": "not found"})
