import os
import tempfile
from pathlib import Path

# Isolate the API TestClient's checkpoint DB from the real .checkpoints DB.
# Must run before app.main is imported by any test module.
os.environ.setdefault(
    "CAD_DB_PATH", str(Path(tempfile.mkdtemp()) / "api_test_cad.db")
)
