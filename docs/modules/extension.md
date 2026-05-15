# 娴忚鍣ㄦ彃浠舵ā鍧?

## 妯″潡鑼冨洿

`extension/` 鏄?Chrome 鎻掍欢瀛愰」鐩紝璐熻矗锛?

- 鍦?B 绔?/ 灏忕孩涔?/ 鎶栭煶 / YouTube 绛夋敮鎸佺殑绔欑偣閲囬泦琛屼负浜嬩欢锛堝钩鍙版棤鍏冲唴鏍?+ 骞冲彴閫傞厤鍣級
- 閫氳繃 background service worker 缂撳啿骞朵笂鎶ュ埌鏈湴鍚庣
- 鍦?side panel 涓睍绀鸿繛鎺ョ姸鎬併€佹帹鑽愮粨鏋溿€佺敾鍍忓拰鑱婂ぉ鍏ュ彛

褰撳墠閲岀▼纰戣繘搴︼細

| 瀛愭ā鍧?| 鐘舵€?| 璇存槑 |
|------|------|------|
| 8.1 琛屼负閲囬泦 | 鉁?| `collector.ts` + `service-worker.ts` 宸叉帴閫氱湡瀹炰簨浠堕摼 |
| 8.2 鍚庣 API | 鉁?| Python 渚?`/api/events`銆乣/api/health`銆乣/api/recommendations` 宸插彲鑱旇皟 |
| 8.3 Side Panel | 鉁?| 宸插垏鍒?side panel 涓诲叆鍙ｏ紝缁х画澶嶇敤 `popup/` 椤甸潰鎵胯浇鎺ㄨ崘 / 鐢诲儚 / 鑱婂ぉ涓?tab |
| 鎸佺画琛ヨ揣涓庨€氱煡 | 鉁?| 杩愯鐘舵€佸凡鎺ュ叆 popup锛宻ervice worker 浼氭媺鍙栭珮缃俊閫氱煡骞跺洖鍐欏彂閫佺姸鎬?|
- 设置页新增“后端端口”项（默认 `8420`），仅影响插件侧 API / runtime-stream 连接，不写入后端 `config.toml`。
| B 绔?Cookie 鑷姩鍚屾 | 鉁?| service worker 浼氳鍙?`SESSDATA` / `bili_jct` / `DedeUserID` 涓変欢濂楀苟鎺ㄩ€佸埌鏈湴鍚庣锛涘悗绔殏鏈惎鍔ㄦ椂鍒囧埌 1 鍒嗛挓閲嶈瘯锛屾垚鍔熷悗鎭㈠ 60 鍒嗛挓鍏滃簳鍒锋柊锛涘悗绔?runtime-stream 涔熷彲鍙?`bilibili_cookie_sync_requested` 璁╂墿灞曠珛鍒诲洖浼?|
| 鎶栭煶 Cookie 鑷姩鍚屾 | 鉁?| service worker 浼氳鍙?douyin.com Cookie header 骞舵帹閫佸埌 `/api/sources/dy/cookie`锛涘悗绔繚瀛樺埌 `data/douyin_cookie.json`锛屼緵 `discover --source douyin` / `discover-douyin` 鍦ㄦ棤鐜鍙橀噺瑕嗙洊鏃朵娇鐢紱鍐峰惎鍔ㄣ€乺untime-stream 璇锋眰鍜?alarm 鍏滃簳閮戒細瑙﹀彂鍚屾 |
| 璁ょ煡鍙樺寲鎻愰啋 | 鉁?| service worker 浼氭彁绀哄叧閿鐭ュ彉鍖栵紝鐢诲儚 tab 浼氭樉绀衡€滈樋B 鏈€杩戞柊璁颁綇浜嗕粈涔堚€?|
| 璁ょ煡鍙樺寲鍘嗗彶鍒嗛〉 | 鉁?| 鐢诲儚 tab 鐨勮鐭ュ崱鐗囨敮鎸佸睍寮€璇︽儏锛屽苟鍙笅鎷夋垨鐐瑰嚮鎸夐挳缁х画鏌ョ湅鏇存棭鐨勫彉鍖栬褰?|
| 璁ょ煡鍗＄墖涓婁笅鏂囨緞娓?| 鉁?| 鐢诲儚 tab 鐨勮鐭ュ崱鐗囬粯璁ゆ€佺幇鍦ㄥ浐瀹氬睍绀衡€滅粨璁?+ 涓婁笅鏂?+ 鐘舵€佹彁绀衡€濓紝鐢ㄦ埛鍙洿鎺ョ湅鍑鸿繖鏄鍝潯鍐呭/鍝疆鑱婂ぉ/鍝粍鑱氬悎淇″彿褰㈡垚鐨勫垽鏂紝浠ュ強杩欏紶鍗＄墖鏄惁杩樿兘灞曞紑 |
| 鐢诲儚澶氬眰璁ょ煡灞曠ず | 鉁?| 鐢诲儚 tab 鐜板凡鎶娾€滀綘鎬庝箞澶勭悊淇℃伅 / 浣犲湪鍐呭閲岄暱鏈熷湪鎵句粈涔?/ 杩欓樀瀛愭洿鍍忓湪缁忓巻浠€涔堚€濆崟鐙媶寮€锛屼笉鍐嶅彧鏄剧ず涓€娈电敾鍍?prose 鍔犲叴瓒?chips |
| 澶氭簮琛屼负閲囬泦锛圡VP锛?| 鉁?| content script 鎷嗘垚銆屽钩鍙版棤鍏?kernel + 骞冲彴閫傞厤鍣ㄣ€嶏紝鏂板灏忕孩涔﹂€傞厤鍣ㄣ€俶anifest 瑕嗙洊 `*.xiaohongshu.com`锛屼簨浠舵惡甯?`source_platform` 瀛楁锛汳VP 浠呴噰 snapshot / click / scroll / search锛宭ike/collect 寤跺悗 |
| xhs token 鍡呮帰锛圡AIN world锛?| 鉁?| `src/main/xhs-token-sniffer.ts` 浠?`world: "MAIN"`銆乣run_at: "document_start"` 娉ㄥ叆 xhs 椤甸潰锛屽姭鎸?`window.fetch` / `XMLHttpRequest` 鎵弿 xhs 鑷 API 鍝嶅簲閲岀殑 `(note_id, xsec_token)` 瀵瑰瓙锛岄€氳繃 `postMessage` 妗ユ帴鍒?isolated world 鍐?`/api/sources/xhs/tokens` 鍥炲～鈥斺€旇В鍐虫悳绱㈤〉姘镐笉甯?token 瀵艰嚧鐐瑰嚮鍛戒腑 300031 鐧诲綍澧欑殑闂 |
| xhs 鍒濆鍖栫敾鍍忎换鍔?| 鉁?| 鍚庣鍙淳鍙?`bootstrap_profile` 浠诲姟锛涙彃浠跺厛鎵撳紑灏忕孩涔?`/explore`锛屾粴鍔ㄤ换鍔′細浠ュ墠鍙?tab 鐐瑰嚮椤甸潰鈥滄垜鈥濆叆鍙ｈ繘鍏?profile锛屽啀浠?profile 椤?state / DOM 瑙ｆ瀽鏀惰棌銆佺偣璧炲拰灏忕孩涔﹂〉闈㈠唴鏄惧紡娴忚璁板綍淇″彿锛涙樉寮忓惎鐢?`max_scroll_rounds` 鏃朵細鏈夐檺婊氬姩锛屽苟鐢?`status="partial"` 鍒嗘壒鍥炰紶缁?`/api/sources/xhs/task-result` |
| 鎶栭煶鍒濆鍖栫敾鍍忎换鍔?| 鉁?| 鍚庣鍙淳鍙?`bootstrap_profile` 浠诲姟锛涙彃浠朵緷娆¤闂姈闊冲彂甯?/ 鏀惰棌 / 鍠滄 / 鍏虫敞 scope锛宑ontent script 缁撳悎 DOM銆丮AIN-world fetch tap 涓?API harvester 閲囬泦鏉＄洰锛屽苟鐢?`partial` 鍒嗘壒鍥炰紶缁?`/api/sources/dy/task-result` |
| 鎶栭煶鎼滅储浠诲姟 | 鉁?| 鍚庣鍙淳鍙?`search` 浠诲姟锛涙彃浠跺湪宸茬櫥褰曟姈闊充細璇濅腑鎵ц鍏抽敭璇嶆悳绱紝MAIN-world search bridge 璋冪敤椤甸潰 `byted_acrawler.frontierSign()` 绛惧悕鎼滅储 API锛屽洖浼?`dy_search` 鍊欓€変緵 CLI smoke 鍜屾寮?`dy-plugin-search` discovery 浣跨敤锛涘崟鍏抽敭璇嶄换鍔?timeout 涓?180 绉?|
| 鎶栭煶鐑偣浠诲姟 | 鉁?| 鍚庣鍙淳鍙?`hot` 浠诲姟锛涙彃浠舵墦寮€ `/hot/{sentence_id}`锛屼粠璺宠浆鍚庣殑 `/video/{aweme_id}` 鍙?seed aweme锛屽苟閫氳繃 MAIN-world related bridge 绛惧悕 `/aweme/v1/web/aweme/related/`锛屽洖浼?`dy_hot` 鍊欓€変緵 `dy-plugin-hot-related` discovery 浣跨敤 |
| 鎶栭煶棣栭〉鎺ㄨ崘娴佷换鍔?| 鉁?| 鍚庣鍙淳鍙?`feed` 浠诲姟锛涙彃浠跺湪宸茬櫥褰曟姈闊抽椤甸€氳繃 MAIN-world feed bridge 绛惧悕 `/aweme/v1/web/tab/feed/`锛屽洖浼?`dy_feed` 鍊欓€変緵 `dy-plugin-feed` discovery 浣跨敤 |
| YouTube 鍒濆鍖栫敾鍍忎换鍔?| 鉁?| 鍚庣鍙淳鍙?`bootstrap_profile` 浠诲姟锛涙彃浠朵緷娆¤闂?`/feed/history`銆乣/feed/channels`銆乣/playlist?list=LL`锛屼粠 DOM 璇诲彇瑙傜湅鍘嗗彶 / 璁㈤槄 / 鐐硅禐骞剁敤 `partial` 鍒嗘壒鍥炰紶缁?`/api/sources/yt/task-result` |

## 鐩綍缁撴瀯

```text
extension/
鈹溾攢鈹€ manifest.json
鈹溾攢鈹€ package.json
鈹溾攢鈹€ popup/
鈹?  鈹溾攢鈹€ popup.html
鈹?  鈹溾攢鈹€ popup.js
鈹?  鈹斺攢鈹€ popup-helpers.js
鈹溾攢鈹€ src/
鈹?  鈹溾攢鈹€ background/
鈹?  鈹?  鈹溾攢鈹€ buffer.ts
鈹?  鈹?  鈹溾攢鈹€ cookie-sync.ts     # B 绔?/ 鎶栭煶 Cookie 鑷姩鍚屾鍒?localhost 鍚庣
鈹?  鈹?  鈹斺攢鈹€ service-worker.ts
鈹?  鈹溾攢鈹€ content/
鈹?  鈹?  鈹溾攢鈹€ kernel.ts          # 骞冲彴鏃犲叧鐨?DOM 瑙傚療 + 浜嬩欢娲惧彂
鈹?  鈹?  鈹溾攢鈹€ bilibili.ts        # B 绔?entry point锛屾寕杞?bilibiliAdapter
鈹?  鈹?  鈹溾攢鈹€ douyin.ts          # 鎶栭煶 entry point锛屾寕杞?fetch tap 涓?task executor
鈹?  鈹?  鈹溾攢鈹€ dy/
鈹?  鈹?  鈹?  鈹溾攢鈹€ bootstrap.ts   # 鎶栭煶 bootstrap scope 缁撴灉鑱氬悎涓?partial payload
鈹?  鈹?  鈹?  鈹溾攢鈹€ dom-extractor.ts # 鎶栭煶椤甸潰 DOM 鍏滃簳瑙ｆ瀽
鈹?  鈹?  鈹?  鈹斺攢鈹€ task-executor.ts # 鎶栭煶鍚庡彴浠诲姟鍦ㄩ〉闈㈠唴鐨勬墽琛屽叆鍙?
鈹?  鈹?  鈹溾攢鈹€ xiaohongshu.ts     # 灏忕孩涔?entry point锛屾寕杞?xiaohongshuAdapter
鈹?  鈹?  鈹溾攢鈹€ youtube.ts         # YouTube entry point锛屾寕杞戒换鍔?executor
鈹?  鈹?  鈹溾攢鈹€ yt/
鈹?  鈹?  鈹?  鈹斺攢鈹€ task-executor.ts # YouTube bootstrap scope DOM 瑙ｆ瀽涓庡洖浼?
鈹?  鈹?  鈹斺攢鈹€ xhs/
鈹?  鈹?      鈹溾攢鈹€ bootstrap.ts   # 鍒濆鍖栫敾鍍忎换鍔＄殑 state / DOM 瑙ｆ瀽 helper
鈹?  鈹?      鈹溾攢鈹€ passive.ts     # 灏忕孩涔﹁鍔?URL / note metadata 閲囬泦
鈹?  鈹?      鈹斺攢鈹€ task-executor.ts # 鍚庡彴浠诲姟鍦ㄩ〉闈㈠唴鐨勬墽琛屽叆鍙?
鈹?  鈹溾攢鈹€ main/
鈹?  鈹?  鈹溾攢鈹€ dy-fetch-tap.ts       # MAIN-world 鎶栭煶 fetch tap + API harvester
鈹?  鈹?  鈹斺攢鈹€ xhs-token-sniffer.ts  # MAIN-world fetch/XHR sniffer锛屾崬 xsec_token
鈹?  鈹斺攢鈹€ shared/
鈹?      鈹溾攢鈹€ behavior.ts        # createBehaviorEvent / DOM snapshot kernel
鈹?      鈹溾攢鈹€ types.ts           # BehaviorEvent + PlatformAdapter 鎺ュ彛
鈹?      鈹斺攢鈹€ platforms/
鈹?          鈹溾攢鈹€ bilibili.ts    # bvid 鎻愬彇銆佸崱鐗囬€夋嫨鍣ㄣ€佸姩浣滃叧閿瓧
鈹?          鈹斺攢鈹€ xiaohongshu.ts # note_id 鎻愬彇銆佸崱鐗囬€夋嫨鍣?
鈹斺攢鈹€ tests/
    鈹溾攢鈹€ collector-helpers.test.ts
    鈹溾攢鈹€ dist-module-specifiers.test.ts
    鈹溾攢鈹€ manifest-assets.test.ts
    鈹溾攢鈹€ popup-helpers.test.ts
    鈹斺攢鈹€ service-worker-buffer.test.ts
