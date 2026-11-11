# 插件设置页列方向空白段修复（2026-11-11）

## 环境

- 后端：当前工作树的 `serve-api`，端口 8420；使用真实 `config.toml` 与真实数据库 / memory 副本，`/api/health` 返回 `profile_ready=true`。
- 浏览器：系统 Chrome，400×700 插件视口；popup 由后端同源 `/web/popup-e2e/popup.html` 提供，七个设置 tab 全部打开。
- 插件页面的 `chrome.storage.local` 只替换后端 endpoint 配置；设置页的网络请求、配置数据、数据库和模型列表均为真实链路。完整 MV3 加载仍受本机 Chrome 管理策略限制，因此没有把「页面内 shim」表述为商店安装包验收。

## 根因

`.settings-probe-status` 原来固定 `flex: 1 1 180px`。它同时用于横向探测行和纵向设置 section：

- 横向 `.settings-probe-row` 需要它占满剩余宽度；
- 在 `.settings-routing-summary` / `.settings-field` 等 **column** flex 容器中，`flex-basis: 180px` 变成垂直基准，空状态也占 180px 高。

结果是「模型」tab 默认链下方、「通用」tab 的 Tailnet / 保存同步 / 重新初始化状态下方出现大段空白。

## 修复

- 基础状态行改为 `flex: 0 1 auto`，按内容高度渲染；
- 仅 `.settings-probe-row .settings-probe-status` 保留 `flex: 1 1 180px`，横向探测文案继续撑满行。
- `extension/tests/popup-layout.test.ts` 增加静态回归，防止以后把 180px 基准加回基础 class。

## 实测

| tab | 修复前 panel | 修复后 panel | 180px 状态行 |
| --- | ---: | ---: | ---: |
| 模型 | 2628px | 2448px | 1 → 0 |
| 通用 | 2718px | 2228px | 3 → 0 |

修复后「通用」tab 的 Tailnet 状态为 17px、重新初始化状态为 33px；空状态为 0。七个 tab 均无水平溢出，tab 标签完整可见。320 / 340 / 360px 窄屏仍按既有三列换行，390px 以上四列换行。

## 证据

- [模型 tab 修复前后](assets/extension-settings-layout-2026-11-11/model-before-after.png)
- [通用 tab 修复前后](assets/extension-settings-layout-2026-11-11/general-before-after.png)
