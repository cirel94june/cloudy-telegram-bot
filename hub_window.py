"""Bounded, window-scoped recovery for the Memory Hub #44 contract."""
import time
from threading import Lock, Thread

import requests


def canonical_ai(value):
    value = str(value or "").strip().lower()
    return "cloudy" if value == "claude" else value


def events_from_response(data, chat_id, thread_id, ai_id, ceci_id, make_event):
    """Reject mismatched scope; missing legacy identities remain unknown."""
    cid, tid = str(chat_id), str(thread_id or "")
    if not isinstance(data, dict) or data.get("error"):
        raise ValueError("invalid window response")
    if str(data.get("chat_id")) != cid or str(data.get("thread_id") or "") != tid:
        raise ValueError("window scope mismatch")
    turns = data.get("turns")
    if not isinstance(turns, list) or len(turns) > 30:
        raise ValueError("invalid turns")
    events = []
    current = canonical_ai(ai_id)
    for row in turns:
        if not isinstance(row, dict):
            raise ValueError("invalid turn")
        if str(row.get("chat_id")) != cid or str(row.get("thread_id") or "") != tid:
            raise ValueError("turn scope mismatch")
        owner = canonical_ai(row.get("ai_id"))
        if not cid.startswith("-") and owner != current:
            raise ValueError("private agent mismatch")
        stamp = str(row.get("created_at") or "")
        mid = str(row.get("message_id") or "")
        sender = str(row.get("sender_id") or "")
        sender_type = "agent" if row.get("sender_type") in ("agent", "bot") else "user"
        stable = ("bot:" if sender_type == "agent" else "user:") + (sender or "unknown")
        if sender_type == "user" and sender and sender == str(ceci_id):
            stable = "ceci"
        marker = str(row.get("turn_id") or row.get("id") or "")
        for role, field in (("user", "user_text"), ("assistant", "ai_text")):
            body = row.get(field) or ""
            if not isinstance(body, str):
                raise ValueError("invalid turn text")
            if not body.strip():
                continue
            if role == "assistant" and not owner:
                owner = "bot:unknown"
            event = make_event(
                role=role, content=body, raw_text=body, chat_id=cid, thread_id=tid,
                telegram_message_id=mid if role == "user" else "",
                sender_type=sender_type if role == "user" else "agent",
                stable_sender_id=stable if role == "user" else owner,
                reply_to_message_id=row.get("reply_to_id", "") if role == "user" else mid,
                created_at=stamp,
                sender_display="" if role == "user" else owner,
            )
            # The Hub stores a combined reply, not its Telegram delivery IDs.
            event["hub_recovery_key"] = (owner, marker, role, body if not marker else "")
            if row.get("truncated"):
                event["context_note"] = "This stored turn is incomplete; do not infer missing text."
            events.append(event)
    return events


def merge_events(restored, live):
    result, positions = [], {}
    for event in list(restored) + list(live):
        mid = str(event.get("telegram_message_id") or "")
        if mid:
            key = ("telegram", str(event.get("thread_id") or ""), mid)
        elif event.get("hub_recovery_key"):
            key = ("hub", tuple(event["hub_recovery_key"]))
        else:
            key = ("legacy", event.get("role"), event.get("stable_sender_id"),
                   event.get("created_at"), event.get("raw_text") or event.get("content"))
        if key in positions:
            result[positions[key]] = event
        else:
            positions[key] = len(result)
            result.append(event)
    return result[-100:]


class WindowRecovery:
    def __init__(self, timeout=3.0, retry_after=60.0):
        self.timeout = timeout
        self.retry_after = retry_after
        self.lock = Lock()
        self.busy = False
        self.states = {}

    def restore(self, history, chat_id, thread_id, *, url, headers, ai_id,
                ceci_id, make_event, history_lock, allow=True):
        if not url or not ai_id or not headers:
            return
        key = (str(chat_id), str(thread_id or ""))
        with self.lock:
            state = self.states.get(key)
            if state is None or state["history"] is not history:
                state = {"history": history, "done": bool(history), "next": 0.0}
                self.states[key] = state
            if state["done"] or not allow or self.busy or time.monotonic() < state["next"]:
                return
            self.busy = True
            state["next"] = time.monotonic() + self.retry_after
        box = {}

        def fetch():
            try:
                response = requests.post(
                    url.rstrip("/") + "/api/window/context", headers=headers,
                    json={"ai_id": ai_id, "chat_id": key[0], "thread_id": key[1],
                          "max_turns": 10, "max_chars": 20000},
                    timeout=(2, self.timeout),
                )
                response.raise_for_status()
                box["events"] = events_from_response(
                    response.json(), *key, ai_id, ceci_id, make_event)
            except Exception as exc:
                # Do not log response bodies, credentials, or private conversation text.
                print(f"[WINDOW] recovery failed chat={key[0]} type={type(exc).__name__}")
            finally:
                with self.lock:
                    self.busy = False

        worker = Thread(target=fetch, daemon=True)
        worker.start()
        worker.join(self.timeout)
        if worker.is_alive():
            print(f"[WINDOW] deadline chat={key[0]}; reply continues")
            return
        if "events" not in box:
            return
        with history_lock:
            history[:] = merge_events(box["events"], history)
        with self.lock:
            state["done"] = True
        print(f"[WINDOW] restored chat={key[0]} thread={key[1] or '-'} events={len(box['events'])}")