```

## 褰撳墠鑳藉姏

### `collector.ts`

璐熻矗鍐呭鑴氭湰渚ч噰闆嗭細

- 鐐瑰嚮涓庢悳绱?
- 瑙嗛 `view` / `pause` / `seek`
- 椤甸潰蹇収 `snapshot`
- 婊氬姩 `scroll`
- 鍗＄墖鍋滅暀 `hover`
- 璇勮 / 鐐硅禐 / 鎶曞竵 / 鏀惰棌鎰忓浘浜嬩欢

鍚屾椂鏀寔 B 绔?SPA 瀵艰埅鎰熺煡锛屽湪 URL 鍙樺寲鏃堕噸鏂板彂閫佸揩鐓у苟閲嶇粦瑙嗛鐩戝惉銆?

### `service-worker.ts`

璐熻矗鍚庡彴缂撳啿涓庝笂鎶ワ細

- 鎺ユ敹鍐呭鑴氭湰浜嬩欢
- 楂橀浜嬩欢鍘婚噸
- 寮轰俊鍙疯涓轰紭鍏?flush
- `chrome.alarms` 鍛ㄦ湡鎬ф壒閲忓彂閫?
- 鍙戦€佸け璐ユ椂鎶婁簨浠跺洖濉埌缂撳啿鍖?
- flush 鎴愬姛鍚庢鏌ヤ竴娆″緟鍙戦€氱煡
- 缂撳啿涓虹┖鏃朵篃浼氬懆鏈熻疆璇㈤珮缃俊閫氱煡
- 姣忔 service worker 鍐峰惎鍔ㄩ兘浼氬惎鍔?B 绔欏拰鎶栭煶 Cookie 鍚屾锛涘鏋?localhost 鍚庣鏆傛椂涓嶅彲鐢紝浼氶€氳繃 `chrome.alarms` 浠?1 鍒嗛挓闂撮殧閲嶈瘯锛屾垚鍔熷悓姝ュ悗鎭㈠涓?60 鍒嗛挓鍒锋柊
- 浠?`client=background` 杩炴帴 `/api/runtime-stream` 鍚庯紝濡傛灉鍚庣鍙戠幇鏈湴缂哄皯 B 绔?Cookie锛屼細鏀跺埌 `bilibili_cookie_sync_requested`锛涘鏋?`[sources.douyin].enabled=true` 涓旂己灏戞姈闊?Cookie锛屼細鏀跺埌 `douyin_cookie_sync_requested`銆傛墿灞曟敹鍒板悗浼氱珛鍗虫墽琛屽搴?Cookie POST
- Cookie 鐩戝惉鍣ㄥ箓绛夋敞鍐岋紝閬垮厤 onInstalled / onStartup / 鍐峰惎鍔ㄩ噸澶嶆寕杞藉鑷村悓涓€娆＄櫥褰曡Е鍙戝娆?POST
- 鐐瑰嚮鎵╁睍鍥炬爣鏃朵紭鍏堟墦寮€ side panel
- 閫氱煡鍜岃鐭ユ彁閱掍篃浼氫紭鍏堟妸鐢ㄦ埛甯﹀洖鎻掍欢 side panel 涓婁笅鏂?
- 鍦ㄦ帹鑽愰€氱煡涔嬪锛岃鐭ュ彉鍖栭€氱煡浼氭墦寮€甯?`?tab=profile` 鐨勬彃浠堕〉闈紝鐩存帴钀藉埌鐢诲儚瑙嗗浘
- 鎯婂枩鎺ㄨ崘閫氱煡鐜板湪浼氭墦寮€甯?`?tab=recommend&delight=<bvid>` 鐨勬彃浠堕〉闈紝钀藉埌瀵瑰簲鐨勯灞忔儕鍠滃崱锛岃€屼笉鏄彧鎶婁汉涓㈠洖閫氱敤鎺ㄨ崘椤?

### 灏忕孩涔︿换鍔℃ˉ

`src/background/xhs-task-dispatcher.ts` 浼氳疆璇㈠悗绔?`/api/sources/xhs/next-task`銆傚綋鏀跺埌 `bootstrap_profile` 鏃讹紝瀹冧細鍏堟墦寮€ `https://www.xiaohongshu.com/explore`锛涢粯璁ょ敤闈炴縺娲?tab锛岃嫢浠诲姟鏄惧紡鍚敤浜?`max_scroll_rounds > 0` 鍒欐墦寮€鍓嶅彴 tab锛屾柟渚块〉闈㈣嚜宸卞鐞?profile 鐐瑰嚮鍜屽悗缁粴鍔ㄣ€俤ispatcher 浼氬悜 content script 鍙戦€侊細

