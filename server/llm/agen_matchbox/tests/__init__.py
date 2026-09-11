"""Agent Matchbox 独立测试套件。

运行方式（在网关目录或主项目 server 目录均可）::

    pytest server/llm/agen_matchbox/tests -q
    # 或进入网关目录
    cd server/llm/agen_matchbox && pytest tests -q

全部用例均为离线契约：不调用真实 LLM、不连外部服务、
不读写真实运行库（统一使用内存 SQLite + tmp_path）。
"""
