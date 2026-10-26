/** 代码来源沿用 Python 工作流的三种输入方式。 */
export type SourceType = "python_file" | "git_repository" | "pasted_code";

/** 任务草稿只保存在页面内存中，刷新页面后清空。 */
export interface TaskDraft {
  sourceType: SourceType;
  path: string;
  revision: string;
  codeText: string;
  fileName: string;
  fileContent: string | null;
  errorReport: string;
  testTarget: string;
}

/** 本次预览中的审核记录；尚未连接数据库写入接口。 */
export interface PreviewReview {
  decision: "accepted" | "rejected";
  reason: string;
  createdAt: string;
}

// budgets：与 repair_state.py 的新任务默认预算保持一致。
export const budgets = [
  {
    label: "最大修复轮数",
    value: "10",
    unit: "轮",
    caption: "限制修复尝试次数",
  },
  {
    label: "总运行时间",
    value: "120",
    unit: "秒",
    caption: "自动执行时间预算",
  },
  { label: "模型 Token", value: "20,000", unit: "", caption: "输入与输出合计" },
  { label: "费用上限", value: "0.10", unit: "元", caption: "人民币" },
] as const;
