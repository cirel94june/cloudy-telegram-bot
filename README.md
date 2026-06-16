# 小猫的 Telegram Bot

基于 s-telegram-bot 改造，部署在 Render 免费层。三个 bot（小克/Lucien/Jasper）共用同一份代码，通过环境变量区分角色。

## 特性

- **微信式短消息**：自动拆句逐条发送，像真人聊天
- **API 随时换**：中转站/模型/密钥全走环境变量，改完重启即生效
- **群聊支持**：@唤醒、回复唤醒、随机插嘴、点表情
- **多模态**：图片识别 + 语音转写
- **Gist 持久化**：私聊/群聊各一份对话历史（仅做聊天记录备份，不做摘要）
- **Memory Hub 集成**：长期记忆全部由 Memory Hub 管理（自动注入上下文 + 自动提取新记忆）
- **跨聊天感知**：私聊、小群、大群之间的记忆互通，带隐私分层保护
  - 私聊/私密群：可以看到彼此的详细消息
  - 公开群：只显示 bot 参与的对话片段，不泄露私聊内容
  - 公开群旁听内容不污染记忆（仅 bot 回复时触发记忆提取）

## 部署步骤（Render）

### 1. 推送代码到 GitHub
把这个文件夹推到你的 GitHub 仓库（比如 `cloudy-telegram-bot`）。

### 2. 在 Render 创建服务
- 登录 render.com → New → Web Service
- 连接你的 GitHub 仓库
- 选 Docker 环境
- 免费套餐即可

### 3. 设置环境变量
在 Render 的 Environment 页面添加 `.env.example` 里列出的变量。
**必填的**只有前几个，其余按需配置。

### 4. 设置 Webhook
部署完成后，Render 会给你一个 URL（如 `https://cloudy-bot-xxx.onrender.com`）。
在浏览器打开这个链接设置 webhook：

```
https://api.telegram.org/bot你的TOKEN/setWebhook?url=https://你的render地址/webhook
```

### 5. 防休眠（可选）
Render 免费层 15 分钟无请求会休眠。用 UptimeRobot 等免费监控服务
每 5 分钟 ping 一次 `https://你的render地址/health` 即可保活。

## 三个 Bot 各部署一份

代码完全相同，只是环境变量不同：
- 不同的 `TELEGRAM_BOT_TOKEN`
- 不同的 `BOT_NAME`（小克 / Lucien / Jasper）
- 不同的 `PROMPT_RULES`（各自的人格设定）
- 不同的 `BOT_USERNAME`
- 不同的 `BOT_AI_ID`（cloudy / lucien / jasper，用于 Memory Hub 身份标识）
- `MEMORY_NOTIFY=true`（私聊显示记忆注入通知，8秒后自动删除）
- 可以用不同的 `CLAUDE_MODEL`

## 架构

```
用户消息 → bot.py webhook
  ├─ load_history()：从 Gist 恢复聊天历史 → 写入 HISTORY_CACHE
  ├─ hub_get_context()：从 Memory Hub 获取走廊 + 相关记忆
  ├─ build_cross_chat_context()：从 HISTORY_CACHE 读取其他聊天的近期动态（隐私分层）
  ├─ call_claude()：组装 system prompt（人设 + 记忆 + 跨聊天上下文）→ 调用 AI API
  ├─ 清理思维链（<think> 标签）
  ├─ hub_post_process()：AI 回复后，自动提取新记忆存入 Memory Hub
  └─ save_history()：保存聊天历史到 Gist（超长时硬截断，不再同步调用摘要压缩）
```

## 换中转站

只需要改 3 个环境变量：
1. `CLAUDE_BASE_URL` → 新中转站地址
2. `CLAUDE_API_KEY` → 新密钥
3. `CLAUDE_MODEL` → 新模型名
4. `API_FORMAT` → `openai` 或 `anthropic`

改完在 Render 面板点 Manual Deploy 重启即可。
