---
name: anxin-ai
description: Use AnxinAI before an agent reads, summarizes, analyzes, uploads, or sends a potentially sensitive local TXT, CSV, DOCX, XLSX, or PDF file. Trigger for Chinese customer, employee, contract, identity, phone, address, credential, privacy, PII, redaction, or 安心AI requests.
metadata:
  version: "0.1.0"
  display_name: "AnxinAI｜安心AI"
  license: "MIT"
---

# AnxinAI 本地文件隐私闸门

## 不可绕过的边界

在 AnxinAI 扫描和用户本地确认之前，不得使用 Read、Shell、Python、Office、PDF、MCP 或其他工具读取原文件。

不得在对话中显示或查找原始映射、本地审核 URL、访问令牌以及 AnxinAI 内部状态文件。

用户在聊天中说“同意”不等于批准文件。只有 `anxin-gate status` 返回 `approved` 才能读取返回的 `safe_path`。

## 检查文件

取得用户提供的单个完整路径后，只运行：

```bash
anxin-gate scan "<完整文件路径>"
```

处理返回的 JSON：

- `pending`：报告命中总数和分类，告诉用户本地审核页已打开，保存 `ticket_id` 后停止。
- `error`：解释 `message` 并停止，不得绕过。

命令不会把审核 URL 或原始敏感值输出到对话。

## 用户完成本地审核后

用户明确表示已在本地页面确认后，只运行：

```bash
anxin-gate status "<ticket_id>"
```

- `approved`：只读取返回的 `safe_path`，再完成用户原任务。
- `pending`：请用户继续完成本地审核；需要重开时运行 `anxin-gate open "<ticket_id>"`。
- `cancelled`、`invalid`、`expired`、`error`：停止，不读取原文件或安全副本。

原文件或安全副本变化会使批准失效，不得复用旧批准。

## 在本机恢复正式结果

如果 `status` 返回 `restore_required: true`，Agent 输出必须保留 `[VG_...]` 占位符。把完整占位符回复保存为 UTF-8 临时文件，然后运行：

```bash
anxin-gate restore "<ticket_id>" "<安全回复临时文件>"
```

返回 `restored_locally` 后，只告诉用户正式结果已在本地页面打开。不要把恢复内容复制回对话。

如果主要产物是 DOCX、XLSX 或文本文件，先生成带占位符的安全输出，再运行：

```bash
anxin-gate restore-file "<ticket_id>" "<安全输出文件>"
```

## 设置

用户要求调整检测类型时运行：

```bash
anxin-gate settings
```

设置页仅监听 `127.0.0.1`，社区版不包含激活、支付或遥测。

