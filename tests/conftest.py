import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# never touch the real memory database from tests
os.environ["BUDDY_MEMORY_DB"] = os.path.join(tempfile.mkdtemp(prefix="buddy-test-"), "memory.db")
