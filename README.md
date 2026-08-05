<p align="center">
  <img src="docs/assets/anxinai-logo.png" width="132" alt="AnxinAI logo">
</p>

<h1 align="center">AnxinAI Community｜安心AI 社区版</h1>

<p align="center">
  在 Agent 读取文件之前，先在本机识别中文敏感信息，生成安全版，再由用户确认是否继续。
</p>

<p align="center">
  Local-first · 中文优先 · 用户确认 · 可审计
</p>

## 为什么需要它

把客户名单、员工表、合同或日志直接交给 AI，往往意味着姓名、手机号、身份证、地址、账号和业务信息也会一起进入模型上下文。

AnxinAI 在文件进入 Agent 之前增加一道本地隐私闸门：

```text
原始文件
  ↓ 仅在本机读取
中文规则 + 可选 Nym Edge INT8
  ↓
生成带占位符的安全版
  ↓ 用户在 127.0.0.1 审核并确认
Agent 只读取安全版
  ↓
正式结果仅在本机恢复
```

原始敏感值、映射表和本地审核令牌不会返回 Agent 对话。

## 30 秒体验

只使用中文规则，不需要下载模型：

```bash
git clone https://github.com/sheldon-lang/anxinai-community.git
cd anxinai-community
python -m pip install -e .
opf "联系人张伟，手机号13800138000，邮箱zhangwei@example.com"
```

输出：

```text
联系人，手机号，邮箱
```

`opf` 保持 OpenAI Privacy Filter 风格的删除式输出；需要可逆占位符时使用下面的 `mask()` API 或文件安全闸门。

Python API：

```python
from opf import OPF

privacy = OPF(device="cpu")
masked = privacy.mask("联系人张伟，手机号13800138000")
print(masked.masked_text)

# 映射只保留在当前本地进程中
restored = privacy.restore(masked.masked_text, masked)
```

## 文件安全闸门

安装文件处理与本地模型依赖：

```bash
python -m pip install -e ".[agent]"
```

可选安装固定版本的 Nym Edge INT8 模型：

```bash
anxin-install-model
```

扫描文件：

```bash
anxin-gate scan "/完整路径/客户名单.xlsx"
```

AnxinAI 会在原文件旁生成安全副本，并打开只监听 `127.0.0.1` 的审核页面。命令只向 Agent 返回命中数量和审核编号；用户在本地确认后，Agent 才能获得安全版路径。

当前支持：

- UTF-8 文本、Markdown、CSV、TSV、JSON、YAML、日志和常见代码文件
- DOCX：保留可安全处理的 OOXML 结构、段落、表格与样式
- XLSX：保留工作簿结构、公式和格式；存在不可验证公式时停止处理
- PDF：提取文字并生成分页安全 Markdown；扫描件默认停止处理

默认单文件上限为 64 MB，可分析文字上限为 20 万字符。

## 中文识别能力

基础规则覆盖中国常见结构化和业务敏感信息，包括：

- 姓名、手机号、固定电话、邮箱、身份证、银行卡和详细地址
- 统一社会信用代码、公司名称、业务编号和合同信息
- 员工编号、工资、奖金、绩效、请假及奖惩信息
- 发票号码、预算金额、收付款账号
- 党建相关编号、党费、民主评议和组织关系
- 内网 IP、密码、Token、API Key 和涉密标识

规则优先；可选 Nym Edge INT8 用于补充姓名、机构和地址等语义实体。模型存在误报和漏报，不能单独作为合规结论。

## Agent 平台

仓库提供标准 `SKILL.md` 模板，可作为 WorkBuddy、Codex、Claude Code 及其他支持 Agent Skills 的平台适配起点：

```text
integrations/agent-skill/anxin-ai/
```

典型交互：

```text
用户：使用安心AI检查客户名单.xlsx，确认安全后帮我分析。

Agent：已在本机生成安全版，请在本地审核页确认。

用户：已确认。

Agent：只读取审核通过的安全版，并继续完成任务。
```

不同平台的 Hook、命令替换和 Skill 安装目录并不完全一致。社区版先公开协议与基础 Skill；欢迎贡献平台适配，提交时请保留“不得预读原文件”和“本地确认后放行”两条安全边界。

## 社区版与商业版

| 能力 | Community | Pro / Enterprise |
|---|:---:|:---:|
| 中文基础规则与 OPF 兼容 API | ✓ | ✓ |
| 可选 Nym Edge INT8 | ✓ | ✓ |
| 单文件安全副本与本地审核 | ✓ | ✓ |
| 一次性本地结果恢复 | ✓ | ✓ |
| 签名安装包与自动更新 | — | ✓ |
| 安全目录强制 Hook | — | ✓ |
| OpenAI-compatible 流式隐私网关 | — | ✓ |
| 企业策略、审计和集中部署 | — | ✓ |
| 定制中文规则与模型 | — | ✓ |

社区版不包含激活、支付、遥测或后台管理代码。

## 隐私与安全

- 默认不发送原始文件、原始敏感值或映射表。
- 审核与恢复页面只绑定 `127.0.0.1`，并使用随机一次性令牌。
- 安全版批准同时绑定原文件和安全文件的 SHA-256；文件变化后批准自动失效。
- 模型从固定上游修订下载，并校验大小与 SHA-256。
- 不自动监控目录，不修改系统代理，不关闭安全软件。

完整边界见 [安全设计](docs/SECURITY-DESIGN.md)、[威胁模型](docs/THREAT-MODEL.md) 和 [开源范围](docs/OPEN-SOURCE-SCOPE.md)。

发现安全问题时请不要公开提交漏洞细节，按 [SECURITY.md](SECURITY.md) 联系维护者。

## 开发

```bash
python -m pip install -e ".[agent,dev]"
ANXINAI_DISABLE_NYM=1 python -m pytest
```

运行包含 115 MB 模型的集成测试：

```bash
anxin-install-model
ANXINAI_RUN_NYM_TESTS=1 python -m pytest tests/test_nym_integration.py
```

贡献规则时请同时提供：正例、反例、中文上下文和误报说明。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 可直接用于平台发布的介绍

**一句话**

> 安心AI是一道运行在本机的中文隐私闸门：文件进入 Agent 前先脱敏，用户确认后才放行安全版。

**短介绍**

> AnxinAI Community 面向 WorkBuddy、Codex、Claude Code 等 Agent 工作流，提供中文 PII 识别、文件安全副本、本地审核和一次性本地恢复。原始敏感值不进入 Agent 对话，代码开放可审计。

**推荐标签**

```text
AI 安全 / 隐私保护 / 中文 PII / Agent Skill / 本地优先 / 数据脱敏
```

## 路线图

- [ ] 更多 Agent 平台的官方适配与安装器
- [ ] 中文误报基准与公开测试集
- [ ] 更多 Office/PDF 安全边界测试
- [ ] 可验证的规则与模型更新清单
- [ ] 社区规则贡献工具

## 许可证

代码采用 [MIT License](LICENSE)。可选 Nym 模型采用其上游 MIT 许可证，模型文件不包含在本仓库中。

AnxinAI/安心AI 名称和 Logo 不随代码许可证授权用于修改版产品的名称或品牌。派生项目请使用不同名称，避免让用户误认为由本项目维护。