```json
{
  "task_id": "...",
  "type": "bootstrap_profile",
  "scopes": ["saved", "liked", "xhs_history"],
  "max_items_per_scope": 20,
  "max_scroll_rounds": 0,
  "scroll_wait_ms": 1200,
  "max_stagnant_scroll_rounds": 5
}
```

`src/content/xhs/task-executor.ts` 浼氳皟鐢?`bootstrap.ts` 瑙ｆ瀽灏忕孩涔﹂〉闈㈠凡缁忔覆鏌撳嚭鐨?state銆傝嫢褰撳墠椤典笉鏄釜浜轰富椤碉紝executor 浼氬彧浠庡彲淇″叆鍙ｆ壘褰撳墠鐧诲綍鐢ㄦ埛鐨?profile URL锛氫紭鍏堜娇鐢ㄥ皬绾功瀵艰埅鏍忊€滄垜鈥濈殑閾炬帴锛屽叾娆′娇鐢?`__INITIAL_STATE__.user.loggedIn=true` 鏃剁殑 `userInfo.userId`銆傛粴鍔ㄤ换鍔℃壘鍒板鑸爮鈥滄垜鈥濇椂锛屼細鍏堟妸 `next_url_clicked=true` 鐨勪腑闂寸粨鏋滃洖浼狅紝鐒跺悗鍦ㄩ〉闈㈠唴瑙﹀彂 anchor click锛沚ackground 鏀跺埌鍚庝笉浼氱洿鎺?`tabs.update(profileUrl)`锛岃€屾槸绛夊緟鍚屼竴 tab 鑷繁瀵艰埅瀹屾垚骞跺啀娆℃墽琛屼换鍔★紝SPA 娌℃湁鍙戝嚭瀹屾暣 load 浜嬩欢鏃朵細鐭殏 fallback 鍒板悓 tab 閲嶅彂銆傚埌杈?profile 鍚庯紝executor 浼氱户缁瓑寰呭皬绾功 React 椤甸潰鍑虹幇 profile state銆佹敹钘?璧炶繃 tab 鏂囨鎴?note 鍗＄墖锛岄伩鍏嶆祻瑙堝櫒 load complete 鏃╀簬椤甸潰鍐呭娓叉煋鏃惰鍒や负绌恒€傚彧鏈夋壘涓嶅埌鍙偣鍑诲叆鍙ｃ€佷絾鑳戒粠 state 鎺ㄥ嚭 profile URL 鏃讹紝background 鎵嶄細鍦ㄥ悓涓€ tab 鐩存帴瀵艰埅鍒?profile 椤点€?

