# -*- coding: utf-8 -*-
"""devflow 引擎 — 领域无关的流水线内核（L1 层）。

模块职责划分：

    core      路径、配置、profile、命令执行等基础设施
    state     运行态单一真相源（run-state.json）
    gates     门禁校验原语 + 计划结构校验
    learning  自学习闭环：采集 / 蒸馏 / 分级 / 注入 / 有效性回收
    repomap   仓库地图预热
    runner    流水线编排：组装 prompt、跑门禁、推进与恢复
    cli       命令行入口

这一层不认识任何具体技术栈；语言、构建、测试、发布相关的知识全部来自
config/profiles/ 下的 profile 文件（L2 层）。
"""

__version__ = "1.0.0"
