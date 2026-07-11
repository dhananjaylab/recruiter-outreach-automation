"""Import context for the test suite.

The Hitchhiker's Guide to Python recommends this pattern to give tests
explicit import context without requiring the package to be installed.

Usage in test modules:
    from tests.context import recruiter_outreach

Or simply import the package directly — this file ensures the project
root is on sys.path regardless of installation state.

Reference: https://docs.python-guide.org/writing/structure/#test-suite
"""

import os
import sys

# Insert the project root at position 0 so `import recruiter_outreach`
# always resolves to the local source tree, even in a bare checkout.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import recruiter_outreach  # noqa: F401, E402
