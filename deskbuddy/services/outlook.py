"""Validated Outlook meeting drafts. Drafting never sends an invitation."""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4


@dataclass(frozen=True)
class MeetingDraft:
    subject: str
    start: datetime
    end: datetime
    attendees: tuple[str, ...] = ()
    body: str = ""
    location: str = ""
    teams: bool = False
    transaction_id: str = field(default_factory=lambda: str(uuid4()))

    def payload(self):
        def stamp(value):
            return {"dateTime": value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"}
        result = {
            "subject": self.subject, "start": stamp(self.start), "end": stamp(self.end),
            "body": {"contentType": "text", "content": self.body},
            "location": {"displayName": self.location},
            "attendees": [{"emailAddress": {"address": email}, "type": "required"} for email in self.attendees],
            "transactionId": self.transaction_id,
        }
        if self.teams:
            result.update(isOnlineMeeting=True, onlineMeetingProvider="teamsForBusiness")
        return result

    def summary(self):
        return (f"{self.subject}\n"
                f"{self.start.astimezone():%a %d %b %Y, %H:%M %Z} → "
                f"{self.end.astimezone():%a %d %b %Y, %H:%M %Z}\n"
                f"Attendees: {', '.join(self.attendees) or 'Just you'}\n"
                f"Location: {self.location or 'Not specified'}\n"
                f"Teams meeting: {'Yes' if self.teams else 'No'}\n\n{self.body}")


def make_draft(subject, start, end, attendees="", body="", location="", teams=False, now=None):
    """ISO times must include offsets so the preview and Outlook agree across time zones."""
    if not subject.strip() or len(subject) > 255:
        raise ValueError("Use a meeting title between 1 and 255 characters.")
    try:
        begin, finish = datetime.fromisoformat(start), datetime.fromisoformat(end)
    except ValueError as exc:
        raise ValueError("Give start and end as ISO date-times including a UTC offset.") from exc
    if begin.tzinfo is None or finish.tzinfo is None:
        raise ValueError("Start and end must include the UTC offset, for example +05:30.")
    if begin <= (now or datetime.now(UTC)) or finish <= begin:
        raise ValueError("The start must be in the future, and the end must follow the start.")
    addresses = tuple(dict.fromkeys(a.strip().lower() for a in re.split(r"[,;]", attendees) if a.strip()))
    if len(addresses) > 50 or any(not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", a)
                                  for a in addresses):
        raise ValueError("Provide up to 50 valid attendee email addresses; do not guess addresses from names.")
    if len(body) > 10000 or len(location) > 500:
        raise ValueError("The description or location is too long.")
    return MeetingDraft(subject.strip(), begin, finish, addresses, body.strip(), location.strip(), teams)


class OutlookError(RuntimeError):
    pass


class OutlookClient:
    """Delegated Graph access for one explicitly selected Microsoft account.

    Network methods are called by the UI's background worker, never the assistant.
    Credentials are stored locally with owner-only permissions, outside the repository.
    """

    SCOPES = ["https://graph.microsoft.com/Calendars.ReadWrite"]

    def __init__(self, root=None):
        import json
        import os
        from pathlib import Path
        self.root = Path(root or os.environ.get("BUDDY_OUTLOOK_DIR") or
                         Path.home() / ".config" / "desktop-buddy" / "outlook")
        try:
            self.config = json.loads((self.root / "account.json").read_text())
        except (OSError, ValueError):
            self.config = {}

    @property
    def account(self):
        return self.config.get("username", "")

    def _save(self, name, value):
        import os
        import tempfile
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        fd, path = tempfile.mkstemp(dir=self.root)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(value)
            os.replace(path, self.root / name)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def _app(self, config, fresh=False):
        import msal
        cache = msal.SerializableTokenCache()
        if not fresh:
            try:
                cache.deserialize((self.root / "tokens.json").read_text())
            except (OSError, ValueError):
                pass
        app = msal.PublicClientApplication(config["client_id"], token_cache=cache,
                                          authority="https://login.microsoftonline.com/" + config["tenant"],
                                          timeout=20)
        return app, cache

    def connect(self, client_id, tenant):
        import json
        from uuid import UUID
        try:
            client_id = str(UUID(client_id.strip()))
            tenant = tenant.strip() or "common"
            if tenant not in ("common", "organizations", "consumers"):
                tenant = str(UUID(tenant))
        except ValueError as exc:
            raise OutlookError("Enter a valid Application ID and tenant ID (or common).") from exc
        config = {"client_id": client_id, "tenant": tenant}
        app, cache = self._app(config, fresh=True)
        result = app.acquire_token_interactive(scopes=self.SCOPES, prompt="select_account", timeout=180)
        if "access_token" not in result:
            raise OutlookError("Microsoft sign-in did not complete: " + result.get("error", "cancelled"))
        accounts = app.get_accounts()
        if len(accounts) != 1:
            raise OutlookError("Sign in with exactly one Outlook account.")
        account = accounts[0]
        config.update(username=account.get("username", "Microsoft account"),
                      account_id=account["home_account_id"])
        self._save("tokens.json", cache.serialize())
        self._save("account.json", json.dumps(config))
        self.config = config
        return self.account

    def disconnect(self):
        for name in ("tokens.json", "account.json"):
            (self.root / name).unlink(missing_ok=True)
        self.config = {}

    def _token(self):
        if not self.account:
            raise OutlookError("Connect Outlook scheduling from Buddy's right-click menu first.")
        app, cache = self._app(self.config)
        account = next((a for a in app.get_accounts()
                        if a["home_account_id"] == self.config["account_id"]), None)
        result = app.acquire_token_silent(self.SCOPES, account=account) if account else None
        if cache.has_state_changed:
            self._save("tokens.json", cache.serialize())
        if not result or "access_token" not in result:
            raise OutlookError("Microsoft sign-in expired. Connect Outlook scheduling again.")
        return result["access_token"]

    def create(self, draft):
        import requests
        if draft.start <= datetime.now(UTC):
            raise OutlookError("This meeting's start time has passed. Ask Buddy for an updated draft.")
        token = self._token()
        try:
            response = requests.post("https://graph.microsoft.com/v1.0/me/events",
                                     headers={"Authorization": "Bearer " + token},
                                     json=draft.payload(), timeout=30, allow_redirects=False)
        except requests.RequestException as exc:
            raise OutlookError("No response from Outlook. Check your calendar before retrying; "
                               "Retry reuses this draft's transaction ID.") from exc
        if response.status_code != 201:
            if response.status_code == 401:
                message = "Microsoft sign-in expired. Connect Outlook scheduling again."
            elif response.status_code == 403:
                message = "Microsoft denied calendar access. Check Calendars.ReadWrite consent with your administrator."
            else:
                message = (f"Outlook returned HTTP {response.status_code}. Check your calendar before retrying. "
                           "For Teams meetings, check that this account supports Teams.")
            raise OutlookError(message)
        try:
            event = response.json()
            if not event.get("id"):
                raise ValueError("missing event id")
        except ValueError as exc:
            raise OutlookError("Outlook returned an unexpected response. Check your calendar before retrying.") from exc
        return event
