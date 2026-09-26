"""The WhatsApp link, as a helper process: `python -m deskbuddy.services.whatsapp_worker DB`.

It runs separately because neonize's connection thread can't be stopped from Python (the
app would never quit), and a crash in its Go core then can't take Buddy down. Stopping it
is just closing its stdin (or killing it).

JSON lines. In (stdin):  {"cmd": "send", "id": 1, "jid": "...", "text": "..."}
                         {"cmd": "contacts"}   {"cmd": "logout"}
Out (stdout): {"event": "qr", "data": "..."}      scan this in WhatsApp → Linked devices
              {"event": "connected"}               {"event": "logged_out"}
              {"event": "contacts", "items": [{"jid": ..., "name": ...}]}
              {"event": "sent", "id": 1, "ok": true}   (or "ok": false, "error": "...")"""

import json
import logging
import os
import sys
import threading

_out = threading.Lock()
_channel = None                  # the real stdout; fd 1 itself goes to stderr (see main)


def emit(**event):
    with _out:
        _channel.write(json.dumps(event, ensure_ascii=False) + "\n")
        _channel.flush()


def contact_list(client):
    items = []
    for c in client.contact.get_all_contacts():
        jid = f"{c.JID.User}@{c.JID.Server}"
        if c.JID.Server != "s.whatsapp.net":
            continue
        info = c.Info
        name = info.FullName or info.FirstName or info.PushName or info.BusinessName
        if name:
            items.append({"jid": jid, "name": name})
    return items


def main(db):
    # neonize and its Go core print to stdout here and there; keep that off our channel
    global _channel
    _channel = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    from neonize.client import NewClient
    from neonize.events import ConnectedEv, LoggedOutEv
    from neonize.utils.jid import build_jid

    os.makedirs(os.path.dirname(db), exist_ok=True)
    client = NewClient(db)
    client.event.qr(lambda _c, data: emit(event="qr", data=data.decode()))
    linked = threading.Event()                   # the Go core aborts on store calls before this

    @client.event(ConnectedEv)
    def _connected(_c, _ev):
        linked.set()
        emit(event="connected")

    @client.event(LoggedOutEv)
    def _logged_out(_c, _ev):
        linked.clear()
        emit(event="logged_out")

    threading.Thread(target=client.connect, daemon=True).start()

    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        cmd = msg.get("cmd")
        if not linked.is_set():
            if cmd == "send":
                emit(event="sent", id=msg.get("id"), ok=False, error="WhatsApp isn't connected")
            continue
        try:
            if cmd == "send":
                user, _, server = str(msg["jid"]).partition("@")
                client.send_message(build_jid(user, server or "s.whatsapp.net"), str(msg["text"]))
                emit(event="sent", id=msg.get("id"), ok=True)
            elif cmd == "contacts":
                emit(event="contacts", items=contact_list(client))
            elif cmd == "logout":
                client.logout()
                emit(event="logged_out")
        except Exception as exc:                 # reported to the app, which tells the user
            if cmd == "send":
                emit(event="sent", id=msg.get("id"), ok=False, error=str(exc)[:200])
            else:
                emit(event="error", message=f"{cmd}: {exc}"[:200])
    os._exit(0)                                  # stdin closed: the app quit


if __name__ == "__main__":
    main(sys.argv[1])
