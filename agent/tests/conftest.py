import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("OE_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="needs the live OpenEMR stack; run with OE_LIVE=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
