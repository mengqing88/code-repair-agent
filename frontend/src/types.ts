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

/** 服务端持久保存的人工审核决定。 */
export interface ReviewRecord {
  decision: "accepted" | "rejected";
  reason: string;
  created_at: string;
  patch_hash: string;
}

export interface TestRun {
  stdout: string;
  stderr: string;
  return_code: number | null;
  passed: boolean;
}

export interface StructuredError {
  error_id?: string;
  operation?: string;
  request_id?: string | null;
  diagnostics_persisted?: boolean;
  causes?: StructuredError[];
  code: string;
  category: string;
  message: string;
  retryable: boolean;
  outcome_unknown: boolean;
  next_action: string;
  retry_after?: number | null;
}

export interface CaseMatch {
  case_id: string;
  revision: number;
  score: number;
  vector_score: number | null;
  keyword_score: number;
  retrieval_mode: "hybrid" | "keyword";
  decision: "pending" | "applicable" | "rejected" | "inconclusive";
  matched_conditions: string[];
  conflicts: string[];
  unknowns: string[];
  document: {
    original_report: string;
    diagnosis: string;
    patch_diff: string;
    applicable_conditions: string[];
    source: Record<string, unknown>;
  };
}

export interface RepairTask {
  progress?: {
    revision: number;
    next_node: string;
    recovery_count: number;
    recovery: { id: string | null; status: "working" | "rolled_back" | "resumed" | "paused" | "rollback_pending"; error?: StructuredError } | null;
  };
  execution: "queued" | "running" | "idle";
  source_label: string;
  review: ReviewRecord | null;
  config: { workspace: string; checkpoint: string | null; test_targets: string[] };
  state: {
    task_id: string;
    status: string;
    diagnosis: string | null;
    patch_diff: string | null;
    patch_hash: string | null;
    attempt_count: number;
    elapsed_seconds: number;
    remaining_budget: { seconds: number; tokens: number; attempts: number; cost: number };
    question: string | null;
    last_error: string | null;
    error_info?: StructuredError | null;
    warnings?: string[];
    accounting?: { tokens_observed: number; cost_observed_yuan: number; cost_complete: boolean; usage_complete: boolean };
    memory_saved: boolean;
    code_evidence: { path: string; start_line: number; end_line: number; quote: string; reason: string }[];
    case_matches: CaseMatch[];
    reproduction: {
      status?: string; conclusion?: string; steps?: string;
      execution?: { stdout: string; stderr: string; returncode: number | null };
    } | null;
    validation: {
      passed: boolean | null; patch_hash: string; summary: string; report_path: string;
      return_code?: number | null; stdout?: string; stderr?: string; timed_out?: boolean;
      regression?: { test_code: string; test_hash: string; baseline: TestRun; patched: TestRun | null } | null;
      reproduction?: { stdout: string; stderr: string; returncode: number | null } | null;
    } | null;
  };
}

export const statusLabels: Record<string, string> = {
  queued: "等待执行", reproducing: "正在复现", retrieving_memory: "收集上下文",
  diagnosing: "分析根因", repairing: "生成候选补丁", validating: "验证补丁",
  reviewable: "等待人工审核", succeeded: "已接受并保存案例", failed: "流程已结束",
  needs_input: "需要补充信息", budget_exceeded: "预算耗尽", cancelled: "已取消", timed_out: "已超时",
};

// budgets：与 repair_state.py 的新任务默认预算保持一致。
export const budgets = [
  {
    label: "最大重试次数",
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
