"""Outlook connection and explicit meeting review. Only the Create button calls Graph."""

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ..services.outlook import OutlookClient


class OutlookWorker(QThread):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.succeeded.emit(self.operation())
        except Exception as exc:
            self.failed.emit(str(exc))


class OutlookScheduler(QObject):
    proposed = pyqtSignal(object)

    def __init__(self, buddy, client=None):
        super().__init__(buddy)
        self.buddy = buddy
        self.client = client or OutlookClient()
        self.worker = None
        self.dialog = None
        self.proposed.connect(self.review)

    @property
    def busy(self):
        return bool(self.worker and self.worker.isRunning())

    def propose(self, draft):
        self.proposed.emit(draft)

    def _run(self, operation, success, failure):
        self.worker = OutlookWorker(operation, self)
        self.worker.succeeded.connect(success)
        self.worker.failed.connect(failure)
        self.worker.start()

    def _dialog(self, title):
        if self.busy or (self.dialog and self.dialog.isVisible()):
            if self.dialog:
                self.dialog.raise_()
            self.buddy.chat.note("Finish or cancel the open Outlook dialog first; the new request wasn't opened.")
            return None
        self.dialog = QDialog(self.buddy)
        self.dialog.setWindowTitle(title)
        self.dialog.resize(540, 480)
        self.dialog.setModal(False)
        return self.dialog

    def connect_account(self):
        dialog = self._dialog("Connect Outlook scheduling")
        if dialog is None:
            return
        layout = QVBoxLayout(dialog)
        help_text = QLabel(
            'Register a Microsoft desktop app with redirect URI http://localhost and delegated '
            'Calendars.ReadWrite permission. No client secret is needed. '
            '<a href="https://entra.microsoft.com/">Open Microsoft Entra</a>', wordWrap=True)
        help_text.setOpenExternalLinks(True)
        layout.addWidget(help_text)
        form = QFormLayout()
        client_id = QLineEdit(self.client.config.get("client_id", ""))
        tenant = QLineEdit(self.client.config.get("tenant", "common"))
        form.addRow("Application (client) ID", client_id)
        form.addRow("Tenant ID (or common)", tenant)
        layout.addLayout(form)
        status = QLabel("Connected: " + self.client.account if self.client.account else "Not connected", wordWrap=True)
        layout.addWidget(status)
        sign_in = QPushButton("Sign in with Microsoft")
        disconnect = QPushButton("Disconnect scheduling")
        layout.addWidget(sign_in)
        layout.addWidget(disconnect)

        def enabled(value):
            for widget in (client_id, tenant, sign_in, disconnect):
                widget.setEnabled(value)

        def success(account):
            enabled(True)
            status.setText("Connected: " + account + ". You can now ask Buddy to schedule meetings.")

        def failed(message):
            enabled(True)
            status.setText(message)

        def sign():
            enabled(False)
            status.setText("Complete Microsoft sign-in in your browser. Waiting up to 3 minutes…")
            app_id, directory = client_id.text(), tenant.text()
            self._run(lambda: self.client.connect(app_id, directory), success, failed)

        def unlink():
            self.client.disconnect()
            status.setText("Disconnected. Calendar reminders using your subscription link are unchanged.")

        sign_in.clicked.connect(sign)
        disconnect.clicked.connect(unlink)
        dialog.show()

    def review(self, draft):
        dialog = self._dialog("Review Outlook meeting")
        if dialog is None:
            return
        layout = QVBoxLayout(dialog)
        account = self.client.account
        layout.addWidget(QLabel("Calendar: " + (account or "Not connected")))
        preview = QTextEdit()
        preview.setReadOnly(True)
        preview.setPlainText(draft.summary())
        layout.addWidget(preview)
        status = QLabel("Creating this meeting sends Outlook invitations to the listed attendees.", wordWrap=True)
        layout.addWidget(status)
        create = QPushButton("Create meeting")
        create.setEnabled(bool(account))
        cancel = QPushButton("Cancel")
        layout.addWidget(create)
        layout.addWidget(cancel)
        if not account:
            status.setText("Connect Outlook scheduling from Buddy's right-click menu, then ask for this meeting again.")

        def failed(message):
            status.setText(message)
            create.setText("Retry same meeting")
            create.setEnabled(True)
            cancel.setEnabled(True)

        def done(event):
            status.setText("Meeting created in Outlook." +
                           (" Invitations sent." if draft.attendees else ""))
            cancel.setEnabled(True)
            cancel.setText("Close")
            text = f"Created Outlook meeting: {draft.subject}, {draft.start.astimezone():%d %b %H:%M %Z}."
            self.buddy.chat.note(text)
            self.buddy.say(text, 7000)
            self.buddy.sync_calendar()

        def send():
            # Capture the reviewed draft; model output and later requests cannot change it.
            if self.busy:
                return
            create.setEnabled(False)
            cancel.setEnabled(False)
            status.setText("Creating meeting in Outlook…")
            self._run(lambda: self.client.create(draft), done, failed)

        create.clicked.connect(send)
        cancel.clicked.connect(dialog.reject)
        dialog.show()
