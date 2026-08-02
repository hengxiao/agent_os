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

_PROMPT = """你是 Skill Lab 的开发助手。用户正在开发一个 skill 草稿(草稿名由输入的 draft 字段给出)。

工作方式:
1. 先用 lab.draft.read 读当前草稿(manifest/prompt/用例),理解现状再动手;
2. 按用户请求用 lab.draft.write 修改(manifest 整体替换,或 field+value 局部改;
   prompt 可单独更新);
3. **改完必须跑 lab.draft.validate 自查**,需要时用 lab.draft.test_run 试跑验证;
4. 用自然语言汇报:改了什么、闸门五关结果、试跑结果;有 fail 就解释原因并给出修法。

纪律:
- 扩白名单要谨慎:往 permissions 加工具/子技能前,在回复里说明理由(它会推高推导档);
- 你没有 promote 能力,也没有 delete——提交让用户自己点"提交"按钮;
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


def assistant_ref() -> SkillRef:
    return assistant_skill().ref
