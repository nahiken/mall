"""pytest 公共夹具（P2-01）。

目标：测试套件在**没有任何外部依赖**的机器上可跑通 —— 不需要 MySQL / Redis /
ES / DeepSeek key。做法：

1. 把 ``deepseek_agent/llm_backend`` 加进 ``sys.path``，这样 ``import mall_agent``
   与 ``python run_mall.py`` 用的是同一套导入路径，不依赖安装成包。
2. ``stats`` 写盘改 no-op：``MallStats.record_llm`` 在没有异步 writer 时会同步
   追加 ``logs/metrics.jsonl``，测试不应污染运行数据。
3. 外部 HTTP 一律用测试内联的假客户端（见各 test 文件），不发真实请求。

注意：``mall_agent.config`` 在导入时读取环境变量，因此所有依赖配置的断言都用
``monkeypatch.setattr(config, ...)`` 在运行期覆盖 —— 这也顺带证伪了「配置只在导入
期生效」这类隐患（auth / knowledge 模块确实每次调用都重新读）。
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(autouse=True)
def _no_stats_io(monkeypatch):
    """禁止测试写 metrics.jsonl（stats 的同步降级分支会真的落盘）。"""
    from mall_agent import stats as stats_mod

    monkeypatch.setattr(stats_mod.stats, "record_llm", lambda *a, **k: None)
