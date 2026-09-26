import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# never touch the real memory, recordings or minutes from tests
_tmp = tempfile.mkdtemp(prefix="buddy-test-")
os.environ["BUDDY_MEMORY_DB"] = os.path.join(_tmp, "memory.db")
os.environ["BUDDY_RECORDINGS_DIR"] = os.path.join(_tmp, "recordings")
os.environ["BUDDY_MINUTES_DIR"] = os.path.join(_tmp, "minutes")
