# verification.md

实际执行验证命令与结果（使用 pytest 9.2.0，Python 3.13）。

对每个 case：
1. 在临时目录放入 buggy target.py + test_existing.py + test_acceptance.py，运行 pytest；
2. 用 reference.patch 应用到 buggy 得到 fixed，再次运行 pytest；
3. 对 experience：放入 before_source + verification_tests 运行（应失败），放入 after_source 运行（应通过）。

## case-61 发票税费中间舍入误差
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-61（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-62 汇率换算方向用反
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-62（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-63 买赠活动免错商品
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-63（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-64 计息天数基数用错
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-64（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-65 分账舍入余数丢失
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-65（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-66 订阅有效期末日包含错误
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-66（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-67 时区换算符号写反
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-67（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-68 营业时间跨午夜未处理
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-68（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-69 年龄计算未校验证生日
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-69（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-70 计费秒数向下取整
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-70（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-71 嵌套配置浅合并
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-71（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-72 环境变量优先级写反
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-72（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-73 列表配置覆盖而非追加
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-73（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-74 None 表示保留却被覆盖
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-74（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-75 布尔配置误解析
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-75（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-76 订单重复支付未幂等
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-76（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-77 部分退款后不能全额退款
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-77（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-78 任务重复启动未幂等
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-78（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-79 重复请求未去重
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-79（历史经验验证）
  - before_source：passed=2 failed=1（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-80 流程阶段跳过约束缺失
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-80（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-81 CSV 引号内逗号截断
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-81（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-82 分桶边界归属错误
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-82（历史经验验证）
  - before_source：passed=1 failed=2（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-83 排序缺失次级键
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-83（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-84 去重保留末次而非首次
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-84（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-85 时长解析遗漏分钟
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-85（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-86 TTL=0 被误判立即过期
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-86（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-87 缓存键规范化导致冲突
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-87（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-88 LRU 容量淘汰差一
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-88（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-89 更新后未失效缓存
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-89（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）

## case-90 缓存键大小写敏感
- reference.patch 应用后是否复现 fixed：是
  - test_existing.py（buggy）：passed=3 failed=0（应全通过）
  - test_acceptance.py（buggy）：passed=0 failed=3（应全失败）
  - test_existing.py（fixed）：passed=3 failed=0（应全通过）
  - test_acceptance.py（fixed）：passed=3 failed=0（应全通过）
## experience-90（历史经验验证）
  - before_source：passed=0 failed=3（应至少 1 失败）
  - after_source：passed=3 failed=0（应全通过）
