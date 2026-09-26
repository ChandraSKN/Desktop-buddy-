"""The app side of deskbuddy/ipc.py: accepts connections on the local socket and hands each
message to Buddy on the UI thread."""

import json

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer

from .. import ipc


class CommandServer(QObject):
    command = pyqtSignal(dict, object)        # message, reply(dict) callback

    def __init__(self, parent=None, path=None):
        super().__init__(parent)
        self.path = str(path or ipc.SOCKET)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)   # owner only
        self.server.newConnection.connect(self._accept)

    def start(self):
        QLocalServer.removeServer(self.path)            # a stale socket from a crash
        return self.server.listen(self.path)

    def _accept(self):
        while self.server.hasPendingConnections():
            conn = self.server.nextPendingConnection()
            conn.readyRead.connect(lambda c=conn: self._read(c))
            conn.disconnected.connect(conn.deleteLater)

    def _read(self, conn):
        while conn.canReadLine():
            line = bytes(conn.readLine()).decode(errors="replace")
            message = ipc.parse(line)
            if message is None:
                continue

            def reply(data, c=conn):
                c.write(json.dumps(data).encode() + b"\n")
                c.flush()
            self.command.emit(message, reply)
