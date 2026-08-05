# 参与贡献

感谢帮助改进 AnxinAI Community。隐私检测最难的部分通常不是新增一个正则表达式，而是在真实中文上下文中减少误报和漏报。

## 开发环境

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[agent,dev]"
ANXINAI_DISABLE_NYM=1 python -m pytest
```

Windows 激活虚拟环境：

```powershell
.venv\Scripts\Activate.ps1
```

## 提交规则

- 每个行为变化必须带测试。
- 新增中文检测规则时至少提供一个正例和两个容易误报的反例。
- 不要提交真实个人信息；测试数据必须为明确的合成数据。
- 不要提交模型权重、构建产物、日志、审核记录或本地配置。
- 不要弱化失败关闭策略，也不要让 Agent 获得本地映射或审核令牌。
- 保持社区版不包含支付、激活和遥测逻辑。

## Agent 适配

适配新平台时必须保证：

1. Agent 不得在扫描前使用其他工具预读原文件。
2. 只有本地审核状态为 `approved` 时才能读取安全版。
3. 对话中不得输出映射原文、本地审核 URL 或一次性令牌。
4. 文件哈希变化后必须重新扫描和确认。

## Pull Request

请在说明中包含问题背景、方案、安全影响、测试结果以及用户可见变化。较大的行为变化请先创建 Issue 讨论。

