# parrot-recorder 功能规格

## 定位

Linux CLI 工具，`pip install parrot-recorder` 安装。将人类终端操作录制并提炼为 AI Agent 可执行的 Skill YAML。

核心理念：**Demonstrate once, execute anywhere.**

---

## CLI 命令

```
parrot                           # 帮助
parrot learn                     # 录制 → 清洗 → 生成 Skill YAML
parrot compose <file>            # 从 .parrot.json 或 .cast 生成 Skill YAML
parrot new                       # 交互式手写创建 Skill（不录制）
parrot validate <skill.yaml>     # 校验已有 Skill 文件
```

### parrot learn

```
parrot learn [-t "任务描述"] [-o output.yaml] [--skip-llm]
```

**默认流程（在线）：**

1. 启动录制：封装 asciinema，自动开启 `--stdin`，预设 `GIT_PAGER=cat PAGER=cat`
2. 用户操作终端，`exit` 或 `Ctrl+D` 结束
3. 自动清洗：pyte 去除 ANSI → 命令/输出分割 → TUI 检测 → 进度条压缩 → 敏感信息扫描
4. 展示摘要：识别到 N 个命令 / TUI 警告 / 敏感信息警告
5. 交互确认：
   - 提示任务描述（`-t` 已指定则跳过）
   - 调用 LLM 生成 Skill YAML
   - 预览 YAML（含参数化建议）
   - 用户选择：保存 / 编辑后再保存 / 放弃
6. 保存至 `skills/<name>.skill.yaml`（默认路径）

**离线流程（`--skip-llm`）：**

1-4 同上
5. 保存 `.parrot.json`——包含清洗后的结构化数据
6. 提示用户：复制到联网环境后运行 `parrot compose <file> -t "<task>"`

参数说明：

| 参数 | 作用 |
|---|---|
| `-t "描述"` | 预先指定任务描述，跳过交互提问 |
| `-o path` | 指定输出路径（默认 .skill.yaml，`--skip-llm` 时为 .parrot.json） |
| `--skip-llm` | 跳过 LLM，保存中间 .parrot.json 供后续 compose |

**`-t` + `-o` 同时指定 → 完全无交互，适合脚本化。**

模型通过 `.env` 的 `PARROT_MODEL` 配置，不暴露为 CLI 参数。

### parrot compose

```
parrot compose <file> [-t "任务描述"] [-o output.yaml] [--skip-llm]
```

**支持两种输入格式：**
- `.parrot.json`——`parrot learn` 的输出，跳过清洗直接生成
- `.cast`——原始 asciinema 录制，自动清洗后再生成

**流程：**

1. 加载输入文件（.parrot.json 或 .cast）
2. 如果是 .cast：自动执行清洗 + 分割
3. 提示任务描述（`-t` 已指定则跳过）
4. 调用 LLM 生成 Skill YAML（`--skip-llm` 则使用模板）
5. 预览 YAML → 确认 → 保存 → 校验

参数说明：

| 参数 | 作用 |
|---|---|
| `-t "描述"` | 预先指定任务描述，跳过交互提问 |
| `-o path.yaml` | 指定输出路径 |
| `--skip-llm` | 跳过 LLM，输出模板 YAML |

**`-t` + `-o` 同时指定 → 完全无交互，适合脚本化。**

### 离线工作流

```
远程（离线服务器）                      本地（联网）
$ parrot learn                        $ parrot compose deploy.parrot.json
  → 录制 + 清洗 + 分割                    -t "部署 user-api 服务"
  → deploy.parrot.json ──── 复制 ────→   → LLM 提炼
                                        → deploy.skill.yaml
```

### parrot new

交互式问答，逐步构建一个 Skill YAML（不需要录制）：

**异常保护**：终端断开时自动保存已录制内容，不会丢失。

### parrot new

交互式问答，逐步构建一个 Skill YAML（不需要录制）：

```
Skill 名称 → 描述 → 参数（名称=默认值，逐个添加） → 并发策略
→ 步骤逐个添加（命令、超时、重试、预期输出、回滚） → 保存
```

### parrot validate

```
parrot validate <skill.yaml>
```

按 Skill YAML Schema v0.1 校验：必填字段、类型检查、模板变量一致性、步骤 ID 唯一性、回滚声明完整性。

---

## Skill YAML Schema v0.2 — 交互步骤

### 新增字段：`interactive`

人类介入循环（Human-in-the-loop）。Agent 执行到该步骤时暂停，将前一步的输出展示给用户，等待用户输入后再继续。

