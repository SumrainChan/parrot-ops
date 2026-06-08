# 录制场景验证汇总

> 2026-05-28，阿里云 ECS (Ubuntu 24.04.3, Docker 29.2.1)，asciinema 2.4.0

## 场景一览

| # | 场景 | 录制文件 | 噪声 | stdin | 可行性 | 说明 |
|---|---|---|---|---|---|---|
| 1 | 简单命令 | `recording_scenario1.cast` | 低 | 无 | ✅ | whoami, uname, df, ls, echo, rm |
| 2 | 进度条 | `recording_scenario2.cast` | 中 | 无 | ✅ | \r 覆盖式进度条，curl 下载 |
| 3 | TUI 检测 | `recording_scenario3.cast` | 高 | 无 | ✅ | 模拟 less 进入交替屏幕缓冲区 |
| 4 | Docker 构建 | `recording_docker.cast` | 极高 | **有** | ✅ | docker build+run+rmi，97% ANSI 噪声 |
| 5 | Git 操作 | `recording_scenario5_git.cast` | 极高 | 无 | ✅ | git clone，\r 进度条产生 180+ 事件 |
| 6 | Nginx reload | `recording_scenario6_nginx.cast` | 低 | 无 | ✅ | docker exec + nginx -t + nginx -s reload |
| 7 | curl API | `recording_scenario7_curl.cast` | 低 | 无 | ✅ | GitHub API + JSON 响应 |
| 8 | pip install | `recording_scenario8_pip.cast` | 中 | 无 | ✅ | PEP 668 环境阻止，但 ANSI 颜色码捕获 |
| 9 | tail -f | `recording_scenario9_tail.cast` | 低 | 无 | ⚠️ 不应成为 step | 无限阻塞输出，需检测 |
| 10 | 敏感信息 | `recording_scenario10_secrets.cast` | 低 | 无 | ⚠️ 需扫描 | AWS Key, DB password, GitHub Token 均出现 |
| 11 | Vim TUI (真实) | `recording_vim_tui.cast` | 极高 | **有** | ❌ TUI 跳过 | \033[?1049h/l 可靠检测，内部按键应过滤 |
| 12 | 交互式 stdin | `recording_realtime.cast` | 低 | **有** | ✅ | whoami, ls, df via tmux，4 条 "i" 事件 |
| 13 | Docker exec 容器 | `recording_docker_exec.cast` | 中 | **有** | ✅ | 进入 Alpine 容器 + 执行命令 + 退出 |

---

## 详细场景

### 场景 1：简单线性命令

**命令序列：**
```bash
whoami
uname -a
df -h /
ls /etc/ssh/
echo "hello world" > /tmp/test.txt && cat /tmp/test.txt
rm /tmp/test.txt
```

**验证结果：**
- 纯文本输出，无 ANSI 噪声
- 无 "i" 事件（`-c` 非交互模式）
- Prompt 正则可正确分割

**生成 Skill 建议：** 演示性命令（whoami, echo, ls）不进入 Skill steps，仅 `df -h /` 可成为健康检查 step。

---

### 场景 2：进度条覆盖

**命令序列：**
```bash
apt update          # 模拟输出
# 进度条: \rProgress: [####  ] 20% ... \rProgress: [####] 100%
curl -L -o /tmp/test_download.html http://example.com
stat -c "%s" /tmp/test_download.html
rm /tmp/test_download.html
```

**验证结果：**
- `\r` 进度条每 0.2s 产生一个事件（共 8 帧）
- pyte 屏幕缓冲区天然处理 `\r` 覆盖
- 压缩后仅保留最终完成行

**生成 Skill 建议：** 进度条被压缩为 `[进度条: 已完成]`，不影响 LLM 理解。

---

### 场景 3：TUI 检测（模拟 less）

**命令序列：**
```bash
less /etc/os-release    # 进入交替屏幕 \033[?1049h
# ... 文件内容 ...
# 退出交替屏幕 \033[?1049l
echo "command after TUI works fine"
```

**验证结果：**
- `\033[?1049h` 和 `\033[?1049l` 在录制中完好保留
- 可据此可靠检测 TUI 区域

**生成 Skill 建议：** less 内部内容标记为"跳过"，只保留前后的正常命令。

---

### 场景 4：Docker 构建（压力测试）

**命令序列（stdin 交互式）：**
```bash
cd /tmp
cat Dockerfile.test
docker build -t parrot-test:v1 -f Dockerfile.test .
docker run --rm parrot-test:v1
docker rmi parrot-test:v1 2>/dev/null; rm Dockerfile.test
exit
```

**验证结果：**
| 指标 | 值 |
|---|---|
| 总事件 | 193 (6 "i", 187 "o") |
| ANSI 噪声率 | 97% (181/187) |
| 原始大小 | 117,823 bytes |
| pyte 清洗后 | 1,233 bytes |
| 压缩比 | **99.0%** |
| 命令分割 | 6/6 正确 |

**生成的 Skill YAML：**
```yaml
name: build-and-run-parrot-test
parameters:
  - image_tag (default: parrot-test:v1)
  - dockerfile_name (default: Dockerfile.test)
  - working_directory (default: /tmp)
steps:
  - build: docker build -t {{image_tag}} -f {{dockerfile_name}} .
  - run: docker run --rm {{image_tag}}
  - cleanup: docker rmi {{image_tag}}; rm {{dockerfile_name}}
```
→ ECS 远程执行通过。

---

### 场景 5：Git Clone（\r 噪声）

**命令序列：**
```bash
git clone https://github.com/sindresorhus/np.git /tmp/test-git-clone
cd test-git-clone && git log --oneline -3
git branch -a
rm -rf /tmp/test-git-clone
```

**验证结果：**
| 指标 | 值 |
|---|---|
| 总事件 | 184 (全 "o") |
| \r 进度事件 | 180+ |
| 原始大小 | 18,532 bytes |
| pyte 清洗后 | 639 bytes |
| 压缩比 | **96.6%** |
| 意外发现 | `git log` 触发 pager → 需 `GIT_PAGER=cat` |

**生成 Skill 建议：** Git 操作是可录制的（但 clone 本身噪声很大）。必须在录制时预设 `GIT_PAGER=cat`。

---

### 场景 6：Nginx 配置 reload

**命令序列：**
```bash
docker exec exif-nginx cat /etc/nginx/conf.d/default.conf | head -5
docker exec exif-nginx nginx -t
docker exec exif-nginx nginx -s reload
echo "reload status: $?"
```

**验证结果：**
- 13 行事件，0% ANSI 噪声
- 输出清晰可读
- `echo "reload status"` 被 LLM 正确排除出 steps

**生成的 Skill YAML：**
```yaml
name: test-and-reload-nginx
parameters:
  - container_name (required)
preconditions:
  - docker ps --filter name={{container_name}}
steps:
  - test-config: docker exec {{container_name}} nginx -t
  - reload: docker exec {{container_name}} nginx -s reload
```
→ ECS 远程执行通过（753ms），两个步骤均成功。

---

### 场景 7：curl API 调用

**命令序列：**
```bash
curl -s https://api.github.com/repos/sindresorhus/np | head -20
curl -s -o /dev/null -w "HTTP Status: %{http_code}\nTime: %{time_total}s\n" https://api.github.com
curl -s "https://jsonplaceholder.typicode.com/posts/1"
```

**验证结果：**
- JSON 输出结构化完好
- LLM 正确识别为只读检查 → `concurrency: allow`

**生成的 Skill YAML：**
```yaml
name: check-api-endpoint
concurrency: allow
parameters:
  - endpoint_url (required)
  - expected_status (default: 200)
steps:
  - check-status: curl -s -o /dev/null -w "..." {{endpoint_url}}
  - fetch-body: curl -s {{endpoint_url}}
```
→ ECS 远程执行通过（1784ms），curl 返回 HTTP 200。

---

### 场景 8：pip install（ANSI 颜色码）

**命令序列：**
```bash
pip3 install --no-cache-dir tqdm
pip3 uninstall -y tqdm
```

**验证结果：**
- PEP 668 "externally-managed-environment" 阻止了实际安装
- 但 ANSI 颜色码被捕获（`\033[1;31m` 红色错误文本）
- 演示了真实世界的错误输出格式

**生成 Skill 建议：** pip install 需要 `--break-system-packages` 或在 venv 中执行。错误输出的 ANSI 格式不影响清洗。

---

### 场景 9：tail -f（无限输出）

**命令序列：**
```bash
echo "line1" >> /tmp/test_log.txt
echo "line2" >> /tmp/test_log.txt
echo "line3" >> /tmp/test_log.txt
tail -f /tmp/test_log.txt &      # ← 无限阻塞
rm /tmp/test_log.txt
```

**验证结果：**
- 真实 `tail -f` 永远不会出现下一个 prompt
- 检测策略：N 秒无新 prompt → 标记为"无限输出"

**生成 Skill 建议：** tail -f 不应成为 Skill step。应警告用户并跳过。

---

### 场景 10：敏感信息检测

**命令序列：**
```bash
export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
export DATABASE_URL="mysql://admin:SuperSecret123@localhost:3306/mydb"
curl -H "Authorization: Bearer ghp_1a2b3c4d5e6f7g8h9i0j" https://api.example.com
echo "Normal command without secrets"
```

**验证结果：**
- AWS Key 模式 → 检测到 "AWS Access Key"
- DB URL → 检测到 "DB URL with password"
- GitHub Token → 检测到 "Auth header Bearer token"
- 3/3 敏感模式被检出

**生成 Skill 建议：** 录制器必须在清洗阶段扫描并警告，不应将敏感信息写入 Skill YAML。

---

### 场景 11：Vim TUI（真实）

**命令序列（stdin 交互式）：**
```bash
cat /tmp/vim_test.txt          # 正常命令
vim /tmp/vim_test.txt           # 进入 TUI
  i                              # vim 按键（在 TUI 内）
  edited in vim                  # vim 内输入
  :wq                            # vim 内命令
echo "back to normal terminal"  # 正常命令（退出 TUI 后）
```

**验证结果：**
| 指标 | 值 |
|---|---|
| \033[?1049h 检测 | ✅ TUI 进入 (1.1s) |
| \033[?1049l 检测 | ✅ TUI 退出 (2.6s) |
| TUI 内 "i" 事件 | 5 个（vim 按键，应过滤） |
| TUI 外命令 | 2 个（cat, echo）正确提取 |

**TUI 内 "i" 事件的组成：**
```
[2] \033[2;2R\033[3;1R\033[>84;0;0c   ← 终端查询响应（假）
[3] i                                     ← vim 按键
[4] edited in vim                         ← vim 内打字
[5] \033                                  ← Escape 键
[6] :wq                                   ← vim 命令
```
这些都应被过滤——TUI 检测信号 + TUI 区间内所有 "i" 事件丢弃。

---

### 场景 12：交互式 stdin 捕获（基准）

**命令序列（stdin 交互式，via tmux）：**
```bash
whoami
ls /etc/ssh
df -h
exit
```

**验证结果：**
- 4 条 "i" 事件，每条对应一个命令
- 命令和输出的时间戳对齐
- 这是"理想录制"的基准

**生成 Skill 建议：** 交互式录制 + "--stdin" 是最高质量的输入路径。

---

### 场景 13：Docker exec 进入容器

**命令序列（stdin 交互式，via tmux）：**
```bash
docker exec -it exif-nginx sh        # 进入 Alpine 容器
whoami                                # ← 在容器内
cat /etc/os-release                   # ← 容器是 Alpine
hostname                              # ← 输出容器 ID
ls /usr/share/nginx/html/
exit                                  # 退出容器
echo "back on host: $(hostname)"      # ← 回到主机
```

**验证结果：**

| 观察点 | 主机 | 容器内 |
|---|---|---|
| 提示符 | `root@iZ7xvh6...:~# ` | `/ # ` |
| hostname | `iZ7xvh6useomc...` | `f215885c2c1d`（容器ID） |
| OS | Ubuntu 24.04 | Alpine Linux v3.23 |

**关键发现：**
1. "i" 事件在容器内完美工作
2. 提示符突变清晰标记容器边界
3. Alpine sh 产生 `\033[6n` 终端查询 → 响应被误标为 "i" 事件（需过滤）
4. 5 个假 "i" 事件：`\033[4;5R`、`\033[6;5R`、`\033[13;5R`、`\033[15;5R`、`\033[17;5R`

**对分割引擎的要求：**
```
检测到提示符突变 → 标记容器上下文
  容器内所有命令 → 标注 container_context: "exif-nginx"
  LLM 生成时 → 包装为 docker exec {{container}} xxx
```

---

## 压缩效果汇总

| 场景 | 噪声类型 | 原始 | 清洗后 | 压缩比 |
|---|---|---|---|---|
| Nginx reload | 无 | 1.1KB | 1.0KB | 16% |
| curl API | 无 | 1.7KB | — | — |
| Git clone | \r 进度条 | 18.5KB | 0.6KB | 96.6% |
| pip install | ANSI 颜色 | 3.3KB | — | — |
| Vim TUI | 交替屏幕 | 2.8KB | 0.2KB | 92.9% |
| Docker 构建 | ANSI 全量 | 117.8KB | 1.2KB | **99.0%** |
| Docker exec | tmux + 终端查询 | 1.3KB | — | — |

---

## 管线决策矩阵

| 场景 | 录制 | "--stdin | 清洗 | 分割 | LLM 生成 | MVP |
|---|---|---|---|---|---|---|
| 简单命令 | ✅ | 可选 | 直接 | 正则 | 高 | ✅ |
| Docker 构建 | ✅ | **必须** | pyte | stdin | 高 | ✅ |
| docker exec 容器 | ✅ | **必须** | pyte | stdin+上下文 | 中高 | ✅ |
| Git 操作 | ✅ | 可选 | pyte | 正则/stdin | 高 | ✅ |
| Nginx reload | ✅ | 可选 | 直接 | 正则 | 高 | ✅ |
| curl API | ✅ | 可选 | 直接 | 正则 | 高 | ✅ |
| pip/npm install | ✅ | **必须** | pyte | stdin | 中 | ✅ |
| 敏感信息 | — | — | — | — | — | ⚠️ 扫描 |
| tail -f | — | — | — | — | — | ❌ 跳过 |
| vim / htop / less | — | — | — | — | — | ❌ 跳过 |
| psql / mongo CLI | ❌ | — | — | — | — | ❌ 排除 |
