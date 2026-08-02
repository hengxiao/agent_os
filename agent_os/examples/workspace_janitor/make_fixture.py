"""workspace_janitor fixture 生成器:真实 scratch 目录树 + 真实演示进程。

用法::

    python make_fixture.py <root>            # 生成 fixture,stdout 打印清单 JSON
    python make_fixture.py --cleanup <manifest.json> [--purge]

生成的都是**真文件、真进程**——爆炸半径由调用方把 <root> 放进 run 的
workdir 沙箱圈住;演示进程是 ``sleep`` 长眠进程(零 CPU,谁都能 kill)。
清单 JSON:{scratch_dir, pid, stale(应删), keep(应保留)}。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

#: (相对路径, 内容, 是否应被清理)——陈旧日志/临时文件与正常文档混合,内容可辨识
FILES = [
    ("logs/app.log.1", "2026-07-01 03:12:44 INFO 陈旧启动日志(第一轮)\n", True),
    ("logs/app.log.2", "2026-07-02 11:02:10 WARN 磁盘水位 91%(早已处理)\n", True),
    ("tmp/render-cache.tmp", "tmpfs-cache:b64:aGVsbG8gd29ybGQ=\n", True),
    ("tmp/extract-0412.tmp", "partial json: {\"rows\": [1, 2,\n", True),
    ("index.cache", "cache-entry:docs-index:v3:stale\n", True),
    ("docs/README.md", "# 项目笔记\n这是要保留的正式文档。\n", False),
    ("docs/notes.md", "- [ ] 季度回顾\n- [ ] 续费证书\n", False),
]


def create(root: Path) -> dict:
    """生成 scratch 树 + 启动演示进程,返回并打印清单。"""
    scratch = root / "scratch"
    stale, keep = [], []
    for rel, content, is_stale in FILES:
        path = scratch / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        (stale if is_stale else keep).append(str(path))
    # 真实演示进程:sleep 长眠(零 CPU);pid 落清单,停进程由 ops.service.stop 演示
    proc = subprocess.Popen(
        ["sleep", "3600"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # 脱离进程组:kill 演示不影响父进程树
    )
    manifest = {
        "scratch_dir": str(scratch),
        "pid": proc.pid,
        "stale": stale,
        "keep": keep,
    }
    (root / "fixture.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def cleanup(manifest_path: Path, *, purge: bool = False) -> None:
    """清理 fixture:杀演示进程(幂等,已死忽略);``--purge`` 连 scratch 树一起删。"""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pid = int(manifest.get("pid") or 0)
    if pid:
        try:
            import os
            import signal

            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass  # 已被剧情停掉——这正是演示成功的样子
    if purge:
        import shutil

        shutil.rmtree(manifest["scratch_dir"], ignore_errors=True)


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "--cleanup":
        cleanup(Path(argv[1]), purge="--purge" in argv[2:])
        return 0
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(argv[0])
    root.mkdir(parents=True, exist_ok=True)
    manifest = create(root)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