```yaml
steps:
  - id: check-ports
    command: ss -tlnp | awk '{print $4}' | grep -oP ':\d+$' | sort -n | uniq
    timeout_seconds: 10
    retry: 0
    rollback: null
    rollback_risk: "Read-only check"

  - id: select-port
    interactive: true                     # 标记为交互步骤
    prompt: "选择一个未被占用的端口"         # Agent 向用户展示的提示
    variable: service_port                # 用户输入存入此变量（自动加入 parameters）
    choices_from_output: check-ports      # 可选：从哪一步的输出中提取选项展示
    validation: "^[0-9]+$"               # 可选：校验用户输入的正则
    default: "8080"                       # 可选：用户直接回车时的默认值

  - id: deploy
    command: docker run -d -p {{service_port}}:8080 --name myapp myapp:latest
    health_check:
      command: curl -s http://localhost:{{service_port}}/health
      expected_pattern: "200"
```

### 交互步骤字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `interactive` | 是 | 必须为 `true` |
| `prompt` | 是 | 展示给用户的问题/提示 |
| `variable` | 是 | 用户输入存储的变量名，可直接在其他步骤中以 `{{variable}}` 引用 |
| `choices_from_output` | 否 | 引用的 step `id`，Agent 将该步骤的输出展示为"当前状态"供用户参考 |
| `validation` | 否 | 用户输入的正则校验，不匹配则重新询问 |
| `default` | 否 | 用户直接回车时使用的默认值 |

### Agent 执行流程

```
1. 执行 check-ports → 输出 "22\n80\n443\n3000"
2. 执行 select-port（interactive: true）
   → 展示: "选择一个未被占用的端口"
   → 展示已占用: "22, 80, 443, 3000"（来自 choices_from_output）
   → 等待用户输入
3. 用户输入: "8080"
   → 校验 validation → 通过
   → 设置变量 service_port=8080
4. 执行 deploy → docker run -d -p 8080:8080 ...
```

### 典型场景

| 场景 | 前置步骤 | 交互步骤 |
|---|---|---|
| 选择空闲端口 | `ss -tlnp` | 用户选择端口号 |
| 选择 GPU（有环境时） | `nvidia-smi` | 用户选择 GPU 编号 |
| 选择部署环境 | `kubectl get namespaces` | 用户选择 namespace |
| 确认高危操作 | `docker ps -a` | 用户确认要删除的容器名 |
| 选择分支/版本 | `git branch -a` | 用户选择要部署的分支 |

---

## 中间格式 `.parrot.json`

`parrot learn` 产出的结构化中间文件，可在离线环境和本地 compose 之间传递。

```json
{
  "version": "0.1.0",
  "source": "parrot-20260529-143022.cast",
  "has_stdin_events": true,
  "tui_warnings": ["[1.1s] TUI mode entered", "[2.6s] TUI mode exited"],
  "secret_warnings": [],
  "segments": [
    {
      "command": "docker exec exif-nginx nginx -t",
      "output": "nginx: configuration file /etc/nginx/nginx.conf syntax is ok\n...",
      "prompt": "root@host:~#",
      "start_time": 0.44,
      "end_time": 1.07,
      "container_context": "exif-nginx",
      "in_tui": false
    }
  ]
}
```

**字段说明：**

| 字段 | 说明 |
|---|---|
| `version` | 格式版本 |
| `source` | 原始 .cast 文件名 |
| `has_stdin_events` | 是否含 "i" 事件（影响 compose 时的分割策略） |
| `tui_warnings` | TUI 检测警告 |
| `secret_warnings` | 敏感信息警告 |
| `segments[].command` | 用户输入的命令 |
| `segments[].output` | pyte 清洗后的命令输出 |
| `segments[].container_context` | 容器上下文（docker exec -it 进入时） |
| `segments[].in_tui` | 该命令是否在 TUI 区域内 |

---

## 录制 & 清洗管线

```
asciinema --stdin 录制
  → pyte 终端仿真（去除 ANSI/CSI/光标控制）
  → 命令/输出分割（"i" 事件精确分割，fallback prompt 正则）
  → 进度条压缩（连续 \r 覆盖行合并）
  → TUI 区域检测（\033[?1049h/l 标记跳过）
  → 容器上下文检测（提示符突变 → 标记 docker exec / ssh 边界）
  → 终端查询噪声过滤（\033[...R 等假 "i" 事件）
  → 超长输出截断（>500 字符保留首尾各 200）
  → 敏感信息扫描（AWS Key / GitHub Token / DB URL / Bearer Token）
```

### 环境预设（防止意外 TUI 触发）

录制开始时自动设置：
- `GIT_PAGER=cat`（防止 `git log` 触发 less）
- `PAGER=cat`（防止其他命令触发 pager）
- `SYSTEMD_PAGER=cat`

