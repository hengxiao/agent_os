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

#: 锚点评论技能名(W2,docs/WIDGETS.md W-bubble;APP-MODEL §16 首个 cascade 消费者)
COMMENTER_NAME = "skill.dev.commenter"

#: 文档评论技能名(D2,docs/DOC-EDITOR.md §5;commenter 先例:白名单空,只读级联)
DOC_COMMENTER_NAME = "skill.dev.doc_commenter"

#: 文档评审技能名(D3,docs/DOC-EDITOR.md §5;全文评审 → 锚点批注集)
DOC_REVIEWER_NAME = "skill.dev.doc_reviewer"

#: 文档编辑技能名(D5,docs/DOC-EDITOR.md §5;doc 作用域主对话:白名单 =
#: 当前文档的 doc.read/doc.edit 两件,改完经工具落盘,host 以全文对比判 changed)
DOC_EDITOR_NAME = "skill.dev.doc_editor"

_DOC_EDITOR_PROMPT = """你是文档编辑助手。用户在一篇 Markdown 文档的专属对话里提需求,输入给你:

- text:用户的消息(写新文档/加一节/改一段/按批注处理等);
- cascade:逐级上下文(文档名、文档全文、段落批注 bubbles 列表)。

你可以用两个工具(白名单收口,没有别的能力):
- doc.read(name):读文档全文;
- doc.edit(name, anchor, replace_text):改文档——anchor = "doc.md#L<start>-L<end>"
  按行号区间替换该段;anchor = "" 时整文替换(新建/大改用)。

纪律:
1. **只能动 cascade 里指定的当前文档**(name 越界工具会拒,不要试);
2. 看着全文改一段:先 doc.read 确认行号,再按段替换;不大改就别整文替换;
3. 用户说"按批注改一遍/处理批注"时,逐条看 bubbles 的批注,能改的都改;
4. 不确定就别动文档,只回复说明;不编造全文里没有的事实;
5. 最后只输出一个 JSON,不要别的文字:{{"reply": "一句话汇报改了什么(没改就说没改)"}}"""

_DOC_REVIEWER_PROMPT = """你是文档评审助手。输入给你三样:

- text:Markdown 文档全文(带行号语义,锚点按 doc.md#L<start>-L<end> 给);
- outline:大纲(标题列表);
- diff:最近版本到当前的全文差异(有则,没有为空)。

任务:通读后输出**锚点批注集**——只输出一个 JSON,不要别的文字:
{{"notes": [{{"anchor": "doc.md#L<start>-L<end>", "severity": "must|should|nit", "text": "批注一句话"}}]}}

纪律:
1. **只读**:你没有任何工具——只能读输入,不能改文档;
2. anchor 给**段级**行号区间(别给单行碎片,也别给全文);
3. severity:must = 不改就有错/有缺失,should = 建议改,nit = 吹毛求疵;
4. 数量控制(≤8 条,重要的先来);不编造输入里没有的事实。"""


def doc_reviewer_skill() -> Skill:
    """文档评审技能(D3;tools=[] —— 只读,输出锚点批注集,不能改文档)。"""
    manifest = SkillManifest(
        name=DOC_REVIEWER_NAME,
        version="0.1.0",
        description=(
            "文档评审助手。Use when 通读 Markdown 文档产出段落级批注集;"
            "Do not use when 要直接改文档(它没有写面,也不能写)。"
        ),
        inputs={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "文档全文"},
                "outline": {"type": "array", "description": "大纲(标题列表)"},
                "diff": {"type": "string", "description": "最近版本 diff(可空)"},
            },
            "required": ["text"],
        },
        outputs={
            "type": "object",
            "properties": {"notes": {"type": "array"}},
            "required": ["notes"],
        },
        permissions=SkillPermissions(tools=[], skills=[]),  # 白名单收口:只读级联内容
        limits=SkillLimits(max_steps=4, timeout=90),
    )
    return Skill(manifest=manifest, prompt=_DOC_REVIEWER_PROMPT)

_DOC_COMMENTER_PROMPT = """你是文档评论助手。用户在 Markdown 文档的某个段落上挂了气泡,输入给你:

- anchor:段落锚点(doc.md#L<start>-L<end>,行号区间);
- text:用户的问题或意见;
- cascade:逐级上下文(APP-MODEL §16;近→远:锚点段原文、文档全文、文档名/版本/脏状态)。

纪律(白名单收口):
1. **只读**:你没有任何工具——只能读输入里的级联内容,**不能直接写文档**;
2. 输出二选一(只输出一个 JSON,不要别的文字):
   - 答疑/建议但不改文:{{"reply": "..."}};
   - 建议改写:{{"edits": [{{"anchor": "<原样带回>", "suggestion": "一句话说清改什么",
     "replace_text": "替换该段的完整新文本(Markdown)"}}]}};
3. replace_text 只覆盖锚点段,不越界写全文;看着全文改一段,不看着一段猜全文;
4. 不确定就回复建议人工确认,不编造上下文里没有的事实。"""


def doc_commenter_skill() -> Skill:
    """文档评论技能(D2;tools=[] —— 只读级联,输出 reply 或 edits 建议,不能直接写)。"""
    manifest = SkillManifest(
        name=DOC_COMMENTER_NAME,
        version="0.1.0",
        description=(
            "文档评论助手。Use when 回应挂在 Markdown 文档段落上的气泡(只读级联给答疑或改写建议);"
            "Do not use when 要直接改文档(它没有写面,也不能写)。"
        ),
        inputs={
            "type": "object",
            "properties": {
                "anchor": {"type": "string", "description": "段落锚点 doc.md#L<start>-L<end>"},
                "text": {"type": "string", "description": "用户批注"},
                "cascade": {"type": "array", "description": "逐级上下文(§16 信封的 cascade 段)"},
            },
            "required": ["anchor", "text"],
        },
        outputs={
            "type": "object",
            "properties": {
                "reply": {"type": "string"},
                "edits": {"type": "array"},
            },
        },
        permissions=SkillPermissions(tools=[], skills=[]),  # 白名单收口:只读级联内容
        limits=SkillLimits(max_steps=4, timeout=60),
    )
    return Skill(manifest=manifest, prompt=_DOC_COMMENTER_PROMPT)


