"""宿主包(docs/RUNNERS.md §2.4):内核之上的薄宿主,只 import 契约与内核公开件。

两个 runner 共用 ``shared`` 的产物组织与 RunRecord 读取层(§2 共同原则:
薄宿主不进内核、产物优先、可复现);``cli`` 面向 coding agent,``web``(R3)面向人。
"""
