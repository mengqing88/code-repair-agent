"""历史修复案例的本地导入和检索入口：python -m knowledge_cli --help。"""

import argparse
import json
from pathlib import Path
import sys

from knowledge_base import KnowledgeBase, default_knowledge_path
from resilience import error_info
from diagnostics import diagnostic_scope


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="导入历史修复案例并检索相似缺陷")
    parser.add_argument("--database", type=Path, default=default_knowledge_path(), help="SQLite 知识库路径")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="导入已接受且验证通过的 JSON/JSONL 历史案例")
    importer.add_argument("path", type=Path, nargs="?", default=Path(__file__).parent / "history_cases")
    search = commands.add_parser("search", help="联合关键词和向量检索相似缺陷")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--min-similarity", type=float, default=0.35)
    commands.add_parser("stats", help="查看已入库案例数量")
    args = parser.parse_args(argv)
    knowledge = KnowledgeBase(args.database)
    try:
        if args.command == "import":
            result = knowledge.import_path(args.path)
        elif args.command == "search":
            result = {"matches": knowledge.search(args.query, args.limit, args.min_similarity)}
        else:
            result = {"database": str(args.database.resolve()), "total": knowledge.count()}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("failed") else 0
    except Exception as error:
        info = error_info(error, operation="knowledge.cli")
        print(json.dumps({"error": info}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