### 容器上下文检测

当用户通过 `docker exec -it` 或 `ssh` 进入容器/远程主机时，终端提示符会突变：

```
root@host:~# docker exec -it nginx sh     ← 主机提示符
/ # whoami                                ← 容器内提示符变了！
/ # cat /etc/os-release
/ # exit
root@host:~#                              ← 回到主机提示符
```

**检测策略：**

1. **提示符突变识别**：跟踪当前活跃的提示符模式，检测到变化时标记上下文切换
2. **分割段标注**：上下文切换后的命令标记 `container_context: nginx`，LLM 据此生成 `docker exec {{container}} xxx` 而非裸命令
3. **噪声过滤**：Alpine sh 等轻量 shell 会产生 `\033[6n`（光标位置查询），终端回应 `\033[...R` 会被误标为 "i" 事件。过滤模式：以 `\033[` 开头、`R` 结尾的 "i" 事件
4. **嵌套退出**：`exit` 从容器退出后，恢复上一个上下文

**对 Skill 生成的影响：**

```yaml
# 不好的生成（丢失容器上下文）:
steps:
  - id: who
    command: whoami              # ← 在主机上跑，不是容器内

# 正确的生成（保留容器上下文）:
steps:
  - id: check-container-user
    # original (in container nginx): whoami
    command: docker exec {{container_name}} whoami
```

### 终端查询噪声过滤

某些 shell（Alpine sh、dash 等）会主动查询终端状态，asciinema 会把这些查询的响应误标为 "i" 事件。需要过滤的假 "i" 事件模式：

| 模式 | 示例 | 来源 |
|---|---|---|
| 光标位置响应 | `\033[4;5R` | 终端响应 `\033[6n` 查询 |
| 设备属性响应 | `\033[>84;0;0c` | 终端响应 `\033[>c` 查询 |
| tmux 粘贴模式 | `\033[?2004h` / `\033[?2004l` | tmux 内部协议 |

过滤规则：任何 `content` 以 `\033[` 开头且不含可打印字符的 "i" 事件应当丢弃。

---

## Skill YAML 生成

### LLM 提炼

- 后端：Claude API / OpenAI API，通过 `.env` 或环境变量配置
- 默认模型：`claude-sonnet-4-6`
- Prompt 架构：系统 prompt（含 Schema + 3 个 few-shot 示例）+ 用户任务描述 + 清洗后的命令输出对
- 预期 token 消耗：~2,200 tokens/次

### 离线模式 (`--skip-llm`)

不调 LLM，基于内置模板生成含所有步骤的半成品 YAML：
- 命令原样保留在 `command` 字段
- `parameters` 为空，需用户手动添加
- `expected_output_pattern` 为 null
- 供用户后续编辑补充

---

## "可逆参数化" 注释方案

LLM 生成的 YAML 中，被参数化的字段用注释保留原始值：

```yaml
parameters:
  - name: port
    type: integer
    default: 8080

steps:
  - id: build
    # original: docker build -t user-api:latest .
    command: docker build -t {{service_name}}:latest .
  - id: start
    # original: docker run -d -p 8080:8080 --name user-api user-api:latest
    command: docker run -d -p {{port}}:{{port}} --name {{service_name}} {{service_name}}:latest
```

用户在编辑器中：
- **撤销某处参数化** → 拷贝注释中的原始值，覆盖 `command` 行
- **改参数名** → 改 `{{port}}` 为 `{{listen_port}}`，parameters 里同步改名
- **不想参数化** → 直接换回原始值

### 参数化检测策略

LLM 自动识别以下模式并建议参数化：
- 端口号（`8080`、`3000` 等）
- 服务/容器名（`user-api`、`web-nginx` 等）
- 路径（`/home/deploy/xxx`）
- 环境名（`test`、`staging`、`production`）
- 版本号/标签（`v1.2.3`、`latest`）
- URL（`https://api.example.com`）

不参数化：包名（`nginx`、`docker`）、系统路径（`/etc`、`/var`）、协议名（`http`、`tcp`）。

---

## 配置

### 完整配置项

所有配置通过项目根目录的 `.env` 文件设置，复制 `.env.example` 开始：

