"""历史修复案例的本地导入和检索入口：python -m knowledge_cli --help。"""

import argparse
import json
import getpass
from pathlib import Path
import sys

from knowledge_base import KnowledgeBase, default_knowledge_path
from resilience import error_info, execution_scope
from diagnostics import diagnostic_scope
from business_scope import require_software_request


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="导入历史修复案例并检索相似缺陷")
    parser.add_argument("--database", type=Path, default=default_knowledge_path(), help="SQLite 知识库路径")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="把 JSON/JSONL 案例放入待审区，由经验审核员核验")
    importer.add_argument("path", type=Path, nargs="?", default=Path(__file__).parent / "history_cases")
    search = commands.add_parser("search", help="联合关键词和向量检索相似缺陷")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--min-similarity", type=float, default=0.35)
    commands.add_parser("stats", help="查看已入库案例数量")
    candidates = commands.add_parser("candidates", help="查看待审案例与审核依据")
    candidates.add_argument("--status", default="needs_human", choices=["all", "pending", "reviewing", "needs_human", "approved", "published", "declined"])
    candidates.add_argument("--limit", type=int, default=100)
    review = commands.add_parser("review", help="本机操作者审核并发布或拒绝导入候选")
    review.add_argument("candidate_id")
    review.add_argument("--decision", required=True, choices=["approve", "decline"])
    review.add_argument("--reason", required=True)
    review.add_argument("--content-hash", required=True)
    args = parser.parse_args(argv)
    knowledge = KnowledgeBase(args.database)
    try:
        with execution_scope(120, task_id="knowledge-review", trace_path=args.database.parent / "knowledge-review.trace.jsonl"):
            if args.command == "import":
                result = knowledge.import_path(args.path)
            elif args.command == "search":
                require_software_request(args.query)
                result = {"matches": knowledge.search(args.query, args.limit, args.min_similarity)}
            elif args.command in ("candidates", "review"):
                from knowledge_review import KnowledgeReview
                reviewer = KnowledgeReview(knowledge)
                if args.command == "candidates":
                    result = {"candidates": reviewer.list(args.status, limit=args.limit)}
                else:
                    result = reviewer.decide(args.candidate_id, decision=args.decision, reason=args.reason,
                        content_hash=args.content_hash, actor="local-operator:" + getpass.getuser())
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
