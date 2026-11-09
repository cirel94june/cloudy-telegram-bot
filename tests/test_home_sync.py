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
