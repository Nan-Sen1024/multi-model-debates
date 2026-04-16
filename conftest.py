# 禁用与 pytest 7 不兼容的 nameko 插件
collect_ignore_glob = []


def pytest_configure(config):
    """禁用 nameko 的 fast_teardown autouse fixture（与 pytest 7 不兼容）"""
    try:
        import nameko.testing.pytest as _nameko_pytest

        # 将 fast_teardown 的 autouse 关闭
        if hasattr(_nameko_pytest, "fast_teardown"):
            _nameko_pytest.fast_teardown = None
    except Exception:
        pass