```bash
# ── LLM Backend ──────────────────────────────────────────────
PARROT_LLM_BACKEND=anthropic      # anthropic | openai

# ── Model ────────────────────────────────────────────────────
PARROT_MODEL=claude-sonnet-4-6    # 或 gpt-4o / claude-opus-4-7 等

# ── Anthropic (Claude) API ───────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-xxx
# ANTHROPIC_BASE_URL=https://api.anthropic.com    # 默认，代理时覆盖

# ── OpenAI API ───────────────────────────────────────────────
OPENAI_API_KEY=sk-xxx
# OPENAI_BASE_URL=https://api.openai.com/v1       # 默认，支持 Azure / Ollama / 代理
# OPENAI_BASE_URL=http://localhost:11434/v1        # 本地 Ollama 示例
# OPENAI_BASE_URL=https://xxx.openai.azure.com/    # Azure OpenAI 示例

# ── Output ───────────────────────────────────────────────────
# PARROT_OUTPUT_DIR=./skills                       # 默认 .skills/
```

### 配置读取优先级

1. 命令行参数（`--model`、`--api-key`）
2. 项目 `.env` 文件
3. 系统环境变量

### 支持的接口兼容性

| 后端 | 原生服务 | 代理/VPN | 本地模型 | Azure |
|---|---|---|---|---|
| Anthropic | ✅ 默认 URL | ✅ `ANTHROPIC_BASE_URL` | - | - |
| OpenAI | ✅ 默认 URL | ✅ `OPENAI_BASE_URL` | ✅ Ollama/vLLM 等 | ✅ Azure URL |
```

---

## 安全扫描

录制清洗阶段自动匹配以下模式，检测到时在摘要中警告：

| 模式 | 正则 |
|---|---|
| AWS Access Key | `AKIA[0-9A-Z]{16}` |
| GitHub Personal Token | `ghp_[0-9a-zA-Z]{36}` |
| GitHub OAuth Token | `gho_[0-9a-zA-Z]{36}` |
| DB URL with password | `mysql://*:*@` |
| Authorization Bearer | `Authorization: Bearer *` |
| 通用密码赋值 | `password/passwd/secret=*` |

检测到敏感信息时：
1. 录制清洗摘要中显示警告（不显示具体值）
2. 对应步骤的注释中标记 `# WARNING: 敏感信息已检测`
3. 不会将敏感信息写入 Skill YAML 的 `command` 字段

---

## 错误 & 异常处理

| 场景 | 行为 |
|---|---|
| 录制中终端断开 | 自动保存已录制的 `.cast`，提示恢复路径 |
| LLM API 调用失败 | 输出错误信息，自动降级为 `--skip-llm` 模式 |
| LLM 返回非法 YAML | 展示原始输出，提示用户手动修复 |
| 目标磁盘空间不足 | 录制前检查，低于 100MB 时拒绝启动并警告 |
| asciinema 未安装 | 提示 `apt install asciinema` 并退出 |
| 网络断开（LLM 调用） | 重试 2 次，仍失败则降级为模板模式 |

---

## 明确不做（Phase 1）

- Skill 库管理（list / search / execute / delete）
- Windows / macOS 支持
- GUI / Web UI
- 多用户协作（通过 Git 间接支持）
- apt/rpm 打包（pip 已足够）
- 录制时长的实时显示
- 多段录制合并

---

## 技术栈（来自可行性验证）

| 组件 | 选型 | 验证结果 |
|---|---|---|
| 终端录制 | asciinema 2.x `--stdin` | 交互式录制产生 "i"/"o" 事件分离 |
| 终端仿真 | pyte 0.8.x | Docker 118KB→1.2KB（99% 压缩） |
| LLM SDK | anthropic / openai | Claude Sonnet 4.6 生成质量良好 |
| YAML 处理 | PyYAML | 读写 + Schema 校验 |
| 安装分发 | pip（setuptools/pyproject.toml） | - |

---

## 管线验证数据（来自阿里云 ECS 实测）

| 场景 | 噪声率 | 清洗压缩比 | 命令分割 | LLM 生成 |
|---|---|---|---|---|
| Nginx 配置 reload | 0% | 84% | 3/3 | 高 ✅ |
| Docker build + run | 97% | 99% | 6/6 | 高 ✅ |
| Git clone | 0%（\r） | 96.6% | - | - |
| curl API 调用 | 0% | - | 2/2 | 高 ✅ |
| Vim（TUI） | 81% | 92.9% | 正确跳过 | - |
| 敏感信息 | - | - | 检测到 2 个密钥 | - |

---

## 成功指标

| 指标 | 目标 | 验证方式 |
|---|---|---|
| 录制到 Skill 耗时 | <3 分钟（含 LLM） | 真实场景计时 |
| 命令分割准确率 | ≥95%（stdin 模式） | 10 个测试场景 |
| LLM 生成可用率 | ≥70% 仅需 ≤3 处修改 | 人工评估 |
| ANSI 清洗完整率 | ≥90% 可读 | pyte 后抽查 |
| 敏感信息检出率 | 100%（已知模式） | 测试用例覆盖 |
