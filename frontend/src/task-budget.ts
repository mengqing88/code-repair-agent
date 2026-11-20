import type { RepairTask } from "./types";

export function tokenBudgetExtensionBlocker(task: RepairTask | null): string {
  if (!task || task.state.status !== "budget_exceeded"
    || (task.state.budget_exhausted !== "tokens" && task.state.error_info?.budget_kind !== "tokens")) return "";
  if (task.state.error_info?.outcome_unknown || task.pending_external_operations?.length
    || task.progress?.run_id || task.progress?.interrupted || task.progress?.in_flight
    || task.progress?.pending_calls?.length || task.worker_stop_unconfirmed) {
    return "存在尚未核实的操作结果，请先完成核对后再续跑。";
  }
  if (task.execution !== "idle") return "任务仍在执行，请等待状态更新。";
  if (task.state.remaining_budget.seconds <= 0) return "任务时间预算也已耗尽；仅追加 Token 不足以续跑。";
  if (task.state.remaining_budget.cost <= 0) return "任务费用预算也已耗尽；仅追加 Token 不足以续跑。";
  return "";
}

export function validTokenBudgetGrant(tokens: number | null, reason: string): boolean {
  return Number.isInteger(tokens) && tokens !== null && tokens >= 1 && tokens <= 200_000 && !!reason.trim();
}
