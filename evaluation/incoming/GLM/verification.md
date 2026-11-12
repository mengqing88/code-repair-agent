# verification.md - 实际执行的验证与结果

验证环境：Python 3.11.9，pytest（见下方命令输出统计）。
每个 case 在独立临时目录中执行：先对缺陷实现跑两组测试，
再用 `git apply` 等价的自研 unified diff 应用器应用参考补丁，重新跑两组测试。

## case-91 阶梯折扣阈值边界
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-91（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-92 日期区间重叠天数（闭区间）
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-92（历史经验验证）
- 旧实现：1 passed / 2 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-93 配置深度合并
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-93（历史经验验证）
- 旧实现：1 passed / 2 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-94 幂等键重复执行
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-94（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-95 多字段排序键顺序错误
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-95（历史经验验证）
- 旧实现：1 passed / 2 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-96 分组键被错误大小写归一化
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-96（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-97 TTL 缓存读取延长有效期
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-97（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-98 优惠券叠加计算顺序
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-98（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-99 版本号按字符串比较
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-99（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-100 库存扣减先扣后校验
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-100（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-101 退避序列整体错位
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-101（历史经验验证）
- 旧实现：1 passed / 2 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-102 金额舍入用了银行家舍入
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-102（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-103 去重丢失首次出现顺序
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-103（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-104 权重抽取分母错误
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-104（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-105 限流窗口边界包含错误
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-105（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-106 路径规范化未处理父目录
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-106（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-107 字段解析未去除空白
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-107（历史经验验证）
- 旧实现：1 passed / 2 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-108 插入位置取了最左侧
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-108（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-109 分转金额显示缺零
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-109（历史经验验证）
- 旧实现：0 passed / 3 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-110 拓扑排序缺少环检测
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-110（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-111 合并区间未先排序
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-111（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-112 轮询调度先自增导致跳过首台
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-112（历史经验验证）
- 旧实现：0 passed / 3 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-113 会话活跃时间未刷新
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-113（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-114 批量校验提前返回
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-114（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-115 可变默认参数共享状态
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-115（历史经验验证）
- 旧实现：1 passed / 2 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-116 并列名次用了竞争排名
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-116（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-117 过期预订可重复释放
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-117（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-118 Top-K 并列词未按字典序
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-118（历史经验验证）
- 旧实现：1 passed / 2 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-119 缓存键拼接歧义冲突
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-119（历史经验验证）
- 旧实现：2 passed / 1 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

## case-120 时长格式化未补零
- 缺陷实现：existing 3 passed / acceptance 3 failed（期望 existing 3 passed + acceptance 3 failed）-> OK
- 应用 reference.patch 后：期望 6 passed（实际 6 passed, 0 failed, 0 error）-> OK
- 结论：通过

### experience-120（历史经验验证）
- 旧实现：0 passed / 3 failed（要求至少 1 条失败）-> OK
- 应用补丁后：3 passed / 0 failed（要求 3 passed）-> OK

---
总体结论：全部通过（30 个 case 的缺陷/修复两轮测试与 30 条历史经验的补丁验证全部通过）
