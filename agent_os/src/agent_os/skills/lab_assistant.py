"""skill.dev.assistant(docs/SKILL-DEV.md §1.1/§2.2;L4):Skill Lab 的内置 meta-skill。

dogfood:助手就是 Agent OS 的一个普通 prompt 技能,白名单是 ``lab.draft.*``
五件(L2 推导档;write 是 reversible)。**工具面里没有 promote/delete**——
能改不能发,提交按钮只有人能点(§1.1;升权原则"注入者无法自己批准自己"
的 dogfood 展示面)。
"""

from __future__ import annotations

from agent_os.api.v1 import (
    Skill,
    SkillLimits,
    SkillManifest,
    SkillPermissions,
    SkillRef,
)
from agent_os.tools.lab_tools import LAB_DRAFT_TOOLS

ASSISTANT_NAME = "skill.dev.assistant"

#: 迭代模式(Flow C 样板)的生成技能名
ITERATOR_NAME = "skill.dev.iterator"

_PROMPT = """你是 Skill Lab 的开发助手,工作单元是**能力包**(docs/SKILL-PACKAGES.md:
用户要的是功能,功能 = 根技能 + 它的依赖闭包)。当前包根由输入的 draft 字段给出。

工作方式(包视角):
1. 先用 lab.pkg.closure 读包树(成员/状态/档位/谁引用谁),再决定改哪个成员
   或该不该拆新子技能;
2. 改动用 lab.draft.write;拆新子技能用 lab.draft.create(只能在当前包命名
   空间内建,有配额);读单个成员用 lab.draft.read;
3. **新增引用必须指向包内成员或已发布技能**——指向不存在名字 = 悬空引用,
   提交期会被闸门拦;
4. **改完必须跑 lab.draft.validate 自查**(草稿期语义:悬空只是 warn,要如实
   汇报),需要时用 lab.draft.test_run 试跑;汇报时附上新的包树摘要。

纪律:
- 扩白名单要谨慎:往 permissions 加工具/子技能前,在回复里说明理由(它会推高推导档);
- 你没有 promote 能力,也没有 delete——能改能建,不能发不能删,
  提交让用户自己点"提交"按钮;
- 不确定的改动先给方案,等用户确认再写。

最终答案输出一个 JSON 对象,reply 字段是给用户的完整汇报(只输出该 JSON,不要输出其他文字)。"""


def assistant_skill() -> Skill:
    """构造助手 Skill 对象(manifest + prompt;每次新建,无共享可变状态)。"""
    manifest = SkillManifest(
        name=ASSISTANT_NAME,
        version="0.1.0",
        description=(
            "Skill Lab 开发助手。Use when 需要对话式创建/修改/自查 skill 草稿;"
            "Do not use when 要直接 promote(它没有提交能力)或处理与草稿无关的任务。"
        ),
        inputs={
            "type": "object",
            "properties": {
                "request": {"type": "string", "description": "用户的开发请求"},
                "draft": {"type": "string", "description": "当前草稿名"},
            },
            "required": ["request", "draft"],
        },
        outputs={
            "type": "object",
            "properties": {"reply": {"type": "string"}},
            "required": ["reply"],
        },
        permissions=SkillPermissions(tools=list(LAB_DRAFT_TOOLS), skills=[]),
        limits=SkillLimits(max_steps=12, timeout=120),
    )
    return Skill(manifest=manifest, prompt=_PROMPT)


_ITERATOR_PROMPT = """你是 Skill Lab 迭代生成器(Flow C 样板:边注驱动迭代)。

输入的 request 里有:当前 working 版本指引、本轮边注(锚定到成员/字段/段落/用例)、
以及用户的补充说明。你的任务:

1. 用 lab.pkg.closure 看包树,用 lab.draft.read 读相关成员的 working 内容;
2. 逐条落实边注:改 description/prompt/permissions/trust/tests——
   产出**改进版**,不是重写(能保留的尽量保留,diff 越小越好评审);
3. 每个要改的成员,用 lab.cand.write 写出**完整新 manifest 与 prompt**
   (没改的成员不用写,候选缺失 = 保持现状);
4. 纪律:不改 tier 语义不擅自扩白名单;边注说"档不对"时只做标注建议,
   不擅改(档由权限面推导);**你只能写候选区**,接不接受是用户点。

最终答案输出一个 JSON 对象,reply 字段是这轮的改动摘要(逐成员一句)。"""


def iterator_skill() -> Skill:
    """迭代生成技能(Flow C 样板;工具面 = 读 working + 写候选,写不到 working)。"""
    manifest = SkillManifest(
        name=ITERATOR_NAME,
        version="0.1.0",
        description=(
            "Skill Lab 迭代生成器。Use when 按边注批量产出候选版本;"
            "Do not use when 要直接改 working 或 promote(它没有这两个能力)。"
        ),
        inputs={
            "type": "object",
            "properties": {
                "request": {"type": "string", "description": "边注 + 补充说明(结构化)"},
                "draft": {"type": "string", "description": "包根草稿名"},
            },
            "required": ["request", "draft"],
        },
        outputs={
            "type": "object",
            "properties": {"reply": {"type": "string"}},
            "required": ["reply"],
        },
        permissions=SkillPermissions(
            tools=["lab.draft.read", "lab.pkg.closure", "lab.cand.write"],
            skills=[],
        ),
        limits=SkillLimits(max_steps=16, timeout=120),
    )
    return Skill(manifest=manifest, prompt=_ITERATOR_PROMPT)


def assistant_ref() -> SkillRef:
    return assistant_skill().ref
