"""Test harness for the generic MCP Bridge tooling (mirrors
schema-governance/tests/conftest.py): the scripts are imported by bare module
name - `import provision_bridge`, `import deploy_bridge`, ... - exactly as the
reusable workflow invokes them (`python "$TOOLING_DIR"/scripts/<tool>.py`),
so ../scripts goes on sys.path. Deliberately NOT a package named `mcp.*`,
which would shadow the PyPI `mcp` (Model Context Protocol SDK) package.
"""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# The repo runs pytest in importlib mode (pytest.ini), which does not put the test
# directory on sys.path; the shared offline Anypoint stand-in (anypoint_fake.py) is
# imported by bare name from several test modules, so this directory goes on too.
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
