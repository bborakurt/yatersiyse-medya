#!/usr/bin/env python3
"""Publishing queue: posts every due item in yayin/queue.json to Instagram through Composio.

Runs on GitHub Actions every few minutes (.github/workflows/yayin.yml). Metricool hit its account limit
on 19 Nov 2026, so this replaces its scheduler for Instagram.

Item states: pending -> posting (container made, creation_id saved) -> done (permalink saved).
An item stuck in "posting" is finished with the SAME container, never a new one, so a crash or
timeout can never produce a double post. An item more than MAX_LATE_H hours late is set to "late"
and left for a human, instead of going out at a bad hour.

Env: COMPOSIO_API_KEY (GitHub secret). Stdlib only.
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://backend.composio.dev/api/v3.1"
HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(HERE, "queue.json")
MAX_LATE_H = 3
GRACE_MIN = int(os.environ.get("GRACE_MIN", "0"))  # Mac backup waits so GitHub gets the first try
TZ = dt.timezone(dt.timedelta(hours=3))  # Europe/Istanbul (no DST)


def execute(slug: str, acc: dict, args: dict, timeout: int = 420) -> dict:
    body = {"connected_account_id": acc["connected_account_id"], "user_id": acc["user_id"], "arguments": args}
    req = urllib.request.Request(f"{API}/tools/execute/{slug}", method="POST", data=json.dumps(body).encode(),
                                 headers={"x-api-key": os.environ["COMPOSIO_API_KEY"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"successful": False, "error": f"HTTP {e.code} {e.read()[:300]!r}"}
    except Exception as e:  # timeouts: the state machine retries with the same container next run
        return {"successful": False, "error": repr(e)}


def data(r: dict):
    d = r.get("data") or {}
    return d.get("data", d) if isinstance(d, dict) else d


def recent_media(acc: dict) -> list:
    r = execute("INSTAGRAM_GET_IG_USER_MEDIA", acc, {"ig_user_id": "me", "limit": 10, "fields": "id,caption,timestamp,permalink"}, 120)
    d = data(r)
    return d if isinstance(d, list) else []


def already_live(acc: dict, caption: str):
    for m in recent_media(acc):
        if (m.get("caption") or "").strip() == caption.strip():
            return m
    return None


def post(item: dict, acc: dict, log) -> None:
    if item["status"] == "pending":
        live = already_live(acc, item["caption"])
        if live:  # posted by hand or by an earlier crashed run
            item.update(status="done", permalink=live.get("permalink"), note="zaten yayındaydı")
            return
        args = {"ig_user_id": "me", "media_type": "REELS", "share_to_feed": True,
                "video_url": item["video_url"], "caption": item["caption"]}
        if item.get("cover_url"):
            args["cover_url"] = item["cover_url"]
        r = execute("INSTAGRAM_POST_IG_USER_MEDIA", acc, args, 180)
        cid = (data(r) or {}).get("id") if r.get("successful") else None
        if not cid:
            item["last_error"] = str(r.get("error"))[:400]
            log(f"{item['id']}: kapsayıcı oluşmadı: {item['last_error']}")
            return
        item.update(status="posting", creation_id=cid)
        save()  # persist the container id before publishing
    if item["status"] == "posting":
        r = execute("INSTAGRAM_POST_IG_USER_MEDIA_PUBLISH", acc, {"ig_user_id": "me", "creation_id": item["creation_id"], "max_wait_seconds": 300})
        time.sleep(5)
        live = already_live(acc, item["caption"])
        if live:
            item.update(status="done", permalink=live.get("permalink"), posted_at=live.get("timestamp"))
            item.pop("last_error", None)
            log(f"{item['id']}: YAYINDA {live.get('permalink')}")
        else:
            item["last_error"] = str(r.get("error"))[:400]
            log(f"{item['id']}: henüz yayında değil, sonraki turda aynı kapsayıcıyla denenecek: {item['last_error']}")


STATE = {}


def save():
    json.dump(STATE["q"], open(QUEUE, "w"), ensure_ascii=False, indent=1)
    open(QUEUE, "a").write("\n")


def main():
    STATE["q"] = q = json.load(open(QUEUE))
    now = dt.datetime.now(TZ)
    log = lambda s: print(f"[{now:%Y-%m-%d %H:%M}] {s}", flush=True)
    for item in q["items"]:
        if item["status"] not in ("pending", "posting"):
            continue
        at = dt.datetime.fromisoformat(item["at"]).replace(tzinfo=TZ)
        if at > now or (now - at).total_seconds() < GRACE_MIN * 60:
            continue
        if item["status"] == "pending" and (now - at).total_seconds() > MAX_LATE_H * 3600:
            item["status"] = "late"
            log(f"{item['id']}: {MAX_LATE_H} saatten fazla gecikti, atılmadı (elle yeniden planlanmalı)")
            continue
        acc = q["accounts"][item["account"]]
        post(item, acc, log)
        save()
    save()


if __name__ == "__main__":
    sys.exit(main())
