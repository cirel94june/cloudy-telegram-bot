# 回家借工具

出门在 TG 上的时候，需要家里（AionsHome）才有的功能，就回家借，用完把结果带回来说。
总方案在 AnimalsHome 仓库 `docs/施工方案-bot出门.md`；家里那边的入口是 `POST /api/outing/tool`。

## 怎么用

模型在回复里写（提示词里会告诉它）：

| 写法 | 做什么 |
|---|---|
| `[回家:搜索:关键词]` | 联网搜索 |
| `[回家:读网页:https://…]` | 打开一个网页读正文 |
| `[回家:淘宝:商品关键词]` | 淘宝搜几件商品 |

bot 先不发这条回复，去家里借（一轮最多 2 次），把结果放进背景再问模型一次，发第二次的回复。
借不成（家里没连上、次数用完、没开给这只……）时，结果里写原因，模型自然地说一声，不编结果。

## 规矩

- 朋友大群（big_group）里只有小猫自己发的消息（sender_id == CECI_ID）才算她开口，家里才肯借；
  私聊和小群随便借。家里那边也会再查一次。
- 每次借用带一个新的 request_id；家里按它只办一次。
- 不跟随跳转（allow_redirects=False），钥匙不会被带去别的地址。

## 配置（Render / Fly 的环境变量，四个都填了才开）

| 变量 | 是什么 |
|---|---|
| `AIONSHOME_URL` | 家的地址，`https://home.animalshome.top` |
| `AIONSHOME_OUTING_KEY` | 家里发给这只 bot 的钥匙（和服务器 `/etc/aionshome.env` 里 `AIONSHOME_OUTING_KEY_<座位>` 一样） |
| `CF_ACCESS_CLIENT_ID` | Cloudflare 服务钥匙的 Client ID（只开「回家借工具」那一扇门） |
| `CF_ACCESS_CLIENT_SECRET` | Cloudflare 服务钥匙的 Client Secret |

少填一个就整个关着：提示词里不提，回复里万一出现 `[回家:…]` 也只是删掉。