鍒?profile 椤靛悗锛宔xecutor 璇诲彇 `__INITIAL_STATE__.user.notes` 鍒嗙粍锛歚[0]` 涓哄彂甯冿紝`[1]` 涓烘敹钘忥紝`[2]` 涓鸿禐杩囷紱濡傛灉鏀惰棌 / 璧炶繃鍒嗙粍灏氭湭鍔犺浇锛屼細灏濊瘯鐐瑰嚮瀵瑰簲 profile tab 绛夊緟椤甸潰鑷繁琛ラ綈 state锛屽啀閫€鍥炲埌宸叉覆鏌?DOM 鍗＄墖瑙ｆ瀽銆俿tate 瑙ｆ瀽鍏煎灏忕孩涔?profile noteCard 缁撴瀯锛坄noteCard.displayTitle`銆乣noteCard.user.nickName`銆乣noteCard.cover.urlDefault`锛夛紝婊氬姩鍚庢瘡杞篃浼氭妸 state 鍜?DOM 缁撴灉鍚堝苟锛岄伩鍏嶅彧鐪嬪綋鍓嶅彲瑙?DOM 鏃舵紡鎺夊凡鍔犺浇浣嗚铏氭嫙鍒楄〃绉诲嚭鐨勫崱鐗囥€傞粯璁や换鍔′笉婊氬姩锛涘鏋滃悗绔换鍔℃樉寮忎紶鍏?`max_scroll_rounds > 0`锛宔xecutor 浼氫紭鍏堟帰娴嬪皬绾功瀹為檯 feed / waterfall / masonry 婊氬姩瀹瑰櫒锛屽苟鎺掗櫎 `clientHeight` 杩囧皬銆乣overflow-y` 涓嶆槸 `auto/scroll/overlay`銆佷互鍙?`channel-list` / sidebar 杩欑被闈炲唴瀹逛晶鏍忥紱濡傛灉娌℃湁鍙敤鍐呭瀹瑰櫒锛屼細鍥為€€鍒扮獥鍙ｇ骇灏忔 `wheel` / `scrollBy`锛岃创杩戠敤鎴锋墜鍔ㄥ墠鍙版粴鍔ㄣ€備换鍔′細杩愯鍒拌揪鍒?`max_items_per_scope`銆佽揪鍒版粴鍔ㄨ疆鏁颁笂闄愶紝鎴栬繛缁簲杞病鏈夋柊澧炲崱鐗囥€傛瘡涓?scope 鐨勯鎵瑰拰鍚庣画鏂板鍗＄墖浼氬厛浠?`status="partial"` 鍥炰紶锛宲artial 鎵规涔熶細鎸夎 scope 鍓╀綑鍚嶉瑁佸壀锛宐ackground 绛夊悗绔‘璁ゅ悗鍐嶇户缁紝鏈€鍚庣敤 `status="ok"` 瀹屾垚浠诲姟銆?

鍚庣鍙互鎸変换鍔℃帶鍒舵粴鍔ㄨ妭濂忥紝涓嶉渶瑕佹敼鎻掍欢甯搁噺锛?

| payload 瀛楁 | 榛樿鍊?| 鎻掍欢绔鍓?| 璇存槑 |
|---|---:|---:|---|
| `scroll_wait_ms` | `1200` | `500..5000` | 姣忚疆婊氬姩鍚庣瓑寰呭皬绾功鐎戝竷娴佸姞杞界殑鏃堕棿 |
| `max_stagnant_scroll_rounds` | `5` | `1..10` | 杩炵画澶氬皯杞病鏈夋柊澧炲崱鐗囧悗鍋滄 |

dispatcher 浼氭妸杩欎袱涓瓧娈甸€忎紶缁?content script锛涘鏋?`scroll_wait_ms` 鎷夐暱锛宐ackground 涔熶細鍚屾鏀惧浠诲姟 timeout锛屾渶澶?6 鍒嗛挓銆?

婊氬姩浠诲姟鐨?debug 浼氬甫 `scroll_candidates` 鍜?`tab_load_results[scope].scroll_metrics`锛氬墠鑰呭垪鍑洪〉闈笂鎺掑悕闈犲墠鐨勬粴鍔ㄥ€欓€夈€乣overflow-y`銆乶ote 鏁板拰璇勫垎锛涘悗鑰呮寜姣忚疆璁板綍瀹為檯婊氬姩鐩爣銆乣scroll_top / scroll_height / client_height`銆佹粴鍔ㄥ墠鍚庝綅缃€佹柊澧炲崱鐗囨暟鍜岀疮璁″崱鐗囨暟銆傜湡瀹炶仈璋冩椂鍙敤瀹冨尯鍒嗏€滈〉闈㈠埌搴曚簡鈥濃€滄粴閿欏鍣ㄤ簡鈥濆拰鈥滈〉闈㈡病鏈夋毚闇叉洿娣辩殑婊氬姩鑺傜偣鈥濄€?

杩欐潯閾捐矾浠嶄笉鐩存帴璋冪敤灏忕孩涔?API銆佷笉璇诲彇 cookie銆佷笉鎺ヨЕ Chrome 娴忚鍣ㄥ巻鍙层€傝繖閲岀殑 `xhs_history` 鎸団€滃皬绾功缃戦〉鑷繁鏄庣‘鏆撮湶鐨勬祻瑙堣褰?/ 瓒宠抗 state鈥濓紝涓嶄細鎶婃櫘閫?`/explore` 鎺ㄨ崘娴佸綋鎴愭祻瑙堣褰曪紱濡傛灉灏忕孩涔︾綉椤垫病鏈夋毚闇茬ǔ瀹氬叆鍙ｏ紝灏辫繑鍥?0 鏉″苟璁╁垵濮嬪寲缁х画銆?

#### v0.3.10 self_info 鍏ㄨ矾寰勬崟鑾?

**浠绘剰** XHS 椤甸潰鍙鐧诲綍,`window.__INITIAL_STATE__.user.userInfo` 灏卞甫 self user_id + nickname銆倂0.3.10 璧锋妸鎶藉彇浠庡彧鍦?bootstrap_profile 浠诲姟閲屽彂鐢?鎵╁埌涓夋潯鍏ユ睜璺緞鍏ㄨ鐩?

| 璺緞 | 鏂囦欢 | 琛屼负 |
|------|------|------|
| 琚姩閲囬泦(浠绘剰 XHS 椤? | `src/content/xiaohongshu.ts:runPassiveCollection` + `src/content/xhs/passive.ts` | 璇?state,scrape-time `filterSelfAuthoredNotes` 鎶?`note.author === self.nickname` 鐨勫崱鐗囩洿鎺?drop;observation 閲屽 `self_info` 缁欏悗绔?|
| search / creator 浠诲姟 | `src/content/xhs/task-executor.ts:executeTaskInPage` 闈?bootstrap 鍒嗘敮 | 鍚屼笂,`TaskResultPayload.self_info` 甯﹀洖 |
| bootstrap_profile 浠诲姟 | `src/content/xhs/task-executor.ts:executeBootstrapTaskInPage` | 鏃㈡湁璺緞涓嶅彉,debug 閲屼粛宓?`xhs_bootstrap.steps[*].self_info` 鍏煎鑰佸悗绔?|

鍚庣 v0.3.57 鐨?`_extract_self_info_from_payload` 浼樺厛璇婚《灞?`self_info`,fallback 鍒版棫鐨?nested 浣嶇疆,**鏂版棫鎵╁睍+鏂版棫鍚庣鐨勫洓绉嶇粍鍚堥兘涓嶇牬**(鑰佹墿灞曢厤鑰佸悗绔笉鍔?鏂版墿灞曢厤鑰佸悗绔細 500鈥斺€斿崌绾х獥鍙ｆ湡鐭殏)銆傝繖鎶?鐢ㄦ埛鑷繁鍙戠殑绗旇杩涙帹鑽愭睜"闂(灞庡睅/鑷165銕″ぇ浜旀埧绛?浠?race condition 娌绘垚纭畾鎬ц繃婊ゃ€?

### 鎶栭煶浠诲姟妗?

`src/background/dy-task-dispatcher.ts` 浼氳疆璇㈠悗绔?`/api/sources/dy/next-task`銆傚綋鏀跺埌 `bootstrap_profile` 鏃讹紝dispatcher 浼氭墦寮€鎶栭煶椤甸潰锛屽苟鎸変换鍔?payload 渚濇鎵ц锛?

```json
{
  "task_id": "...",
  "type": "bootstrap_profile",
  "scopes": ["dy_post", "dy_collect", "dy_like", "dy_follow"],
  "max_items_per_scope": 300,
  "max_scroll_rounds": 15
}
```

`src/content/dy/task-executor.ts` 璐熻矗鍦ㄩ〉闈㈠唴鍒囨崲 scope銆佹粴鍔ㄤ笌鍥炰紶銆俙src/main/dy-fetch-tap.ts` 杩愯鍦?MAIN world锛屾嫤鎴姈闊抽〉闈?fetch锛屽苟瀵规敹钘?/ 鍠滄 scope 璧扮珯鍐?API harvester锛歚/aweme/v1/web/aweme/favorite/` 瀵瑰簲 `dy_collect`锛宍/aweme/v1/web/aweme/like/` 瀵瑰簲 `dy_like`銆傞噰闆嗗埌鐨勬潯鐩€氳繃 `postMessage` 鍥炲埌 isolated world 鍚庤繘鍏?`BootstrapItemSink` 鍘婚噸锛屽啀浠?`status="partial"` 鍒嗘壒 POST 鍒?`/api/sources/dy/task-result`锛涙渶缁?scope 璺戝畬鍚庣敤 `ok` 瀹屾垚浠诲姟銆傚悗绔細鎶婃柊澧?videos 杞垚缁熶竴浜嬩欢锛氬彂甯?鈫?`view`锛屾敹钘?鈫?`favorite`锛岀偣璧?鈫?`like`锛屽叧娉?鈫?`follow`銆?

CLI 渚у垎涓ゅ眰浣跨敤杩欐潯閾捐矾锛?

- `openbiliclaw init --yes-douyin` 浼氭妸浠诲姟缁撴灉鍔犲叆鍒濆鍖栦簨浠堕泦鍚堬紝杩涘叆 `analyze_events()` 鍜?`build_initial_profile()`銆?
- `openbiliclaw fetch-douyin` 鍙仛鍗曟簮 smoke / 琛ユ媺锛涗簨浠剁敱 daemon 鍦ㄦ帴鏀?partial 鏃跺啓鍏?memory锛孋LI 鑷韩涓嶄細鍐嶄紶鎾竴娆★紝涔熶笉浼氶殣寮忚Е鍙戠敾鍍忛噸寤恒€?

### YouTube 浠诲姟妗?

`src/background/yt-task-dispatcher.ts` 浼氳疆璇㈠悗绔?`/api/sources/yt/next-task`銆傚綋鏀跺埌 `bootstrap_profile` 鏃讹紝dispatcher 浼氭墦寮€涓€涓墠鍙?YouTube tab锛屽苟鎸変换鍔?payload 涓茶鎵ц锛?

```json
{
  "task_id": "...",
  "type": "bootstrap_profile",
  "scopes": ["yt_history", "yt_subscriptions", "yt_likes"],
  "max_items_per_scope": 300,
  "max_scroll_rounds": 10
}
```

