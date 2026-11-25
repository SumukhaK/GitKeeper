"""Fail-closed policies that gate a GitKeeper worker publication.

``gitkeeper.policy`` holds the two deterministic gates:

* :mod:`gitkeeper.policy.models` - the host-local OpenCode model ladder.
* :mod:`gitkeeper.policy.patch_scope` - the machine-checkable patch-scope rule.

Both are pure policy objects with no UI, network, or subprocess dependency, so
the same rules run in worker preflight and in tests.
"""
