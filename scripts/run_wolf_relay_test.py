import asyncio
import importlib.util
import os
import sys

TEST_PATH = os.path.join(os.path.dirname(__file__), '..', 'tests', 'test_wolf_relay.py')

def load_test_module(path):
    spec = importlib.util.spec_from_file_location('test_wolf_relay', path)
    mod = importlib.util.module_from_spec(spec)
    # Ensure project root is on sys.path so 'src' package imports resolve
    proj_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if proj_root not in sys.path:
        sys.path.insert(0, proj_root)
    spec.loader.exec_module(mod)
    return mod

async def main():
    mod = load_test_module(os.path.abspath(TEST_PATH))
    # call the coroutine directly
    try:
        await mod.test_wolf_relay_multiple_sends()
        print('TEST PASSED')
    except AssertionError as e:
        print('TEST FAILED:', e)
        # print debug info: if module has access to bot via closure, attempt to print registered users
        try:
            # attempt to access local variables from test function (best-effort)
            # fallback: import tests.test_wolf_relay and print bot._users if available
            import importlib
            tmod = importlib.import_module('tests.test_wolf_relay')
            if hasattr(tmod, 'DummyBot'):
                # no easy access to instance; just notify
                print('Debug: test module loaded; inspect test file directly for more info')
        except Exception:
            pass
        raise

if __name__ == '__main__':
    asyncio.run(main())