`src/content/yt/task-executor.ts` 璐熻矗鍦ㄩ〉闈㈠唴婊氬姩骞惰鍙?DOM銆俙yt_history` 瀵瑰簲 `/feed/history`锛宍yt_subscriptions` 瀵瑰簲 `/feed/channels`锛宍yt_likes` 瀵瑰簲 `/playlist?list=LL`銆傛瘡涓?scope 瀹屾垚鍚庯紝background 浠?`partial` 鍥炰紶鏂板 items 鍜?scope counts锛屾渶鍚庝互 `ok` 瀹屾垚浠诲姟銆傚悗绔細鎶婃柊澧?items 杞垚缁熶竴浜嬩欢锛氳鐪嬪巻鍙?鈫?`view`锛岃闃?鈫?`follow`锛岀偣璧?鈫?`like`銆?

CLI 渚у垎涓ゅ眰浣跨敤杩欐潯閾捐矾锛?

- `openbiliclaw init --yes-youtube` 浼氬湪鎶栭煶 collect 瀹屾垚鍚庢墠鍏ラ槦 YouTube锛岄伩鍏嶄袱涓墠鍙?tab 浠诲姟鍚屾椂鎶㈡祻瑙堝櫒鐒︾偣锛屽苟鎶婄粨鏋滃姞鍏?`analyze_events()` 鍜?`build_initial_profile()`銆?
- `openbiliclaw fetch-youtube` 鍙仛鍗曟簮 smoke / 琛ユ媺锛屼笉闅愬紡瑙﹀彂鐢诲儚閲嶅缓銆?

鎶栭煶 dispatcher 鏀跺埌 `search` 鏃讹紝浼氬厛鎵撳紑鎶栭煶棣栭〉锛屽啀涓烘瘡涓叧閿瘝鎵撳紑鎶栭煶鎼滅储椤靛苟鍙戦€?`DY_SEARCH_EXECUTE`锛?

```json
{
  "task_id": "...",
  "type": "search",
  "keywords": ["鐚?, "鏈烘閿洏"],
  "max_items_per_keyword": 20
}
```

dispatcher 绛夊緟棣栭〉銆佹悳绱㈤〉鍜岀儹鐐归〉 ready 鏃朵細鍚屾椂澶勭悊涓ょ鎯呭喌锛氭甯哥殑 `chrome.tabs.onUpdated(status="complete")`锛屼互鍙婃姈闊?SPA 宸茬粡璺冲埌鐩爣椤典絾娌℃湁鍐嶅彂瀹屾暣 `complete` 浜嬩欢鐨?fallback timer锛岄伩鍏嶄换鍔″崱浣忕洿鍒?`task_timeout`銆俿earch 浠诲姟鎸夊叧閿瘝鏁拌绠楄秴鏃剁獥鍙ｏ紝鍗曞叧閿瘝鑷冲皯 180 绉掞紝瑕嗙洊棣栭〉鎵撳紑銆佹悳绱㈤〉璺宠浆銆丮AIN-world acrawler 绛惧悕 API 鍜?DOM 鍏滃簳瑙ｆ瀽鐨勭湡瀹炶€楁椂锛涘悗绔?`DouyinPluginSearchClient` 榛樿涔熺瓑 180 绉掞紝閬垮厤鎻掍欢鍒氬紑濮嬫墽琛?search bridge 灏辫鍚庣娓呮垚 stale銆俙src/content/douyin.ts` 浼氬皾璇曡Е鍙戦〉闈㈡悳绱?UI銆佺洃鍚〉闈㈣嚜韬悳绱㈠搷搴旓紝骞堕€氳繃 `src/main/dy-fetch-tap.ts` 鐨?MAIN-world search API bridge 鍏滃簳鎷夊彇 `/aweme/v1/web/general/search/single/`銆傝繖涓?bridge 浼氳ˉ榻愭祻瑙堝櫒鍙傛暟锛屽苟璋冪敤椤甸潰 `byted_acrawler.frontierSign()` 鐢熸垚 `X-Bogus` 鍚庣敤 `credentials: "include"` 璇锋眰锛岄伩鍏嶇畝鍖栫洿杩炴帴鍙ｅ懡涓?`antispam_check / hit_shark` 杞┖銆傜儹鐐逛换鍔″鐢ㄥ悓涓€ MAIN-world 绛惧悕鑳藉姏锛氬悗鍙版墦寮€ `/hot/{sentence_id}` 鍚庯紝content script 浠庡綋鍓?`/video/{aweme_id}` 瑙ｆ瀽 seed aweme锛屽啀璇锋眰 `/aweme/v1/web/aweme/related/` 鎷夌浉鍏宠棰戯紱dispatcher 浼氭寜浠诲姟鎬荤洰鏍囨暟绱锛岃揪鍒扮洰鏍囧悗涓嶅啀缁х画鎵撳紑鍚庣画 hot seed銆俧eed 浠诲姟鍚屾牱澶嶇敤 MAIN-world 绛惧悕鑳藉姏锛屽湪棣栭〉璇锋眰 `/aweme/v1/web/tab/feed/` 鎷夋帹鑽愭祦銆傛悳绱㈢粨鏋滀互 `scope="dy_search"`銆佺儹鐐圭粨鏋滀互 `scope="dy_hot"`銆侀椤垫帹鑽愮粨鏋滀互 `scope="dy_feed"` 鍥炲啓鍒?`dy_tasks.result_json`锛屼笉浼氳浆鎴愬垵濮嬪寲鐢诲儚浜嬩欢锛沗DouyinPluginSearchClient` 浼氭妸杩欎簺鍊欓€夋槧灏勬垚 aweme-like JSON锛屽垎鍒互 `dy-plugin-search` / `dy-plugin-hot-related` / `dy-plugin-feed` 杩涘叆 discovery銆?

CLI 鍏ュ彛锛?

- `openbiliclaw search-douyin -k 鐚?--max-items-per-keyword 10 -w 180`锛氱湡瀹?smoke 鎻掍欢鎼滅储鍙洖銆?
- `discover-douyin --source hot --limit 3 --no-cache --no-evaluate`锛氱湡瀹?smoke 鐑 related 鍙洖銆?
- `discover-douyin --source feed --limit 3 --no-cache --no-evaluate`锛氱湡瀹?smoke 棣栭〉鎺ㄨ崘娴佸彫鍥炪€?
- direct-cookie `discover-douyin --source search` 濡傛灉閬囧埌绌虹粨鏋滐紝鍙敤 `search-douyin` 鍒ゆ柇鐧诲綍娴忚鍣ㄨ矾寰勬槸鍚︿粛鑳芥媺鍒板€欓€夈€?

### `popup/`

`popup/` 鐩綍褰撳墠鎵胯浇 side panel 椤甸潰锛屽凡鍏峰锛?

- 设置页新增“后端端口”项（默认 `8420`），仅影响插件侧 API / runtime-stream 连接，不写入后端 `config.toml`。
- 鍚庣杩炴帴鐘舵€佹鏌?
- 浠?`/api/recommendations` 鎷夊彇鎺ㄨ崘鍒楄〃
- 璁剧疆椤典細閫氳繃 `/api/config` 璇诲彇骞朵繚瀛樺悗绔厤缃紝淇濆瓨鍚庤姹傚悗绔儹閲嶈浇锛涘綋鍓嶈鐩?LLM provider/key/model銆丏eepSeek reasoning銆丱penRouter headers銆乸er-module LLM override銆丅 绔欐祻瑙堝櫒銆侀€氱敤 source 娴忚鍣ㄣ€佸皬绾功 / 鎶栭煶 source 棰勭畻銆佹暟鎹洰褰曘€丼QLite 璺緞銆佽皟搴︺€佽嚜鍔ㄦ洿鏂般€佸€欓€夋睜骞冲彴閰嶆瘮銆佺寽娴嬪叴瓒ｅ弬鏁板拰鏃ュ織娓呯悊鍙傛暟
- 璁剧疆椤典繚瀛橀厤缃椂浼氫繚鐣欏悗绔凡鏈夌殑楂樼骇瀛楁锛歚save_config()` 浼氫覆琛屽寲 scheduler speculation / auto-update 鍜?logging unmanaged cleanup 瀛楁锛岄伩鍏?UI 淇敼甯哥敤椤规椂鎶婇殣钘忛珮绾ч」鍐欏洖榛樿鍊?
- 鎺ㄨ崘 tab 鐜板凡鏀规垚鈥滄崲涓€鎵光€濓紝浼氳皟鐢?`/api/recommendations/reshuffle` 鐩存帴浠?discovery pool 绉掔骇鎹㈠嚭涓€鎵规柊鎺ㄨ崘
- 鎺ㄨ崘 tab 婊氬埌搴曟椂浼氳皟鐢?`/api/recommendations/append` 缁х画寰€涓嬬画 10 鏉★紝涓嶄細鎶婂綋鍓嶈繖涓€灞忕洿鎺ユ浛鎹㈡帀锛涢娆℃覆鏌撱€佸垏鍥炴帹鑽?tab 鍜岃拷鍔犲畬鎴愬悗涔熶細鍐嶆鏌ヤ竴娆″簳閮ㄨ窛绂伙紝閬垮厤鍋滃湪搴曢儴鏃舵病鏈夋柊 scroll 浜嬩欢瀵艰嚧缁〉鍗′綇
- popup API 鐜板湪浼氱粺涓€瑙勮寖鍖栨帹鑽愰」锛岃拷鍔犲嚭鏉ョ殑 `cover_url` 涔熶細琚敹鏁涙垚鍙洿鎺ュ姞杞界殑 `https://` 鍦板潃
- `/api/recommendations/refresh` 浠嶄繚鐣欎负鍚庡彴琛ヨ揣鍏ュ彛锛岀敤浜庣户缁線鍊欓€夋睜閲屾寔缁繘璐?
- popup 鎺ㄨ崘鍗＄墖鐜板湪涓嶄細鍐嶆妸绌?`expression / topic_label` 琛ユ垚鍥哄畾鍗犱綅鏂囨锛涘悗绔鐢熸垚娌″畬鎴愭椂锛岃繖涓ゅ潡浼氱洿鎺ラ殣钘?
- 浜壊 side panel 瑙嗚绯荤粺锛氶《閮?hero + inline 鐘舵€佸窘鏍囥€佽兌鍥?tab銆佺粺涓€鍗＄墖浣撶郴锛屾暣浣撴洿璐磋繎 B 绔欏唴瀹逛骇鍝佹皵璐?
- 鎺ㄨ崘 tab锛氬睍绀鸿棰戝皝闈€佹爣棰樸€乁P 涓汇€乣topic_label`銆佹湅鍙嬪紡鎺ㄨ崘鏂囨锛屽苟閫氳繃鈥滄墦寮€瑙嗛鈥濇槑纭烦杞埌瀵瑰簲 B 绔欒棰戦〉
- 濡傛灉鏌愭潯鍐呭鏆傛椂娌℃湁鍙敤灏侀潰锛屽崱鐗囦細鍥為€€鍒板崰浣嶆€侊紝涓嶅奖鍝嶆崲鐗囧拰鍙嶉
- 鎺ㄨ崘灏侀潰涓嶅啀渚濊禆鍘熺敓 `loading="lazy"`锛岄伩鍏嶅唴閮ㄦ粴鍔ㄥ鍣ㄧ画椤垫椂鏂板崱鐗囧皝闈㈠伓鍙戠┖鐧?
- 搴曢儴鎻愮ず鍖哄凡鍗囩骇涓烘洿鏄庢樉鐨勭姸鎬佹í鏉★紝浼氭寜鎴愬姛 / 鎻愮ず / 閿欒鍒囨崲瀵规瘮搴﹀拰鐘舵€佺偣锛屽噺灏戔€滃弽棣堝彂鍑哄幓浜嗕絾鐪嬩笉瑙佲€濈殑鎰熻
- 淇鍗＄墖璇烦杞細`鍠滄` / `涓嶅枩娆 / `鍐欎竴鍙 / 杈撳叆妗?/ 鍙戦€佹寜閽笉鍐嶅啋娉¤Е鍙戣棰戞墦寮€
- `鍠滄` / `涓嶅枩娆 / `鍐欎竴鍙 閮戒細璋冪敤 `/api/feedback`
- 鎺ㄨ崘鍗＄墖閲岀殑 `鍐欎竴鍙?-> 鍙戝嚭鍘籤 鐜板湪浼氬湪鎸夐挳鏈湴鏄剧ず `鍙戦€佷腑... / 宸插彂鍑?/ 鍙噸璇昤 涓夋€侊紝鍗＄墖搴曢儴涔熶細鍚屾鍐欐槑杩欏彞鏄惁鐪熺殑鍙戝嚭鍘讳簡
- 椤甸潰浼氳鍙?`/api/runtime-status`锛屽尯鍒嗏€滄湭鍒濆鍖?/ 姝ｅ湪琛ヨ揣 / 鎺ㄨ崘鍙敤鈥濅笁绉嶇姸鎬侊紱鍒濆鍖栧垰瀹屾垚浣?`initialized` 鏍囪灏氭湭鍚屾鏃讹紝濡傛灉宸叉湁琛ヨ揣涓垨鍊欓€夋睜淇″彿锛屼笉鍐嶈鎻愮ず鐢ㄦ埛閲嶆柊鎵ц init
- popup 鎵撳紑鏈熼棿鐜板湪浼氬缓绔?`/api/runtime-stream` websocket 杩炴帴锛屽簳閮ㄦ彁绀烘潯鍜屾睜瀛愮姸鎬佷細璺熺潃鍚庣浜嬩欢瀹炴椂鍙樺寲
- popup 搴曢儴鎻愮ず鍖哄凡鍗囩骇鎴愬彲灞曞紑鍔ㄦ€佸崱锛氶粯璁や袱琛屾樉绀衡€滅幇鍦ㄥ湪蹇欎粈涔?/ 鏈€杩戜竴娆″叧閿彉鍖栤€濓紝鐐?`鏇村` 鍙互灞曞紑鏈€杩戝巻鍙?
- 鏂板 `/api/activity-feed` 鑱氬悎鎺ュ彛锛宲opup 浼氭妸璁ょ煡鏇存柊銆佸弽棣堣涓嬩簡銆佹崲涓€鎵瑰拰琛ヨ揣缁撴灉鏀舵垚鍚屼竴鍧楀姩鎬侀潰鏉?
- 鈥滄崲涓€鎵?/ 缁х画杩藉姞鈥濈幇鍦ㄤ紭鍏堢洿鎺ユ秷璐?discovery pool 閲岄鐢熸垚濂界殑 `expression / topic_label`
- 濡傛灉鏌愭潯鍊欓€夌殑棰勭敓鎴愭枃妗堣繕娌¤ˉ濂斤紝鍗＄墖浼氬厛鍙睍绀烘爣棰樸€佸皝闈㈠拰 UP 淇℃伅锛屼笉浼氬啀鏄剧ず缁熶竴鍗犱綅璇濋鎴栭粯璁ゆ帹鑽愮悊鐢?
- 鍚庡彴琛ヨ揣缁х画寮傛杩涜锛屼笉浼氶樆濉?popup 绔嬪埢鎹㈢墖
- pool 鐘舵€佹憳瑕佺幇鍦ㄤ細鍖哄垎鈥滄鍦ㄨˉ璐р€濃€滆繖杞壘鍒颁簡鍐呭浣嗗彲鎹㈠簱瀛樻病鍙樷€濃€滃垰琛ヨ繘 N 鏉♀€濓紝涓嶅啀鎶?refresh 杩涜涓拰涓婁竴杞噣鏂板涓?0 娣锋垚鍚屼竴鍙?
- 鎺ㄨ崘 tab 澶撮儴鐜板凡杩涗竴姝ュ帇缂╂垚鍙屽眰鍐呭鍨嬪叆鍙ｏ細绗竴灞傚彧淇濈暀 `For You`銆佹爣棰樺拰 `鎹竴鎵筦锛岀浜屽眰鎶婃睜瀛愮姸鎬佹敹鎴愪笁鏋氱揣鍑?chips锛岃绗竴寮犳帹鑽愬崱鏇存棭杩涘叆棣栧睆
- 鎺ㄨ崘 tab 鐜板湪杩樹細鍦ㄥご閮ㄤ笅鏂瑰睍绀虹嫭绔嬬殑鈥滄儕鍠滄帹鑽愨€濋灞忓崱浣嶏細popup 鍚姩鏃朵細涓诲姩璇诲彇 `/api/delight/pending`锛宺untime stream 鏀跺埌鏂扮殑 `delight.candidate` 涔熶細绔嬪埢鍒锋柊杩欏紶鍗?
- 鎺ㄨ崘 tab 浼氬睍绀哄€欓€夋睜鎽樿锛?
  - `褰撳墠鍙崲`
  - `鏈€杩戣ˉ杩沗
  - `鐜板湪鍦ㄥ繖`
  - 涓夋潯鐘舵€佷粛鐒朵繚鐣欙紝浣嗘枃妗堝凡鏀剁煭鎴愭洿閫傚悎 chips 鐨勫舰寮忥紝渚嬪 `杩樻湁 151 鏉″彲鎹?/ 鍒氳ˉ杩?6 鏉?/ 杩欎細鍎垮厛涓嶈ˉ璐
  - refresh 杩樺湪璺戞椂锛岀姸鎬?chip 浼氫紭鍏堟樉绀?`姝ｅ湪琛ヨ揣`锛屼笉鍐嶅厛钀芥垚 `杩欒疆杩樻病琛ヨ繘`
  - 鐐瑰嚮 `鎹竴鎵筦 鏃讹紝杩涜涓殑鏂囨浼氱洿鎺ヨ繘鍏モ€滅幇鍦ㄥ湪蹇欌€?chip锛岃€屼笉鏄啀棰濆鎸ゅ嚭涓€鏉＄嫭绔嬬姸鎬佽
- 鎺ㄨ崘鍗＄墖鐜板凡杩涗竴姝ユ敼鎴愭洿鍋忕紪杈戝紡鐨勫唴瀹规祦锛氬皝闈€佹爣棰樸€佹帹鑽愮悊鐢卞拰鎿嶄綔鍖虹殑灞傜骇琚噸鏂版媺寮€锛屽ご閮ㄤ俊鎭笉浼氬啀鍜岄寮犲唴瀹瑰崱鎶㈣瑙変富瑙?
- 鎯婂枩鎺ㄨ崘鍗′細鐩存帴灞曠ず灏侀潰銆乭ook銆佹爣棰樺拰鎯婂枩鐞嗙敱锛屽苟鎻愪緵 `鐪嬬湅 / 涓嶆劅鍏磋叮 / 鑱婁竴鑱?/ 绋嶅悗鐪媊 鍥涗釜鍔ㄤ綔
- `鐪嬬湅` 浼氭墦寮€瀵瑰簲鍐呭骞舵妸杩欐鐐瑰嚮淇濈暀鎴愮ǔ瀹氱殑鏈湴宸插鐞嗘€侊紱`鑱婁竴鑱奰 浼氬湪鍗″唴鐩存帴鍙戦€佷竴鏉″甫涓婁笅鏂囩殑鑱婂ぉ娑堟伅锛屼笉鍐嶅己鍒舵妸鐢ㄦ埛鍒囧幓鑱婂ぉ tab
- 鐢诲儚 tab锛氳皟鐢?`/api/profile-summary` 灞曠ず杞婚噺浜烘牸鐢诲儚銆佹牳蹇冪壒璐ㄣ€佹繁灞傞渶姹傘€佹洿瀹屾暣鐨勮繎鏈熷叴瓒ｅ叧閿瘝锛屼互鍙婂崟鐙殑鈥滄渶杩戞槑鏄句細閬垮紑鈥濆垎缁?
- 鐢诲儚 tab 鐜板湪杩樹細鍗曠嫭灞曠ず `cognitive_style / motivational_drivers / current_phase` 涓夊眰璁ょ煡鎽樿锛岃鈥滆繖浼氬効鐨勪綘鈥濇洿鍍忓鐢ㄦ埛鐨勭悊瑙ｏ紝鑰屼笉鏄叴瓒ｆ爣绛炬鼎鑹?
- 鐢诲儚 tab 浼氶澶栧睍绀衡€滈樋B 鏈€杩戞柊璁颁綇浜嗕粈涔堚€濓紝璁╃敤鎴疯兘鐪嬪埌鏈€杩戝嚑娆￠珮缃俊搴﹁鐭ュ彉鍖?
- 杩欏潡宸茬粡浠庡崟琛屽垪琛ㄥ崌绾т负鍙睍寮€璁ょ煡鍗＄墖锛氶粯璁ゅ彧鐪嬩竴鍙ユ€荤粨锛屽睍寮€鍚庡彲鐪嬧€滆繖瀵圭敾鍍忕殑褰卞搷 / 涓轰粈涔堣繖涔堝垽鏂?/ 杩欐渚濇嵁鈥?
- 璇勮绫昏鐭ュ崱鐗囦細甯︿笂瀵瑰簲鍐呭鏍囬锛屼緥濡傗€滈樋B 鍒氳涓嬩簡浣犲銆婃煇鏉¤棰戙€嬬殑璇勮鈥濓紝涓嶅啀缂哄皯涓婁笅鏂?
- 榛樿鎬佺幇鍦ㄥ浐瀹氭樉绀猴細
  - 缁撹
  - `鏉ヨ嚜锛氥€婃煇鏉″唴瀹广€媊 / `鏉ヨ嚜鏈€杩戣繖杞亰澶╋細鈥 / `鍩轰簬鏈€杩戜富棰橈細鈥 / `鍩轰簬鏈€杩戝嚑鏉＄浉鍏冲唴瀹筦
  - 浠ュ強 `灞曞紑 / 鏀惰捣 / 浠呯粨璁篳 杩欑被鏄惧紡鐘舵€佹彁绀猴紝涓嶅啀璁╃敤鎴风寽鑳戒笉鑳界偣寮€
- `/api/profile-summary` 鐜板凡鏀寔 `limit / cursor` 鍒嗛〉鍙傛暟锛屽苟杩斿洖 `has_more_cognition_updates / next_cognition_cursor`
- popup 棣栧睆鍏堝睍绀?3 鏉¤鐭ュ崱鐗囷紱婊氬姩鍒扮敾鍍忓垪琛ㄥ簳閮ㄦ椂浼氳嚜鍔ㄧ画椤碉紝搴曢儴涔熶繚鐣欌€滃姞杞芥洿澶?/ 閲嶈瘯鍔犺浇鈥濇寜閽綔涓哄厹搴?
- 鎺ㄨ崘閲屾彁浜?`dislike` 鎴?`璇磋鍘熷洜` 鍚庯紝杩欏潡浼氬嵆鏃跺埛鏂帮紝涓嶅啀蹇呴』绛夊埌鍙嶉鎵瑰鐞嗛槇鍊兼弧瓒?
- 鑱婂ぉ鎴栨帹鑽愬弽棣堟垚鍔熷悗锛屽鏋?side panel 宸茬粡鐪嬭繃鐢诲儚鎽樿锛宲opup 浼氬己鍒堕噸鎷?`/api/profile-summary`锛岃鈥滈樋B 鏈€杩戞柊璁颁綇浜嗕粈涔堚€濆敖蹇悓姝ュ埌褰撳墠瑙嗗浘
- 鑱婂ぉ tab锛氳皟鐢?`/api/chat`锛屽湪 side panel 鍐呭拰鈥滈樋B鈥濊繘琛岃交閲忓杞璇濓紱瀵硅瘽浼氳褰曚负 `dialogue` 浜嬩欢锛屽苟鍦ㄩ珮缃俊搴﹂噸澶嶅嚭鐜版椂鍙備笌鍚庣画鐢诲儚鏇存柊
- 鎺ㄨ崘銆佺敾鍍忓拰鑱婂ぉ鏂囨鍏变韩鍚庣鐨?`ToneProfile`锛屽熀纭€椋庢牸鏄€滆€丅鍙嬧€濓紝浣嗕細鏍规嵁鐢诲儚鍜岃繎鏈熷弽棣堝湪淇℃伅瀵嗗害銆佹俯搴﹀拰姊楁劅涓婂姩鎬佽皟鏁?
- 鎺ㄨ崘銆佺敾鍍忋€佽亰澶╀笁涓?tab 宸茬粺涓€涓哄悓涓€濂楁祬鑹插崱鐗囪瑷€锛屾帹鑽愬唴瀹硅鎻愬崌涓轰晶杈规爮棣栧睆瑙嗚閲嶅績

### 鏋勫缓閾捐矾

- 杩愯鏃惰剼鏈笉鍐嶇洿鎺ユ妸 `tsc` 鐨?ESM 浜х墿浜ょ粰 Chrome
- `scripts/build.mjs` 浣跨敤 `esbuild` 灏?`collector.ts` 鍜?`service-worker.ts` bundle 涓哄彲鐩存帴鍔犺浇鐨勫崟鏂囦欢
- `tsc --emitDeclarationOnly` 缁х画璐熻矗绫诲瀷澹版槑浜х墿
- 鏂板鏋勫缓鍥炲綊娴嬭瘯锛岀‘淇?content script 涓嶄細鍐嶆浜у嚭娴忚鍣ㄦ棤娉曟墽琛岀殑 `import` 璇彞

## 鏈湴寮€鍙?

鍦?`extension/` 鐩綍涓嬶細

```bash
npm install
npm test
npm run typecheck
npm run build
```

`npm test` 鐜板湪浼氳鐩栵細

- 椤甸潰璇嗗埆 / BV 鎻愬彇 / 鍔ㄤ綔璇嗗埆
- 缂撳啿鍘婚噸涓庡己淇″彿 flush
- B 绔?/ 鎶栭煶 Cookie 鑷姩鍚屾鐨勯噸璇曢椆閽熷拰骞傜瓑鐩戝惉鍣?
- manifest 鍥炬爣璧勬簮瀛樺湪鎬?
- popup 璁剧疆椤靛瓧娈典笌 `/api/config` schema 鐨勫熀纭€瀵归綈
- `dist/` 杩愯鏃惰剼鏈彲琚?Chrome 鐩存帴鍔犺浇

## Release 鍒嗗彂

鎻掍欢鐜板湪璧扮嫭绔?release 閫氶亾锛?

- 鍙戝竷 tag锛歚extension-vX.Y.Z`
- Release 璧勪骇锛歚openbiliclaw-extension-vX.Y.Z.zip`
- 涓嬭浇鍏ュ彛锛欸itHub Releases 椤甸潰涓煡鎵炬渶鏂扮殑 `extension-v*` release

鍚庣妗岄潰鍖呬笉鍐嶅拰鎻掍欢鍏辩敤鍚屼竴涓?release 璇箟锛涘悗绔敼鐢?`backend-v*` 閫氶亾鍗曠嫭鍙戝竷銆?

## 鎵嬪姩鑱旇皟

1. 鍦ㄩ」鐩牴鐩綍鍚姩鍚庣锛?

```bash
openbiliclaw start
```

2. 鍦?`extension/` 鐩綍鏋勫缓鎻掍欢锛?

```bash
npm run build
```

3. 鍦?Chrome 鐨勬墿灞曠鐞嗛〉鍔犺浇 `extension/` 鐩綍
4. 鎵撳紑 B 绔欓椤点€佹悳绱㈤〉銆佽棰戦〉锛屾墽琛岀偣鍑汇€佹悳绱€佹挱鏀俱€佹殏鍋溿€佹粴鍔ㄧ瓑琛屼负
5. 瑙傚療鍚庣 `/api/events` 鍐欏叆鏁堟灉锛屾垨鐩存帴鏌ョ湅 SQLite `events` 琛?

鐩墠宸查€氳繃鐪熷疄鑱旇皟纭锛?

- `collector` 鑳藉湪棣栭〉鍜屾悳绱㈤〉鎴愬姛娉ㄥ叆
- `service worker` 鑳藉惎鍔ㄥ苟鎵归噺涓婃姤
- `/api/events` 鑳芥帴鏀舵彃浠堕妫€璇锋眰涓庝簨浠舵壒娆?
- SQLite `events` 琛ㄥ凡鑳藉啓鍏?`snapshot` 浜嬩欢
- popup 鑳芥牴鎹?`/api/health` 涓?`/api/recommendations` 鍒囨崲鍦ㄧ嚎銆佺┖鐘舵€佷笌鎺ㄨ崘鍒楄〃灞曠ず
- side panel 椤甸潰鍙嶉鎸夐挳宸茶兘缁?`/api/feedback` 鍐欏洖鎺ㄨ崘琛ㄥ拰浜嬩欢灞?
- side panel 鐜板凡鏀寔 `鎺ㄨ崘 / 鎴戠殑鐢诲儚 / 鍜岄樋B鑱婅亰` 涓変釜 tab锛屽苟宸叉帴閫氱敾鍍忔憳瑕佷笌鑱婂ぉ鎺ュ彛
- side panel 鑱婂ぉ淇″彿宸茶繘鍏ュ悗绔涔犻摼锛屼絾浠嶉噰鐢ㄥ彈鎺хН绱紝涓嶄細鍥犱负鍗曡疆鑱婂ぉ绔嬪嵆閲嶅啓鐢诲儚
- side panel 鎺ㄨ崘銆佺敾鍍忓拰鑱婂ぉ鍥炲鐜板湪鍏辩敤鈥滆€丅鍙嬧€濆姩鎬佽姘旓紝涓嶅啀鍥哄畾鎴愪竴濂楁満姊版ā鏉?
- side panel 鑳芥牴鎹?`/api/runtime-status` 鍒囨崲鈥滃厛鍒濆鍖?/ 姝ｅ湪琛ヨ揣 / 鎺ㄨ崘鍙敤鈥濅笁鎬?
- side panel 鐜板湪杩樿兘閫氳繃 websocket 鐪嬪埌鈥滃紑濮嬭ˉ鍊欓€?/ 褰撳墠璺戝埌鍝釜绛栫暐 / 鍒氳ˉ杩涘嚑鏉℃柊鐨?/ 杩欐壒鍏堟崲濂戒簡鈥濊繖绫诲疄鏃惰繍琛岀姸鎬?
- service worker 鐜板湪浼氬湪楂樼疆淇℃帹鑽愬嚭鐜版椂瑙﹀彂娴忚鍣ㄩ€氱煡锛屽苟閫氳繃鍚庣鍥炲啓 `notification_sent`
- service worker 鐜板湪涔熶細鎷夊彇璁ょ煡鍙樺寲閫氱煡锛涘鏋滄渶杩戠郴缁熷鐢ㄦ埛褰㈡垚浜嗘柊鐨勯珮缃俊鐞嗚В锛屼細鍙戜竴鏉℃洿鍏嬪埗鐨勨€滈樋B 鍙堝浣犲鐪嬫竻浜嗕竴鐐光€濇彁閱?
- side panel 鏂扮増浜壊甯冨眬宸查€氳繃鏈湴闈欐€侀〉闈㈠揩鐓ф鏌ワ紝鎺ㄨ崘 / 鐢诲儚 / 鑱婂ぉ涓変釜瑙嗗浘缁撴瀯娓叉煋姝ｅ父
- 灏忕孩涔?`bootstrap_profile` 浠诲姟宸查€氳繃鍗曞厓娴嬭瘯瑕嗙洊锛歞ispatcher 璇嗗埆浠诲姟绫诲瀷骞惰兘璺熼殢 profile URL 浜屾鎵ц锛宔xecutor 鍙粠 mock `__INITIAL_STATE__` 鐨?saved / liked / history 鍒嗙粍鎻愬彇 scoped notes锛屽苟鑳界敤 `partial` 鎵规鍦ㄦ粴鍔ㄤ换鍔′腑鎸佺画鍥炰紶鏂板缁撴灉
- 鎶栭煶 `bootstrap_profile` 浠诲姟宸查€氳繃鎵╁睍鍜屽悗绔洖褰掕鐩栵細MAIN-world API harvester 鍙垎椤垫媺鍙栨敹钘?/ 鐐硅禐锛宒ispatcher 褰㈡€佺殑 partial 鎵规浼氬湪鍚庣鍚堝苟銆佸幓閲嶅苟杞垚缁熶竴 memory 浜嬩欢
- 鎶栭煶 `search` / `hot` / `feed` 浠诲姟宸查€氳繃鎵╁睍鍥炲綊瑕嗙洊锛歁AIN-world search bridge 浼氳皟鐢ㄩ〉闈?acrawler 绛惧悕鎼滅储 URL锛宧ot-related bridge 浼氱鍚?related URL锛宖eed bridge 浼氱鍚?`/aweme/v1/web/tab/feed/`锛泂earch 鍗曞叧閿瘝 timeout 鑷冲皯 120 绉掞紱`search-douyin -k 鐚?--max-items-per-keyword 10 -w 180` 鍙媺鍒?10 鏉?`dy_search` 鍊欓€夛紝`discover-douyin --source search --keyword 鐚?--limit 5 --no-cache --no-evaluate` 鍙媺鍒?5 鏉?`dy-plugin-search` 鍊欓€?

## 褰撳墠闄愬埗

- 琛屼负鎸夐挳璇嗗埆鍩轰簬 DOM 鏂囨湰銆佺被鍚嶅拰 `aria-label`锛屼笉鏄湇鍔＄鏈€缁堢粨鏋滅‘璁?
- 閲囬泦鑼冨洿浼樺厛瑕嗙洊棣栭〉銆佹悳绱㈤〉鍜岃棰戦〉锛屾湭鎵胯鎵€鏈?B 绔欐ā鏉垮畬鍏ㄤ竴鑷?
- side panel chat 浼氳瘽鍙繚鐣欏湪褰撳墠鎵撳紑鍛ㄦ湡鍐咃紝涓嶅仛鏈湴鎸佷箙鍖?
- inline comment 閲囩敤杞婚噺杈撳叆锛屼笉鏀寔澶嶆潅鍙嶉鍘嗗彶娴忚
- side panel 瑙嗚楠岃瘉褰撳墠浠ラ潤鎬佸揩鐓?+ extension 鏋勫缓鍥炲綊涓轰富锛屼粛寤鸿缁撳悎鐪熷疄鍚庣鍋氫竴娆℃墜鍔ㄨ仈璋?
- 娴忚鍣ㄩ€氱煡褰撳墠鍙帹閫佷竴鏉℃渶楂樺垎鏈€氱煡鍐呭锛屼笉鍋氶€氱煡涓績鎴栧鏉￠槦鍒?
- 鎯婂枩鎺ㄨ崘褰撳墠鍙淮鎶や竴涓灞忓€欓€変綅锛屼笉鍋氬鏉¤疆鎾垨鍘嗗彶鏀朵欢绠憋紱`绋嶅悗鐪媊 鍙湪褰撳墠 popup 浼氳瘽閲岄殣钘忥紝涓嶅仛闀挎湡鎸佷箙鍖?
- 璁ょ煡鍙樺寲閫氱煡褰撳墠鍙彁绀烘渶閲嶈鐨勪竴鏉★紝涓嶆敮鎸佺敤鎴风‘璁?鍙嶉┏锛屼篃涓嶄細鍦ㄦ彃浠堕噷缁存姢瀹屾暣閫氱煡鍘嗗彶
- 鑱氬悎鍨嬭鐭ュ崱鐗囧鏋滃悗绔殏鏃舵嬁涓嶅埌鍙俊鏍囬锛屼細淇濆畧鏄剧ず涓衡€滃熀浜庢渶杩戝嚑鏉＄浉鍏冲唴瀹光€濓紝涓嶄細浼€犲叿浣撹棰戝悕
- 鈥滄崲涓€鎵光€濅緷璧?discovery pool 褰撳墠宸叉湁鍊欓€夛紱濡傛灉鍊欓€夋睜鏈韩渚涚粰涓嶈冻锛屼粛鍙兘鎻愮ず鈥滄睜瀛愰噷杩欎細鍎胯繕娌″埛鍑烘柊鐨勨€?
- 鑷姩缁〉鍚屾牱渚濊禆 discovery pool 褰撳墠宸叉湁鍊欓€夛紱濡傛灉姹犲瓙鏆傛椂涓嶅锛岀画椤电粨鏋滃彲鑳藉皯浜?10 鏉★紝鐢氳嚦鐩存帴鎻愮ず鍏堢瓑鍚庡彴鍐嶈ˉ涓€鐐规柊鐨?
- 姹犲瓙鎽樿閲岀殑鈥滄渶杩戝湪琛モ€濈洰鍓嶅熀浜庣瓥鐣ュ拰鍊欓€夋爣绛惧仛杞婚噺鑱氬悎锛屽睘浜庢柟鍚戞彁绀猴紝涓嶆槸绮剧‘ taxonomy
- 灏忕孩涔﹀垵濮嬪寲瀵煎叆鏄?best-effort锛氬悗绔笉鐧诲綍銆佷笉鐖彇灏忕孩涔︼紝鍙瓑寰呮彃浠跺湪鐢ㄦ埛宸茬櫥褰曟祻瑙堝櫒閲岃В鏋愰〉闈紱鏀惰棌/鐐硅禐/娴忚璁板綍浠讳竴 scope 涓嶆毚闇叉椂锛屼細璺宠繃璇?scope銆傛櫘閫氭帹鑽愭祦涓嶄細琚爣鎴?`xhs_history`锛涘彈鎺ф粴鍔ㄥ彧鍦ㄤ换鍔℃樉寮忚缃?`max_scroll_rounds` 鏃跺惎鐢?