def doc_editor_skill() -> Skill:
    """文档编辑技能(D5;tools = doc.read/doc.edit 两件——只能动当前文档,
    工具侧还有引用围栏双保险;changed 由 host 全文对比判定,不信技能自报)。"""
    manifest = SkillManifest(
        name=DOC_EDITOR_NAME,
        version="0.1.0",
        description=(
            "文档编辑助手。Use when 在 Markdown 文档的专属对话里按用户消息读/改该文档;"
            "Do not use when 要动别的文档或别的系统面(白名单只有当前文档的读/改两件)。"
        ),
        inputs={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "用户消息"},
                "cascade": {"type": "array", "description": "逐级上下文(文档名/全文/批注)"},
            },
            "required": ["text"],
        },
        outputs={
            "type": "object",
            "properties": {"reply": {"type": "string"}},
            "required": ["reply"],
        },
        permissions=SkillPermissions(tools=["doc.read", "doc.edit"], skills=[]),  # 白名单收口
        limits=SkillLimits(max_steps=10, timeout=120),
    )
    return Skill(manifest=manifest, prompt=_DOC_EDITOR_PROMPT)

#: 版本差异摘要技能名(Godot 回溯卷轴的 diff 人话摘要;结果落 diffsum 缓存)
DOC_DIFF_SUMMARIZER_NAME = "skill.dev.doc_diff_summarizer"

_DOC_DIFF_SUMMARIZER_PROMPT = """你是版本差异摘要助手。输入给你:

- from_version / to_version:两个版本号(如 v001、v014);
- diff:这两个版本的差异行(行首 + = 新版新增,- = 旧版被删;只有变更行)。

任务:用**一两句人话**概括"从 from_version 到 to_version 改了什么"——
说内容与结构的变化(加了什么章节/改了什么表述/删了什么),不搬术语、不引行号。

纪律:
1. **只读**:你没有任何工具——只能读输入,不能改任何东西;
2. 只输出一个 JSON:{{"summary": "…"}},不要别的文字;
3. diff 里没有的事不编;变更太小就说"只有措辞微调";
4. 输出给开发者当回溯预览,信息密度优先于修辞。"""


def doc_diff_summarizer_skill() -> Skill:
    """版本差异摘要技能(tools=[] —— 只读 diff,输出人话摘要,不写任何面)。"""
    manifest = SkillManifest(
        name=DOC_DIFF_SUMMARIZER_NAME,
        version="0.1.0",
        description=(
            "版本差异摘要助手。Use when 把文档两版本的 diff 概括成一两句人话(回溯预览);"
            "Do not use when 要读版本全文或改文档(它没有那些面)。"
        ),
        inputs={
            "type": "object",
            "properties": {
                "diff": {"type": "string", "description": "差异行(封顶 4000 字符)"},
                "from_version": {"type": "string"},
                "to_version": {"type": "string"},
            },
            "required": ["diff", "from_version", "to_version"],
        },
        outputs={
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
        permissions=SkillPermissions(tools=[], skills=[]),  # 白名单收口
        limits=SkillLimits(max_steps=2, timeout=60),
    )
    return Skill(manifest=manifest, prompt=_DOC_DIFF_SUMMARIZER_PROMPT)

_COMMENTER_PROMPT = """你是锚点评论助手。用户在某段内容上挂了气泡提问或提意见,输入给你:

- anchor:锚点(§14 路径语义:成员/字段/可选 span 段落号);
- text:用户的问题或意见;
- cascade:逐级上下文(APP-MODEL §16;近→远:widget 的 span/段落/全文、app 的成员与草稿状态)。

纪律(白名单收口):
1. **只读**:你没有任何工具——只能读输入里的级联内容,不能改任何文件;
2. 回复 = 建议(改法/批注措辞/判断),看着全文改一段,不看着一段猜全文;
3. 不确定就说"建议人工确认";不编造上下文里没有的事实。

最终答案输出一个 JSON 对象,reply 字段是给用户的回复(只输出该 JSON)。"""


def commenter_skill() -> Skill:
    """锚点评论技能(W2;tools=[] —— 只读级联,回复建议,不能直接改)。"""
    manifest = SkillManifest(
        name=COMMENTER_NAME,
        version="0.1.0",
        description=(
            "锚点评论助手。Use when 回应挂在内容上的气泡批注(只读级联上下文给建议);"
            "Do not use when 要直接修改草稿(它没有写面,也不能写)。"
        ),
        inputs={
            "type": "object",
            "properties": {
                "anchor": {"type": "object", "description": "锚点(成员/字段/span)"},
                "text": {"type": "string", "description": "用户批注"},
                "cascade": {"type": "array", "description": "逐级上下文(§16 信封的 cascade 段)"},
            },
            "required": ["anchor", "text"],
        },
        outputs={
            "type": "object",
            "properties": {"reply": {"type": "string"}},
            "required": ["reply"],
        },
        permissions=SkillPermissions(tools=[], skills=[]),  # 白名单收口:只读级联内容
        limits=SkillLimits(max_steps=4, timeout=60),
    )
    return Skill(manifest=manifest, prompt=_COMMENTER_PROMPT)


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
