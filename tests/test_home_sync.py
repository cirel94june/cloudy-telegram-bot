import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import home_sync


def test_sync_chat_needs_exactly_one_public_group():
    assert home_sync.sync_chat(["1", "-100", "-200"], ["-200"]) == "-100"
    assert home_sync.sync_chat(["-100", "-300"], []) == ""
    assert home_sync.sync_chat(["1"], []) == ""


def test_observe_only_sends_target_chat(monkeypatch):
    got = []
    monkeypatch.setattr(home_sync, "_enqueue", got.append)
    args = dict(target_chat="-100", bot_id="9", sender_name="甲", sender_id=5, sender_is_bot=False, is_owner=False)
    home_sync.observe({"message_id": 1, "date": 2}, "-200", "别的群", **args)
    home_sync.observe({"message_id": 1, "date": 2}, "-100", "大群", **args)
    home_sync.observe({"message_id": 2, "date": 3, "photo": [{}]}, "-100", "", **args)
    assert [b["text"] for b in got] == ["大群", "[图片]"]
    assert got[0]["sender_id"] == "5" and got[0]["reporter_bot_id"] == "9"


def test_bring_home_needs_owner_and_reply():
    ok = dict(target_chat="-100", is_owner=True, is_reply=True)
    assert home_sync.is_bring_home("-100", " /带回家 ", **ok)
    assert not home_sync.is_bring_home("-100", "/带回家", **{**ok, "is_owner": False})
    assert not home_sync.is_bring_home("-100", "/带回家", **{**ok, "is_reply": False})
    assert not home_sync.is_bring_home("-200", "/带回家", **ok)
    assert not home_sync.is_bring_home("-100", "带回家吧", **ok)


def test_sender_only_starts_for_the_chosen_bot(monkeypatch):
    started = []
    monkeypatch.setattr(home_sync.home_tools, "enabled", lambda: True)
    monkeypatch.setattr(home_sync.threading, "Thread", lambda **k: type("T", (), {"start": lambda s: started.append(1)})())
    monkeypatch.setattr(home_sync, "_sender_started", False)
    home_sync.start_sender("lucien", "-100", None, "小猫")
    assert not started
    home_sync.start_sender("cloudy", "", None, "小猫")
    assert not started
    home_sync.start_sender("cloudy", "-100", None, "小猫")
    assert started == [1]


def test_post_never_follows_redirects(monkeypatch):
    seen = {}
    monkeypatch.setattr(home_sync.requests, "post", lambda url, **k: seen.update(k))
    home_sync._post("message", {})
    assert seen["allow_redirects"] is False


def test_enqueue_never_raises(monkeypatch):
    class Broken:
        def __init__(self, **k): pass
        def start(self): raise RuntimeError("no thread")
    monkeypatch.setattr(home_sync.home_tools, "enabled", lambda: True)
    monkeypatch.setattr(home_sync.threading, "Thread", Broken)
    monkeypatch.setattr(home_sync, "_worker_started", False)
    home_sync._enqueue({"text": "x"})


def test_sender_refuses_other_group(monkeypatch):
    class End(BaseException):
        pass
    ticks = []
    def sleep(_):
        if ticks:
            raise End()
        ticks.append(1)
    monkeypatch.setattr(home_sync.time, "sleep", sleep)
    acks = []
    class R:
        status_code = 200
        def json(self):
            return {"item": {"id": 1, "chat_id": "-999", "text": "x"}}
    monkeypatch.setattr(home_sync, "_post", lambda p, b: acks.append(b) if p == "ack" else R())
    sent = []
    try:
        home_sync._send_loop(lambda c, t: sent.append(c) or True, "小猫", "-100")
    except End:
        pass
    assert not sent and acks == [{"id": 1, "ok": False}]


def test_sync_chat_falls_back_to_proactive_list():
    assert home_sync.sync_chat([], ["-200"], ["-100", "-200"]) == "-100"
    assert home_sync.sync_chat(["1"], [], ["-100"]) == "-100"
    assert home_sync.sync_chat(["-300"], [], ["-100"]) == "-300"
    assert home_sync.sync_chat([], [], ["-100", "-200"]) == ""
