# std/transform — 纯函数技能包

STDLIB 第 2 波(STDLIB-CATALOG §W2;STDLIB §4.1):20 个 code 技能,全部零 LLM、
零外部依赖(仅标准库)、确定性,permissions 全空——纯转换,不触网、不读状态
(`hash_digest` 的 `ref` 形参除外,它按调用方显式给的路径读文件取指纹)。

内容:`extract_json` / `template_render` / `diff_text` / `word_count` /
`token_estimate` / `hash_digest` / `csv_to_rows` / `rows_to_markdown` / `slugify` /
`normalize_whitespace` / `chunk_text` / `bm25_score` / `rrf_merge` /
`retrieval_metrics` / `injection_scan` / `redact_pii` / `identifier_guard` /
`make_handoff` / `date_normalize` / `citation_check`。

## 装配

handler 为 `transform:<fn>` dotted path(本目录 `transform.py`),装配方需把本目录
放上 `sys.path`(LocalFileSkillRegistry 不做注入):测试侧由仓库根 `conftest.py`
钉入;宿主/CLI 侧把本目录作为一个 skill set 经 `--skillsets <其父目录>` 加载
(`load_skillsets` 约定:`<root>/<set>/skills.yaml`,set 目录在装配时自动注入
`sys.path`,见 runtime/config.py 与 host/web/run_manager.py)。

```bash
.venv/bin/pytest -q tests/skills/test_std_transform.py   # 锚点
```
