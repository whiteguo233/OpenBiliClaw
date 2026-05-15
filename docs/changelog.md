# 鍙樻洿鏃ュ織

> 鎸夐噷绋嬬璁板綍鍚勯樁娈典氦浠樺唴瀹广€傛瘡娆″垎鏀悎鍥?main 鏃惰拷鍔犳潯鐩€?

---

## v0.3.69: 鎶栭煶棣栭〉鎺ㄨ崘娴?discovery锛?026-05-12锛?

- 插件设置页新增“后端端口”配置（默认 `8420`，范围 `1-65535`），仅影响插件连接本地后端的 API 与 runtime-stream 地址；保存后即时重连并刷新，不写入后端 `config.toml`。
- 鎻掍欢璁剧疆椤典笌鍚庣閰嶇疆 schema 瀵归綈锛氭柊澧?DeepSeek reasoning銆丱penRouter headers銆乸er-module LLM override銆丅 绔?/ sources 娴忚鍣ㄩ厤缃€佸皬绾功 / 鎶栭煶棰勭畻銆佹暟鎹洰褰?/ SQLite銆乻cheduler 楂樼骇椤广€佸€欓€夋睜骞冲彴閰嶆瘮銆佽嚜鍔ㄦ洿鏂板拰 logging 娓呯悊鍙傛暟锛屽苟閫氳繃 `/api/config` 瀹屾暣璇诲啓銆?
- `/api/config` 鐜板湪鏆撮湶骞朵繚瀛?`sources.*`銆乻cheduler speculation / `pool_source_shares` / auto-update interval銆乴ogging rotation / unmanaged cleanup 鍜?`llm.deepseek.reasoning_effort`锛沗save_config()` 鍚屾涓茶鍖栬繖浜涢殣钘忛珮绾у瓧娈碉紝閬垮厤鎻掍欢淇濆瓨甯哥敤椤规椂鎶婂畠浠涪鍥為粯璁ゅ€笺€?
- 閰嶇疆榛樿鍊兼枃妗ｅ拰绀轰緥琛ラ綈锛歚discovery_cron` 缁熶竴涓?`"0 */8 * * *"`锛宍auto_update_enabled` 缁熶竴涓轰繚瀹堥粯璁?`false`锛岄厤缃弬鑰冪Щ闄ゅ凡搴熷純鐨?`[sources.xiaohongshu].sidecar_url`锛屽苟琛ヤ笂 YouTube / XHS / Douyin init 鐜鍙橀噺璇存槑銆?
- YouTube 宸叉帴鍏ラ娆?`init` 鐨勫婧愮敾鍍忛摼璺細浜や簰寮?`--yes-youtube` / `--no-youtube` 鍐崇瓥銆乣OPENBILICLAW_NO_YOUTUBE=1` 鐜璺宠繃銆佹祻瑙堝櫒鎵╁睍 `yt_tasks` 涓茶鎷夊彇瑙傜湅鍘嗗彶 / 璁㈤槄 / 鐐硅禐锛屽苟鎶婁簨浠堕€佸叆 `analyze_events()` 涓?`build_initial_profile()`銆?
- YouTube discovery 鐪熷疄 smoke 琛ュ己骞朵慨澶嶉泦鎴愰棶棰橈細`yt_search` 鐜板湪姝ｇ‘瑙ｆ瀽鐪熷疄 `LLMService` 杩斿洖鐨?`LLMResponse.content` 浣滀负鎼滅储鍏抽敭璇嶏紝`yt_channel` 鍙粠鐪熷疄 YouTube follow 浜嬩欢閲岀殑棰戦亾 URL 鎷夊彇鏈€鏂拌棰戝苟鍦?`scrapetube` 澶辨晥鏃朵娇鐢?`yt-dlp` fallback锛宍ContentDiscoveryEngine` 鏀逛负鎸夎法婧?`source_platform + content_id` 鍘婚噸 / 缂撳瓨锛岄伩鍏嶅涓?YouTube 鍊欓€夊洜绌?`bvid` 琚悎骞躲€?
- `yt_trending` 澧炲姞鐪熷疄缃戠粶 fallback锛氬綋 YouTube 褰撳墠 `FEtrending` InnerTube browseId 杩斿洖 400 鏃讹紝鏀逛负鎶撳彇鍏紑 topic 椤碉紙gaming / sports / news / podcasts / live锛夌殑 `ytInitialData` 瑙嗛骞剁户缁繘鍏?LLM 鎵撳垎锛岀湡瀹?smoke 宸蹭粠 `fetched=0` 鎭㈠涓哄彲浜у嚭鍊欓€夈€?
- 鏂板 YouTube 鍗曟簮宸ュ叿锛歚openbiliclaw fetch-youtube` 鐢ㄤ簬 smoke 娴忚鍣ㄦ墿灞曚换鍔℃ˉ锛宍openbiliclaw import-youtube <path>` 鏀寔 Google Takeout `.zip` 鎴栫洰褰曞鍏ヨ鐪嬪巻鍙?/ 璁㈤槄 / 鐐硅禐銆?
- 鏂板 GitHub Pages 椤圭洰涓婚〉锛歚docs/index.html` 浣滀负 `/docs` 鍙戝竷鍏ュ彛锛岄灞忕獊鍑虹函鏈湴 / 绉佹湁 / 寮€婧?/ 鑷繘鍖栬法骞冲彴鍐呭鍙戠幇 Agent 瀹氫綅锛屽苟鎻愪緵涓€鍙ヨ瘽瀹夎鎻愮ず銆丆hrome 鎻掍欢涓嬭浇銆丟itHub 婧愮爜銆佷骇鍝侀棴鐜拰鎺ㄨ崘 / 浠峰€肩敾鍍?/ 璁ょ煡椋庢牸 / 鑱婂ぉ鏍″噯鎴浘锛涘師鏂囨。瀵艰埅淇濈暀鍦?`docs/index.md`銆?
- GitHub Pages 椤圭洰涓婚〉鏂板涓嫳鏂囧弻璇垏鎹細榛樿璺熼殢娴忚鍣ㄨ瑷€锛岀敤鎴锋墜鍔ㄩ€夋嫨鍚庡啓鍏?`localStorage`锛屽畨瑁呮彁绀恒€佸鑸€丆TA銆佹埅鍥捐鏄庛€佹灦鏋勮鏄庡拰澶嶅埗鎸夐挳鐘舵€佸潎鍚屾鍒囨崲銆?
- Chrome 鎻掍欢鐗堟湰鎻愬崌鍒?v0.3.20 骞跺噯澶囧彂甯冿細鎵撳寘杩欏嚑澶╁凡鍚堝叆鐨勬姈闊充换鍔℃ˉ銆丏ouyin search / hot / feed 鎻掍欢绛惧悕閾捐矾銆佹姈闊?Cookie 鍚屾鍜屽皬绾功 / 鎶栭煶 dispatcher 浜掓枼锛宮anifest 鎻忚堪鍚屾鏀逛负璺ㄥ钩鍙板唴瀹瑰彂鐜?Agent銆?
- README / README_EN 椤堕儴鏂板椤圭洰涓婚〉鍏ュ彛锛岀洿鎺ラ摼鎺ュ埌 `https://whiteguo233.github.io/OpenBiliClaw/`銆?
- README / README_EN 蹇€熷紑濮嬮噸鎺掞細鏅€氱敤鎴疯矾寰勬敹鏁涗负鈥滃畨瑁呮彃浠?鈫?澶嶅埗涓€鍙ヨ瘽缁?AI 鍔╂墜閮ㄧ讲鍚庣 鈫?鍦ㄥ悓涓€娴忚鍣ㄧ櫥褰曞唴瀹瑰钩鍙扳€濓紝鑴氭湰銆丏ocker銆佸婧愮櫥褰曡鏄庛€佹湰鍦?embedding 鍜?discovery 璋冭瘯鍛戒护缁熶竴绉诲叆楂樼骇鎶樺彔椤癸紝鍑忓皯棣栨瀹夎鏃剁殑骞叉壈淇℃伅銆?
- 淇 CDP 鏂囨。瀹氫綅锛氬皬绾功鍜屾姈闊冲綋鍓嶇ǔ瀹氶摼璺兘璧?Chrome 鎻掍欢浠诲姟锛屼笉鍐嶅湪 README銆丏ocker 閮ㄧ讲鏂囨。鍜岄厤缃弬鑰冮噷鎺ㄨ崘鐢ㄦ埛涓鸿繖涓や釜婧愰澶栧惎鍔?CDP 璋冭瘯 Chrome锛沗[sources.browser].cdp_url` 淇濈暀缁欓€氱敤 Web / 鑷畾涔夌綉椤垫簮銆?
- 鏂板鎶栭煶棣栭〉鎺ㄨ崘娴?discovery锛歚discover-douyin --source feed` 浼氬叆闃?`dy_tasks(type="feed")`锛屾墿灞曞湪宸茬櫥褰曟姈闊抽椤甸€氳繃 MAIN-world `byted_acrawler.frontierSign()` 绛惧悕 `/aweme/v1/web/tab/feed/`锛屽€欓€変互 `dy-plugin-feed` 杩涘叆 discovery銆?
- 鎶栭煶鍏紑 discovery 瀛愭潵婧愯皟鏁翠负 `search` / `hot` / `feed`锛沗creator` 涓嶅啀浣滀负 CLI 鍙€夋笭閬擄紝閬垮厤鎶婁綔鑰呬富椤垫椂闂寸嚎褰撲綔榛樿鍐呭鍙戠幇鏉ユ簮銆?
- `[sources.douyin]` 鏂板 `daily_feed_budget`锛岄檺鍒舵瘡鏃?`dy_tasks(type="feed")` 鍏ラ槦娆℃暟锛沗daily_search_budget` / `daily_hot_budget` 缁х画鍒嗗埆绾︽潫 search / hot銆?
- 鏂板 `[scheduler.pool_source_shares]` 骞冲彴绾у€欓€夋睜閰嶆瘮閰嶇疆锛岄粯璁?B 绔?/ 灏忕孩涔?/ 鎶栭煶 = 8 / 1 / 1锛沗pool_target_count=600` 鏃剁洰鏍囦负 `bilibili=480`銆乣xiaohongshu=60`銆乣douyin=60`銆?
- runtime refresh 鏀逛负鎸夊钩鍙版棌缁熻鍜屼慨鍓€欓€夋睜锛欱 绔欏洓涓瓥鐣ョ粺涓€璁″叆 `bilibili`锛屽皬绾功 `xhs-extension-*` 璁″叆 `xiaohongshu`锛屾姈闊?`dy-plugin-*` 璁″叆 `douyin`锛涘皬骞冲彴浣庝簬閰嶉鏃朵細淇濇姢 / 澶嶆椿鍏跺€欓€夛紝骞冲彴鏃忚秴杩囬厤棰濇椂鍗充娇鎬绘睜瀛愭湭婊′篃浼氬厛鍘嬪洖閰嶉鍐呫€?
- discovery LLM 璇勪及澧炲姞姹犲瓙瀹归噺鎰熺煡锛歳untime 浼氭寜 B 绔欏钩鍙扮己鍙ｈ€屼笉鏄€绘睜瀛愮己鍙ｅ喅瀹氭湰杞?limit锛沗search` / `trending` / `related_chain` / `explore` / `douyin_direct` 鍦ㄩ€?LLM 鍓嶄細鎶婂€欓€夌獥鍙ｆ敹缂╁埌 `max(12, limit*4)`銆佷笂闄?90锛岄伩鍏嶅彧缂哄皯閲忓€欓€夋椂浠嶈瘎浼板嚑鍗佹潯骞堕殢鍚庣珛鍒?suppressed銆?
- discovery batch 璇勪及瑙ｆ瀽琛ュ己锛氬吋瀹?provider 鍥炴樉杈撳叆 JSON 鍚庡啀杈撳嚭缁撴灉銆丮arkdown fenced JSON锛屼互鍙婁竴琛屼竴涓?JSON object 鐨?NDJSON 缁撴灉锛岄伩鍏?batch 瑙ｆ瀽澶辫触鍚庨€€鍥?N 娆″崟鏉?LLM 璇勪及銆?
- 灏忕孩涔?/ 鎶栭煶 bootstrap task-result 鐨勬柊澧炰簨浠剁幇鍦ㄤ笉鍙惤 memory锛歱rofile 宸插垵濮嬪寲鍚庝細杞垚 `ProfileSignal` 杩涘叆 `ProfileUpdatePipeline`锛岃鍚庣画鎷夊埌鐨勬敹钘?/ 鐐硅禐 / 鍏虫敞浜嬩欢鍙備笌澧為噺鐢诲儚鏇存柊锛涢娆?init 浠嶇敱 `analyze_events()` + `build_initial_profile()` 缁熶竴澶勭悊锛岄伩鍏嶉噸澶嶅涔犮€?
- 鍒濆鍖栧亸濂藉垎鏋愮殑骞跺彂鍒嗙墖澧炲姞瀹归敊锛氬綋鏌愪釜鍒嗙墖琚?LLM 椋庢帶鎷掔粷鎴栬繑鍥為潪 JSON 鏃讹紝浼氶€掑綊鎷嗗皬瀹氫綅闂浜嬩欢锛屾渶缁堝彧璺宠繃浠嶅け璐ョ殑鍗曟潯浜嬩欢锛岄伩鍏嶄竴涓爣棰樺鑷存暣娆?`init` 涓柇锛沺rovider / 缃戠粶閿欒浠嶄細姝ｅ父澶辫触骞舵毚闇层€?
- 鍒濆鍖栫敾鍍忕敓鎴愬鍔?compact retry锛氶杞?`history_summary` 瑙﹀彂妯″瀷椋庢帶鎴栧潖 JSON 鏃讹紝浼氱Щ闄ゅ師濮嬫爣棰?/ context 鍚庣敤缁撴瀯鍖栧亸濂姐€佹潵婧愬垎甯冦€佽瀵熷拰娲炲療閲嶈瘯涓€娆★紝閬垮厤鐪熷疄澶氭簮鍒濆鍖栧湪鏈€鍚庣敾鍍忛樁娈佃鍗曚釜楂橀闄╂爣棰樹腑鏂€?
- `ProfileBuilder` 鐨勭敾鍍忛暱搴︽牎楠屼笂闄愪粠 320 鏀惧鍒?500 瀛楋細prompt 浠嶈姹?150-260 瀛楋紝浣嗙湡瀹炴ā鍨嬪伓灏斾細杩斿洖 330 瀛楀乏鍙崇殑鏈夋晥鐢诲儚锛屼笉鍐嶅洜涓鸿交寰秴闀胯瀹屾暣 init 澶辫触銆?
- `ProfileBuilder` 瀵圭敾鍍忚緟鍔╁瓧娈垫洿瀹归敊锛歚core_traits` / `cognitive_style` / `motivational_drivers` / `values` / `deep_needs` / `life_stage` / `current_phase` 缂哄け鎴栧垪琛ㄦ牸寮忚交寰笉绗︽椂浼氫繚瀹堣ˉ绌哄€煎苟璁板綍 warning锛屼笉鍐嶅洜涓哄崟涓緟鍔╁瓧娈垫紡鍚愪腑鏂娆″垵濮嬪寲銆?
- `openbiliclaw init --yes-douyin` 瀹屾垚鎽樿鐜板湪浼氭妸鎶栭煶淇″彿涔熷啓杩涒€滄湰娆＄敾鍍忕患鍚堜簡...鈥濇彁绀猴紱鍙惎鐢ㄦ姈闊虫垨鍚屾椂鍚敤灏忕孩涔?/ 鎶栭煶鏃讹紝涓嶅啀閿欒鏄剧ず鈥滀袱涓钩鍙扳€濅笖婕忔帀鎶栭煶銆?
- 涓€鍙ヨ瘽瀹夎鐨?auto-init 鐜板湪浼氬湪鍘熸牱杈撳嚭 `openbiliclaw init` 鏃ュ織鐨勫悓鏃讹紝棰濆鍙?`BOOTSTRAP_STATUS status=progress message=init_progress` 缁撴瀯鍖栦簨浠讹紱AI agent 鍙疄鏃舵彁绀?1/4銆?/4銆?/4銆?/4 鍜岃ˉ璐ч樁娈佃繘搴︼紝涓嶅繀绛夋渶缁?`init_complete`銆?
- 鏂板 runtime `DouyinDiscoveryProducer`锛氬綋鎶栭煶浣庝簬骞冲彴閰嶉涓?`[sources.douyin].enabled=true` 鏃讹紝鍚庡彴閫氳繃 `DouyinDiscoveryService(cache=True)` 澶嶇敤 search / hot / feed 鎻掍欢绛惧悕閾捐矾琛ユ睜銆?
- 淇 B 绔?Cookie 鑷姩鍚屾鍚庣殑鍚庡彴寰幆涓㈠け锛歚/api/bilibili/cookie` 鐑噸杞?runtime 鍚庝細閲嶆柊鍚姩 refresh / account sync / auto update 浠诲姟锛岄伩鍏嶆墿灞曢娆″悓姝?Cookie 鍚庢妸灏忕孩涔︿笌鎶栭煶 producer 鍋滀綇锛屽鑷存姈闊抽厤棰濋暱鏈熶负 0锛涢噸澶嶅悓姝ョ浉鍚?Cookie 鏃朵繚鎸佸箓绛夛紝涓嶅啀鍙嶅 hot-reload 鎵撴柇鎶栭煶 discovery 绛夊緟銆?
- 鎶栭煶鎻掍欢 discovery 鍏ラ槦鍓嶄細娓呯悊杩囨湡鐨?search / hot / feed pending 浠诲姟锛岄伩鍏嶆棫鐗堟湰閲嶅 hot-reload 鐣欎笅鐨勯檲鏃ч槦鍒楁尅浣忓綋鍓?producer锛屽鑷存柊浠诲姟绛夊埌瓒呮椂鎵嶅洖閫€銆?
- discovery engine 娉ㄥ唽鍚屽悕 strategy 鏃舵敼涓烘浛鎹㈡棫瀹炰緥锛岄伩鍏?runtime `DouyinDiscoveryService(cache=True)` 姣忚疆杩藉姞涓€涓柊鐨?`douyin_direct`锛屽鑷村悗缁竴娆℃姈闊?discovery 鍚屾椂璺戝涓浉鍚?search 浠诲姟銆佸揩閫熻€楀敖 `daily_search_budget`銆?
- B 绔?`SearchStrategy` 鐨勪笓鐢?search client 鐜板湪浼氱户鎵胯繍琛屾椂 B 绔?Cookie锛氱湡瀹?smoke 鍙戠幇鍖垮悕 WBI search 绋冲畾杩斿洖 `data.v_voucher`锛岃€屽悓涓€绛惧悕璇锋眰甯︽湁鏁?Cookie 鍙甯歌繑鍥?`result`锛涗繚鐣欑嫭绔?client 闄嶄綆 session 涓叉壈锛屼絾涓嶅啀涓㈣璇佹€併€?
- 鎶栭煶鎵╁睍 search 浠诲姟鐨勫崟鍏抽敭璇嶈秴鏃剁獥鍙ｄ粠 60 绉掓斁瀹藉埌 180 绉掞紝鍚庣 runtime / CLI 榛樿绛夊緟绐楀彛鍚屾涓?180 绉掞紱鐪熷疄 smoke 鏄剧ず鎼滅储椤靛鑸埌 `DY_SEARCH_EXECUTE` 鍙兘宸叉秷鑰?100s+锛屾棫 120s 浼氬湪 search API bridge 杩斿洖鍓嶅厛瑙﹀彂 `task_timeout`銆?
- runtime 鎶栭煶 producer 姣忚疆鍙彇 1 涓敾鍍忓叧閿瘝鍋?search锛岀劧鍚庣户缁窇 hot / feed锛岄伩鍏嶅悗鍙拌ˉ姹犲湪澶氫釜鎼滅储鍏抽敭璇嶄笂涓茶绛夊緟鎻掍欢瓒呮椂骞舵秷鑰楄繃澶?search budget锛汣LI `discover-douyin` 浠嶅彲鎸夋樉寮忓叧閿瘝璋冭瘯澶?search銆?
- runtime 琛ユ睜杩涗竴姝ユ敹鏁涙棤鏁堟垚鏈細B 绔欏洓绛栫暐鍏变韩鍚屼竴涓钩鍙扮己鍙ｉ绠楀苟閫氳繃 `strategy_limits` 鍒嗘憡鍒板悇绛栫暐锛屾墜鍔?refresh 涔熷鐢ㄥ悓涓€濂楀钩鍙扮己鍙ｈ鍒掞紱灏忕孩涔?producer 浼氭寜灏忕孩涔︾己鍙ｅ噺灏戞湰杞叧閿瘝鏁帮紱鎶栭煶 producer 鍦ㄥ皬缂哄彛鏃朵紭鍏?feed / hot锛屽彧鏈夌己鍙ｈ緝澶ф墠鎭㈠ search锛涘悇绛栫暐閫?LLM 璇勪及鍓嶇殑绐楀彛浠?`max(12, limit*4)` 鏀剁揣鍒?`max(6, limit*2)`銆佷笂闄?90銆?
- 鏂板 pool distribution snapshot 鍩虹妯″瀷锛歚PoolDistributionSnapshot` 姹囨€诲€欓€夋睜鎬婚噺銆佸钩鍙版棌鏁伴噺 / 缂哄彛鍜?topic/style/franchise 楗卞拰鏂瑰悜锛屽苟閫氳繃 `Database.get_pool_distribution_counts()` 澶嶇敤 fresh銆侀潪 dislike銆佹湭鎺ㄨ崘涓斿彲鎵撳紑鐨勫€欓€夌粺璁″彛寰勶紱榛樿楗卞拰闃堝€间负 topic `max(8, pool_target_count // 20)`銆乻tyle `max(12, pool_target_count // 8)`銆乫ranchise 10锛屼笖 `source_deficits` 鏄庣‘淇濇寔涓哄钩鍙?/ 鏉ユ簮缂哄彛淇″彿锛屼笉娣峰叆鍐呭杞淬€?
- runtime refresh 鐜板湪浼氬湪 B 绔?discovery 鍓?fail-soft 鏋勫缓 pool snapshot锛屽苟閫氳繃 `ContentDiscoveryEngine.discover(..., pool_snapshot=...)` 鍏煎杞彂缁欐敮鎸佽鍙傛暟鐨勪富绛栫暐涓?backfill 绛栫暐锛屾棫鐗?strategy 绛惧悕淇濇寔鍙敤銆?
- `SearchStrategy.discover(..., pool_snapshot=...)` 鐜板湪浼氭妸 `PoolDistributionSnapshot.to_prompt_hints()` 娉ㄥ叆鎼滅储 query prompt锛氬宸叉嫢鎸?topic/style/franchise 鍋氳蒋閬胯锛屾樉寮?`undercovered_axes` 鍙舰鎴?`prefer_axes`锛涜繍琛屾椂蹇収鏆備笉鎶婂钩鍙板悕杞垚鍐呭 `prefer_axes`锛屼笖鍧?hint 浼氳涓㈠純鍚庣户缁蛋姝ｅ父 LLM query 鐢熸垚銆?
- discovery engine 浼氬湪鏈€缁堝帇缂╁拰鍏ユ睜鍓嶅簲鐢?pool snapshot 杞噸鎺掞細楗卞拰 topic/style/franchise 杞诲井闄嶆潈锛寀ndercovered axes 杞诲井鍔犳潈锛屽己鐩稿叧鍊欓€変繚鐣欎紭鍏堢骇涓斿師濮?`relevance_score` 涓嶈鏀瑰啓锛涙帹鑽?serving 璺緞淇濇寔浠?`content_cache` 鍙栧凡棰勭敓鎴愬€欓€変笉鍙樸€?
- 鎶栭煶琛ユ睜棰勭畻淇锛歚dy_tasks` 涓洜 daemon 閲嶅惎 / 鎻掍欢鏈強鏃舵秷璐硅€屽け璐ョ殑 `stale_pending` discovery 浠诲姟涓嶅啀璁″叆 search / hot / feed 姣忔棩棰勭畻锛岄伩鍏嶅巻鍙查檲鏃?pending 鍚冨厜褰撳ぉ search 閰嶉銆?
- 鎶栭煶 runtime 澶х己鍙ｈˉ姹犳敼涓轰紭鍏?`search` / `hot`锛屼笉鍐嶆妸浣庝骇鍑虹殑 `feed` 娣疯繘澶ф壒閲忚ˉ姹狅紱`daily_hot_budget` 鍦?runtime 涓細鎸夋湰杞姈闊崇己鍙ｅ姩鎬佹姮楂樺埌鏈€澶?60锛岄粯璁?`5` 浠嶄綔涓哄皬缂哄彛 / 鎵嬪姩璋冭瘯鐨勪繚瀹堝熀绾裤€?
- 鍙傝€冨紑婧愬疄鐜扮‘璁ら椤垫帹鑽愭祦绔偣锛欶2 鏆撮湶 `fetch_post_feed` + `TAB_FEED=/aweme/v1/web/tab/feed/`锛孌ouyin_TikTok_Download_API 涔熻褰曚簡 `TAB_FEED` 鍜?`PostFeed` 鍙傛暟妯″瀷锛涙湰椤圭洰涓嶅紩鍏ョ涓夋柟渚濊禆锛屽彧澶嶇敤绔偣鍜屽弬鏁板舰鎬併€?
- 浼樺寲鎶栭煶 hot discovery 绋冲畾鎬э細hot 鎻掍欢浠诲姟鐜板湪甯︽€荤洰鏍?`max_items`锛岀疮璁¤揪鍒扮洰鏍囧嵆鎻愬墠缁撴潫锛涘悗绔皬鎵归噺 hot 璇锋眰鍙睍寮€灏戦噺 hot seed锛岄伩鍏?`--limit 3` 涓轰簡 3 鏉″€欓€変覆琛屾墦寮€ 3 涓?`/hot/{sentence_id}` 椤甸潰骞舵挒涓?`task_timeout`銆?
- 鏂囨。鍚屾琛ラ綈鎶栭煶浜嬩欢涓?discovery锛歊EADME / README_EN銆佷竴鍙ヨ瘽瀹夎銆乤gent 閮ㄧ讲銆丱penClaw quickstart 鍜?discovery 妯″潡鏂囨。閮芥洿鏂颁负鎶栭煶 search / hot / feed銆乣--yes-douyin` / `--no-douyin`銆乣BOOTSTRAP_STATUS init_progress` 鐨勫綋鍓嶈涓恒€?

---

## v0.3.68: 鎶栭煶鎻掍欢鎼滅储 smoke 璺戦€氾紙2026-05-11锛?

- 鏂板 `openbiliclaw search-douyin` 鐙珛鍛戒护锛欳LI 鍏ラ槦 `dy_tasks(type="search")`锛屾祻瑙堝櫒鎵╁睍鍦ㄥ凡鐧诲綍鎶栭煶浼氳瘽涓墦寮€鎼滅储椤碉紝鍥炰紶 `dy_search` 鍊欓€夛紝渚夸簬鍗曠嫭璋冭瘯鎶栭煶鎼滅储 discovery 鍙洖銆?
- 鎶栭煶鎵╁睍浠诲姟妗ユ柊澧?search 绫诲瀷锛歜ackground dispatcher 鏀寔鍏抽敭璇嶉槦鍒椼€侀€愯瘝鎵ц銆乸artial + final 鍥炲啓锛涘悗绔繚鐣欐悳绱㈢粨鏋滃湪 `dy_tasks.result_json`锛屼笉浼氫紶鎾垚鍒濆鍖栫敾鍍忎簨浠讹紝閬垮厤鎶?discovery 鍊欓€夎褰撶敤鎴疯涓恒€?
- 淇鎻掍欢鎼滅储 0 缁撴灉闂锛歁AIN-world search API bridge 鐜板湪浣跨敤瀹屾暣娴忚鍣ㄥ弬鏁帮紝骞惰皟鐢ㄩ〉闈?`byted_acrawler.frontierSign()` 缁欐悳绱?URL 杩藉姞 `X-Bogus`锛涗富鎼滅储绔偣鏈夌粨鏋滄椂涓嶅啀缁х画鎵?fallback 绔偣銆?
- 淇鎶栭煶鎻掍欢鎼滅储鍋跺彂 `task_timeout`锛歞ispatcher 绛夊緟鎶栭煶棣栭〉 / 鎼滅储椤?ready 鏃讹紝闄や簡鐩戝惉 `chrome.tabs.onUpdated(status=complete)`锛屼篃浼氬湪 tab 宸茬粡 complete 鎴栨姈闊?SPA 娌℃湁鍐嶅彂 complete 浜嬩欢鏃惰蛋 fallback锛岄伩鍏嶄换鍔″仠鍦?`/jingxuan` 涓嶇户缁烦鎼滅储椤点€?
- `discover-douyin --source search` / `discover --source douyin` 鐨?search 瀛愭潵婧愮幇鍦ㄤ紭鍏堝鐢ㄦ彃浠剁鍚嶆悳绱㈤摼璺紝鍊欓€変互 `dy-plugin-search` 鍐欏叆 discovery 缁撴灉锛涙彃浠朵换鍔＄┖ / 澶辫触鏃跺啀鍥為€€ direct-cookie search銆?
- `discover-douyin --source hot` / `discover --source douyin` 鐨?hot 瀛愭潵婧愭敼涓烘彃浠?hot-related 閾捐矾锛氬悗绔厛浠?hot board 鍙?`sentence_id`锛屾墿灞曟墦寮€ `/hot/{sentence_id}` 瑙ｆ瀽璺宠浆鍚庣殑 seed aweme锛屽啀鐢ㄩ〉闈?acrawler 绛惧悕 `/aweme/v1/web/aweme/related/`锛屽€欓€変互 `dy-plugin-hot-related` 杩涘叆 discovery锛涙彃浠剁┖缁撴灉鏃跺啀鍥為€€ direct-cookie hot銆?
- `[sources.douyin].daily_hot_budget` 鐜板湪瀹為檯闄愬埗 `dy_tasks(type="hot")` 鍏ラ槦娆℃暟锛宍daily_search_budget` 缁х画闄愬埗 search 鎻掍欢浠诲姟銆?
- 鐪熷疄 smoke锛氬叧闂棫涓存椂鏈櫥褰?Chrome 骞叉壈鍚庯紝`openbiliclaw search-douyin -k 鐚?--max-items-per-keyword 10 -w 180` 鎷夊埌 10 鏉″€欓€夈€?
- 鐪熷疄 smoke锛歚openbiliclaw discover-douyin --source search --keyword 鐚?--limit 5 --no-cache --no-evaluate` 鎷夊埌 5 鏉?`dy-plugin-search` 鍊欓€夈€?

---

## v0.3.67: 鎶栭煶鏀惰棌/鐐硅禐鎷夊彇 E2E 琛ュ己锛?026-05-09锛?

- 鏂板鎶栭煶 direct-cookie discovery 璁捐涓庨鎵瑰疄鐜帮細`discover --source douyin` 鍙湪 `[sources.douyin].enabled=true` 涓斿瓨鍦ㄧ幆澧冨彉閲忚鐩栨垨鎵╁睍鍚屾 Cookie 鏃舵媺鍙?`dy-direct-search` / `dy-direct-hot` / `dy-direct-creator` 鍊欓€夛紝骞舵寜 `source_platform="douyin"` 鍐欏叆 discovery pool锛涘垵濮嬪寲鐢诲儚浠嶄繚鐣欐墿灞曡矾寰勩€?
- 娴忚鍣ㄦ墿灞曟柊澧炴姈闊?Cookie 鑷姩鍚屾锛歴ervice worker 璇诲彇 douyin.com Cookie 鍚?POST 鍒?`/api/sources/dy/cookie`锛屽悗绔繚瀛樺埌 `data/douyin_cookie.json`锛沗discover --source douyin` / `discover-douyin` 鐜板湪鎸夆€滅幆澧冨彉閲忚鐩?鈫?鎵╁睍鍚屾鏂囦欢鈥濊В鏋?Cookie锛屼笉鍐嶈姹傛櫘閫氱敤鎴锋墜鍔ㄥ鍑恒€?
- 鎶栭煶 Cookie 鍚屾闂ㄦ浠庘€滃繀椤绘湁 `msToken`鈥濇斁瀹戒负鈥滃瓨鍦ㄧ櫥褰曟€?/ session / passport 绫?Cookie 鍗冲悓姝モ€濓細鐪熷疄 Chrome 鐧诲綍鎬佸彲鑳藉彧鏈?`sessionid` / `sid_guard` / `ttwid` / `odin_tt` 绛?Cookie锛屾墿灞曚細瀹屾暣鍚屾 header锛岃 direct discovery 鑷繁閫氳繃 smoke 鍒ゆ柇鏈夋晥鎬с€?
- 鎵╁睍 Cookie alarm 鍏滃簳鍚屾鐜板湪鍚屾椂鍒锋柊 B 绔欏拰鎶栭煶 Cookie锛氬悗绔噸鍚€乺untime-stream 鐭殏鏂紑鎴栫敤鎴风櫥褰曟€佹棭宸插瓨鍦ㄦ椂锛屼笉鍐嶅彧琛ュ彂 B 绔?Cookie銆?
- 鎶栭煶 direct-cookie 璇锋眰閬囧埌杩炴帴寮傚父鏃舵敼涓鸿蒋澶辫触杩斿洖绌虹粨鏋滃苟璁板綍鏃ュ織锛岄伩鍏?`discover-douyin` 鍦ㄥ崟娆＄綉缁滄姈鍔ㄦ椂鐩存帴 traceback銆?
- 鎶栭煶 creator discovery 澧炲姞鏈€杩?bootstrap 浣滆€呭厹搴曪細涓嶆樉寮忎紶 `--creator-sec-uid` 鏃讹紝浼氬厛璇?`OPENBILICLAW_DOUYIN_CREATOR_SEC_UIDS`锛屽啀浠庢渶杩戝畬鎴愮殑鎶栭煶鍙戝竷 / 鏀惰棌 / 鐐硅禐 / 鍏虫敞浠诲姟缁撴灉閲屾彁鍙?creator `sec_uid`锛屼紭鍏堢敤 creator timeline 鎷夊叕寮€瑙嗛锛岄伩鍏?search / hot 杞繑鍥炵┖鍒楄〃鏃堕粯璁?discovery 鍙兘浜у嚭 0 鏉°€?
- 鎶栭煶 discovery 鎶芥垚鐙珛 `DouyinDiscoveryService`锛欳LI銆乺untime 鎴栨湭鏉?API 閮藉彲浠ュ鐢ㄥ悓涓€鏈嶅姟锛涙柊澧?`openbiliclaw discover-douyin` 鐙珛璋冭瘯鍛戒护锛屾敮鎸佹寚瀹氬叧閿瘝銆乧reator sec_uid銆佸瓙鏉ユ簮锛屽苟鍙敤 `--no-cache --no-evaluate` 鐩存帴鏌ョ湅婧愭帴鍙ｅ彫鍥炪€?
- 鎶栭煶鎵╁睍 MAIN-world API harvester 澧炲姞鍙祴璇曞鍑猴紝骞惰ˉ榻愭敹钘?/ 鐐硅禐鍒嗛〉妗ユ帴鍗曟祴锛岃鐩?`dy_collect`銆乣dy_like` 浠庨〉闈?API 鍒?isolated world 鐨?postMessage 璺緞銆?
- 鍚庣 `/api/sources/dy/task-result` 澧炲姞鐪熷疄 dispatcher 褰㈡€佸洖褰掞細鍚?scope 浠?`partial` 鍒嗘壒鍥炰紶 videos锛屾渶缁?`ok/empty` 瀹屾垚浠诲姟鏃朵繚鐣欏凡鍥炰紶瑙嗛銆佸幓閲嶅苟瀹屾垚浠诲姟銆?
- CLI 澧炲姞 `init --yes-douyin` 瀵规帴娴嬭瘯锛岀‘璁ゆ姈闊充簨浠朵細杩涘叆 `analyze_events()` 涓?`build_initial_profile()`锛涘悓鏃舵槑纭?`fetch-douyin` 浠嶆槸绾媺鍙栧懡浠わ紝涓嶄細闅愬紡閲嶅缓鐢诲儚銆?
- 灏忕孩涔?/ 鎶栭煶 bootstrap collect 榛樿绛夊緟缁熶竴鍒?`180s`锛歚init --yes-xhs --yes-douyin` 杩炵画璺戜袱婧愭椂锛屽皬绾功鏈夋洿闀跨獥鍙ｇ粨鏉熷墠鍙?tab 浠诲姟锛岄檷浣庤秴鏃跺悗绔嬪埢鍚姩鎶栭煶閫犳垚鐒︾偣绔炰簤鐨勬鐜囷紱`fetch-xhs` / `fetch-douyin` 榛樿 smoke 绐楀彛涔熷悓姝ヤ负 `180s`銆?
- `agent_bootstrap.py` / 涓€鍙ヨ瘽瀹夎鑴氭湰澧炲姞 `--yes-douyin` / `--no-douyin` 鏄惧紡鍐崇瓥閫忎紶锛汻EADME銆丆LI銆丼oul銆佹灦鏋勩€丏ocker 鍜?agent 瀹夎鏂囨。鍚屾璁板綍鎶栭煶 init 鏁版嵁娴併€?

---

## v0.3.66: 淇 pool 涓婇檺澶卞畧锛坮efresh 缁撴潫鏃舵紡 enforce 鎬婚噺 cap锛夛紙2026-05-08锛?

### 鑳屾櫙

绾夸笂 popup 鐪嬪埌 `pool_available_count = 668`锛岄厤缃噷 `pool_target_count = 600`锛屾槑鏄捐秴閲忋€傛棩蹇楅噷鐪嬪埌 `_enforce_pool_cap` 鍦?04:25:58 鎶?pool 鐮嶅埌 556 涔嬪悗鏁存暣 10+ 鍒嗛挓娌″啀璺戯紝鏈熼棿 daemon 涓€鐩村湪璺?discovery锛堜竴鍫?`discovery.evaluate_single` LLM 璋冪敤锛夛紝pool 闈欓粯浠?556 娑ㄥ洖 668銆?

### Root Cause

`_run_refresh_plan`锛坉iscovery 涓绘祦绋嬶級璺戝畬涓€杞悗鍙皟浜嗕笁涓?trim锛?
- `trim_explore_cluster_overflow`锛堟瘡涓?explore cluster 涓嶈秴杩?N 鏉★級
- `trim_topic_group_overflow`锛堟瘡涓?topic_group 涓嶈秴杩?pool_target / 10锛?
- `evict_stale_pool_items`锛堟寜 14 澶╁勾榫勬窐姹帮級

**杩欎笁涓兘鏄寜"缁村害"鐮嶏紝涓嶅崱鎬婚噺**銆傛墍浠ヤ竴杞?discovery 瀹屾垚鏃讹紝姣忎釜缁村害閮藉湪閰嶉鍐咃紝浣嗗姞鎬诲彲浠ヨ繙瓒?`pool_target_count`銆傛瘡涓?strategy 鍐呴儴 LLM 璇勪及涓€鎵瑰氨寰€ `content_cache` 鍐欎竴鎵?`pool_status='fresh'`锛泂trategy 涔嬮棿鐨?`if current_pool_count >= self.pool_target_count: break` 鍙槻姝?*鍚姩鏂?strategy**锛屽鍗曚釜 strategy 鍐呴儴鐨勬孩鍑烘棤鏁堛€?

`_enforce_pool_cap`锛堟寜鎬婚噺鐮嶏級铏界劧瀛樺湪锛屼絾鍙湪 `run_forever` 鐨勫懆鏈熸€?tick 閲岃窇銆傚綋 discovery 鎸佺画 10-30 鍒嗛挓鏃讹紙v0.3.47 璧凤紝LLM eval batch 鍙兘鏇存參锛夛紝鍛ㄦ湡鎬?tick 琚帇浣忥紝pool 涓€璺定銆?

### 淇

`runtime/refresh.py::_run_refresh_plan` 鏈熬銆佺姸鎬佸啓鍏ヤ箣鍓嶏紝鍔犱竴娆?`self._enforce_pool_cap()`銆傝繖鏉¤矾寰勫凡缁忓仛榻愪簡锛?
1. `trim_topic_group_overflow`锛堝啀璺戜竴閬嶏級
2. `reactivate_under_quota_pool_sources`锛堟寜 source family 閰嶉澶嶆椿 suppressed 涓彲鎭㈠椤癸級
3. 绗簩娆?`trim_topic_group_overflow`
4. 鎬婚噺 trim 鍒?`pool_target_count`锛坄trim_pool_to_target_count`锛?

涔熷氨鏄姣忚疆 discovery 瀹屾垚鍚?pool 蹇呯劧 鈮?target锛宲opup 涓嶄細鍐嶇湅鍒拌秴閲忋€?

### 娴嬭瘯

- `test_run_refresh_plan_enforces_cap_when_discovery_overshoots` 澶嶇幇 bug锛歞iscovery 鍗曟 push 25 鏉℃妸 pool 浠?25 鎺ㄥ埌 50锛坱arget=30锛夛紝鏂█ force_refresh 瀹屾垚鍚?`pool_count <= 30`
- `test_run_refresh_plan_stops_midway_when_cap_hit` 绛夋棦鏈?37 涓?refresh runtime 娴嬭瘯鍏ㄩ儴閫氳繃锛屾棤鍥炲綊

### 褰卞搷

- 鐢ㄦ埛鐪嬪埌鐨?杩樻湁 N 鏉″彲鎹?涓嶄細鍐嶈秴杩?`pool_target_count`
- 闀胯窇 discovery 鏈熼棿 pool 涔熷畧寰椾綇锛堜笉鍐嶄緷璧?run_forever 鍛ㄦ湡鎬у厹搴曪級
- 娌?schema 鏀瑰姩锛屽彧鏄璋冧竴娆＄幇鎴?helper锛屾€ц兘寮€閿€鍙拷鐣ワ紙涓€娆?SQL group-by + 鑷冲涓€娆?UPDATE锛?

---

## v0.3.65: 淇 speculator 婊炵暀 bug锛坈onfirmed 鍗犳弧 active 妲戒綅瀵艰嚧鎺㈤拡鍗℃锛夛紙2026-05-08锛?

### 鑳屾櫙

绾夸笂瑙傚療鍒?`openbiliclaw probe` 鏄剧ず銆屾殏鏃舵病鏈夋椿璺冪殑鐚滄祴銆嶏紝浣?`force_tick` 浠嶇劧杩斿洖 `generated=0`銆俤ump `data/memory/speculative_state.json` 鍚庣湅鍒?`active` list 閲?5 椤瑰叏鏄?`status="confirmed"`锛堜笉鏄?`"active"`锛夛紝鎶?`max_active=5` 鐨勯搴﹀叏鍗犳弧浜?鈥斺€?LLM 璋冪敤纭疄璺戜簡銆佽繑鍥炰簡 7 涓€欓€夈€乹uality gate 涔熼兘杩囦簡锛屼絾 `_generate` 鍐呴儴 `if len(state.active) >= self._max_active: break` 姘歌繙绔嬪嵆瑙﹀彂锛屼竴涓€欓€夐兘 append 涓嶈繘鍘汇€?

### Root Cause

鐘舵€佹満鏈潵璁捐鏄細
- `active` 鈫?淇″彿绱Н婊?threshold 鈫?`promote_ready` 鎼埌 promoted 鍒楄〃 鈫?pipeline 鍔犺繘 profile.likes
- `active` 鈫?鐢ㄦ埛纭锛圕LI/popup锛?鈫?`confirmed`锛坄user_confirm_speculation` 鍚屾椂鎶?`confirmation_count` 璁句负 threshold锛?
- `active` 鈫?鐢ㄦ埛鎷掔粷 鈫?`rejected` 杩?cooldown
- `active` 鈫?TTL 杩囨湡 鈫?`rejected` 杩?cooldown

**浣?* `promote_ready` 鍙尮閰?`status == "active"`锛宍expire_stale` 鍚屾牱鍙鐞?`"active"`銆傛墍浠?`status="confirmed"` 鐨勯」杩涗簡**姝诲惊鐜?*锛?
- `promote_ready` 涓嶆敹锛坰tatus != "active"锛?
- `expire_stale` 涓嶆敹锛坰tatus != "active"锛?
- `_generate` 鎶婂畠浠鍏?`len(state.active)` 瑙﹀彂婊″憳鍒ゆ柇 鈫?闃诲鏂扮敓鎴?

鐢ㄦ埛姣忓 confirm 涓€涓氨澶氫竴涓案杩滀笉鍔ㄧ殑灏镐綋锛屾渶缁?active list 鎾戞弧鍚?*鏁翠釜鎺㈤拡鐢熸垚閾捐矾灏卞崱姝?*銆?

### 淇

`speculator.py::promote_ready` 鍔犱竴鏉?OR 鍒嗘敮锛?

```python
ready = (
    spec.status == "active"
    and spec.confirmation_count >= spec.confirmation_threshold
) or spec.status == "confirmed"
```

杩欐牱涓ゆ潯 promote 璺緞姹囪仛鍒板悓涓€涓嚭鍙ｏ細鑷劧绱Н鍒伴槇鍊肩殑 + 鐢ㄦ埛涓诲姩纭鐨勶紝閮戒粠 `state.active` 鎼嚭 鈫?pipeline 鑷姩鍔犲埌 `profile.interest.likes`銆?

### 娴嬭瘯

鏂板涓や釜鍥炲綊 case 鍦?`tests/test_speculator.py`锛?
- `test_promote_ready_handles_user_confirmed_status` 鈥?鍗曞厓灞傞潰楠岃瘉 confirmed + active(threshold met) 涓ゆ潯璺緞閮借姝ｇ‘鏀跺壊
- `test_force_tick_unblocked_when_active_full_of_confirmed` 鈥?E2E 澶嶇幇鎶ュ憡鍦烘櫙锛? 涓?confirmed 鍗犳弧 active 鏃讹紝涓嬫 force_tick 蹇呴』 (1) 鎶?5 涓叏閮?promote (2) 鍦ㄨ吘鍑虹殑妲戒綅鐢熸垚鏂扮寽娴?

### 褰卞搷

- 宸叉湁鐢ㄦ埛 `data/memory/speculative_state.json` 閲屽鏋滄湁婊炵暀 confirmed 椤癸紝涓嬫 daemon 璺?speculator tick 鏃朵細琚嚜鍔ㄦ竻鐞?+ 鍔犺繘 `profile.interest.likes`銆傛湰娆′慨澶嶅悓鏃惰ˉ鍋氫簡涔嬪墠婕忔帀鐨?鏅嬪崌杩涙寮忓叴瓒?鍔ㄤ綔 鈥斺€?鐢ㄦ埛鏇剧粡鎵嬪姩 confirm 杩囩殑鐚滄祴鏂瑰悜缁堜簬浼氳惤鍒扮敾鍍忛噷銆?
- 娌℃湁 schema 鏀瑰姩锛宻tate.json 鏂囦欢鏍煎紡涓嶅彉銆?

---

## v0.3.64: 灏忕孩涔?bootstrap 鎷夊彇涓婇檺 50 鈫?300 (2026-05-06)

### 鑳屾櫙

XHS bootstrap 鐨?`max_items_per_scope` 榛樿 50 / `max_scroll_rounds`
榛樿 3,瀵规敹钘忓鐨勭敤鎴?鍑犵櫨鏉?绛変簬"鍙妸鏈€杩?60 鏉℃渶鏂?save 褰撲綔
鐢诲儚杈撳叆",寰堥毦鐪熷疄鍙嶆槧闀挎湡鍙ｅ懗銆傜敤鎴锋彁鍑烘妸涓婇檺鏀瑰埌 300銆?

### 鏀瑰姩

`src/openbiliclaw/cli.py:_enqueue_xhs_bootstrap_task`:

| 鍙傛暟 | 鏃ч粯璁?| 鏂伴粯璁?| 鎺у埗 env var |
|---|---|---|---|
| `max_items_per_scope` | 50 | **300** | `OPENBILICLAW_XHS_BOOTSTRAP_MAX_ITEMS` |
| `max_scroll_rounds` | 3 | **15** | `OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS` |

`scroll_rounds` 涔熷緱璺熺潃璋?鍚﹀垯铏氭嫙鍒楄〃姣忚疆 ~20-30 鏉?脳 3 杞笂闄?~80,
300 鏄┖澶存敮绁ㄣ€?5 杞槸涓婇檺涓嶆槸鍥哄畾寮€閿€:executor 鐢?
`bootstrapScrollShouldContinue` 璺熻釜 `stagnantRounds`,榛樿杩炵画 5 杞?
娌″嚭鏂?note 灏辨棭閫€,鎵€浠ユ敹钘忓皯鐨勭敤鎴蜂笉浼氳窇婊?15 杞€?

extension 渚?`MAX_BOOTSTRAP_SCROLL_ROUNDS = 30` 鏄?hard ceiling,15
瀹屽叏鍦ㄨ寖鍥村唴,**鎻掍欢鏃犻渶閲嶆柊鍙戠増**銆?

### 涓嶅奖鍝嶇殑

- 璁捐繃 env var 鐨勭敤鎴风户缁寜鑷畾涔夊€艰窇
- 宸茬粡璺戣繃 init 鐨勭敤鎴蜂笉浼氶噸澶?bootstrap
- discovery / continuous 璺緞鐢ㄧ殑鏄笉鍚屽叆鍙?`xhs.search` /
  `xhs.creator`),鍜?bootstrap 鏃犲叧
- xhs_history scope 鍦ㄥ皬绾功 profile 椤垫牴鏈笉鏆撮湶,杩欐渚濈劧 0 鏉?
  (涓庝笂闄愬澶ф棤鍏?

### 娴嬭瘯

`tests/test_cli.py::test_enqueue_xhs_bootstrap_task_uses_env_overrides`
鏄?env-override 娴嬭瘯(鐢?5 / 100),閫昏緫涓嶅彉,缁х画 green銆?

---

## v0.3.63: LLM 鍏ㄥ眬浼樺厛绾ч槦鍒?+ detached task registry (2026-05-05)

### 鑳屾櫙

v0.3.62 瑙ｅ喅浜?浜掔浉鎷栫疮"鐨?lock 闂,浣嗙暀涓嬩簡鐢ㄦ埛鏋舵瀯 review 涓殑涓ゆ潯灏惧反:

1. **LLM 璧勬簮浠嶇劧娌℃湁浼樺厛绾ф蹇点€?* 褰撲竴杞?delight scoring (涓婄櫨娆¤皟鐢? 鍦ㄨ窇鏃?popup 鎬ラ渶鐨?`write_expression` (1-2 娆¤皟鐢? 鍙兘鍦?FIFO 闃熷垪鍚庨潰鎺掗槦,鐢ㄦ埛鑳界湅瑙佺殑姹犲瓙琛ㄨ揪寮忓洖濉彲鑳借绛夋暟鍒嗛挓銆?
2. **detached task 鍦?hot reload 鍚庤繕鍦ㄨ窇銆?* `RuntimeContext.rebuild_from_config` 鍙?cancel 椤跺眰 loop task,`asyncio.create_task(...)` 璧风殑 fire-and-forget 鍗忕▼(per-strategy precompute銆乸rewarm helper銆乸er-event trigger銆乵anual refresh handle)鎸佹湁鏃?runtime 寮曠敤缁х画鎶?SQLite 鍐欏拰 LLM token,鍙兘鎸佺画寰堝绉掋€?

杩欎竴鐗堟敹灏捐繖涓ゆ潯銆備袱浠跺伐浣滀粛鐒舵槸骞惰 agent 璧风殑(LLM 浼樺厛绾?/ task registry 鍒嗗埆涓€缁?,鏈€缁堝湪涓讳笂涓嬫枃閲屾敹鏁涖€佽ˉ 4 涓泦鎴愮偣 + 8 涓祴璇曘€?

### 涓€銆丩LM 鍏ㄥ眬浼樺厛绾ч槦鍒?

`src/openbiliclaw/llm/service.py` 鍔犱簡涓€涓?`PrioritySemaphore` 绫?鐢?heapq + monotonic 璁℃暟鍣ㄥ疄鐜颁紭鍏堢骇 + FIFO 骞冲眬:capacity=1,瀹屽叏 free 鏃舵棤寮€閿€鐩撮€?鏈夌珵浜夋椂涓ユ牸鎸変紭鍏堢骇鍞ら啋 waiter銆?

`LLMService` 鍔犱簡:

- `_PRIORITY_MAP` ClassVar:`recommendation.write_expression`/`discovery.evaluate_batch` = **1**(鐢ㄦ埛鍙銆佸牭浣忓氨鏄庢樉);`recommendation.delight_score`/`soul.*`/`xhs.*` = **2**(鍚庡彴鎵归噺鎵撳垎);鍏朵粬榛樿 **3**銆?
- `_resolve_priority(caller)`:瀵?`caller` tag 鍋?longest-prefix 鍖归厤銆俙"soul.preference"` 鍖归厤 `"soul"` 鍓嶇紑鎷垮埌 priority=2銆?
- `_priority_sem: PrioritySemaphore`(`init=False`,榛樿 capacity=1):`complete_with_core_memory` 鐜板湪鎶?`await self.registry.complete(...)` 鍖呰繘 `async with self._priority_sem.slot(priority):`銆?

鍞竴鏀瑰姩鐐规槸鍦?`complete_with_core_memory` 閲屸€斺€旇繖鏄墍鏈?LLM 璋冪敤鐨勫崟涓€鍏ュ彛(`complete_structured_task` / `complete_with_tools` / `complete_socratic_dialogue` 鍏ㄩ儴璧拌繖鏉¤矾寰?,涓嶉渶瑕佹敼涓嬫父姣忎釜 caller銆?

**棰勬湡鏁堟灉**:鍦?delight scoring 璺戞壒鐨勬椂鍊?popup 瑙﹀彂鐨?`write_expression` 鎶㈠埌涓嬩竴涓?LLM slot 鑰屼笉鏄帓鍒伴槦灏?鍚庡彴 priority=3 鐨勪复鏃?caller 涔熶笉浼氭彃闃熸尋鎺?priority=2 鐨?soul 鍒嗘瀽銆?

### 浜屻€丏etached task registry

`src/openbiliclaw/runtime/task_registry.py` 鏂板 `BackgroundTaskRegistry`:

- `track(name, coro)`:灏佽 `asyncio.create_task(coro, name=name)`,璁板綍鍒?`dict[Task, str]`銆倀ask 瀹屾垚鏃堕€氳繃 `add_done_callback` 鑷姩 untrack,涓嶄細鏃犵晫澧為暱銆?
- `cancel_all(grace_seconds=1.5)`:cancel 鎵€鏈?tracked task,绛?1.5s 浼橀泤閫€鍑?瓒呮椂鍒?logger.warning 骞跺己鍒?`_tasks.clear()`,鏂?runtime 绔嬪埢鍙敤銆?
- `stats()`:鎸夊悕瀛楀墠缂€鍒嗙粍鐨勮瘖鏂鏁?future-proof 缁欒娴嬮潰鏉?銆?

`RuntimeContext`:
- 鏂板 `task_registry: BackgroundTaskRegistry` 瀛楁銆?
- `rebuild_from_config` 鎷嗘垚 async 鍏紑鏂规硶(椤堕儴 `await task_registry.cancel_all()` + INFO 鏃ュ織) + sync `_rebuild_components` 鍐呴儴銆?
- 娉ㄥ叆 registry 鍒?`RecommendationEngine` 鍜?`ContinuousRefreshController`銆?
- 4 涓?background task(refresh / account_sync / auto_update / prewarm)缁熶竴璧?`task_registry.track(...)`銆?

`RecommendationEngine` / `ContinuousRefreshController` 鍚勬柊澧炲彲閫?`task_registry` kwarg + `_spawn_detached_task` / `_track_task` helper銆傛墍鏈?`asyncio.create_task` 璋冪敤鐐?`_safe_classify_pool_backlog`銆乣_safe_precompute_delight_scores`銆乣_manual_refresh_task`銆乸er-strategy precompute銆乸er-event trigger)璧?helper;helper 鍦ㄦ病鏈?registry 鏃?fallback 鍒拌８ `create_task`,淇濊瘉鏃?registry 鐨勬棫娴嬭瘯澶瑰叿缁х画 green銆?

`api/app.py` 涓ゅ `ctx.rebuild_from_config(...)` 鏀规垚 await銆?

**棰勬湡鏁堟灉**:鐢ㄦ埛鍦ㄨ繍琛屾椂鏀逛簡 config 閲嶈浇涔嬪悗,鏃?detached task 鍦ㄦ渶澶?1.5s 鍐呭叏閮ㄩ€€鍦?涓嶄細鍜屾柊 runtime 鎶㈠悓涓€涓?SQLite 鍐欐垨 LLM token銆?

### 娴嬭瘯

- 鏂板 `tests/test_task_registry.py`(5 涓祴璇?:track/cancel/stats/瓒呮椂闄嶇骇/浜屾鍙敤鎬с€?
- `tests/test_llm_service.py` +3 涓祴璇?`_resolve_priority` longest-prefix 琛ㄣ€乣PrioritySemaphore` 澶?waiter 椤哄簭鍞ら啋銆乣complete_with_core_memory` 閫氳繃 priority 闂ㄤ覆琛屽寲銆?
- `tests/test_api_app.py` 鐨?`FakeRecommendationEngine.__init__` 鎺ュ彈 `task_registry=None` 鍙傛暟銆?

### 涓嶅奖鍝嶇殑

- LLM caller 鐨?`caller=` tag 涔犳儻娌″彉;鐜版湁 caller tag 鍦?priority map 閲屽懡涓棦鏈夎鍒?鏂板姞 caller 榛樿 priority=3 涓嶄細鐮村潖鐜版湁璋冪敤銆?
- `LLMService(...)` 鏋勯€犵鍚嶅悜鍚庡吋瀹?`_priority_sem` 鏄?`init=False`)銆?
- 娌℃湁 registry 娉ㄥ叆鏃?`RecommendationEngine` / refresh loop 鐨勮涓哄拰 v0.3.62 瀹屽叏涓€鑷淬€?

---

## v0.3.62: 涓夊鏋舵瀯鎬?lock 鎷嗗垎 + DB 鍐欓噸璇曟敹绱?(2026-05-05)

### 鑳屾櫙

鐢ㄦ埛鍋氫簡涓€杞灦鏋?review,璇嗗埆鍑?7 涓綔鍦ㄤ簰鐩告嫋绱偣銆傛垜浠繖杞鐞?top 3 鐪熼棶棰?骞惰 agent 瀹炵幇):

### 淇硶

#### 馃敶 #1 鎷?`_precompute_lock` 鈫?`_expression_lock` + `_delight_lock`(`recommendation/engine.py`)

```python
# 涔嬪墠
self._precompute_lock = asyncio.Lock()  # expression + delight 閮界敤杩欎竴鎶?

# 涔嬪悗
self._expression_lock = asyncio.Lock()  # 鍙?gate 鎺ㄨ崘鏂囨
self._delight_lock = asyncio.Lock()     # 鍙?gate 鎯婂枩璇勫垎
```

`precompute_pool_copy` 閲?
- expression 鐢熸垚鍧楀寘鍦?`async with self._expression_lock`
- delight scoring 鎶藉埌 `_safe_precompute_delight_scores` helper,**fire-and-forget** 璺?`asyncio.create_task`),鐢ㄨ嚜宸辩殑 `_delight_lock` 闃插悓鏈?double-spend銆?
- 鏃╄繑鍥?(`if not candidates`) 璺緞鍚屾牱璧?detached delight,涓嶅啀闃诲 caller銆?

鏁堟灉:鎺ㄨ崘鏂囨姘歌繙涓嶈 delight 鎶㈤攣銆俤elight 鎱簡,popup 涔熺収鏍疯兘鎹㈠唴瀹广€?

#### 馃敶 #2 鍏ㄥ眬 `_refresh_lock` 闃?4 鍏ュ彛鍙犲姞(`runtime/refresh.py`)

```python
_refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
```

`refresh_if_needed` 鍏ュ彛澶勫厛妫€鏌?`if self._refresh_lock.locked():` 鈫?绔嬪嵆杩斿洖 `{"skipped": True, "reason": "another refresh holds lock"}`,**涓嶆帓闃?*(閬垮厤 manual 绛?5 鍒嗛挓鍦?periodic 鍚庨潰)銆?

`force_refresh`(manual refresh 瀹為檯鍏ュ彛)鍚屾牱鍔?lock:鎶藉嚭 `_force_refresh_locked` 鍐呴儴浣?澶栧眰 `force_refresh` 鍋?lock check + acquire銆?*4 涓叆鍙?*(`_loop_refresh` / `_complete_manual_refresh` / `refresh_after_event_ingest` / `refresh_after_feedback`)鐜板湪閮戒簰鏂?涓嶅啀鍙?B 绔?API 鍜?SQLite 鍐欍€?

#### 馃煛 #3 `_execute_write` 閲嶈瘯鍙傛暟鏀剁揣(`storage/database.py`)

```python
# 涔嬪墠: 5 脳 100ms = 鏈€澶?500ms 鍚屾闃诲 event loop
_LOCK_RETRY_ATTEMPTS = 5
_LOCK_RETRY_SLEEP_SECONDS = 0.1

# 涔嬪悗: 8 脳 20ms = 鏈€澶?160ms (鏇村娆￠噸璇?姣忔鏇寸煭)
_LOCK_RETRY_ATTEMPTS = 8
_LOCK_RETRY_SLEEP_SECONDS = 0.02
```

`time.sleep` 浠嶆槸鍚屾鐨?浣嗘瘡娆?20ms 杩滀綆浜庝汉鎰熺煡闃堝€?鍗充娇鍦?asyncio 涓婁笅鏂囬噷鐭殏鍗′綇涔熷熀鏈笉鍙銆?*鐪熷紓姝ュ寲**(`asyncio.to_thread` 鎴?`await asyncio.sleep`)闇€瑕佺骇鑱旀敼 18+ 涓?caller,鐣欑粰 v0.3.63 澶ч噸鏋勩€?

### 涓嶅湪鏈鑼冨洿

| 鐢ㄦ埛鏍囪鐨勫叾浠栭棶棰?| 鎺掓湡 |
|---|---|
| LLM 娌″叏灞€浼樺厛绾ч槦鍒?| v0.3.63 (鏋舵瀯绾?闇€瑕佽璁? |
| Hot reload detached task 涓嶅彇娑?| v0.3.63 (task registry) |
| Embedding semaphore=2 | 涓嶅姩(Ollama 鏈湴鎺ㄧ悊璁捐濡傛) |

### 娴嬭瘯

134 passing(test_recommendation_engine + test_refresh_runtime + test_storage)銆?000 passed/29 pre-existing failed,鏃犳柊澧炲け璐ャ€?

### 鑷磋阿

鏁村淇瀹屽叏鏄敤鎴锋灦鏋?review 椹卞姩:浠栫敤 `git diff` + 浠ｇ爜闈欒鎶婃綔鍦ㄦ閿?鎶㈤攣/绔炴€佸叏閮ㄨ瘑鍒嚭鏉?鐒跺悗鎸変紭鍏堢骇鎺掑簭銆侫gent #1 (engine.py) 鍜?Agent #2 (refresh.py) 骞惰瀹炴柦浜掍笉鍐茬獊;鎴戣嚜宸辨敼 database.py 璧板皬鏀瑰姩璺嚎閬垮紑 await 绾ц仈銆?

---

## v0.3.61 + extension v0.3.18: v_voucher 椋庢帶缂撹В + popup 鐘舵€佽В鑰?(2026-05-05)

### 鑳屾櫙

v0.3.60 鎶?precompute drain 鎷嗘垚鐙珛 loop 鍚?popup 宸茬粡鑳芥嬁鍒版帹鑽愪簡,浣嗙敤鎴峰弽鏄?
1. `manual_refresh_state="running"` 闀挎湡鎸傝捣,refresh 鍥?B 绔?v_voucher 椋庢帶鍙嶅閲嶈瘯
2. popup 鐘舵€佹潯 chip 鏄剧ず"姝ｅ湪琛ヨ揣",灏界 pool 宸茬粡鏈?59+ 鏉″彲鎹㈠唴瀹?

### 涓変釜淇硶

#### 馃敶 v_voucher mitigation(`discovery/strategies/search.py`)

`_execute_search_queries` 鍗囩骇:
- **Per-query jitter**:`asyncio.sleep(0.5)` 鈫?`asyncio.sleep(0.5 + random.uniform(0, 0.5))`,desync 鍚屾椂钀藉埌 WBI rate-limit bucket 鐨勮姹傛尝
- **Storm detection**:杩炵画 3 涓?query 杩斿洖绌虹粨鏋?璇存槑 client.search 鍐呴儴涓夎疆 v_voucher 閲嶈瘯閮?exhausted) 鈫?log warning + 涓鏈疆鍓╀綑 query銆傜瓑涓嬩竴涓?60s refresh tick 鍐嶆潵,涓嶆繁鎸栧潙銆?

```
v_voucher storm detected (3 consecutive empty queries) 鈥?aborting
remaining N query(ies) this round; next refresh tick (60s) gets a
fresh attempt
```

#### 馃煚 init 寤惰繜棣栬疆 refresh(`runtime/refresh.py`)

鏂板 `_init_grace_consumed: bool = False` 瀛楁銆俙_loop_refresh` 绗竴娆¤窇鏃惰烦杩?`refresh_if_needed`,鍙窇 profile-ready hook銆傜浜屾璧锋仮澶嶆甯?60s 鍛ㄦ湡銆?

```
Init grace period 鈥?skipping first refresh tick to let Bilibili WBI
bucket cool down (next tick will run normally)
```

涓轰粈涔堣杩欐潯:init 鍚屾闃舵(history/favorites/following 鎷夊彇)10 绉掑唴鎵撲簡 30+ 娆?Bilibili API,WBI 妗跺熀鏈濉弧銆傜珛鍒?fire discovery 鎼滅储 鈫?50% v_voucher 閫€閬裤€傜粰 60s 缂撳啿,IP 鍑変竴涓嬨€?

#### 馃煛 popup 鐘舵€佹潯瑙ｈ€?`extension/popup/popup-helpers.js`)

`getPoolStatusSummary` 褰?`pool_available_count > 0` AND `manual_refresh_state="running"` 鏃舵敼鏂囨:

| 涔嬪墠 | 鐜板湪 |
|------|------|
| 褰撳墠鍙崲:杩樻湁 59 鏉″彲鎹?| 褰撳墠鍙崲:杩樻湁 59 鏉″彲鎹?|
| 鏈€杩戣ˉ杩?**姝ｅ湪琛ヨ揣** | 鏈€杩戣ˉ杩?**鍚庡彴缁х画鍦ㄦ壘鏇村** |
| 鐜板湪鍦ㄥ繖:鍚庡彴杩樺湪缁х画缁欎綘鎵炬柊鐨?| 鐜板湪鍦ㄥ繖:鍙互鍏堟崲涓€鎵?鏂扮殑闅忔椂杩?|

涓嶅啀鎶?姝ｅ湪琛ヨ揣"鍠傜粰宸茬粡鑳芥崲涓€鎵圭殑鐢ㄦ埛鈥斺€旈伩鍏嶈浠ヤ负杩樺緱缁х画绛夈€?

### 褰卞搷

| 鍦烘櫙 | 涔嬪墠 | 鐜板湪 |
|------|------|------|
| Init 鍚庣涓€娆?search 鍛戒腑 v_voucher 姣斾緥 | ~50% | 棰勬湡 <10%(grace + jitter 鍙屾姢) |
| 涓€杞?v_voucher 椋庢毚鏈熼棿 | 鎶婃墍鏈?queries 閮芥墦鎸?姣忎釜 21s 閫€閬? | 3 娆?empty 鍚庝腑姝?~90s 鍗崇粓姝?|
| Popup 鐘舵€佹潯 | 鍗充娇 pool 婊¤浇涔熸樉绀?姝ｅ湪琛ヨ揣" | 鍙湪 pool 鐪熺┖鏃舵樉绀?|

### 鑷磋阿

鏁村 v0.3.59 鈫?v0.3.60 鈫?v0.3.61 婕旇繘瀹屽叏鏄敤鎴风殑 systematic-debugging 娴佺▼椹卞姩:
- v0.3.59 鈫?鎴戝姞浜?drain 浣嗘斁閿欎綅缃?琚?refresh 鍗?
- v0.3.60 鈫?鐢ㄦ埛璋冭瘯鍑?drain 姘歌繙杞笉鍒?寤鸿鎷嗙嫭绔?loop;鎴戠収淇?
- v0.3.61 鈫?鐢ㄦ埛杩涗竴姝ュ彂鐜?refresh 鍗＄殑鏍瑰洜鏄?v_voucher 椋庢帶,涓?popup 鐘舵€佹潯浠嶈瀵?鎴戞妸杩欎咯涓€璧蜂慨

---

## v0.3.60: precompute drain 鎷嗘垚鐙珛 loop,涓嶅啀琚參 refresh 鍗?(2026-05-05)

### 鑳屾櫙

鐢ㄦ埛鐢?systematic-debugging 娴佺▼绮剧‘瀹氫綅:

```
PID 32644(22:35:12 鍚姩)
鍐呭瓨鐗堟湰 0.3.59 鉁?
_safe_classify_pool_backlog 鏂规硶瀛樺湪 鉁?
content_cache fresh = 184(132 鏉℃弧瓒?needing_copy)
浣?pool_expression=0銆乸ool_topic_label=0
llm_usage 娌℃湁 caller=recommendation.write_expression
runtime status: manual_refresh_state="running" 闀挎椂闂翠笉杩斿洖
```

鈫?v0.3.59 鐨?`_drain_pool_precompute_backlog` 浠ｇ爜纭疄瀛樺湪,浣?*鎸傚湪 `_loop_refresh` 閲?`await self.refresh_if_needed()` 涔嬪悗**銆侭 绔?v_voucher 椋庢帶璁?refresh 鍑犲垎閽熶笉缁撴潫 鈫?drain 姘歌繙杞笉鍒般€?

### 淇硶

鎸夌敤鎴峰缓璁?鎶?drain 浠?`_loop_refresh` 鎷嗗嚭鏉?鍋氭垚 `_loop_pool_precompute()` 鐙珛 loop:

```python
async def run_forever(self):
    tasks = [
        asyncio.create_task(self._loop_refresh()),
        asyncio.create_task(self._loop_pool_precompute()),  # 鈫?鏂板
        asyncio.create_task(self._loop_soul_pipeline()),
        asyncio.create_task(self._loop_xhs_producer()),
        asyncio.create_task(self._loop_proactive_push()),
    ]

async def _loop_pool_precompute(self):
    while True:
        with suppress(Exception):
            await self._drain_pool_precompute_backlog()
        await asyncio.sleep(self.check_interval_seconds)
```

寮曟搸鐨?`_precompute_lock` 宸茬粡鑳藉幓閲?per-strategy fire-and-forget 瑙﹀彂鐨?precompute,鎵€浠ョ嫭绔?loop 涓嶄細涓?`_run_refresh_plan` 閲岀殑瑙﹀彂 double-spend LLM銆?

### 褰卞搷

| 鍦烘櫙 | v0.3.59 | v0.3.60 |
|------|---------|---------|
| refresh 鍥?v_voucher 鍗″嚑鍒嗛挓 | drain 璺熺潃鍗?姘镐笉鎵ц | drain 鐙珛 60s tick,瀹屽叏涓嶅彈褰卞搷 |
| 鍚姩鍚庣涓€娆?popup 鍙 | 涓嶅彲棰勬祴(鍙栧喅浜?refresh 鏄惁鍗? | 60s 鍐?|

鑷磋阿:鐢ㄦ埛鐢?superpowers:systematic-debugging 娴佺▼涓€姝ユ鎺掗櫎鍋囪(杩涚▼娌℃崲 鈫?鍐呭瓨鐗堟湰瀵?鈫?drain 浠ｇ爜瀛樺湪 鈫?姹犲瓙鏈?184 鏉?fresh 鈫?write_expression=0 鈫?manual_refresh_state stuck)瀹氫綅鍒拌繖涓€琛?鎴戠洿鎺ョ収淇€?

---

## v0.3.59: precompute 瑙ｈ€?classify + 瀹氭湡涓诲姩 drain (2026-05-05)

### 鑳屾櫙

production logs 2026-05-05 21:15-21:36(21 鍒嗛挓浼氳瘽):

```
21:26:42  Soul profile became ready, classify_pool_backlog: 87 items (xiaohongshu)
21:27:15-21:29:35  recommendation.evaluate_batch 脳 6 batch (classify done)
21:28:45 鈫?21:31:08  pool_available=0 鎸佺画
                     caller=recommendation.expression 脳 **0** 鈫?precompute 涓€娆℃病璺?
```

popup 鎴浘鏄剧ず"FOR YOU 1/17"(姹犲瓙閲?17 鏉?浣嗘樉绀?闃緽 姝ｅ湪琛ヨ揣"鈥斺€旇繖 17 鏉″叏鍗″湪 P3 gate 鍚庨潰,鍥犱负娌′汉甯畠浠敓鎴?`pool_expression`銆?

### 鏍瑰洜

precompute 鍙€氳繃涓ゆ潯璺緞瑙﹀彂:
1. `_run_refresh_plan` 閲?`if discovered: precompute_tasks.append(...)` 鈥斺€?Bilibili search 鍦?v_voucher 椋庢帶涓嬪鏁扮瓥鐣ヨ繑鍥?[],precompute 涓?fire
2. `precompute_pool_copy` 鍐呴儴鍏?`await classify_pool_backlog(...)`(鍚屾闃诲)鍐嶈 candidates 鈥斺€?classify 鑷繁璺戝緱鎱㈡椂 precompute 璺熺潃鍗?

涓ゆ潯璺緞鍙犲姞 = pool_expression 姘歌繙濉笉涓?= popup 姘歌繙"姝ｅ湪琛ヨ揣"銆?

### 淇硶

#### 1. `recommendation/engine.py:precompute_pool_copy` 瑙ｈ€?classify

`await classify_pool_backlog(...)` 鈫?`asyncio.create_task(self._safe_classify_pool_backlog(...))`銆傝 classify 鍦ㄥ悗鍙拌嚜宸辫窇,precompute 绔嬪埢璇?鐜板湪宸茬粡鍒嗙被濂界殑" candidates 寮€濮嬪～ expression銆?

鏂板 `_safe_classify_pool_backlog` 鈥斺€?detached task wrapper,寮傚父鍚炴帀闃叉 UnobservedException銆?

#### 2. `runtime/refresh.py:_loop_refresh` 鍔犲畾鏈?drain

姣忎釜 60s tick 鏈熬璋冪敤 `_drain_pool_precompute_backlog()`:
- 妫€鏌?profile ready
- `await engine.precompute_pool_copy(...)` 涓€娆?

寮曟搸鍐呴儴鐨?`_precompute_lock` 鑷姩 dedup 涓?`_run_refresh_plan` 鐨?per-strategy 瑙﹀彂,涓嶄細 double-spend LLM tokens銆?

### 褰卞搷

| 鍦烘櫙 | 涔嬪墠 | 鐜板湪 |
|---|---|---|
| Bilibili 椋庢帶,鎵€鏈?strategy 杩?0 | precompute 姘歌繙涓?fire | 60s 涓€娆″畾鏈?drain |
| classify 鎱?澶?backlog) | precompute 涓茶绛?| precompute 骞惰璇诲凡 classified 鐨?|
| pool 绌虹獥鏃堕暱 | 17 min(瀹炴祴) | 搴旈檷鍒?~3-5 min |

### 椋庨櫓

- precompute 鐜板湪鎸?60s 鍛ㄦ湡涓诲姩 fire,濡傛灉 pool 涓€鐩寸┖,姣忓垎閽熼兘浼氳涓€娆?`_load_pool_candidates_needing_copy(limit=60)`銆係QL 鏄?indexed,璐熻浇鍙拷鐣ャ€?
- LLM token 娑堣€?鍚屾牱鐨?candidates,鍚屾牱鐨勬彁绀鸿瘝銆俙_precompute_lock` 闃?double-spend銆傜敓浜х幆澧冨鑺?0 鍏冦€?
- 濡傛灉 classify 澶辫触瀵艰嚧 pool 涓暱鏈熸湁 `style_key=''`/`topic_group=''` 鐨?row,杩欎簺浼氳 `precompute_pool_copy` 鐩存帴璇诲埌鈥斺€旂簿鎺?LLM 鎷垮埌娌″垎绫荤殑鍐呭涔熻兘鐢熸垚鍏滃簳鏂囨,鍙槸 topic_label 鍙兘涓嶅噯銆侫cceptable 杈圭晫,涓嶉樆濉?popup銆?

娴嬭瘯:1000/1029 閫氳繃(鍚?29 涓?pre-existing failures 涓嶅涓嶅噺)銆?

---

## v0.3.58: init 鎽樿鎸夊钩鍙板垎绫绘樉绀轰俊鍙峰叆搴撴暟 (2026-05-05)

### 鑳屾櫙

鑰佺殑 `openbiliclaw init` 鎽樿闈㈡澘鎶?B 绔?/ 灏忕孩涔︾殑浜嬩欢娣锋垚涓€琛?`灏忕孩涔︿簨浠? N`,鏃㈢湅涓嶅嚭 saved/liked/xhs_history 鎬庝箞鍒嗗竷,涔熶笉鐭ラ亾 B 绔欒繖杈?history/favorites/following 鍚勮础鐚簡澶氬皯銆侫I Agent 瑁呮満鏃朵篃娌℃硶娓呮櫚杞憡鐢ㄦ埛"鐢诲儚鍚冧簡澶氬皯淇″彿"銆?

### 淇硶

`cli.py:init` 鐨勬渶缁堟憳瑕佽〃鏍奸噸鏋?鎸夊钩鍙板垎缁勬樉绀?甯?emoji 瑙嗚鍒嗛殧:

```
馃摵 B 绔欒鐪嬪巻鍙?      302 鏉?
馃摵 B 绔欐敹钘忓す         8 鏉?
馃摵 B 绔欏叧娉?UP        350 浜?
馃寪 B 绔?鍏ュ簱浜嬩欢      660 鏉?
馃摃 灏忕孩涔?鏀惰棌(saved) 50 鏉?
馃摃 灏忕孩涔?鐐硅禐(liked) 50 鏉?
馃摃 灏忕孩涔?娴忚璁板綍    0 鏉?
馃寪 灏忕孩涔?鍏ュ簱浜嬩欢    100 鏉?
馃搳 鐢诲儚寤烘ā鎬讳簨浠?    760 鏉?
鉁?鐏甸瓊鐢诲儚           宸茬敓鎴?
馃攳 棣栬疆鍙戠幇鍐呭       180 鏉?
```

涔嬪悗璺熶竴琛屾儏澧冨寲鎻愮ず:
- 灏忕孩涔︿笁涓?scope 鍏?0 鈫?鎻愮ず"鎵╁睍鏈 / 娴忚鍣ㄦ病鐧诲綍 XHS / 浠诲姟鍚庡彴璺?绛夊父瑙佸師鍥?+ 澶嶈窇鍛戒护
- 灏忕孩涔︽湁鏁版嵁 鈫?鎻愮ず"鏈鐢诲儚缁煎悎浜?X 鏉?B 绔?+ Y 鏉″皬绾功淇″彿,daemon 鍚庣画澧為噺琛ュ厖"

### 閰嶅 doc 鏀瑰姩

`agent-install.md` 鍔?"After init succeeds 鈥?relay the per-source signal counts" 娈?瑕佹眰 AI Agent 鎶婃憳瑕佹暟瀛?paraphrase 缁欑敤鎴?B 绔?灏忕孩涔﹀悇 N 鏉?+ 鎬讳簨浠?+ 棣栬疆鍙戠幇姹?銆? 淇″彿鍦烘櫙蹇呴』鎶?CLI 鐨?鈩癸笍 灏忕孩涔?0 鏉?閭ｈ鍘熸牱杞憡,涓嶈兘涓㈡帀銆?

闆惰涓哄彉鍖?绾?UX 鈥斺€?鏁板瓧鏈潵灏辨湁,鍙槸琛ㄨ揪鏇存竻妤氥€?

---

## extension v0.3.17: service worker WS 閲嶈繛鎸囨暟閫€閬?(2026-05-05)

### 鑳屾櫙

v0.3.14 宸茬粡鎶?popup-stream.js 鐨?WS 鏀规垚鎸囨暟閫€閬?2s鈫?0s),浣?*service worker 鑷繁鏈夌浜屾潯 WS 杩炴帴**(`connectRuntimeStream` 缁?background 鐢ㄧ殑 runtime-stream)渚濈劧鐢ㄥ浐瀹?5s 闂撮殧閲嶈瘯銆傚悗绔鎺夋椂:

```
service-worker.ts:170 WebSocket connection ... failed: ERR_CONNECTION_REFUSED
service-worker.ts:170 WebSocket connection ... failed: ERR_CONNECTION_REFUSED
service-worker.ts:170 WebSocket connection ... failed: ERR_CONNECTION_REFUSED
... 姣?5 绉掍竴琛?鏃犻檺鍒?
```

### 淇硶

`service-worker.ts:scheduleWsReconnect` 鏀圭敤鎸囨暟閫€閬?5s 鈫?10s 鈫?20s 鈫?40s 鈫?60s 灏侀《銆俙onopen` 鎴愬姛鎻℃墜鏃堕噸缃洖 5s,鐬椂缃戠粶鎶栧姩 fast-recover 涓嶆墦鎶樸€?

```ts
const WS_RECONNECT_BASE_DELAY = 5_000;
const WS_RECONNECT_MAX_DELAY = 60_000;
let wsReconnectDelay = WS_RECONNECT_BASE_DELAY;

// scheduleWsReconnect:
const delay = wsReconnectDelay;
setTimeout(connectRuntimeStream, delay);
wsReconnectDelay = Math.min(wsReconnectDelay * 2, WS_RECONNECT_MAX_DELAY);

// onopen:
wsReconnectDelay = WS_RECONNECT_BASE_DELAY;
```

### 褰卞搷

鍚庣姝?1 鍒嗛挓鍐?console:涔嬪墠 ~12 琛?鈫?鐜板湪 5 琛?5s/10s/20s/40s/60s);1 鍒嗛挓涔嬪悗:涔嬪墠涓€鐩?12 娆?鍒嗛挓 鈫?鐜板湪 1 娆?60s銆傞厤鍚?v0.3.14 鐨?popup-stream 閫€閬?鎵╁睍涓ゆ潯 WS 杩炴帴鐜板湪閮戒笉鍐嶅埛灞忋€?

---

## extension v0.3.16: 鍏虫帀鎵€鏈?OS toast,閫氱煡鏀跺洖 popup 鍐?(2026-05-05)

### 鑳屾櫙

鐢ㄦ埛鍙嶉鍙充笅瑙掑脊鐨?Chrome OS 閫氱煡骞叉壈澶ぇ,瑕佹眰"鎵€鏈夐€氱煡閮藉湪鎻掍欢閲岄潰杩涜灏辫"銆傚啀鍔犱笂 v0.3.14/v0.3.15 淇簡 ack 寰幆 + 缁濆 URL 涔嬪悗,Chrome 鍐呴儴 imageUtil 浠嶇劧鍋跺彂 `Uncaught (in promise) Error: Unable to download all specified images.`(鎴戜滑 catch 涓嶅埌鐨勩€佸唴閮?promise 閾?,console 杩樻槸涓嶅共鍑€銆?

### 淇硶

鎶婁笁澶?`chrome.notifications.create` **鍏ㄩ儴鍘绘帀**:

1. `service-worker.ts:checkPendingNotification`(杞鎷夌殑 recommendation + cognition 閫氱煡)鈫?鐜板湪鍙皟 `acknowledgeNotificationSent` / `acknowledgeCognitionUpdateSeen`,璁╁悗绔?pending 闃熷垪姝ｅ父鍑洪槦,浣嗕笉寮?OS toast銆侾opup 鑷繁鏈?WebSocket 璁㈤槄,鎺ㄨ崘鐓у父鍑虹幇鍦ㄥ崱鐗囧垪琛ㄩ噷銆?
2. `service-worker.ts:handleRuntimeEvent` 澶勭悊 `interest.probe`(WS 鎺ㄩ€佺殑鍏磋叮鎺㈤拡)鈫?鍚屼笂鍘绘帀,popup inbox 宸茬粡鏄剧ず
3. `service-worker.ts:handleRuntimeEvent` 澶勭悊 `delight.candidate`(WS 鎺ㄩ€佺殑鎯婂枩鎺ㄨ崘)鈫?鍚屼笂鍘绘帀,delight 宸茬粡鍦?popup 鎺ㄨ崘鍒楄〃閲屽甫 hook badge 鏄剧ず銆備粛鐒?`acknowledgeDelightSent` 闃叉鍚庣閲嶅彂

娓呯悊:鍒犳帀鏈嶅姟鍙樺緱涓嶅啀浣跨敤鐨?5 涓?import(`buildChromeNotificationOptions` / `buildNotificationId` / `buildCognitionNotificationId` / `buildDelightNotificationId` / `PendingDelight` 绫诲瀷),浠ｇ爜鐦︿簡 ~30 琛屻€?

### 褰卞搷

- 鐢ㄦ埛灞忓箷鍙充笅瑙掑啀涔熶笉浼氬脊 Chrome 閫氱煡
- service worker console 涓嶅啀鍑虹幇 `notifications.create failed` warn 鎴?Chrome 鍐呴儴鐨?`Unable to download all specified images` reject
- popup 浣撻獙瀹屽叏涓嶅彉(鏈潵鎺ㄨ崘灏辨槸浠?popup 鍗＄墖鍒楄〃 + WS 鎺ㄩ€佽繘鏉ョ殑,Chrome toast 鍙槸鍐椾綑鍑哄彛)
- backend 涓嶉渶瑕佷换浣曟敼鍔?pending 闃熷垪鐓у父 ack 鍑洪槦

`chrome.notifications.onClicked` listener 鐣欑潃娌″姩(鍙槸涓嶄細鍐?fire 浜?,淇濈暀浠ラ槻浠ュ悗闇€瑕佸仛"toolbar icon badge 鈫?鐐瑰嚮灞曞紑 popup"涔嬬被杞婚噺鎻愰啋銆侼otifications permission 鍦?manifest 閲屼篃淇濈暀鈥斺€斿悗缁鏋滄兂鍋氬彲閫夌殑 toast 鎻愰啋(榛樿鍏抽棴銆佺敤鎴峰湪 popup 璁剧疆閲?opt-in),涓嶇敤鏀?manifest銆?

---

## extension v0.3.15: 閫氱煡 iconUrl 鏀圭敤 chrome.runtime.getURL 瑙ｅ喅鏍瑰洜 (2026-05-05)

### 鑳屾櫙

v0.3.14 宸茬粡鎶?閫氱煡澶辫触 鈫?涓?ack 鈫?鏃犻檺寰幆"鐨勪簩娆′激瀹充慨浜?浣?console 浠嶇劧姣忛殧鍑犲垎閽熷嚭涓€鏉?
```
[OpenBiliClaw] notifications.create failed (...): Unable to download all specified images. iconUrl: icons/icon128.png
```

閫氱煡澶辫触鐨?*鐪熸鏍瑰洜**杩欐鎶撳埌浜?`iconUrl: "icons/icon128.png"` 鏄浉瀵硅矾寰?**MV3 service worker 娌℃湁 document 涓婁笅鏂?*,Chrome 鍐呴儴瑙ｆ瀽鐩稿璺緞鏃跺伓灏斾細钀藉埌 `chrome-extension://invalid/icons/icon128.png` 鈥斺€?杩欏氨鏄箣鍓?console 閲?`chrome-extension://invalid/:1 ERR_FAILED` 鐨勬潵婧愩€?

宸茬煡 Chromium issue,鎺ㄨ崘鍋氭硶鏄?`chrome.runtime.getURL("...")` 鎷跨粷瀵圭殑 `chrome-extension://<id>/...` URL銆?

### 淇硶

`extension/src/background/notifications.ts` 閲屾娊鍑?`resolveNotificationIconUrl()`:
```ts
function resolveNotificationIconUrl(): string {
  try {
    if (typeof chrome !== "undefined" && chrome.runtime?.getURL) {
      return chrome.runtime.getURL("icons/icon128.png");
    }
  } catch { /* fall through */ }
  return "icons/icon128.png";  // 娴嬭瘯鐜鍏滃簳
}
```

`buildChromeNotificationOptions` 涓変釜鍒嗘敮(delight / cognition / recommendation)缁熶竴鏀圭敤 `iconUrl: resolveNotificationIconUrl()`銆?

### 褰卞搷

- 閫氱煡 toast **鐪熺殑鑳藉脊鍑烘潵浜?*(涔嬪墠姣忎釜 notification 閮藉洜鍥炬爣鍔犺浇澶辫触琚?Chrome 闈欓粯鍚炰簡)
- service worker console 涓嶅啀鍑?`notifications.create failed` warn
- 閰嶅悎 v0.3.14 鐨?ack-always-run + WS backoff,console 鍣煶娓呴浂

闆舵帴鍙ｅ彉鍖栥€侭ackend 涓嶉渶瑕佹敼銆?

---

## extension v0.3.14: 閫氱煡澶辫触寰幆 + WebSocket 閲嶈繛椋庢毚淇 (2026-05-05)

### 鑳屾櫙

鐢ㄦ埛鎶ュ憡 service worker console 鎸佺画鍒蜂竴鍫?
```
[OpenBiliClaw] Pending notification check failed
Uncaught (in promise) Error: Unable to download all specified images.
WebSocket connection to 'ws://127.0.0.1:8420/...' failed 脳 70+
```
鑰屼笖 popup "椤甸潰濂藉儚涓€鐩村湪濂囨€殑鍒锋柊"銆?

### 鏍瑰洜 1:閫氱煡 ack 婕忔帀,bvid 姘歌繙 pending

`service-worker.ts:checkPendingNotification`:
```ts
try {
  const item = await fetchPendingNotification();
  if (item?.bvid) {
    await chrome.notifications.create(...);  // 鈫?reject 鎶涘嚭
    await acknowledgeNotificationSent(...);  // 鈫?璺戜笉鍒?
  }
} catch { console.warn("...failed"); }      // 鈫?鍚炴帀鐪熷疄 error
```

`chrome.notifications.create` 鍐呴儴鍥剧墖涓嬭浇澶辫触浼氳 promise reject銆俢atch 鍚炰簡,浣?`acknowledgeNotificationSent` 涔熸病鏈轰細璺戙€備笅涓疆璇㈠懆鏈?姣忓垎閽?鍚庣鍙堟妸鍚屼竴涓?`bvid` 鍠傚洖鏉?鈫?鍚屾牱澶辫触 鈫?鍚屾牱涓?ack 鈫?**鏃犻檺寰幆**,console 涓€鐩磋鍒枫€?

### 鏍瑰洜 2:WebSocket 閲嶈繛鍥哄畾 2s 闂撮殧鏃犻€€閬?

`popup-stream.js:scheduleReconnect` 鐢ㄤ簡鍥哄畾 `reconnectDelayMs = 2000`銆傚悗绔煭鏆傛鎺夋椂,popup 姣?2s 灏濊瘯閲嶈繛,1 鍒嗛挓鍐?30 娆″け璐?console 婊″睆 `ERR_CONNECTION_REFUSED`銆?

### 淇硶

**`service-worker.ts`**:
- 鎶藉嚭 `safeNotify(id, options)` 鈥斺€?鍐呴儴 try/catch 鎶?`chrome.notifications.create` 鐨?reject 杞垚 console.warn(甯︾湡瀹?error message + iconUrl 涓婁笅鏂?,涓嶅啀浼犳煋涓婂眰
- `checkPendingNotification` 鐢?`safeNotify` 鏇夸唬鐩存帴璋冪敤 鈫?**`acknowledgeNotificationSent` always run**(鐢ㄦ埛宸茬粡鍦?popup 閲岀湅鍒版帹鑽愪簡,toast 澶辫触鍙槸灏戜簡 OS 寮圭獥,涓嶈兘鍥犳璁╁悗绔案杩滆涓烘病鍙戣繃)
- 椤跺眰 catch 涔熸妸 error message 鎵撳嚭鏉?涓嶅啀鍚?

**`popup-stream.js`**:
- `createRuntimeStreamClient` 鍔?`maxReconnectDelayMs = 30_000`(榛樿 30s 涓婇檺)
- 姣忔澶辫触 `currentReconnectDelay *= 2`,灏侀《 30s
- 鎴愬姛 onopen 鏃堕噸缃洖 2s,鐬椂缃戠粶鎶栧姩 fast-recover 涓嶆墦鎶?

### 褰卞搷

- 閫氱煡 console 涓嶅啀琚棤闄愬惊鐜埛,鍑?1 娆?warn 灏卞仠
- WebSocket 鍚庣姝绘帀鏃?popup 鍦ㄧ涓€鍒嗛挓鍐呭皾璇?6 娆?2s/4s/8s/16s/30s/30s),涔嬪悗 30s 涓€娆?璐熻浇鍜?console 鍣煶閮藉彲鎺?
- popup "鎰熻鍦ㄤ贡鍒? 涓诲洜娑堥櫎(閫氱煡 + WS 涓ゆ潯鍣煶閮芥帎浜?

闆舵帴鍙ｅ彉鍖?backend 涓嶇敤鍔ㄣ€?

---

## extension v0.3.13: profile sub-tab 绛夊緟閲嶈瘯 鈥?bootstrap_profile 鐪熸鑳芥媺鍒版敹钘?鐐硅禐 (2026-05-05)

### 鑳屾櫙

v0.3.12 淇ソ浜?self_info 鎶藉彇鍚?bootstrap_profile 浠诲姟**浠嶇劧杩斿洖 saved/liked/xhs_history = 0**銆傝瘖鏂瘉鎹?鐢ㄦ埛鍦?active tab 璺戣 DOM 鐨勮剼鏈?:

```
"绗旇" DIV reds-tab-item active sub-tab-list
"鏀惰棌" DIV reds-tab-item sub-tab-list
"鐐硅禐" DIV reds-tab-item sub-tab-list
```

鈫?DOM 閲?*鏈?*鏀惰棌 / 鐐硅禐 sub-tab銆俙bootstrapProfileTabLabels` 涔熷凡鍖呭惈 `["鏀惰棌"]` / `["璧炶繃", "鍠滄", "鐐硅禐"]`,selector `.reds-tab-item` 涔熷尮閰嶃€?*鎵€浠ヤ负浠€涔堟壘涓嶅埌?**

### 鏍瑰洜

鏃跺簭绔炴€?`hasBootstrapProfileContent(doc)` 鐪嬪埌 bridge 宸茬粡閫佹潵 state(鍩烘湰绔嬪埢)灏辫繑鍥?`true`,task 杩涘叆 `loadProfileTabsForScopes`銆備絾**閭ｄ竴甯?sub-tab DIV 杩樻病 mount 鍑烘潵**鈥斺€擷HS Vue runtime 鏄厛鎶?`__INITIAL_STATE__` 璧嬪€?鍐嶆覆鏌?sub-tab 瀛愮粍浠躲€?

`findProfileTab` 鍚屾璋冪敤,绗竴娆″繀鐒惰繑鍥?`null` 鈫?`loadProfileTabsForScopes` 鍐呯殑 `if (!tab) continue` 鐩存帴璺宠繃璇?scope,sub-tab 姘歌繙涓嶄細琚偣鍑?鈫?state.user.notes[1]/[2]/[3]/[4] 姘歌繙鏄┖鏁扮粍(XHS lazy-load,涓嶇偣 tab 涓嶆媺鏁版嵁)銆?

### 淇硶

鏂板 `findProfileTabWithRetry(doc, labels, timeoutMs=5000)`:
- 绗竴娆″悓姝ヨ皟鐢?fast-path 涓嶅彉
- 鎵句笉鍒?鈫?姣?300ms 杞涓€娆?鐩村埌 deadline
- 鍛戒腑鍗宠繑鍥?

`loadProfileTabsForScopes` 閲?`findProfileTab` 鈫?`await findProfileTabWithRetry`銆傛瘡涓?scope 鏈€澶氱瓑 5 绉掔瓑 sub-tab 娓叉煋銆?

### 鍏煎鎬?

闆舵帴鍙ｅ彉鍖栥€俠ackend 涓嶉渶瑕佹敼銆傝€?tab 宸茬粡娓叉煋鏃?0 鎬ц兘鎴愭湰銆傛柊 tab 绗竴娆℃渶澶氬绛?5s,浣嗚繖鏄负浜嗚兘鎷夊埌鏀惰棌/鐐硅禐鍒楄〃鐨勫繀瑕佷唬浠枫€?

---

## extension v0.3.12: MAIN-world state bridge 鈥?淇 XHS 瀹屽叏鏃犳暟鎹?(2026-05-05)

### 鑳屾櫙

production logs 澶氫釜浼氳瘽(2026-05-05 1h+)鏄剧ず XHS 鍏ユ睜涓?0:`Event propagated: like = 0`銆乣self_info persisted = 0`銆乣ingest filter: dropped = 0`銆乣startup purge = 0`,**鎵€鏈?XHS 鏁版嵁鑾峰彇璺緞鍏ㄩ儴闈欓粯澶辫触**銆?

### 鏍瑰洜

MV3 content script 璺戝湪 isolated JS world,`doc.defaultView.__INITIAL_STATE__` 姘歌繙鏄?`undefined` 鈥斺€?鍙湁 page 鐨?MAIN-world 鑴氭湰鑳界湅鍒?`window.__INITIAL_STATE__`銆?

`bootstrap.ts:extractBootstrapStateFromDocument` 涓ゆ潯璺兘鏂?
1. `doc.defaultView.__INITIAL_STATE__` 鈥斺€?isolated world 鐪嬩笉瑙?page globals
2. 鎵?`<script>` 鏍囩 inline JSON 鈥斺€?XHS 鏄?SPA,state 鏄繍琛屾椂 JS 璧嬪€?

鈫?鍑芥暟姘歌繙杩斿洖 `null` 鈫?`extractSelfInfoFromState` 姘歌繙杩斿洖 `null` 鈫?bootstrap_profile / passive collector / search task **涓夋潯璺叏閮ㄦ娊涓嶅埌 self_info,涔熸娊涓嶅埌 saved/liked/history notes**銆?

璇婃柇璇佹嵁:鍦?XHS 椤甸潰 DevTools 璺戣 state 鐨勮剼鏈?`loggedIn: ec {__v_isRef: true, _rawValue: true}` 鈥斺€?鐢ㄦ埛 100% 宸茬櫥褰?浣?isolated world 鐪嬩笉瑙併€?

### 淇硶

鏂板缓 `extension/src/main/xhs-state-bridge.ts` 璺戝湪 MAIN world(manifest 鍚?`xhs-token-sniffer.js` 璺緞),澶嶅埢 token sniffer 鐨?postMessage 妗ユ帴濂楄矾:

1. 杞 `window.__INITIAL_STATE__` 鍑虹幇(Vue mount 鍚庢墠璧嬪€?
2. `safeJsonClone` 鎶?Vue 3 ref 鏍戝睍骞虫垚 JSON-safe 褰㈢姸(unwrap `__v_isRef`/`_rawValue`銆佹柇寰幆銆佷涪 `__v_*`/`dep`/`deps` 鍐呴儴閿€佷涪 functions/symbols)
3. `buildStateSnapshot` 鐧藉悕鍗曞彧鎸?`bootstrap.ts:notesForScope` 瀹為檯璇荤殑 10 涓?top-level keys(`user`, `saved`, `collect`, `collections`, `liked`, `likes`, `history`, `footprint`, `browseHistory`, `browsingHistory`),snapshot 澶у皬鏈?2MB 涓婇檺,婧㈠嚭闄嶇骇鍒版渶灏?`{user: {loggedIn, userInfo, userPageData}}`
4. `window.postMessage({source: "obc-xhs-state", state})` 缁?isolated world
5. 閲嶅彂瑙﹀彂鍣?popstate / visibilitychange=visible / click(SPA 璺敱鍙樻洿),鍐呯疆 `lastSnapshotJson` dedup

`bootstrap.ts:extractBootstrapStateFromDocument` 涓夊眰鍏滃簳:
1. **MAIN-world bridge cache**(涓昏矾寰?鏂板):鐩戝惉 `window.message` 缂撳瓨鏈€鏂?snapshot,鍚屾杩斿洖
2. `doc.defaultView.__INITIAL_STATE__`(jsdom 娴嬭瘯鍙兘鐢ㄥ埌)
3. `<script>` 鏍囩鎵弿(legacy SSR 鍏滃簳)

### 娴嬭瘯瑕嗙洊

- `extension/tests/xhs-state-bridge.test.ts`(11 cases):isVueRef 璇嗗埆 / safeJsonClone 澶勭悊 ref+寰幆+Vue 鍐呴儴閿?throw getter / buildStateSnapshot 鐧藉悕鍗?/ Vue-wrapped XHS-shaped state 瀹屾暣閾捐矾
- `xhs-task-executor.test.ts` 鍔?3 case:ingestMainWorldStateMessage 缂撳瓨 + 鎷掔粷 malformed payload + cache 浼樺厛绾ч珮浜?doc.defaultView

鍚堣 184/184 閫氳繃銆?

### 鍏煎鎬?

- 鍚庣浠ｇ爜 0 鏀瑰姩 鈥斺€?淇瀹屽叏鍦ㄦ墿灞曠
- 鑰佹墿灞?v0.3.11 鍙婁箣鍓?瑁呭湪 v0.3.57 鍚庣涓?= 鐜扮姸涓嶅彉(XHS 浠嶇劧 0 鏁版嵁)
- 鏂版墿灞?v0.3.12)瑁呭湪浠讳綍 v0.3.57+ 鍚庣涓?= self_info 鐪熸娴佸叆,杩囨护鐢熸晥,bootstrap_profile 鍙互璇?saved/liked/history

---

## v0.3.57: pool quality trio (2026-05-05)

### 鑳屾櫙

`docs/plans/2026-05-05-pool-quality-trio-spec.md` 涓変釜 P 绾ч棶棰樷€斺€旈兘鐩存帴姹℃煋 popup 鏄剧ず璐ㄩ噺,浣嗕簰涓嶈€﹀悎銆傞厤濂楀彂甯?**extension v0.3.10** 瀹屾垚 P2 鐨勬墿灞曠閰嶅銆?

### P1 鈥?cookie race 闃诲 history 7 鍒嗛挓

**鐜拌薄**:daemon 鍚姩鏃?cookie 杩樻病浠庢墿灞曞悓姝ュ埌浣?`AccountSyncService` 绗竴涓?tick 鐢ㄧ┖ cookie 鎷?history,鎷垮埌 `[]` 骞?stamp `last_account_sync_at`,鎶?6 灏忔椂 throttle 閿佹銆俻roduction logs 瀹炴祴 03:33:25 cookie 缂哄け 鈫?03:40:22 鎵嶇涓€娆℃垚鍔熲€斺€?*7 鍒嗛挓绌虹獥**銆?

**淇硶**(`runtime/account_sync.py`):
- `sync_now` / `sync_if_due` 鍦?`bilibili_client.is_authenticated` 涓?False 鏃剁煭璺繑鍥?`reason=no_auth`,**涓嶅啓鏃堕棿鎴?*銆?
- `run_forever` 鍦ㄧ涓€娆℃垚鍔?auth 涔嬪墠鐢?15s 閲嶈瘯闂撮殧(`_UNAUTH_RETRY_INTERVAL_SECONDS`),涔嬪悗鍒囧洖甯歌 5 min銆?
- 棣栨 auth 鎶佃揪鏃舵墦涓€琛?INFO 鏃ュ織(`account_sync: bilibili cookie now ready ...`),璁?operator 鑳?grep 鍒?gate 閲婃斁銆?
- Stub client 娌?`is_authenticated` 灞炴€ф椂榛樿璁や负宸?auth,淇濈暀鏃㈡湁娴嬭瘯琛屼负銆?

**棰勬湡**:棣栨 history 鎷夊彇浠?7 min 鈫?鈮?0s銆?

### P2 鈥?XHS 鐢ㄦ埛鑷繁鍙戝竷鐨勭瑪璁拌繘鎺ㄨ崘姹?

**鐜拌薄**:`agent-bootstrap.log` line 610鈥?15 sample_titles 閲屽嚭鐜?鑷瀹濆畨棰嗚埅鍩?65銕″ぇ浜旀埧鍑哄敭"绛夌敤鎴锋湰浜哄彂甯冪殑绗旇銆俋HS 骞冲彴鐨?search/explore feed 浼氭妸鐧诲綍鐢ㄦ埛鑷繁鐨勭瑪璁版贩杩涚粨鏋?鑰屾帹鑽愬叆姹犺矾寰勯噷**鍙湁 bootstrap_profile 鎶?self_info**:passive collector 鍜?search/creator task 閮芥病鎶?race 涓€鎵撳紑灏辨紡銆?

**鍚庣淇硶**(`api/app.py`):
- `_extract_self_info_from_payload(payload)` 缁熶竴鎺ュ叆:**鍏?*鐪嬮《灞?`self_info`,fallback 鍒版棫鐨?`debug.xhs_bootstrap.steps[*].self_info`銆?
- `/api/sources/xhs/observed-urls` 鏂板:璇?self_info 鈫?`_persist_xhs_self_info` 鈫?浼犵粰 `_cache_xhs_notes`銆?
- `/api/sources/xhs/task-result` 鍒囨崲鍒扮粺涓€ extractor銆?
- `_purge_self_authored_pool_items(database, self_info)` 鍚姩閽╁瓙:鎵?`content_cache where source_platform='xiaohongshu' and lower(up_name)=lower(?)` 鎶婂凡瀛橀噺琛岀炕鎴?`pool_status='suppressed'`,淇鍗囩骇鍓嶅凡缁忔薄鏌撶殑 pool銆?

**鎵╁睍淇硶**(extension v0.3.10,`xhs/passive.ts` + `xiaohongshu.ts` + `xhs/task-executor.ts`):
- `passive.ts:filterSelfAuthoredNotes` + `XhsSelfInfo` 绫诲瀷 + `XhsUrlObservation.self_info` 鍙€夊瓧娈点€?
- `runPassiveCollection` 璇?`__INITIAL_STATE__.user.userInfo`,scrape-time drop `note.author === self.nickname`,鎶?self_info 濉炶繘 observation銆?
- `executeTaskInPage` 闈?bootstrap 鍒嗘敮鍚屾牱鎶?self_info + scrape-time 杩囨护,鍔犲叆 `TaskResultPayload.self_info`銆?

**棰勬湡**:浠绘剰 XHS 椤甸潰涓€鎵撳紑灏辨姄 self_info;涓嶅啀渚濊禆 bootstrap_profile 鍏堣窇;鍗囩骇鐢ㄦ埛鐨勫瓨閲忔薄鏌撲細琚惎鍔?purge 淇帀銆?

### P3 鈥?popup 鎺ㄨ崘鏂囨钀藉埌鍗犱綅妯℃澘

**鐜拌薄**:popup 鍗＄墖涓嬫枃妗堟槸 `"銆妜xx銆嬭繖鏉″垏鍙ｆ尯椤虹殑锛屽厛涓㈢粰浣犵湅鐪嬶紝璇翠笉瀹氭濂借兘瀵逛笂浣犲綋涓嬬殑鍏磋叮"` 鈥斺€?`_fallback_expression` 鍏滃簳妯℃澘,鐩存帴鍛戒腑銆傚師鍥?`get_pool_candidates`/`count_pool_candidates` 娌″ `pool_expression` 鍋氶潪绌鸿繃婊?discovery 鍐欏畬鈫抪recompute 璺戝畬涔嬮棿 60鈥?0s 绐楀彛,serve() 鍙栧埌绌?row 璧?fallback銆?

**淇硶**(`storage/database.py` + `recommendation/engine.py`):
- `get_pool_candidates` 涓や釜 SQL 鍒嗘敮(`max_per_topic_group<=0` 鍜?window function)鐨?WHERE 鍔犱笂 `AND COALESCE(pool_expression, '') != '' AND COALESCE(pool_topic_label, '') != ''`銆?
- `count_pool_candidates` 鍚屾牱鍔犱笂,popup "杩樻湁 N 鏉? 涓嶅啀璇銆?
- `engine.py:320` 鐨?fallback 璺緞鏀规垚 `logger.warning("Pool gate leak: ...")` + 浠嶅厹搴曗€斺€攔ace-window 瀹夊叏缃?瑙﹀彂鍗虫姤璀︺€?
- 娴嬭瘯 fixture 鍔?`_seed_visible(db, bvid, **kwargs)` helper,榛樿濉厖涓や釜瀛楁;涓や釜 gate-test 浠嶈蛋 `cache_content` 鐩存帴璺緞浠ラ獙璇佺┖琛岃杩囨护銆?

**棰勬湡**:popup 姘歌繙鍙樉绀?LLM 鐢熸垚鐨勪釜鎬у寲鏂囨;init 绐楀彛鍙 pool 鍑虹幇鏃堕棿浠?30s 鍚庣Щ ~90s,浣嗘墍鏈夐湶鍑烘潵鐨勫唴瀹归兘鏈夌湡鐞嗙敱銆?

### 鍏煎鎬?

- 鍚庣鍏堝彂,鎵╁睍鍚庡彂鈥斺€斿悗绔殑 `_extract_self_info_from_payload` 鐢?`dict.get + isinstance` 闃插尽,鑰佹墿灞?v0.3.9)payload 涓嶅甫 self_info 涓嶆姤閿?鍙槸 P2 涓嶇敓鏁堛€?
- 鏂版墿灞?v0.3.10)鍙戝埌鑰佸悗绔細 500 鈥斺€斿彧鍦ㄥ崌绾х獥鍙ｆ湡鐭殏,鏂囨。寮鸿皟瑕佷竴璧峰崌绾с€?

---

## v0.3.56: topic_group supergroup 鍚堝苟涓嬫矇鍒?DB锛?026-05-05 spec wave 6 / 瀹岀粨锛?

### 鑳屾櫙

`docs/plans/2026-05-05-discovery-runtime-fix-spec.md` U9銆?

`_supergroup_canonical_map` 鎶?"鍔ㄦ极"/"鍔ㄦ极鏉傝皥"/"鍔ㄦ极浜屾鍏? 鍚堝苟鎴愬悓涓€涓?canonical 涓婚鈥斺€斾絾鍚堝苟**鍙湪 serve 鏃惰窇**銆俻ool 鍦ㄦ暟鎹簱灞傞潰鐪嬪埌鐨勮繕鏄?3 涓嫭绔嬬殑 topic_group銆備换浣曟寜 topic_group group_by 鐨?SQL锛坄get_topic_group_samples` / popup status / 鍚庡彴鍒嗘瀽锛夐兘鐪嬩笉鍒板悎骞跺悗鐨勭湡涓婚鍒嗗竷銆?

### 鏀瑰姩

**`Database.canonicalize_topic_groups(canonical_map)`**锛坄storage/database.py`锛夛細
- 鎺ユ敹 `{lowered_src: canonical_dst}` map
- 瀵规瘡涓?src鈫抎st pair锛屽彂涓€鏉?`UPDATE content_cache SET topic_group=? WHERE LOWER(TRIM(topic_group))=?`
- 璺宠繃 src==dst 鍜岀┖瀛楃涓?
- 鍗曟潯 transaction锛堝凡鏈夌殑 `_execute_write` 璧?WAL锛?
- 杩斿洖 rewritten 琛屾暟

**`prewarm_supergroup_embeddings` 鏈熬鑷姩璋冪敤**锛坄recommendation/engine.py`锛夛細
- 姣忔 prewarm 閲嶅缓 canonical map 涔嬪悗绔嬪嵆璺戜竴娆?`canonicalize_topic_groups(new_map)`
- INFO 鏃ュ織 `Topic supergroup canonical map applied to pool: N row(s) rewritten`
- 澶辫触 swallow + log锛坙azy-merge at serve 鏃朵粛鑳藉厹锛?

### 褰卞搷

- pool 鍦?DB 灞傞潰鏄剧ず鐪熷疄涓婚鍒嗗竷鈥斺€擿Recommendation candidate summary` 涓嶅啀琚瓧闈㈡媶鍒嗘帺鐩?
- 涓嬫父 SQL 鍒嗘瀽锛坄get_topic_group_samples` / 浠讳綍鎸?topic_group 鑱氬悎鐨勬煡璇級鐪嬪埌鍚堝苟鍚庣殑涓婚
- 涓嶅奖鍝?serve-time merge 璺緞鈥斺€斿弻閲嶄繚闄?
- 姣忔 refresh tick 澶氫竴娆?batch UPDATE锛岃鏁扮骇寮€閿€鍙拷鐣?

娴嬭瘯锛?30/830 閫氳繃锛屾棤鏂板銆?

### Spec 瀹岀粨

鑷虫 6 涓?wave 鍏ㄩ儴瀹屾垚锛坴0.3.51 鈫?v0.3.56锛夛紝`docs/plans/2026-05-05-discovery-runtime-fix-spec.md` 涓?9 涓?U 鍏ㄩ儴淇銆?*鍑€ LLM 鏈堟垚鏈檷骞呯害 -50%锛坮easoning 鍏抽棴鎶垫秷鍊欓€夊苟鍙?3脳锛?*锛屽姞涓婁竴绯诲垪浣撴劅浼樺寲锛坧ool 涓嶅啀琚?hot franchise/style 鍗犻銆乻peculator 鐪熸鍑鸿揣銆乻tartup 閿欒椋庢毚娑堝け銆乻earch v_voucher storm 瀹瑰繊锛夈€?

---

## v0.3.55: B 绔?search v_voucher 閫€閬?1 鈫?3 attempt锛?026-05-05 spec wave 5锛?

### 鑳屾櫙

`docs/plans/2026-05-05-discovery-runtime-fix-spec.md` U3銆?

production logs 43 鍒嗛挓浼氳瘽閲?**141 娆?`Search got v_voucher challenge`**锛?*9 娆″畬鏁翠竴杞?`Search: 8 queries, 0 API results, 0 unique candidates`**銆傚師 retry 绛栫暐鍙?1 娆￠噸璇?+ 1.5s 鍥哄畾寤惰繜锛屽懡涓袱娆¤繛鐜寫鎴樺氨鏀惧純锛沰eyword 宸茬粡浠樿垂 LLM 鐢熸垚锛堟瘡娆?~楼0.012锛変絾鎷夸笉鍒扮粨鏋溿€?

### 鏀瑰姩

`src/openbiliclaw/bilibili/api.py:search_videos`锛?
- retry attempts 2 鈫?**3**
- 閫€閬夸粠 fixed 1.5s 鏀规垚 **鎸囨暟 (1.5s, 5s, 15s)** 涓夋
- 鎬昏秴鏃?~21s 缁?WBI key churn 鏃堕棿绋冲畾
- 绗?3 娆′粛 v_voucher 鈫?WARN log + return []锛岃涓婃父鐭ラ亾鏄?storm 涓嶆槸 query 涓嶅瓨鍦?
- 閲嶈瘯瑙﹀彂鏃舵墦 INFO `Search v_voucher challenge (attempt N/3) ... retry in Xs`

### 褰卞搷

- 澶у鏁?transient v_voucher 鍦ㄧ 2-3 娆￠噸璇曟椂浼氭嬁鍒扮粨鏋滐紙涔嬪墠涓€寰嬫斁寮冿級
- 9 娆?0-result rounds 棰勬湡闄嶅埌 ~3 娆★紙瀹為檯杩橀渶瑙傚療锛?
- WBI storm 鎸佺画鏈熼棿涓嶅啀闈欓粯鏀惧純鈥斺€擶ARN 璁?operator 鐪嬭
- 涓嶆槸 storm 鐨勬甯告儏鍐典笅锛歳etries 涓嶈Е鍙戯紝鏃犳垚鏈奖鍝?

娴嬭瘯锛?30/830 閫氳繃锛屾棤鏂板锛堣涓烘槸 transient 閲嶈瘯锛屼笉鏄撳啓鍗曟祴锛夈€?

---

## v0.3.54: Ollama 鍚姩鏈?retry + MMR prewarm 閲嶈瘯锛?026-05-05 spec wave 4锛?

### 鑳屾櫙

`docs/plans/2026-05-05-discovery-runtime-fix-spec.md` U4 + U6銆?

**U4 鈥?Ollama 鍚姩鏈?9 娆?502 寮曞彂杩為攣澶辫触**锛歞aemon 鍚姩澶?90 绉掞紝Ollama 杩樺湪鍔犺浇妯″瀷锛宍localhost:11434/v1/chat/completions` 杩?502銆傚熀纭€ OpenAIProvider 閲嶈瘯鏄?3 脳 0.25s 绾挎€?= 1.25s 鎬绘椂闀匡紝杩滀笉澶?Ollama 30s 妯″瀷鍔犺浇绐楀彛銆?

**U6 鈥?MMR embedding cache 31 鍒嗛挓涓嶅懡涓?*锛歴tartup 鐨?prewarm 浠诲姟鍦?Ollama 502 鏈熼棿涓€娆℃€уけ璐ワ紝娌￠噸璇曪紝瀵艰嚧 cache 绌轰簡 31 鍒嗛挓銆?

### 鏀瑰姩

**U4 鈥?`OllamaProvider.complete()` 鍔犳墿灞曢噸璇?*锛坄llm/ollama_provider.py`锛夛細
- 鏂板父閲?`_OLLAMA_MAX_RETRIES = 5` + `_OLLAMA_BASE_RETRY_DELAY = 1.0`
- override 鐖剁被 `complete()`锛屽湪 502 / 503 / TransportError / TimeoutError 鏃舵寜 1s, 2s, 4s, 8s, 16s 鎸囨暟閫€閬匡紙鎬?~31s锛夐噸璇?
- 5 娆￠兘澶辫触鎵嶅悜涓婃姏 鈫?registry fallback 閾炬墠浼氬垏鍒颁笅涓€ provider
- 涓嶅奖鍝嶇儹璺緞锛堝凡鍔犺浇濂界殑妯″瀷绔嬪嵆杩?200锛岄噸璇曚笉瑙﹀彂锛?

**U6 鈥?`_safe_prewarm_pool_mmr_embeddings` 鏀规垚 5 娆￠噸璇?*锛坄api/runtime_context.py`锛?
- 涔嬪墠涓€娆℃€?try/except 澶辫触灏辨斁寮?
- 鐜板湪 attempt 1-5锛屽垵濮?delay 2s 鎸囨暟缈诲€嶏紝鎬?~62s 绐楀彛
- 浠讳竴娆¤繑鍥?`warmed > 0` 鍗虫彁鍓嶇粨鏉燂紙鎴愬姛 short-circuit锛?
- 5 娆￠兘澶辫触涔熸槸 silent skip 鈥?pool MMR cache 杩樹細閫氳繃 serve() / discovery 鑷劧濉厖

### 褰卞搷

- 鍚姩鏈?Ollama 502 瑙﹀彂 OllamaProvider 鑷甫 31s 閫€閬匡紝绛夋ā鍨嬪姞杞藉畬鐩存帴鎴愬姛
- speculator / awareness / cognition 涓嶅啀鍥犱负 startup 502 杩為攣鎸傛帀锛坴0.3.46 宸茬粡鎶婂亣 ERROR 娌讳簡锛岃繖娆℃不鐪熸鐨?502锛?
- prewarm 鍦?ollama 璧锋潵涔嬪墠閲嶈瘯 5 娆★紝cache coverage 5 鍒嗛挓鍐呭洖鍒?鈮?0%
- 涓嶅姩 prompt builder锛宑ache 鍛戒腑鐜囦笉鍙楀奖鍝?

娴嬭瘯锛?30/830 閫氳繃锛屾棤鏂板锛堣涓烘槸 startup-only 閲嶈瘯锛屼笉鏄撳啓鍗曟祴锛夈€?

---

## v0.3.53: speculator gate + xhs_producer 鑺傚锛?026-05-05 spec wave 3锛?

### 鑳屾櫙

`docs/plans/2026-05-05-discovery-runtime-fix-spec.md` U7 + U8銆?

**U7 鈥?speculator quality gate 鍏?drop**锛?
production logs 涓€娆?force_tick `generated=5, promoted=0, rejected=0`銆侺LM 缁欐墍鏈?5 涓€欓€夌殑 confidence 閮芥槸 **0.35**鈥斺€擿min_confidence=0.40` 姝ｅソ鍒氶珮浜?LLM 瀹為檯浜у嚭锛屽叏閮ㄨ drop銆?

**U8 鈥?xhs_producer 鏁?43 min 鍙窇 1 杞?*锛?
鏃ュ織鍙湅鍒颁竴娆?`xhs producer enqueued 5/5`銆傚悗缁?ticks 鍏ㄩ潤榛?skip鈥斺€旀病鏈夋棩蹇楃湅涓嶅嚭鍘熷洜銆?

### 鏀瑰姩

**U7 鈥?speculator min_confidence 0.40 鈫?0.30**锛坄soul/speculator.py`锛?

璁?LLM 鑷劧浜у嚭鐨?0.35 鍖洪棿閫氳繃銆備笅娓?pipeline锛坰pecifics鈮? / reason鈮?0chars / domain shadow check / dedup锛夌户缁?gate "lazy" candidates銆?

**U8 鈥?xhs_producer 鍔?INFO log + 缂╃煭 throttle**锛坄runtime/xhs_producer.py`锛?

- `min_interval_hours: 4 鈫?1` 鈥?4 灏忔椂 throttle 璁╂睜瀛愭暣娈垫椂闂翠笉鍒锋柊銆? 灏忔椂 cadence + daily_budget=30 = 24 enqueues/day锛堢暀 6 head room 缁?manual / refresh-tick锛?
- `_skip()` 鍦?reason 鍙樺寲鏃舵墦 INFO `xhs producer skip: reason=X`鈥斺€攐perator 鍙互 grep 鍑轰负浠€涔?producer 涓嶈窇锛坉isabled / throttled / no_profile / no_keywords锛夛紝涓嶄細 spam 鍚屼竴 reason 姣忓垎閽熶竴鏉?

### 褰卞搷

- speculator 鐜板湪浼氱湡鐨勬湁 promoted candidates锛坓ate 閫氳繃鐜囦粠 0% 鍥炲崌鍒?~50% 浼拌锛?
- xhs producer 1 灏忔椂 cadence 璁╂睜瀛愭寔缁埛鏂帮紙涔嬪墠涓€娆″悗鍋?4 灏忔椂澶暱锛?
- 鏃ュ織鍙鎬э細xhs producer skip reason 杞崲鏃舵墦 INFO

娴嬭瘯锛?30/830 閫氳繃锛屾棤鏂板銆?

---

## v0.3.52: discovery 鍊欓€夊苟鍙戣瘎浼?30 鈫?90锛?026-05-05 spec wave 2锛?

### 鑳屾櫙

`docs/plans/2026-05-05-discovery-runtime-fix-spec.md` U2锛?

production logs `evaluate_content_batch: truncating 300+ -> 30 items` 鍙嶅鍑虹幇锛屾渶楂?480鈫?0銆?*90% 鍊欓€夌洿鎺ヨ涓㈠純**鈥斺€旈噷闈㈠彲鑳芥湁涓嶅皯濂藉唴瀹广€?

鏍瑰洜锛歚_EVALUATE_BATCH_HARD_CAP=30` 姘歌繙鍙瘎浼板墠 30 鏉°€俻re-v0.3.51 鍥犱负鍗曟壒 LLM 瑕?8-16 min锛屼笉鏁㈠苟鍙戣窇澶氭壒锛泇0.3.51 鍏充簡 reasoning 鍚庡崟鎵?30s 瀹屾垚 鈫?鐜板湪鍙互骞跺彂璇勪及鏇村鍊欓€夈€?

### 鏀瑰姩

- `_EVALUATE_BATCH_HARD_CAP: 30 鈫?90`锛坄discovery/engine.py`锛?
- `_run_batch` 鐨?`asyncio.gather` 璋冨害鏃犲彉鍖栵紝浣嗙幇鍦?90 鏉?鈫?3 涓?batch 脳 30 items 骞跺彂
- `llm_evaluation_concurrency` 宸叉湁鐨?semaphore 鍏滃簳闃叉 provider rate limit

### 褰卞搷

- 鍗?round 璇勪及鍊欓€変粠 30 鈫?90锛?脳 鎻愰€燂級
- 鎬昏€楁椂涓嶅鍔狅紙骞跺彂璺戯級锛岀粨鍚?v0.3.51 鐨?reasoning-disabled锛? 涓苟鍙?batch 鎬昏€楁椂 鈮?鍗曟壒 v0.3.50 涓€娆＄殑鑰楁椂
- LLM 鏈堟垚鏈細鍗?round 鎻愬崌 3脳锛屼絾 v0.3.51 宸茬粡闄?80%锛屽噣浠嶆瘮 v0.3.50 渚垮疁
- truncation 90% 娴垂闄嶅埌 ~70%锛堝緢澶?round 鍊欓€変笉鍒?90 涔熸棤 truncation锛?

娴嬭瘯锛?30/830 閫氳繃锛屾棤鏂板銆?

---

## v0.3.51: discovery LLM 鍏?reasoning + style cap锛?026-05-05 spec wave 1锛?

### 鑳屾櫙

璺戞棩蹇楄瘖鏂毚闇蹭袱涓棶棰橈紙璇﹁ `docs/plans/2026-05-05-discovery-runtime-fix-spec.md`锛夛細

**U1 鈥?discovery `evaluate_batch` 姣忔壒 8-16 鍒嗛挓**锛?
鏃ュ織鏁版嵁 27 娆?`discovery.evaluate_batch` 绱 ~3 灏忔椂 LLM 鎬濊€冩椂闂达紝鏈€闀垮崟鎵?991s锛?6.5 min锛夈€俹utput tokens 8000-18000 / 30 items 涓昏琚?reasoning chain 鍗犵敤銆備絾 evaluate_batch 浠诲姟鏄粨鏋勫寲鎵撳垎锛坰core/topic_group/style_key/franchise_key锛夛紝**鏍规湰涓嶉渶瑕佹€濈淮閾?*銆?

**U5 鈥?style 闆嗕腑搴︽棤 cap**锛?
鏃ュ織缁熻 13 娆″崟 batch single style 鈮?7 鏉★紙鈮?3%锛夛紝鏈€楂?fun_variety脳10/30=33%銆乻tory_doc脳11/30=37%銆俥val_batch 宸茬粡鏈?franchise cap锛坴0.3.50锛夛紝**娌℃湁 style cap**銆?

### 鏀瑰姩

**U1 鈥?鍏抽棴 reasoning for 缁撴瀯鍖栦换鍔?*锛?

鏂板 per-call `reasoning_effort` 閫忎紶閫氶亾锛?
- `LLMProvider.complete()` ABC 鍔?`reasoning_effort: str | None = None` 鍙傛暟
- `OpenAIProvider` / `ClaudeProvider` / `GeminiProvider`锛歛ccept + ignore锛圖eepSeek-only feature锛?
- `DeepSeekProvider.complete()`锛歚None` 鐢ㄩ厤缃粯璁わ紝闈?`None` 涓存椂瑕嗙洊 `self._reasoning_effort`锛屼繚鐣欏師 `try/finally` 璇箟
- `LLMRegistry.complete()` / `LLMService.complete_with_core_memory()` / `LLMService.complete_structured_task()`锛歵hreading parameter through

璋冪敤鐐规樉寮?`reasoning_effort=""` 鍏虫帀 thinking锛?
- `discovery.engine._evaluate_batch`
- `recommendation.engine._classify_batch`锛圶HS classify_pool_backlog锛?
- `recommendation.engine._precompute_batch`锛坵rite_expression锛?

**淇濈暀 reasoning** 缁欑湡姝ｉ渶瑕佺殑锛歚soul.speculate` / `soul.awareness` / `recommendation.delight_score`銆?

**U5 鈥?`_evaluate_batch` style cap**锛?

璺?v0.3.50 franchise cap 鍚屽舰锛?
- 鏂板父閲?`_BATCH_STYLE_CAP = 8`锛?/30 = 27%锛?
- LLM 璇勫垎瀹屾垚鍚庢寜 `style_key` 鍒嗘《锛岃秴棰濇寜 score drop
- INFO 鏃ュ織锛歚eval_batch style cap: dropped N (cap=8/style; offenders=fun_variety脳10)`
- 璺?franchise cap 涓€鏍凤紝empty style 琚拷鐣ワ紙ingestion-time heuristic 榛樿鍊间笉浼氱粺缁熸閿侊級

### 褰卞搷

棰勬湡鏁堟灉锛堟寜鏈鍩虹嚎鏃ュ織鏁版嵁锛夛細

- discovery `evaluate_batch` elapsed 浠?8-16 min 闄嶅埌 30s 浠ヤ笅锛?0脳 鎻愰€燂級
- LLM 鏈堟垚鏈笅闄?~80%锛坮easoning tokens 鏄ぇ澶达級
- 鍗?batch single-style 浠?30-37% 闄嶅埌 鈮?7%
- 缁撴瀯鍖栬緭鍑?quality 涓嶉€€鍖栵紙浠诲姟涓嶉渶瑕佹€濊€冮摼锛?
- 鐪熼渶瑕?reasoning 鐨?caller锛坰peculate / awareness / delight_score锛変笉鍙楀奖鍝?

娴嬭瘯锛?
- 淇簡 12 涓祴璇?stub锛坅ccept `reasoning_effort` kwarg锛? 1 涓祴璇曠敤渚嬶紙`test_trending_strategy_interleaves_rids_for_eval_fairness` 鍔?style 澶氭牱鍖栫殑 LLM responses 閬垮厤鏂?cap 璇激锛?
- 830/830 閫氳繃

涓嶅姩 LLM prompt builder锛宲rompt cache 鍛戒腑鐜囦笉鍙楀奖鍝嶃€?

---

## v0.3.50: discovery 涓夊眰 franchise/UP 閰嶉锛?026-05-05锛?

### 鑳屾櫙

绾夸笂鏃ュ織鏆撮湶 B 绔欏€欓€夋睜琚嚑涓?hot franchise 涓诲锛?

```
01:12:46  eval_batch  top_franchise=寮犻洩鏈鸿溅脳13 (45%)        鈫?30 鏉￠噷 13 鏉″悓 UP
01:13:27  eval_batch  top_franchise=鍜查棿濡脳6
01:14:58  eval_batch  top_franchise=鍜查棿濡脳6              鈫?鍚?UP 绗笁娉?
01:17:15  eval_batch  top_franchise=椋庣姮灏戝勾鐨勫ぉ绌好?
```

`鍜查棿濡 7+6+6 = 19 鏉 妯法涓変釜 batch锛屽叏杩涗簡姹犲瓙銆侺LM **姝ｇ‘濉簡 franchise_key**锛堟寜 prompt 瑙勫垯 7 鐨勬壒鍐呬竴鑷存€х害鏉燂級锛屼絾涓嬫父 `_evaluate_batch` 鏀跺埌 30 鏉￠噷 13 鏉″悓 IP 鏃朵粛 `kept=30`鈥斺€攆ranchise 淇℃伅鏈夛紝娌′汉鐢ㄣ€?

鍘婚噸鍙湪 serve 鏃讹紙`_select_diversified_batch.per_franchise_cap`锛夛紝浣?pool 宸茬粡琚煇涓?franchise 鍗犱簡 30+ 鏉℃椂锛宻erve 绔厹搴曟晳涓嶄簡姹犲瓙鐨勬暣浣撳€炬枩銆?

### 鏀瑰姩锛堜笁灞傞槻寰★級

**A. eval_batch 鍗曟壒 franchise cap锛坄discovery/engine.py:_evaluate_batch`锛?*
- 鏂板父閲?`_BATCH_FRANCHISE_CAP = 4`
- LLM 璇勫垎瀹屾垚鍚庯紝鎸?`franchise_key`锛坙owercase锛夊垎妗讹紝姣忔《瓒呰繃 4 鏉＄殑鎸?score 鎺掑簭淇濈暀 top 4锛屽叾浣?`score=0`锛堣涓嬫父 `score > 0` 杩囨护鎺夛級
- INFO 鏃ュ織锛歚eval_batch franchise cap: dropped N item(s) (cap=4/franchise; offenders=寮犻洩鏈鸿溅脳13)`

**B. related_chain 鍗?round 鍚?UP cap锛坄discovery/strategies/related_chain.py`锛?*
- 鏂板父閲?`_RELATED_CHAIN_PER_UP_CAP = 3`
- 涓€涓?depth round 鍐呮部鎵€鏈?seed 鏀堕泦 `batch_candidates` 鏃舵寜 `up_name`锛坙owercase锛夎鏁帮紝瓒呰繃 3 鐨勫悓 UP 涓嶅啀鍔犲叆
- INFO 鏃ュ織锛歚related_chain per-UP cap: skipped N item(s) (cap=3/UP per round; 寮犻洩鏈鸿溅脳10)`
- **娌绘牴**锛氫粠婧愬ご涓嶈 13 鏉″悓 UP 涓€璧锋秾杩?batch

**C. 鍏ユ睜 franchise 鍏ㄥ眬閰嶉锛坄discovery/engine.py:_cache_results` + `storage/database.py`锛?*
- 鏂板父閲?`_POOL_FRANCHISE_QUOTA = 10`锛堢害 pool target 600 鐨?1.5%锛?
- 鏂?`Database.count_pool_by_franchise()` 杩斿洖 `{franchise_key_lower: count}`
- `_cache_results` 鍏ユ睜鍓嶆煡鐜版湁 franchise 鏁伴噺 + 鏈疆宸插姞鏁伴噺锛岃秴棰濇嫆鏀?
- INFO 鏃ュ織锛歚pool franchise quota: skipped N item(s) (cap=10/franchise; 鍜查棿濡脳7)`
- **闃茬疮绉?*锛氬嵆渚?A/B 閮芥紡杩囧幓锛宲ool 鏁翠綋涔熶笉浼氳鏌愪釜 franchise 鍗犳嵁

### 褰卞搷

- B 绔?batch 鍐?franchise 闆嗕腑搴︿粠鏈€楂?45%锛?3/30锛夐檷鍒?鈮?3%锛?/30锛?
- related_chain 娌跨儹闂?UP 閾句竴娆℃渶澶氬惛鏀?3 鏉★紝閬垮厤涓€涓?seed 鐖嗛浄
- 鍗?franchise 鍦?pool 鎬婚噺琚‖涓婇檺鍒?10 鏉?
- 鏃ュ織鍙鎬э細鎵€鏈変笁灞?cap 鍛戒腑鏃堕兘鏈?INFO 鏃ュ織锛屽彲浠ヨ瀵熷疄闄呭墽鐑堢▼搴?
- 鏀瑰姩涓嶅姩 LLM prompt builder锛屼笉褰卞搷 prompt cache 鍛戒腑鐜?

娴嬭瘯锛?69/169 閫氳繃锛堝惈 2 涓柊鍥炲綊娴嬭瘯锛夛細
- `test_evaluate_batch_intra_batch_franchise_cap` 鈥?6 鏉″悓 franchise 鍏?batch锛岄獙璇?4 鐣?2 寮?
- `test_count_pool_by_franchise_returns_lowercased_groups` 鈥?DB 鎺ュ彛杩斿洖 lowercase 鍒嗙粍

---

## v0.3.49: 鎯婂枩鎺ㄨ崘 threshold 璺?LLM rubric 瀵归綈锛?026-05-05锛?

### 鑳屾櫙

鐢ㄦ埛鍙嶉 popup 閲?鎯婂枩鎺ㄨ崘"鏁伴噺澶銆傛棩蹇楃‘璁?43 鍒嗛挓浼氳瘽閲?`Delight candidate found` 鎵撲簡 35 娆★紝鍗?01:05 閭ｄ竴娉㈠氨 20+ 鏉°€?

鏍瑰洜锛歚DEFAULT_DELIGHT_THRESHOLD = 0.57` 璺?`_DELIGHT_BATCH_SCORE_SYSTEM_PROMPT` 閲?LLM 鑷繁瀹氫箟鐨?score 鏍囧昂**瀵逛笉涓?*锛?

```
prompt rubric:
  0.85+:       鏋佸皯鏁扮湡姝ｃ€屽搰杩欎釜鎰忓濂藉鑳冨彛銆?
  0.70-0.85:   璺ㄥ煙鍛煎簲,鐢ㄦ埛澶ф鐜囦細鎰熷叴瓒ｄ絾鑷繁涓嶄細涓诲姩鎵? 鈫?鐪?delight
  0.55-0.70:   鏈夋儕鍠滄綔鍔涗絾鐩稿甯歌                          鈫?NOT delight
  0.40-0.55:   璺熺敤鎴峰叴瓒ｆ湁浜涘叧鑱斾絾澶櫘閫?
```

鏃?threshold 0.57 钀藉湪 prompt 鑷繁鏍囪涓恒€岀浉瀵瑰父瑙勩€嶇殑 0.55-0.70 鍖洪棿鈥斺€?*LLM 閮借"杩欎笉绠楁儕鍠?浜嗭紝浠ｇ爜鍗存帹閫佺粰鐢ㄦ埛**銆傛棩蹇楅噷鍑虹幇鐨?hook 涔熶綈璇侊細銆屽父瑙勮ˉ缁欍€嶃€屽疄鐢ㄥ伐鍏枫€嶃€屼俊鎭暣鍚堛€嶃€孉I瓒ｅ懗銆嶈繖绉嶆槑鏄句笉鏄儕鍠滅殑鏍囩閮借鎺ㄩ€併€?

threshold 鍘嗗彶杞ㄨ抗锛歷0.3.36锛?.44鈫?.55锛夆啋 v0.3.37锛?.55鈫?.57锛夈€傛瘡娆″姞涓€鐐圭偣锛?*濮嬬粓娌¤法杩?LLM rubric 鐨?0.70 鐪熸儕鍠滅嚎**銆?

### 鏀瑰姩

`src/openbiliclaw/recommendation/delight.py`:
- `DEFAULT_DELIGHT_THRESHOLD: 0.57 鈫?0.70`锛堣创榻?LLM rubric銆岃法鍩熷懠搴斻€嶈捣鐐癸級
- `CONSERVATIVE_DELIGHT_THRESHOLD: 0.67 鈫?0.80`锛堜繚瀹堢敤鎴峰悜涓婁竴妗ｃ€屾瀬灏戞暟鐪熸鎯婂枩銆嶉潬锛?

鏂板鍥炲綊娴嬭瘯 `tests/test_delight_scorer.py`:
- `test_default_thresholds_align_with_llm_rubric` 鈥?lock floor at 0.70 / 0.80
- `test_score_065_rejected_at_default_threshold` 鈥?0.65 鍒嗭紙rubric 鏍囩殑"鐩稿甯歌"锛夊繀椤昏鎷?

### 褰卞搷

鎸夋湰娆℃棩蹇楁暟鎹及绠楋紙35 涓?candidates 鐨?score 鍒嗗竷锛夛細

| score 娈?| 鏃э紙鈮?.57锛墊 鏂帮紙鈮?.70锛墊
|------|------|------|
| 0.85+ | 0 | 0 |
| 0.70-0.85 | 14 | **14**锛堜繚鐣欙級|
| 0.57-0.70 | 21 | **0**锛堣鎷掞級|
| **鎬昏** | 35 | **14** 锛?60%锛墊

- 閫氳繃鐨勫叏鏄?LLM 鑷繁璇?0.70+ 鐨?鐢ㄦ埛澶ф鐜囦細鎰熷叴瓒ｄ絾鑷繁涓嶄細涓诲姩鎵?
- 鎷掓帀鐨?21 鏉″叏鏄?LLM 鑷繁璇淬€岀浉瀵瑰父瑙勩€嶇殑鍐呭
- LLM 璋冪敤棰戠巼涓嶅彉锛堜粛瑕佹壂鎵€鏈夊€欓€夛級锛屽彧鏄?surface 鍙樹弗
- 鍍?"甯歌琛ョ粰" / "瀹炵敤宸ュ叿" / "淇℃伅鏁村悎" 杩欑 hook 涓嶅啀瑙﹀彂鎺ㄩ€?

娴嬭瘯锛?6/26 閫氳繃锛?4 鍘熸湁 + 2 鏂帮級銆?

---

## v0.3.48 / extension v0.3.9: 鎷︽埅"鑷繁鍙戠殑灏忕孩涔︾瑪璁拌鎺ㄥ洖缁欒嚜宸?锛?026-05-05锛?

### 鑳屾櫙

鐢ㄦ埛鍙嶉锛?鎴戠湅鍒?popup 閲屾帹浜嗗ソ澶氭垜鑷繁鍙戠殑绗旇锛堝睅灞?涓夎姳/鐚富棰橈級"銆傛棩蹇楃‘璁?XHS 鎺ㄨ崘姹犻噷澶ч噺鍑虹幇鐢ㄦ埛鑷繁鍙戝竷鐨勫唴瀹癸紝涓変釜鏉ヨ矾閮戒細姹℃煋锛?

- `xhs-extension-task` (XHS 鍏抽敭璇嶆悳绱? 鈥?xhs_producer 鐢ㄧ敤鎴峰叴瓒ｇ敾鍍忕敓鎴?keyword锛屾悳绱㈢粨鏋?*鑷劧鍛戒腑鐢ㄦ埛鑷繁鍙戠殑鍚屼富棰樼瑪璁?*
- `xhs-extension-explore` (XHS 鎺ㄨ崘娴? 鈥?XHS 鑷繁鐨?feed 绠楁硶**浼氭妸鐢ㄦ埛鑷繁鐨勫唴瀹规帹缁欑敤鎴?*
- `xhs-extension-profile` (bootstrap 鏀惰棌/璧炶繃) 鈥?鍋跺彂锛岃嚜浜掑姩鍦烘櫙

鍚庣 `_cache_xhs_notes` 娌℃湁浠讳綍"鏄惁鏄嚜宸?鐨勮繃婊わ紝author 瀛楁鐩存帴钀藉簱銆?

### 鏀瑰姩

**鎵╁睍**锛坄extension/src/content/xhs/`锛宐umped 0.3.8 鈫?0.3.9锛夛細
- 鏂?`extractSelfInfoFromState(state)` 浠?XHS profile 椤?state 鎶?`userId` + `nickname`锛堝凡鏈?`extractOwnProfileUrlFromState` 鎻愪緵璺緞妯℃澘锛?
- `XhsBootstrapDebugStep.self_info?: {user_id, nickname}` 瀛楁
- `executeBootstrapTaskInPage` 鍦?partial / final 涓や釜杩斿洖璺緞閮芥敞鍏?`selfInfo`锛岃窡 task-result POST 涓€璧峰洖鍒板悗绔€俵ate-bound锛氱涓€闃舵鍦?/explore 鏃舵嬁涓嶅埌锛岀浜岄樁娈佃繘鍏?profile 椤靛悗绔嬪嵆鎷垮埌

**鍚庣**锛坄api/app.py`锛宐umped 0.3.47 鈫?0.3.48锛夛細
- `_extract_self_info_from_debug` / `_persist_xhs_self_info` / `_load_xhs_self_info` / `_is_self_authored_note` 鍥涗釜 helper
- self_info 鎸佷箙鍒?`discovery_runtime_state["xhs_self_info"]`锛坘ey-value锛屾棤 schema 鍙樻洿锛?
- `xhs_task_result` 鏀跺埌鏃剁珛鍗?persist锛屽苟鎶?*鏈璇锋眰**鐨?self_info 鐩存帴浼犵粰涓嬫父杩囨护璺緞锛堥伩鍏?round-trip 閫氳繃 state锛屽 in-process test stub 鍙嬪ソ锛?
- `_cache_xhs_notes` 鍔?`self_info: dict | None` 鍙傛暟锛屽尮閰嶏紙鎸?nickname 鎴?user_id 鍙屽悜鍖归厤锛宑ase-insensitive锛夌殑 note 鍦ㄥ叆 `content_cache` 涔嬪墠琚涪寮冿紝涓㈠純鏁拌蛋 INFO 鏃ュ織
- bootstrap event propagation 鍚屾牱 gate锛氳嚜鍙戠瑪璁颁笉浼氳褰撴垚 favorite / like 淇″彿姹℃煋鐢诲儚

### 褰卞搷

- XHS 鎼滅储 / explore / 鏀惰棌璺緞鍥炴潵鐨勭瑪璁伴噷锛宎uthor 璺熺櫥褰曠敤鎴峰尮閰嶇殑**鍏ㄩ儴琚嫤鍦?content_cache 涔嬪**鈥斺€攑opup 涓嶄細鍐嶆帹鐢ㄦ埛鑷繁鐨勭瑪璁?
- 鑷彂绗旇涔熶笉浼氬啀浠?favorite / like 鐨勫舰寮忚繘鍏?events 琛ㄥ杺 soul profile锛堜箣鍓嶄細璁?LLM 瀛﹀埌"鐢ㄦ埛鍠滄鑷繁"鐨勫惊鐜俊鍙凤級
- 鏃ュ織鍙鎬э細`xhs ingest filter: dropped N self-authored note(s)` / `xhs bootstrap propagate: dropped N self-authored note(s)`
- 娴嬭瘯锛氭柊澧?`test_xhs_self_authored_notes_are_filtered`锛坆ootstrap 甯?self_info 鈫?鑷彂绗旇涓嶈繘 cache銆佷笉杩?events锛屼粬浜虹瑪璁扮収甯搁€氳繃锛夈€?08/108 閫氳繃

---

## v0.3.47: 鎺ㄨ崘鏂囨绮炬帓鎻愬墠鍑鸿揣 鈥?涓?discovery 鍚?strategy 骞惰锛?026-05-05锛?

### 鑳屾櫙

绾夸笂鏃ュ織鐪嬪埌涓€涓湡闂锛歱opup 鎺ㄨ崘鍗￠噷澶ч噺鍑虹幇銆屻€奨銆嬪亸瀹炴搷涓€鐐癸紝淇℃伅鏄兘鐩存帴鎷挎潵鐢ㄧ殑銆嶈繖绉?fallback 妯℃澘鏂囨鈥斺€斿畠**灏辨槸婧愮爜閲?11 濂楃‖缂栫爜妯℃澘涔嬩竴**锛岃Е鍙戞潯浠舵槸鍊欓€夌殑 `pool_expression` 瀛楁涓虹┖銆?

璺熻釜鍘熷洜锛歚precompute_pool_copy`锛堢敓鎴?expression 鐨勯偅涓€姝ワ級鎺掑湪 `_run_refresh_plan` 鏈熬锛?*鎵€鏈?discovery strategy 閮借窇瀹屾墠杞埌瀹?*銆傝€?deepseek-v4-flash 寮€浜?`reasoning_effort` 涔嬪悗鍗曟壒 `evaluate_batch` 瑕?8-16 鍒嗛挓銆備竴娆?refresh 涓茶澶氫釜 strategy = 30+ 鍒嗛挓涔嬪悗 expression 鎵嶅紑濮嬭窇銆傝繖娈垫椂闂村唴 popup 鐪嬪埌鐨勫唴瀹瑰叏鐢?fallback 妯℃澘銆?

瀹炴祴涓€浠?43 鍒嗛挓鐨?daemon 浼氳瘽鏃ュ織锛歚recommendation.write_expression` LLM 璋冪敤**鍙彂浜?2 娆?* 鈫?鏁翠釜浼氳瘽鍙湁 ~14 鏉″€欓€夋嬁鍒颁簡鐪?LLM 鏂囨锛屽叾浣?95% 閮芥槸妯℃澘銆?

### 鏀瑰姩

- **`RecommendationEngine._precompute_lock`** (`recommendation/engine.py`): 鏂板 `asyncio.Lock` 涓茶鍖栧苟鍙戠殑 `precompute_pool_copy` 璋冪敤鈥斺€斿涓?per-strategy fire-and-forget task 涓嶄細鍚屾椂 load 鐩稿悓鐨?un-precomputed 鍊欓€夛紝閬垮厤瀵瑰悓涓€鎵?item 鍙屽紑 LLM 璋冪敤娴垂 token銆?
- **`precompute_pool_copy` 鍐呴儴骞惰鍖?* + **batch_size 8 鈫?30**: 涔嬪墠 `for batch in batches: await _precompute_batch(...)` 涓茶锛岀幇鍦?`asyncio.gather` 骞跺彂銆備竴娆＄簿鎺?60 鏉″€欓€夊彧瑕?1 涓?batch latency锛垀30s锛夎€屼笉鏄?8 涓?脳 30s銆?
- **`_run_refresh_plan` 姣忎釜 strategy 瀹屾垚鍚庣珛鍒?fire 涓€涓?expression task**锛坄runtime/refresh.py`锛? 涓嶅啀绛夋墍鏈?strategy 璺戝畬鎵嶇粺涓€绮炬帓銆傛瘡涓?strategy 瀹屾垚涓€璋?`asyncio.create_task(self._safe_precompute_pool_copy(...))`锛岃 expression 璺熶笅涓€涓?strategy 鐨?LLM 璋冪敤**骞惰**銆侺ock 鍦?engine 鍐呬覆琛屾帓闃燂紝瀹夊叏銆傛渶鍚?`await asyncio.gather` 杩欎簺 task 鎵嶈繘 cleanup锛坱rim / prewarm锛夈€?
- **`_safe_precompute_pool_copy` helper**: 鍖呰 `precompute_pool_copy` 鍚炴帀寮傚父 + log锛岀粰 fire-and-forget task 鎻愪緵骞插噣鐨勫け璐ュ厹搴曘€?
- **鍥為€€鍒嗘敮**: 鏁翠釜 refresh round 娌′骇鐢熶换浣?strategy锛坧lan 涓虹┖ / 鍏ㄩ儴 short-circuit锛夋椂浠嶇劧 sync 璺戜竴娆?`_safe_precompute_pool_copy`锛屼繚璇佹棭鏈?cycle backlog 杩樿兘琚簿鎺掓竻瀹屻€?

### 褰卞搷

- **expression 鍑鸿揣鏃舵満浠庛€屽叏閮?strategy 璺戝畬銆嶆彁鍓嶅埌銆岀涓€涓?strategy 璺戝畬銆?*鈥斺€旀寜鏃ュ織鏁版嵁浼扮畻 popup 鐪嬪埌鐪?LLM 鏂囨鐨勫欢杩熶粠 ~22 min 闄嶅埌 ~5-10 min銆?
- **single precompute_pool_copy 鍐呴儴 N 涓?batch 骞惰**: 60 鏉″€欓€変粠 N 脳 30s 闄嶅埌 ~30s 鍏ㄩ儴瀹屾垚銆?
- **Lock 闃?LLM token 娴垂**: 澶氫釜 fire-and-forget task 鎺掗槦锛屼笉閲嶅瀵瑰悓涓€鎵?item 璺戠簿鎺掋€?
- 涓嶅姩 prompt builder锛坄build_batch_expression_prompt` 宸茬粡鏀寔浠绘剰 batch 澶у皬锛屽彧鏄粯璁?batch_size=8 娌″厖鍒嗙敤涓婏級锛孡LM cache 鍛戒腑鐜囦笉鍙楀奖鍝嶃€?
- 娴嬭瘯锛歚tests/test_refresh_runtime.py` 75/75 閫氳繃锛屾洿鏂颁竴澶?assertion锛坧recompute_pool_copy 鐜板湪鎸?strategy 鏁拌璋冪敤 N 娆¤€屼笉鏄?1 娆★級+ 鍦?`_FakeRecommendationEngine` 琛?`prewarm_pool_mmr_embeddings`銆?

---

## v0.3.46: init 鏈?profile-not-ready 鍋囬敊璇桨鐐告不鐞嗭紙2026-05-05锛?

### 鑳屾櫙

璺ㄦ棩蹇楋紙agent-bootstrap.log + openbiliclaw.log锛夎仈鍚堣瘖鏂彂鐜帮細daemon 鍚姩鍒?soul profile 寤哄ソ涔嬮棿绾?7 鍒嗛挓閲岋紝鎵€鏈変緷璧?profile 鐨勫悗鍙颁换鍔￠兘鍦ㄧ‖璋?`get_profile()`锛屾挒涓?`SoulProfileNotInitializedError`锛岃 `except Exception` 鎺ヤ綇鍚庢寜 ERROR / WARNING 绾у埆鎵撴棩蹇椼€?*鍗曟 init 绱 4 娆?ERROR + 9 娆?WARNING + 6 鍒嗛挓瀛楅潰鎴柇 topic 鍚?*鈥斺€斿姛鑳藉叾瀹為兘娌″潖锛屼絾鐢ㄦ埛浣撴劅鍍忚鐐镐簡銆?

鍚屾椂 profile 寤哄ソ涔嬪悗锛岀涓€娆?`classify_pool_backlog` 瑕佺瓑涓嬩竴涓嚜鐒?refresh tick锛堟渶澶?60s锛夛紝**鏈熼棿 popup 鐪嬪埌 `topic_group` 瀛楁绌猴紝琚?fallback 閫€鍖栨垚"灞庡睅/165/涓夎姳"杩欑浠庢爣棰橀噷鎶犵殑瀛楅潰 token**銆?

### 鏀瑰姩

- **`SoulEngine.is_profile_ready()`** (`soul/engine.py`): 鏂板寤変环銆佷笉鎶涘紓甯哥殑 profile-瀛樺湪妫€鏌ャ€傚悗鍙?consumer 涓嶅啀鐢?`try get_profile() except SoulProfileNotInitializedError` 褰撴祦鎺с€?
- **`_classify_new_pool_items` profile 鏈氨缁椂闈欓粯璺宠繃**锛坄api/app.py`锛? 鏀圭敤 `is_profile_ready()` 鍓嶇疆 gate锛屾湭灏辩华灏?DEBUG 涓€琛岃繑鍥烇紝涓嶅啀 ERROR-level 鎵?stack trace銆?
- **`CognitionCycle.run_if_due` 绛?preference 灞傚氨缁?*锛坄soul/cognition_cycle.py`锛? 鏃╂湡 awareness/insight 鍒嗘瀽鍣ㄥ湪 preference 灞備负绌烘椂纭窇 LLM 蹇呭穿銆傛敼鎴愬湪 `_run_awareness` 涔嬪墠鐪?preference layer 鏄惁闈炵┖锛屽惁鍒?`throttled=True` 闈欓粯杩斿洖銆?
- **`xhs_producer` 鐢?`is_profile_ready()` 鏇夸唬 try/except**锛坄runtime/xhs_producer.py`锛? 涔嬪墠姣忓垎閽熶竴娆?`WARNING xhs producer: soul profile unavailable`锛岀幇鍦?DEBUG 绾у埆闈欓粯鐩村埌 profile 钀藉湴銆?
- **profile-ready 杞崲閽╁瓙**锛坄runtime/refresh.py`锛? `_loop_refresh` 姣?tick 妫€娴?`_is_initialized()` false鈫抰rue 杞崲銆備竴鏃﹁娴嬪埌锛岀珛鍒昏皟 `classify_pool_backlog(limit=100)` 鎶?init 绐楀彛閲屽爢鐨勬湭鍒嗙被鍊欓€変竴娆℃€х倰鐔燂紝涓嶅啀绛変笅涓?cron tick銆侷NFO 涓€琛?`Soul profile became ready 鈥?kicking classify_pool_backlog`銆?
- **`_build_debug_summary` topic fallback 鏀规垚 `_unclassified_`**锛坄recommendation/engine.py`锛? 鍊欓€夌己 `topic_group` / `topic_key` / `tags` 鏃朵笉鍐嶈椽濠粠鏍囬閲屾姞 `[涓€-榭縘{2,4}` 褰?topic 鍚嶏紙涔嬪墠鐢ㄦ埛鏃ュ織閲岀湅鍒扮殑"灞庡睅"/"涓夎姳"/"165"锛夛紝鏀规墦瀛楅潰鍗犱綅绗?`_unclassified_`銆?*diversifier 瀹為檯 bucketing 閫昏緫淇濈暀 fallback**锛堜笉鑳借鎵€鏈夋湭鍒嗙被濉屾垚涓€妗讹級锛屽彧鍔?summary 杩欎竴灞傘€?

### 褰卞搷

- **init 澶?7 鍒嗛挓**锛? 娆?`Background pool classification failed (SoulProfileNotInitializedError)` ERROR銆? 娆?`Awareness analyzer failed during cognition cycle` ERROR銆? 娆?`xhs producer: soul profile unavailable` WARNING **鍏ㄩ儴娑堝け**锛堥檷绾у埌 DEBUG 鎴栫洿鎺?silent skip锛夈€?
- **profile 涓€灏辩华绔嬪嵆 classify_pool_backlog**锛氬師鏈绛変笅涓?60s tick锛岀幇鍦ㄥ悓 tick 绔嬪嵆瑙﹀彂锛屽€欓€?topic_group / style_key 鎻愬墠 ~50s 灏变綅銆?
- **summary 鏃ュ織閲屽啀涔熺湅涓嶅埌"灞庡睅/165/涓夎姳"**锛氭湭鍒嗙被鍊欓€夋槑纭墦 `_unclassified_`锛岀湅鐨勪汉涓嶄細浠ヤ负妯″瀷鐤簡銆?
- 涓嶅姩浠讳綍 LLM prompt builder锛屼笉褰卞搷 LLM 缂撳瓨鍛戒腑鐜囥€?

---

## v0.3.45: 銆屾崲涓€鎵广€嶆亽瀹氫簹绉掔骇 鈥?MMR embedding 鎻愬墠鍒?discovery 鏆栧叆锛?026-05-04锛?

### 鑳屾櫙

v0.3.44 鐨?MMR 澶氭牱鍖栨妸鍊欓€?embedding 鎷夊埌 serve() 鐑矾寰勶紝闈?`_merge_topic_supergroups` 椤烘墜鏆栧埌鐨?L1 缂撳瓨鍏滃簳銆備絾 supergroup 鐢ㄧ殑鏂囨湰 shape 鏄?`"{label} | {titles}"`锛岃窡 MMR 鐢ㄧ殑 `"{title} {desc[:160]}"` 涓嶆槸鍚屼竴涓?cache key鈥斺€旂粨鏋滅涓€娉?reshuffle 30+ 鏉″€欓€夊叏 miss锛屼覆琛岃皟 embedding API 鎶?P50 鎷栧埌 6-10s銆?

### 鏀瑰姩

- **`RecommendationEngine.warm_mmr_embeddings`** (`recommendation/engine.py`): 鏂板叕寮€鏂规硶锛岀粺涓€ MMR cache key 鏂囨湰锛坄_mmr_embedding_text` 闈欐€佹柟娉曞仛 single source of truth锛夛紝骞惰璋?`EmbeddingService.embed`锛堣嚜甯?provider semaphore锛夛紝缁撴灉钀?SQLite L2 鎸佷箙鍖栥€?
- **`_classify_pool_backlog_locked` 鎸佷箙鍖栧悗绔嬪嵆 warm**: 姣忎釜鍒嗙被鎵规钀藉簱鎴愬姛鐨?item 閮借繃涓€閬?`warm_mmr_embeddings`銆?
- **`ContentDiscoveryEngine._cache_results` detached task warm**: 涓?discovery 璺緞姣忔潯鏂板唴瀹瑰叆姹犳椂 `loop.create_task(_warm_mmr_embeddings)`锛屼笉闃诲 discovery 鏀跺熬銆?
- **`EmbeddingService.lookup_cached`**: 鏂板 cache-only 鍚屾鏌ヨ鎺ュ彛锛圠1鈫扡2锛宯ever API锛夈€俙SupportsEmbeddingService` 鍗忚鍚屾鍔犵銆?
- **`_fetch_candidate_embeddings` 鏀?cache-only**: serve() 鐑矾寰?*缁濅笉**瑙﹀彂 provider API 璋冪敤鈥斺€斿彧鏌?L1/L2锛宮iss 鐨?item 璧?string-cap fallback 鍏滃簳銆傛崲鏉?<1s 鐨勭‖淇濊瘉锛泈armer 鍚庡彴濉紝涓嬩竴娆?reshuffle 鑷劧鍛戒腑銆?
- **`prewarm_pool_mmr_embeddings`**: 鏂板叕寮€鏂规硶锛岃鐩栫幇鏈?200 鏉℃睜鍐呭€欓€夆€斺€斾笓娌诲崌绾х獥鍙ｏ紙宸叉湁 pool 鏃╀簬 warm hook 钀藉簱锛屽崟闈?per-item hook 姘歌繙鏆栦笉鍒帮級銆傚湪 `restart_background_tasks` 鍚姩鏃惰窇涓€娆★紙detached task 涓嶉樆濉?API ready锛夛紝骞舵帴鍏?refresh tick 璺?`prewarm_supergroup_embeddings` 鍚屽銆?
- **MMR embedding fetch 鍩嬬偣**: serve() 鏂板 `MMR embedding fetch: coverage=N/M elapsed=Xms` INFO锛岃鐩栫巼/鑰楁椂鍥炲綊绔嬪嵆鍙銆?
- **`mark_pool_items_shown` 绂诲紑鍏抽敭璺緞**: serve() 鍘熸湰鍚屾绛?`mark_pool_items_shown` 鎻愪氦鎵嶈繑鍥烇紱refresh tick 鐨?`_enforce_pool_cap` 鍦?reactivate 300+ 琛?`content_cache` 鐨勭灛闂翠細鎶婅繖涓?UPDATE 鍗?0.5-1.5s锛堟挒 SQLite write lock锛夈€傛敼鎴?`loop.create_task(self._mark_pool_shown_async(...))` fire-and-forget鈥斺€攚ithin-session 鍙屽嚮閲嶅鐢?`_last_served_bvids` in-memory 鍏滃簳锛孌B 钀藉湴绋嶅悗璺熷嵆鍙€傞厤濂椾繚鐣?`batch_insert_recommendations_and_mark_shown` 浣滀负鍙鐢?API锛坈aller 鑷鍐冲畾鏄惁鍚堝苟 / 寮傛锛夈€?
- **涓嶅姩浠讳綍 LLM prompt builder**: 瀹屽叏涓嶅紩鍏ユ柊 LLM 璋冪敤锛宍build_batch_content_evaluation_prompt` 鐨?system_prompt 闈欐€佺害瀹氫笉鍙橈紝DeepSeek/Claude/Gemini 鍓嶇紑缂撳瓨鍛戒腑鐜囦笉鍙楀奖鍝嶃€?

### 褰卞搷

- 銆屾崲涓€鎵广€嶅疄娴?30 杞紙娣峰悎鑺傚锛氳儗闈犺儗 / 2s 闂撮殧 / 5s 闂撮殧瑙﹀彂 refresh tick锛夊叏閮?<1s銆傝儗闈犺儗 P50鈮?.61s P99鈮?.85s锛涢棿闅旀ā寮?P50鈮?.28s锛堟渶蹇?0.14s锛夛紝瀹屽叏娌℃湁 >1s 绂荤兢鐐广€?
- 棣栨 fresh-install 鍒锋柊锛歴tartup detached prewarm 璺戝悗鍙板～ L2锛寀ser 鐢ㄥ暐鏃跺埢鍒烽兘 <1s銆?
- SQLite `embedding_cache` 琛ㄦ瘡 discovery cycle 澧為暱 ~30-100 琛岋紝鏃?schema 鍙樻洿銆?
- LLM 鏈堟敮鍑烘棤鍙樺寲锛坧rompt cache 鍛戒腑鐜囦笉鍔紝鏃犳柊 LLM 璋冪敤锛夈€?

---

## v0.3.37 / extension v0.3.5: popup 涓庡悗绔疄鏃跺悓姝ヤ慨澶嶏紙2026-05-04锛?

### 鏀瑰姩

- **`delight.refreshed` 瀹炴椂浜嬩欢**: refresh tick 鏈熬姣旇緝 precompute 鍓嶅悗 delight 鍊欓€夋暟,鏂板 鈮? 鏃堕€氳繃 WebSocket 鍙?`{type: "delight.refreshed", count, total_pending}` 浜嬩欢銆?*涓嶅甫 per-item payload銆佷笉瑙﹀彂 chrome 閫氱煡**鈥斺€旂函绮规槸瑙﹀彂 popup 閲嶆媺 `/api/delight/pending-batch`銆備慨澶嶇敤鎴风棝鐐广€屾儕鍠滄帹鑽愬彧鏈夐噸鏂板姞杞芥彃浠舵墠鍑烘潵銆嶃€?
- **`pool_status` 瀹炴椂浜嬩欢**: `_enforce_pool_cap` 鍚?姣忓垎閽熻窇涓€娆?濡傛灉 pool_count 璺熶笂娆″彂甯冪殑涓嶅悓,鎺?`{type: "pool_status", pool_available_count, pool_target_count}`銆俻opup `mergeRuntimeStatusEvent` 宸茬粡鏈?handler,浼氳嚜鍔ㄩ噸娓叉煋銆備慨澶嶇敤鎴风棝鐐广€屾粴鍔ㄥ垪琛ㄦ椂鍊欓€夋睜鏁伴噺涓嶅彉銆嶃€?
- **proactive_push_interval_seconds 600鈫?20**: 鎶婂悗鍙板厹搴曟帹閫?cadence 浠?10 鍒嗛挓鏀剁揣鍒?2 鍒嗛挓銆備富璺緞宸茬粡鏄嵆鏃?`delight.refreshed`,杩欓噷鍙槸瀹夊叏缃?闄嶄綆寤惰繜灏惧反銆?
- **popup `onEvent` 鍔?`delight.refreshed` 鍒嗘敮**: 鏀跺埌浜嬩欢鍚庤皟 `fetchPendingDelightBatch(20)` 閲嶆媺闃熷垪,`clearDelightQueue` + `pushDelightCandidate(item)` 涓叉帴 + `renderDelightSlot()`銆傚嚭閿欓潤榛?涓嬩竴杞?proactive 鎺ㄩ€佷細鑷剤銆?

### 褰卞搷

- 鏂?delight 鍦?backend 璺戝畬 `precompute_delight_scores` 鍑犵鍐呭氨鍑虹幇鍦ㄥ凡鎵撳紑鐨?popup 閲?鏃犻渶鎵嬪姩閲嶆柊鍔犺浇鎵╁睍銆?
- 鍊欓€夋睜鏁伴噺鍦?trim/reactivate 杩囩殑 60s 鍐呭悓姝ュ埌 popup UI銆?
- `proactive_push_interval_seconds` 榛樿鍊兼敼浜?濡傛灉浣犵殑 config.toml 鏄惧紡璁捐繃 600 浠嶄細娌跨敤,鏂拌/榛樿鍊兼槸 120銆?

---

## v0.3.36: Delight LLM JSON 瑙ｆ瀽瀹归敊锛?026-05-04锛?

### 淇

- **`LLMDelightScorer` 涓嶅啀鍥?provider 杈撳嚭褰㈡€佸穿婧?*: DeepSeek 涓ユ牸鎸?prompt 杩?`[...]`,浣?mimo-v2.5-pro 绛夋ā鍨嬪湪 JSON 妯″紡涓嬪€惧悜杩?`{"results": [...]}` / `{"items": [...]}` / 鎴栧涓?root 瀵硅薄 newline 鍒嗛殧(瑙﹀彂 `JSONDecodeError: Extra data`)銆傛柊澧?`_extract_delight_entries` 鍏滃簳:tolerant parse 鈫?宸茬煡 wrapper 閿В鍖?results/items/delights/data/scores/candidates/output/list/array)鈫?JSONL 琛岀骇鍥為€€ 鈫?single-dict-with-bvid 鍖呰銆傜敤鎴峰垏鍒?mimo 鍚?12/12 澶辫触 鈫?鐜板湪鍏?shape 閮借兘鍚炰笅銆?

---

## v0.3.35: 鎯婂枩鎺ㄨ崘鏀逛袱娈靛紡妫€绱紙绮楀彫 + 绮炬帓锛夛紙2026-05-04锛?

### 鏀瑰姩

- **绮楀彫鍥?*: `get_pool_candidates_needing_delight_score` 鍔?`min_relevance_score=0.55` 鍙傛暟,SQL `WHERE` 鍔犱笂 `relevance_score >= 0.55` 杩囨护銆傚師鏉?SQL 鍙?`ORDER BY relevance_score DESC LIMIT N`,姹犵█鐤忔椂浼氬杺缁?LLM 涓€鍫?weak-fit 鍨冨溇銆?.55 瀵归綈 discovery rubric銆宮oderate fit銆嶅熀鍑嗏€斺€斿啀鎯婂枩涔熷緱鑷冲皯鍗?fit銆?
- **绮炬帓鎵╁**: `precompute_delight_scores` 鐨?`limit` 榛樿 30 鈫?50,姣?cycle 璁?LLM 澶氱湅 20 鏉″€欓€?鎻愰珮鐪熸儕鍠滆鍛戒腑鐨勬鐜囥€傛垚鏈粠 楼0.06/cycle 鍗囧埌 楼0.10/cycle (楼0.80/澶?vs 楼0.48/澶?,鎹㈢害 67% 鏇村鐨勬悳绱㈤潰銆?

### 鎬濊矾

`relevance_score` 鏄?discovery 闃舵 LLM 宸茬粡鍒よ繃鐨勩€岀敤鎴?鍐呭鍖归厤搴︺€?鍏嶈垂鍙敤銆傚綋浣滅矖鍙洖淇″彿 + LLM-judge 鍋氱簿鎺?缁忓吀涓ゆ寮? 鐮嶆帀 95% 娌℃湜鍛戒腑鐨勪綆璐?item,鎶?LLM 璋冪敤闆嗕腑鍦ㄦ渶鍊煎緱璇勫垽鐨?candidate 涓娿€?

---

## v0.3.34: 鎯婂枩鎺ㄨ崘鏀圭敤 LLM 璇勫垎锛?026-05-04锛?

### 鏀瑰姩

- **`DelightScorer` 浠?embedding-cosine 鍗囩骇涓?LLM batch 璇勫垎**:涔嬪墠鐨勫疄鐜扮敤 `likes_alignment` / `deep_need_alignment` / `dislike_penalty` 绛?embedding 浣欏鸡鐩镐技搴︹€斺€斾絾銆屾儕鍠溿€嶈涔変笂璺熴€岀浉浼煎害楂樸€嶅绔?鐢ㄦ埛涓嶅枩娆€屽張涓€鏉?DeepSeek 娴嬭瘎銆?,embedding 瓒婇珮瓒婂儚鍙嶈€岃秺涓嶆儕鍠溿€傛柊澧?`LLMDelightScorer` 绫?姣忎釜 batch (榛樿 5 鏉? 涓€娆?LLM 璋冪敤,LLM 鐩存帴鎸夐璁?rubric 鍒ゅ垎(0-1)+ 缁欏嚭 rationale + hook,**鎯婂枩鐨勬牳蹇冨垽鎹粠銆岀浉浼笺€嶅彉鎴愩€岃法鍩熷懠搴?/ 闅愯棌闇€姹?/ 姒傚康妗ユ帴銆?*銆?
- **鐪佹帀浜屾 reason generation 璋冪敤**:LLM 璇勫垎鏃跺凡缁忚繑鍥?80-180 瀛楃殑 rationale 鍜?2-4 瀛?hook,鐩存帴褰?`delight_reason` / `delight_hook` 鍐欏叆鏁版嵁搴?涓嶅啀鍗曠嫭璋?`_generate_delight_reason`銆?
- **鎴愭湰**:绋虫€佹瘡 cycle ~6 batch call 脳 楼0.01 = 楼0.06/cycle,8 cycle/day = **~楼0.48/澶?*;鐪佷笅鏉ョ殑 reason generation 鏄?楼0.6/澶?**鍑€鏀瑰杽 -楼0.12/澶?*銆傞娆℃睜瀛愬畬鏁撮噸鎵撳垎涓€娆℃€?楼1-2銆?
- **`build_delight_score_batch_prompt` 鍦?`llm/prompts.py` 鏂板**:闈欐€?system prompt(cache-friendly,绗﹀悎 v0.3.28+ 瑙勭害),user payload 鐢?sort_keys 淇濊瘉 deterministic prefix銆?
- **鏁版嵁杩佺Щ**:鍒犳帀鎵€鏈?`pool_status='fresh'/'shown'` 鐨勮€?delight_score(閮芥槸 embedding-era 鏍囧畾鐨勪笉鍙俊鍊?,璁?LLM scorer 鍏ㄩ噺閲嶅垽銆?

### 娴嬭瘯

- 閲嶅啓 `test_precompute_delight_scores_*` 鐢ㄤ緥鍙嶆槧鏂?LLM-batch 褰㈡€?LLM mock 杩斿洖 `[{bvid, score, rationale, hook}]` 鏁扮粍銆?

---

## v0.3.33: Delight 鍊欓€夎繃婊や慨澶嶏紙2026-05-04锛?

### 淇

- **`get_delight_candidates` 涓嶅啀杩斿洖 `pool_status='suppressed'` 鐨?item**:涔嬪墠 SQL 鍖呭惈 `IN ('fresh', 'shown', 'suppressed')`,浣?suppressed 鏄 topic-group cap / 鏉ユ簮閰嶉瑁佸嚭娲昏穬姹犵殑 item,delight 璇勫垎杩樻寕鍦ㄤ笂闈€傜粨鏋?popup 姣忔鍒锋柊璋?`/api/delight/pending-batch?limit=20` 閮戒粠 562 鏉?suppressed 鍘嗗彶璇勫垎锛坴0.3.32 dislike/threshold 鏀瑰墠鎵撶殑锛夐噷鎹?20 鏉″嚭鏉?**鐢ㄦ埛姣忔閲嶆柊鍔犺浇鎵╁睍閮界湅鍒?20 涓湅浼兼儕鍠滅殑"骞界伒鎺ㄨ崘"**銆傛敼鎴?`IN ('fresh', 'shown')`,鍙繚鐣欐椿璺冩睜銆?
- **涓€娆℃€ф竻鐞?9991 鏉?suppressed 鐘舵€佷笅鐨?delight 娈嬬暀**:`UPDATE content_cache SET delight_score=0, delight_reason='', delight_hook='', delight_notified=0 WHERE pool_status='suppressed'`銆備慨鏀?SQL 鍚庤繖浜涙暟鎹湰韬凡涓嶄細鍐?leak锛屼絾娓呮帀閬垮厤 suppressed 鈫?reactivate 鏃跺啀甯︾潃鑰?delight 婕傚洖鏉ャ€?

### 娴嬭瘯

- 鍙嶈浆 `test_database_get_delight_candidate_allows_suppressed_delight_item` 鐨勮涔夛細鍘熸祴璇曠敤娉ㄩ噴銆岃櫧鐒舵櫘閫氭睜鍘嬫帀浜嗭紝浣嗚繖鏉″浣犺繕鏄緢鍙兘鏄儕鍠溿€嶅浐鍖栦簡 bug 琛屼负锛岀幇鏀瑰悕 `..._excludes_suppressed_pool_items` 骞舵柇瑷€ None銆?

---

## v0.3.32: Embedding 涓?LLM Provider 瑙ｈ€?+ OpenAI 鍗忚鍏煎 provider锛?026-05-04锛?

### 鏀瑰姩

- **`[llm.embedding]` 鎷ユ湁鐙珛鐨?`api_key` / `base_url`**锛歟mbedding 涓嶅啀鍊熺敤 `[llm.<provider>]` 鐨勮繛鎺ワ紝閬垮厤銆屾兂鐢?OpenAI 璺?embedding 浣?chat 璧?DeepSeek銆嶆椂琚揩鍦ㄤ袱澶勫～鍚屼竴涓潡銆俙build_embedding_service` 鐩存帴鏍规嵁 `[llm.embedding]` 鏋勯€犱竴涓嫭绔?provider 瀹炰緥锛屼笌 chat 绔?`LLMRegistry` 瀹屽叏瑙ｈ€︺€?
- **鏂板 `openai_compatible` 涓€绾?provider**锛氱敤浜庢帴鍏?Groq / Together / Azure OpenAI / vLLM / 鑷缓绛変换浣曡蛋 OpenAI 鍗忚鐨勬湇鍔°€傚拰 `[llm.openai]` 瀹屽叏鐙珛锛堜笉鍐嶇敤 base_url override 澶嶇敤 openai block锛夛紝鍙互鍚屾椂鍦ㄤ竴涓」鐩噷璺戜袱濂楋紙chat 鐢ㄧ湡 OpenAI銆佽緟鍔╀换鍔℃寕 Groq 鍔犻€燂級銆俙base_url` 蹇呭～锛岀己澶变細琚?`_collect_config_issues` 鎷︿笅锛岄伩鍏?401 hit `api.openai.com`銆侲mbedding 娈典篃鏀寔閫?`openai_compatible`锛堝鏁?OpenAI-compat 鍚庣閮芥毚闇?`/v1/embeddings`锛屾瘮濡?Together銆乿LLM銆丄zure锛夈€?
- **鍚戝悗鍏煎鍥炶惤**锛氳€?config锛堜粎璁句簡 `[llm.embedding] provider` 娌″～ api_key锛変粛鍙伐浣?鈥斺€?閫忔槑鍥炶惤鍒?`[llm.<provider>].api_key`锛屽苟鎵撲竴鏉′竴娆℃€?WARNING 鎻愮ず杩佺Щ锛涗笅涓ぇ鐗堟湰浼氱Щ闄よ鍥炶惤銆?
- **鍒犳帀 `embedding_wants_ollama` 鑷姩娉ㄥ唽 hack**锛歟mbedding 鐜板湪鑷繁鏋勯€?Ollama锛宑hat registry 涓嶅啀鍥犱负 `[llm.embedding] provider="ollama"` 鑰岃寮烘彃涓€鏉?embedding-only 鏉＄洰銆?
- **API 灞?`EmbeddingConfigOut` 鏆撮湶 `api_key`锛堝凡鑴辨晱锛? `base_url`**锛歚PUT /api/config` 鎺ュ彈鏂板瓧娈碉紱`api_key` 瀛楁鑻ユ敹鍒板惈 `*` 鐨勫洖鏄撅紙鑴辨晱鍊煎師鏍峰洖鍐欙級锛屼繚鐣欏師鍊间笉瑕嗙洊銆?
- **鎵╁睍 popup Embedding 娈?*锛氭柊澧?`EMBEDDING API KEY` / `BASE URL` 瀛楁锛沺rovider 鍒囨崲鏃惰仈鍔ㄦā鍨?placeholder锛坄bge-m3` / `text-embedding-3-small` / `gemini-embedding-001`锛夊拰瀛楁鍙鎬э紙Ollama 闅愯棌 api_key銆丟emini 闅愯棌 base_url锛夈€傚垹闄?OpenRouter 閫夐」锛堟棤 embedding 鎺ュ彛锛夈€?
- **閰嶇疆娓叉煋 / 鍔犺浇鍚屾鏇存柊**锛歚save_config` 鍐欏嚭鏂板瓧娈碉紝`_build_config` 鎺ュ彈鏂板瓧娈碉紱鑰?TOML锛堟棤鏂板瓧娈碉級姝ｅ父鍔犺浇锛屾柊瀛楁榛樿 `""`銆?

### 褰卞搷

- 璺戣€?config 鐨勭敤鎴烽娆″惎鍔ㄤ細鐪嬪埌涓€鏉?`[llm.embedding] api_key/base_url is empty 鈥?falling back to [llm.<x>] credentials. ...` 鐨?WARNING锛涜涓轰笉鍙橈紝鎸夋彁绀烘妸鍑嵁鎼埌 `[llm.embedding]` 鍗冲彲娑堝け銆?
- `setup-embedding` 鍚戝鍜屾墿灞曠殑 GET/PUT `/api/config` 璋冪敤鏂瑰紡鍧囨棤鐮村潖鎬ф敼鍔ㄣ€?

---

## v0.3.31: Discovery 鏉ユ簮鍧囪　鍏煎灏忕孩涔︼紙2026-05-03锛?

### 淇

- **灏忕孩涔︿綔涓轰竴绛夋潵婧愭棌鍙備笌鍊欓€夋睜閰嶉**:`_SOURCE_TARGET_SHARES` 澧炲姞 `xiaohongshu`锛?00 姹犵洰鏍囩害鍒嗛厤涓?`search=141 / related_chain=141 / trending=35 / explore=141 / xiaohongshu=142`銆俙xhs-extension-task/search/profile` 绛?raw source 浼氬綊骞跺埌鍚屼竴涓?`xiaohongshu` 鏉ユ簮鏃忥紝閬垮厤灏忕孩涔﹀簱瀛樺湪 share-aware trim 涓褰撲綔鏈煡鏉ユ簮鎴栬鎷嗘垚澶氫釜鏉ユ簮銆?
- **婊℃睜鏃朵篃鑳芥仮澶嶅凡 suppressed 鐨勫皬绾功楂樺垎鍊欓€?*:`reactivate_under_quota_pool_sources()` 浼氬湪鏉ユ簮鏃忎綆浜庨厤棰濇椂锛屼粠 `pool_status='suppressed'` 涓斿甫 `xsec_token` 鐨勫彲鎵撳紑鍊欓€変腑澶嶆椿涓€鎵癸紝鍐嶇敱 `trim_pool_to_target_count(source_share_quotas=...)` 鎸夌粺涓€閰嶉瑁佹帀杩囬噺鏉ユ簮銆傜幇鏈夎鍘嬩綇鐨勫皬绾功鍐呭涓嶅繀绛夐噸鏂版祻瑙堝悓涓€椤甸潰鎵嶆湁鏈轰細鍥炲埌 fresh pool銆?
- **姹犲瓙璁℃暟鎺掗櫎涓嶅彲鎵撳紑鐨勫皬绾功瑁?URL**:`count_pool_candidates()` 鍜?`count_pool_candidates_by_source()` 鐜板湪鍙妸甯?`xsec_token` 鐨勫皬绾功琛岀畻浣滃彲鐢ㄥ€欓€夛紝閬垮厤 runtime 鐘舵€佹樉绀衡€滄睜瀛愭弧浜嗏€濅絾 UI 瀹為檯涓嶈兘鎺ㄨ崘銆?
- **explore 鍩熺敓鎴愰亣鍒?DeepSeek 绌哄唴瀹逛細鑷剤涓€娆?*:绾夸笂鏃ュ織閲岀殑 `deepseek returned empty content` 鏉ヨ嚜 DeepSeek HTTP 200 浣?`content=""`锛屼箣鍓嶆櫘閫氭ā寮忔病鏈?provider 灞傞噸璇曪紝瀵艰嚧 `discovery.explore.queries` 鐩存帴杩斿洖 0 涓帰绱㈠煙銆俙DeepSeekProvider` 鐜板湪瀵圭┖鍐呭缁熶竴閲嶈瘯涓€娆★紱`reasoning_effort` 寮€鍚椂浠嶅叧闂?thinking 閲嶈瘯锛屾櫘閫氭ā寮忔寜鍘熷弬鏁伴噸璇曘€?
- **灏忕孩涔?bootstrap 浠诲姟鏃犳潯浠跺墠鍙般€乨iscovery 濮嬬粓鍚庡彴**:涔嬪墠 `xhs-task-dispatcher` 鐢?`isScrollableBootstrapTask`锛堝嵆 `max_scroll_rounds > 0`锛夋潵鍐冲畾 bootstrap 鏄惁鍓嶅彴,鎵€浠ヨ嫢鏈夌敤鎴风敤 `OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS=0` 璺宠繃婊氬姩浼氳惤鍒板悗鍙版媺鏁版嵁銆傝涔夋敼鎴愩€宨nit-time bootstrap 濮嬬粓鍓嶅彴 + discovery (search/creator) 濮嬬粓鍚庡彴銆? bootstrap 鏄敤鎴疯窇 `openbiliclaw init` 鏃朵富鍔ㄦ湡鏈涚湅鍒扮殑杩囩▼(閫忔槑鎬?,涓?XHS 铏氭嫙鍒楄〃鍙湪 active tab 鎵嶆纭垎椤?discovery 鏄悗鍙拌繛缁壂鎻?涓嶈鎵撴壈鐢ㄦ埛娲昏穬娴忚銆?
- **Ollama embedding 鍦ㄧ郴缁熶唬鐞嗙幆澧冧笅鍏ㄥけ璐?*:鐢ㄦ埛寮€浜嗘湰鍦?HTTP 浠ｇ悊锛堝 7897 绔彛鐨?VPN 瀹㈡埛绔級鏃讹紝`httpx.AsyncClient` 榛樿 `trust_env=True` 浼氭妸 localhost embedding 璇锋眰涔熻蛋浠ｇ悊 鈫?鍏ㄩ儴 `httpx.ReadTimeout`銆傛棩蹇楃粺璁℃樉绀轰竴澶?140+ 娆″け璐ワ紝**鐩存帴鎷栧灝鎯婂枩鎺ㄨ崘**锛歚DelightScorer` 鐨?`likes_alignment` / `deep_need_alignment` / `dislike_penalty` 鍏ㄨ繑 0锛?9.5% 姹犲唴 item锛?04/607锛夎惤鍒?0.01-0.50 鍖洪棿姘歌繙杩囦笉浜?0.65 闃堝€笺€俙OllamaProvider.embed()` 鐜板己鍒?`trust_env=False`锛岀粫寮€浠ｇ悊鐩磋繛鏈湴 Ollama銆?
- **EmbeddingService 缂撳瓨琚┖鍚戦噺姘镐箙姹℃煋**:embedding 鏄敤鎴烽厤缃?`provider="ollama"` 鏃剁殑**涓昏矾寰?*锛堜笉鏄檷绾э級锛屼絾 `EmbeddingService.embed()` 涔嬪墠浼氭棤鏉′欢鎶?provider 杩斿洖鐨?`[]` 涔熷啓杩?L1 + L2 缂撳瓨銆備唬鐞?bug 閭ｆ鏃堕棿 ~140 娆″け璐ユ妸 170 鏉℃牳蹇?likes 鏂囨湰锛坄娓告垙鏀荤暐` / `鍔ㄦ极鏉傝皥` / `娲涘厠鐜嬪浗` / `閲戦摬閾蹭箣鎴榒 绛夛級鍏ㄩ儴姣掑寲涓虹┖鍚戦噺 鈫?鍗充娇淇簡浠ｇ悊锛孌elightScorer 姘歌繙浠庣紦瀛樻嬁鍒扮┖鍒楄〃 鈫?likes_alignment 姘歌繙杩?0銆傛柊澧炵┖鍚戦噺瀹堝崼锛歱rovider 杩?`[]` 鏃惰烦杩囩紦瀛樺啓鍏ャ€佹墦 WARNING 璁╁け璐ユā寮忓湪鏈嶅姟灞傚彲瑙佽€屼笉鏄煁鍦?provider 鏃ュ織閲岋紱鍚屾椂娓呯悊浜?`data/embedding_cache.db` 閲屽凡缁忚姣掑寲鐨?170 鏉″巻鍙叉暟鎹€?
- **EmbeddingService 骞跺彂鎶婃湰鍦?Ollama 鎵撶垎**:proxy fix 涔嬪悗 daemon 绔嬪埢鐢ㄥ苟鍙?embed 琛ラ綈绉帇锛坉elight scoring + 涓婚鍘婚噸 + speculator + 姹犲唴 candidate batch 鍚屾椂鍙戣捣锛夛紝瀹炴祴涓€绉掑唴 14+ 涓苟鍙戣姹傜亴杩?bge-m3 鍗曡繘绋?GGUF runner锛孋PU 4 鏍?100%銆乣ollama runner` 鍗犵敤 406%銆乧url 鐩磋繛 30s 閮芥敹涓嶅埌鍝嶅簲銆佹墍鏈?in-flight 璇锋眰 60s timeout 澶辫触銆傛柊澧?`EmbeddingService` 鍐呴儴 `Semaphore(2)` 闄愭祦锛堥粯璁?2锛屽彲閫氳繃 `max_concurrent_provider_calls` 鏀癸級锛屽悓鏃舵妸 `OllamaProvider.embed` 鐨?httpx timeout 浠?60s 鎻愬埌 120s 鍚告敹鍐峰惎鍔?+ 闃熷垪绛夊緟銆?
- **Speculator 鎺㈤拡闀垮鍚堜腑鏂囩煭璇案杩滃尮閰嶄笉涓婁簨浠?*:LLM 鐢熸垚鐨?probe 鍩熷悕甯告槸 `'AI鍥惧儚鐢熸垚宸ヤ綔娴佹繁搴︽媶瑙?` 杩欑 13 瀛楄繛缁腑鏂囷紝鍘熷尮閰嶅櫒涓夋潯璺緞鍏ㄥけ鏁堬紙鏁翠覆 substring 涓嶅懡涓€乣[涓庡拰路銆?\s鍙奭+` 鍒囦笉鍔ㄣ€亀hitespace-tokenize 鍙骇 1 涓?token锛夆啋 涓€澶╄瀵?0 娆″尮閰嶏紝鎵€鏈夋帰閽堟寕鍦?active 妲?3 澶╁悗 TTL 杩囨湡琚嫆銆傛柊澧?Chinese-bigram 鍏滃簳锛歯ame 绔姹?鈮? 涓?distinct bigram銆乪vent 绔姹?鈮? 涓?bigram 閲嶅彔鎵嶇畻鍛戒腑锛岄厤鍚堜笂娓?`confirmation_threshold=3` 闃茶鍗囥€?
- **Speculator "generated N new" 鏃ュ織楠椾汉**:`result.generated` 涔嬪墠鍙?`state.active` 鍏ㄩ泦锛屽鑷存瘡杞?tick 閮芥妸鎼哄甫杩囨潵鐨勮€佹帰閽堥噸澶嶆墦鎴?"generated 2 new"锛屽埗閫犲湪宸ヤ綔鐨勫亣璞°€傛敼鎴愬彇 `_generate` 璋冪敤鍓嶅悗鐨?domain 闆嗗悎宸紝鍙睍绀虹湡姝ｆ柊澧炵殑锛涚┖闆嗘椂钀藉埌 `force_tick: no-op (active full)` DEBUG 琛屻€俙Speculator observed` 鏃ュ織鍚屾浠?DEBUG 鍗囧埌 INFO锛岃浜嬩欢鈫掓帰閽堢‘璁や俊鍙峰湪鐢熶骇鏃ュ織閲屽彲瑙併€?
- **Speculator slot-aware 鎻愭棭 skip LLM 璋冪敤**:`_should_generate` 涔嬪墠鍙鏌?`active_count < max_active`,浣?LLM 鍑犱箮鑲畾浼氶噸澶嶆彁妗堝凡瀛樺湪 active 闆嗗悎涓殑 domain 鈫?dedup 涔嬪悗鍑€鏂板 0銆傝姹傝嚦灏?2 涓┖闂?slot 鎵嶅彂璧?LLM 璋冪敤,鍚﹀垯璺宠繃銆傜矖鐣ヤ及绠楁瘡澶╃渷 ~楼0.04 鐨?speculator 娴垂璋冪敤銆?
- **CLI 涓変釜 Ollama 鎺㈡祴**(`_ollama_is_running` / `_ollama_has_model` / `_ollama_pull_model`)鍚屾牱瀛樺湪浠ｇ悊鍔寔闂,琛?`trust_env=False`,閬垮厤 `setup-embedding` 鍦ㄤ唬鐞嗙幆澧冧笅璇垽 "Ollama 娌″惎鍔?銆?
- **DelightScorer 澧炲姞 embedding 瀛愮郴缁熸浜″憡璀?*:鍥涗釜 embedding-driven 淇″彿(likes / deep_need / insight / dislike)鍚屾椂涓?0.0 鏃?鍑犱箮鍙彲鑳芥槸 embedding 瀛愮郴缁熸寕浜?鐢ㄦ埛鐨?likes/deep_needs/insights/disliked_topics 鍚屾椂涓虹┖鍦ㄧǔ鎬佷笅涓嶅彲鑳?銆傛柊澧?per-candidate WARN 璁╁け璐ヤ俊鍙峰湪 recommendation 灞傚彲瑙?涓嶅啀琚煁鍦?1GB 鐨?provider HTTP DEBUG 閲屻€?
- **`trim_topic_group_overflow` 姣忓垎閽熶竴琛?INFO 鍣煶闄嶇骇**:绋虫€佷笅姹犲瓙閲?`浜哄伐鏅鸿兘:8 over cap` 杩欑鏁版嵁姣?60s 閲嶅鎵撲竴閬?涓€澶?1440 鏉°€侱atabase 閲岀殑 emit 鏀规垚 DEBUG;Refresh 灞傜殑 `enforce_pool_cap: reactivated=N` 鍔?fingerprint 缂撳瓨,reactivated 鏁颁笌涓婁竴 tick 鐩稿悓鍒欓檷鍒?DEBUG,鍙樺寲鏃舵墠 INFO銆?
- **EmbeddingService L1 cache 鏀?LRU**:涔嬪墠鐢ㄦ櫘閫?dict + `next(iter)` 椹遍€愭渶鑰?瀹炶川鏄?FIFO,500 鏉″閲?+ bursty 璁块棶涓嬩細椹遍€愬垰鍒氬懡涓繃鐨勭儹 key銆傛敼鐢?`OrderedDict` + `move_to_end(key)` on hit + `popitem(last=False)` on evict,姝ｇ‘ LRU銆?
- **OllamaProvider 鍔?1 娆￠噸璇?*:bge-m3 鐭殏 OOM / Ollama runner 閲嶅惎 / 妯″瀷 hot-swap 杩欎簺鐬椂鏁呴殰涔嬪墠鐩存帴杩?`[]` 璧伴潤榛橀檷绾с€傛敼鎴?`for attempt in (1, 2)` 妯″紡,棣栨澶辫触 DEBUG 涓€琛屽悗绔嬪埢閲嶈瘯,涓ゆ閮藉け璐ユ墠 WARN銆傚悓鏃舵妸 `Ollama embedding failed` 鏃ュ織鏀规垚 `failed after 2 attempts`銆?
- **`config.toml` 鍚屾 v0.3.30 logging 榛樿鍊?*:鎶婄敤鎴锋棫鐨?`max_file_size_mb = 1024` 闄嶅埌 100,琛ヤ笂 `aggregate_budget_mb = 500` / `unmanaged_truncate_mb = 200` / `unmanaged_max_age_days = 30`,璁?v0.3.30 寮曞叆鐨勬棩蹇楀厹搴曟満鍒跺疄闄呯敓鏁堛€傝繖涓敼鍔ㄥ彧鍔?`config.toml`(gitignored),浠撳簱 `config.example.toml` 鏃╁氨鏄柊鍊笺€?
- **DelightScorer dislike_penalty 闃堝€?鏀惧ぇ鍣ㄦ寜 bge-m3 閲嶆柊鏍囧畾**:涔嬪墠 `(sim - 0.55) * 2.5` 鏄寜 Gemini 鏍囩殑,bge-m3 瀵逛綆璇箟涓枃(鐩存挱鐗囨鏍囬銆乵etadata)鏈?閫氱敤涓枃 cluster"鐜拌薄,baseline cosine 0.78-0.85,鎵€鏈夊€欓€夐兘琚?dislike 鎷夊噺 0.30 鍒嗐€傛敼鎴?`(sim - 0.78) * 1.5` 鍚?鍘嗗彶 3 鏉?鈮?.65 delight item 閲嶆墦鍒嗕粠琚?dislike 鍋囬槼鎬у帇鍒?0.20 鈫?鎭㈠鍒扮湡瀹?0.51-0.52,鏂板€欓€夋渶楂?likes 涔熶粠琚帇鍒?0.13 鈫?鐪熷疄 0.40-0.48銆?
- **DelightScorer threshold 鍚屾鎸?bge-m3 瀹為檯鍒嗗竷涓嬭皟**:0.65/0.75 榛樿鏄寜 Gemini embedding 鏍囩殑,鍦?bge-m3 涓婄瓑浜?姘歌繙涓嶈Е鍙?delight"銆傚熀浜庡疄娴?100 鏉℃睜鍐?top-relevance 鍊欓€夌殑瀹為檯鍒嗘暟鍒嗗竷(max=0.485, p95=0.440, p90=0.428),`DEFAULT_DELIGHT_THRESHOLD` 浠?0.65 鏀规垚 0.45(瀵瑰簲 ~p95 鐨?鐗瑰埆鍖归厤"浣嶇疆),`CONSERVATIVE_DELIGHT_THRESHOLD` 浠?0.75 鏀规垚 0.55銆?
- **DelightScorer "embedding 瀛愮郴缁熸浜?鍛婅鏀圭敤鐩存帴鎺㈡祴**:涔嬪墠鍒ゅ畾鏉′欢鏄?4 涓?embedding 淇″彿鍚屾椂涓?0,浣嗕竴涓敤鎴峰叴瓒ｈ寖鍥翠箣澶栫殑鍚堟硶鍐呭(濡?tech-only 鐢ㄦ埛鐪嬪埌涓€鏉″巻鍙茬邯褰曠墖鏍囬)涔熶細鍏?0,瀵艰嚧鍛婅姣忔潯 candidate 閮?false-positive銆傛敼鎴愬崟娆?`embed(content_text)` 鎺㈡祴,鍙湁 provider 鐪熻繑绌哄悜閲忔墠鍛婅銆?

### 娴嬭瘯

- 鏂板 storage / refresh runtime 鍥炲綊娴嬭瘯瑕嗙洊灏忕孩涔︽潵婧愭棌褰掍竴銆乽nder-quota suppressed 澶嶆椿銆佹弧姹犺鍓紶閫掑皬绾功閰嶉銆?
- 鏂板 LLM provider 鍥炲綊娴嬭瘯瑕嗙洊 DeepSeek 鏅€氭ā寮忕┖鍐呭閲嶈瘯銆?
- 鏂板 `test_observe_matches_long_chinese_composite_phrase` 瑕嗙洊 bigram 鍖归厤鍏滃簳锛堝懡涓湡瀹炴爣棰樸€佷笉璇腑鏃犲叧鍐呭锛夈€?

---

## v0.3.30: 鏃ュ織鑷姩娓呯悊锛堟寜澶у皬 / 鎸夊勾榫?/ 鎸夋€婚绠楋級锛?026-05-02锛?

鐢ㄦ埛瀹炴祴鍙戠幇 `logs/` 鐩綍涓嬫湁鍑犱釜鏈墭绠＄殑澶ф枃浠跺崰鐩?`backend-restart.log` 2.2 GB銆乣openbiliclaw-restart.log` 296 MB,鍔犱笂鍘熸湰鐨?`openbiliclaw.log` 1 GB 涓绘棩蹇?鏁翠釜鐩綍 5 GB+銆傚師 `RotatingFileHandler` 鍙 *鏈韩閰嶇疆鐨勯偅涓? 鏂囦欢,鍏朵粬 stdout-redirect 鍑烘潵鐨勮剼鏈棩蹇楀畬鍏ㄦ病浜虹銆傝ˉ涓€濂?unmanaged 鏃ュ織鍏滃簳娓呯悊銆?

### 鏂板

- **鍚姩鏃惰嚜鍔?sweep `logs/` 鐩綍鐨?unmanaged 鏂囦欢**(`logging_setup._sweep_unmanaged_logs`):
  1. 鍗曟枃浠惰秴杩?`unmanaged_truncate_mb` MB 鈫?鐩存帴 `truncate` 涓?0(鐣欎竴琛?marker)銆備笓娌?`backend-restart.log` 杩欑被琚剼鏈棤闄?append 浣嗛」鐩唬鐮佹帶鍒朵笉鍒扮殑鏂囦欢
  2. mtime 瓒呰繃 `unmanaged_max_age_days` 澶?鈫?鐩存帴鍒犻櫎
  3. 鏁翠釜 logs/ 鐩綍(鍚?managed)鎬诲ぇ灏忚秴杩?`aggregate_budget_mb` MB 鈫?鎸?mtime 浠庢渶鏃х殑 *unmanaged* 鏂囦欢寮€濮嬪垹,鐩村埌鍥炲埌棰勭畻鍐呫€?*Managed 鏂囦欢(`<filename>` + `<filename>.N`)姘歌繙涓嶈杩欎釜 pass 鍒?*(rotation 鑷繁绠?
  
  姣忎釜 truncate / delete 閮芥墦 INFO 鏃ュ織,daemon 鍚姩鏃?tail 涓€鐪艰兘鐪嬪埌娓呬簡浠€涔?
- **`openbiliclaw logs-prune` CLI**(榛樿 dry-run)鈥斺€?鎵嬪姩瑙﹀彂鍏滃簳娓呯悊,鍙复鏃剁敤鏇存縺杩?/ 鏇翠繚瀹堢殑闃堝€笺€俙--apply` 鎵嶇湡鏀规枃浠躲€俁ich 琛ㄦ牸鎸?traffic-light 鑹叉樉绀?keep / truncate / delete (age) / delete (budget) 鍥涚 plan
- 4 涓柊鍗曟祴瑕嗙洊 truncate / age delete / aggregate budget eviction / sweep_unmanaged=False 璺宠繃

### 榛樿鍊煎彉鍖?褰卞搷鏂拌)

- **`max_file_size_mb` 1024 鈫?100**:1 GB 鍗曟枃浠跺お澶?缁濆ぇ澶氭暟 daemon 璺戜袱澶╁氨鎶婄鐩樺悆鎺変竴鎴€?00 MB 脳 2 backups = 200 MB 涓婇檺,澶?1-2 鍛?INFO 绾ф棩蹇?
- **`aggregate_budget_mb = 500`**(鏂?:鏁翠釜 `logs/` 鐩綍鎬荤鐩橀绠?500 MB,unmanaged 瓒呭嚭鎸夋椂闂磋瘎鏈€鏃╁垹
- **`unmanaged_truncate_mb = 200`**(鏂?:鍗曟枃浠惰秴杩?200 MB 鐩存帴 truncate
- **`unmanaged_max_age_days = 30`**(鏂?:30 澶╁墠鐨?unmanaged 鏂囦欢鐩存帴鍒?

### 淇敼

- `LoggingConfig` 鍔?3 涓柊瀛楁(`aggregate_budget_mb` / `unmanaged_truncate_mb` / `unmanaged_max_age_days`),鏃?config.toml 娌℃湁杩欎簺瀛楁涔熷吋瀹?鐢?dataclass 榛樿鍊?
- `configure_logging` 鏂板 `sweep_unmanaged: bool = True` kwarg銆侰LI `_initialize_logging` 妫€娴?`logs-prune` 鍛戒护鏃朵紶 `False`,閬垮厤 dry-run 琚叏灞€ callback 椤烘墜娓呮帀(鍚﹀垯 dry-run 绛変簬鑷姩 apply)
- `config.example.toml` 鍚屾鏇存柊,鍔犱笂 4 琛屾敞閲婅鏄庢瘡涓槇鍊肩殑鎰忎箟

### 淇

- **鎵╁睍鑷姩鍚屾 B 绔?Cookie 鐨勯瑁呯珵鎬?*:濡傛灉鎵╁睍宸插畨瑁呬絾鏈湴鍚庣杩樻病璧锋潵,涔嬪墠棣栨 POST 澶辫触鍚庤绛?cookie 鍙樺寲鎴栨渶闀?1 灏忔椂 alarm 鎵嶄細閲嶈瘯,瀵艰嚧 AI agent 涓€鍙ヨ瘽瀹夎鍚庣湅璧锋潵"鑷姩鑾峰彇涓嶅埌 Cookie"銆傜幇鍦?service worker 鍐峰惎鍔ㄤ細鍚姩 cookie sync,POST 澶辫触鏃舵妸 alarm 涓存椂鍒囧埌 1 鍒嗛挓閲嶈瘯,鎴愬姛鍚庢仮澶?60 鍒嗛挓鍒锋柊;`startCookieSync()` 涔熸敼鎴愮湡姝ｅ箓绛?閬垮厤閲嶅娉ㄥ唽 `chrome.cookies.onChanged` 鐩戝惉鍣ㄣ€?
- **鍚庣鍙富鍔ㄨ姹傛墿灞曞洖浼?Cookie**:`/api/runtime-stream?client=background` 寤鸿繛鏃?濡傛灉鍚庣瑙ｆ瀽涓嶅埌 B 绔?Cookie,浼氬厛鍙?`bilibili_cookie_sync_requested`;鎵╁睍鏀跺埌鍚庣珛鍗?POST 褰撳墠娴忚鍣?Cookie 鍒?`/api/bilibili/cookie`銆傝繖璁╁悗绔惎鍔ㄥ悗涓嶇敤绛変笅涓€杞?alarm,鑳戒富鍔ㄦ媺璧蜂竴娆?Cookie 鍚屾銆?
- **AI agent 涓€鍙ヨ瘽瀹夎涓嶅啀璺宠繃 embedding / 灏忕孩涔︾‘璁?*:`agent_bootstrap.py` 鏂板 `--yes-xhs` / `--no-xhs` 骞跺湪 auto-init 鍓嶆鏌ヤ袱涓樉寮忓喅绛?embedding 鏂规鍜屽皬绾功鏀惰棌 / 鐐硅禐 opt-in銆傚嚟鎹綈鍏ㄤ絾娌￠棶杩欎袱椤规椂,bootstrap 杩斿洖 `status=needs_decisions` 鑰屼笉鏄洿鎺ヨ窇 `openbiliclaw init`;install.sh / install.ps1 鐨勭姸鎬佸潡浼氭妸榛樿 `--embedding-provider ollama --embedding-model bge-m3 --no-xhs` 绀轰緥鍛戒护鎵撳嵃鍑烘潵,璁╂櫤鑳戒綋蹇呴』鍏堥棶鐢ㄦ埛鍐嶇户缁€?
- **鎻掍欢鎺ㄨ崘鍒楄〃婊氬埌搴曠画椤典笉鍐嶅崱浣?*:side panel 鎺ㄨ崘 tab 鍦ㄩ娆℃覆鏌撱€佸垏鍥炴帹鑽愰〉鍜岃拷鍔犲畬鎴愬悗閮戒細閲嶆柊妫€鏌ヤ竴娆″簳閮ㄨ窛绂?涓嶅啀鍙緷璧栨柊鐨?scroll 浜嬩欢瑙﹀彂 `/api/recommendations/append`銆?
- **鎻掍欢鍒濆鍖栧悗涓嶅啀璇樉绀?init 鎻愮ず**:popup 绌烘帹鑽愮姸鎬佷細浼樺厛璇嗗埆 `manual_refresh_state=running`銆乸ending signal 鍜屽€欓€夋睜琛ヨ揣淇″彿;鍒濆鍖栧悗棣栬疆琛ヨ揣 / 姹犲瓙宸叉湁鍐呭浣?`initialized` 鏍囪鐭殏婊炲悗鏃?涓嶅啀缁х画鏄剧ず鈥滆繕娌″畬鎴愬垵濮嬪寲鈥濄€?
- **鎻掍欢鍙戝竷鐗堟湰鎺ㄨ繘鍒?`extension-v0.3.3`**:鏈鎻掍欢 release 鍖呭惈 Cookie 鑷姩鍚屾绔炴€併€佹帹鑽愮画椤靛拰鍒濆鍖栫姸鎬佹彁绀轰慨澶嶃€?

### 娴嬭瘯

- 鍏ㄥ 944 閫氳繃 / 16 澶辫触(鍩虹嚎) / 15 璺宠繃 鈥?0 鏂板洖褰?

---

## v0.3.29: prompt-cache 閫氱敤鍖栨敼閫?+ 鍛戒腑鐜囪娴?+ Claude 鏄惧紡 marker锛?026-05-02锛?

涓?daemon 闀胯窇鎴愭湰鎷変綆 50-80% 鍋氭灦鏋勬€ч摵鍨€傛寲鍒?v0.3.26 璁¤垂鍙拌处娌℃湁 cache 瀛楁(provider 鎶ヤ絾娌″綊涓€鍖?,v0.3.27 prompt builders 澶氫釜鎶?per-call 鍙橀噺濉炶繘 system 娑堟伅(璁?provider-side 鑷姩缂撳瓨鍛戒腑鐜囨案杩滄槸 0),Claude 杩欑"鏄惧紡 marker 鎵嶆縺娲? 鐨?provider 瀹屽叏娌℃帴鍏ャ€備笁涓眰涓€璧锋敼銆?

### 鏂板 (Layer 3 鈥?璺?provider 鐨勫懡涓巼瑙傛祴鍩虹)

- **姣忓 LLM provider 鎻愬彇 cache 瀛楁骞?normalize 鍒?`LLMResponse.usage["cached_input_tokens"]`** 鈥斺€?OpenAI 绯?(`prompt_tokens_details.cached_tokens`)銆丏eepSeek (`prompt_cache_hit_tokens`)銆丆laude (`cache_read_input_tokens`,鍙﹀淇濈暀 `cache_creation_input_tokens` 鍗曠嫭璁拌处)銆丟emini (`usage_metadata.cached_content_token_count`),OpenRouter / 涓浆绔?/ 鍥戒骇瀹樻柟鍥犱负缁ф壙 OpenAIProvider 鑷姩鑾风泭
- **`pricing.CACHE_HIT_DISCOUNT`** 琛?+ `estimate_cost(..., cached_tokens=N)` 鎵╁睍 鈥斺€?鍚勫 cache 鎶樻墸鐜囧垪琛?DeepSeek 0.10 / OpenAI 0.50 / Claude 0.10 / Gemini 0.25 / Ollama 0 / 鏈煡 0.5),split prompt_tokens 鎸?cached/non-cached 鍒嗗埆璁¤垂
- **`Database.llm_usage` 鍔?`cached_input_tokens` 鍒?+ migration `_ensure_llm_usage_cache_columns`** 鈥斺€?瀛橀噺 DB 鑷姩 backfill,鏂拌皟鐢ㄦ寜 cache 鎶樻墸瀛樿处銆俙query_llm_usage_by_caller` / `_total` / `_since_id` 鍏ㄩ儴杩斿洖 cache 瀛楁
- **`UsageRecorder` 鎻愬彇 cache 瀛楁骞跺啓搴?* 鈥斺€?INFO 鏃ュ織澶氫簡 `cache_hit=4000/8500 (47%)` 娉ㄩ噴,鐩存帴 tail daemon 鐪嬪疄鏃跺懡涓巼
- **`openbiliclaw cost --by caller` 鍔?cache 鍛戒腑鐜囧垪** 鈥斺€?绾?(<30%) / 榛?(30-60%) / 缁?(>60%) 涓夎壊,绾㈣壊 caller = prompt 鍓嶇紑鏈夋薄鏌?鐩存帴瀹氫綅鍒拌 audit 鐨?builder
- **`init` 鏀跺熬鐨?cost summary 涔熷睍绀?per-caller cache 鍛戒腑鐜?* 鈥斺€?璺戝畬涓€娆?init 鐩存帴鐪嬪懡涓垎甯?

### 閲嶆瀯 (Layer 1 鈥?璁?system_prompt 100% 闈欐€佷互婵€娲?provider 缂撳瓨)

涔嬪墠 audit 鍑?`build_batch_content_evaluation_prompt` / `build_content_evaluation_prompt` / `build_recommendation_expression_prompt` / `build_batch_expression_prompt` / `build_delight_reason_prompt` 杩?5 涓渶鐑偣鐨?builder 閮芥妸 `source_hint` / `_platform_friend_label` / `_platform_content_label` / `_render_tone_profile` 鎷兼帴鍒?system_prompt,**姣忔鍒?strategy / platform / 鐢ㄦ埛 鈫?鏁翠釜 ~3500 token 鐨?system prompt 澶遍厤,provider 鑷姩 cache 姘歌繙鍛戒笉涓?*銆傛敼閫犳垚"system 100% 闈欐€?+ 鎵€鏈夊彉閲忔尓鍒?user_prompt 鍓嶇紑":

- 5 涓?builder 鍏ㄩ儴鐢?module-level 甯搁噺 `_<NAME>_SYSTEM_PROMPT` 琛ㄨ揪 system,姣忎釜甯搁噺閮芥槸瀛楃涓插瓧闈㈤噺(涓嶈兘 f-string,涓嶈兘鎷兼帴,涓嶈兘 substitute);鎵€鏈夊師 system 閲岀殑鍙橀噺(source_context / source_platform / tone_profile / friend_label / content_label)鎸埌 user_prompt
- user_prompt 椤哄簭: 骞冲彴 / 涓婁笅鏂?/ tone (semi-stable per user) 鈫?profile (slow-changing) 鈫?content_batch (every call)銆傝繖鏍?provider auto-cache 涓嶄粎鍛戒腑 system,椤哄簭鍚堢悊鏃惰繕鑳藉欢浼稿懡涓?user 鍓嶇紑
- JSON 搴忓垪鍖栧叏閮ㄥ姞 `sort_keys=True`,闃叉 dict 椤哄簭鍙樺姩璁?cache miss
- system 閲屽姞涓€鍙?"涓嬮潰 user 娑堟伅浼氱粰鍑?<X>(...)" 璁?LLM 鏄庣‘鐭ラ亾鍘诲摢閲岃鍙橀噺(prompt engineering 涓婁笉鎹熷け)

### 渚嬪 (Layer 1 鍗曠敤鎴峰満鏅笅淇濈暀 user-specific system)

- **`build_socratic_dialogue_prompt` 淇濇寔鍘熸牱** 鈥斺€?瀹冪殑 system 鍖呭惈 friend_label / tone / core_memory_text銆傚湪 OpenBiliClaw 杩欑**鍗曠敤鎴峰満鏅?*涓?per-user 鐘舵€佸湪璇ョ敤鎴风殑澶氭璋冪敤閲岀ǔ瀹?鈫?cache 浠嶅懡涓€傚鐢ㄦ埛閮ㄧ讲鎵嶉渶瑕侀噸鏋?鐩墠涓嶅繀

### 宸ョ▼绾緥 (Layer 4)

- **`CLAUDE.md` 鏂板 "LLM Prompt-Cache Convention" 娈?* 鈥斺€?缁欐湭鏉ヨ础鐚€呯珛瑙勫垯:浠讳綍鏂?prompt builder MUST 婊¤冻 system 100% 闈欐€?JSON 搴忓垪鍖栧繀椤?deterministic,鎵€鏈夊彉閲忓叆 user_prompt
- **`test_llm_prompts.py::test_prompt_builder_system_messages_are_call_invariant`** 鈥斺€?鑷姩鍖栧厹搴?閬嶅巻鎵€鏈?prompt builder,涓ょ粍涓嶅悓 input 鈫?assert system msg byte-identical,杩濆弽鍒欐姤閿欏苟鎸囨槑 cache-poisoning builder

### Layer 2 鈥?Claude 鏄惧紡 cache marker

- **`ClaudeProvider` 鑷姩缁?system message 鎵?ephemeral cache_control 鏍囪** 鈥斺€?Anthropic prompt cache 鏄樉寮忔満鍒?绾瓧绗︿覆 `system="..."` 姘歌繙涓嶇紦瀛?蹇呴』鐢?list-of-blocks 褰㈠紡 + `cache_control: {"type": "ephemeral"}` 鎵嶄細婵€娲汇€傛柊澧?`_render_system_param()` 鎶?system 鏂囨湰鍖呮垚鍗?block 鍒楄〃 + cache marker,5min TTL,90% off on cache reads,棣栨鍐?+25% 鍔犱环銆傜郴缁?prompt 鐭簬 per-model 闃堝€兼椂(Sonnet 1024 / Opus-Haiku 2048 token)Anthropic 闈欓粯蹇界暐 marker,鎵€浠ヨ繖涓敼鍔ㄥ鐭?prompt 涔熷畨鍏?
- 2 涓柊鍗曟祴 covering: marker 姝ｇ‘鎻掑叆鍒?system list-of-blocks 褰㈠紡,浠ュ強 `cache_read_input_tokens` / `cache_creation_input_tokens` 閫氳繃 `LLMResponse.usage` 姝ｇ‘娴佽浆

### 浠嶆湭鍋?deferred)

- **Gemini 鏄惧紡 Context Caching API** 鈥斺€?Gemini 鐨?prompt cache 涓嶆槸 in-line marker,鑰屾槸鍙﹁捣涓€涓?`cachedContents.create()` API 鎻愬墠涓婁紶 stable 閮ㄥ垎寰楀埌 `cache_id`,鐒跺悗璋?`complete()` 鏃跺紩鐢?cache_id銆傞渶瑕?cache_id LRU 姹?+ TTL 绠＄悊,鏀瑰姩閲忔瘮 Claude 澶у緱澶氥€傚厛瑙傚療 Layer 3 鏁版嵁 鈥斺€?濡傛灉鐢?Gemini 鐨勪汉澶氫笖鍛戒腑鐜囩‘瀹炰綆,鍐嶆姇璧?

### 娴嬭瘯

- 8 涓柊鍗曟祴瑕嗙洊 cache 鎶樻墸璁＄畻 / per-caller 鎸佷箙鍖?/ 璺?provider 鍛戒腑瀛楁 round-trip / Claude cache_control marker 娉ㄥ叆 / Claude cache_read+creation token 鎻愬彇
- audit invariant 娴嬭瘯瑕嗙洊 6 涓?cache-friendly builder
- 鍏ㄥ 940 閫氳繃 / 16 澶辫触(鍩虹嚎) / 15 璺宠繃 鈥?0 鏂板洖褰?

### 棰勬湡鏁堟灉

- DeepSeek 榛樿鍦烘櫙:`discovery.evaluate_batch` 5 娆?strategy 璇勪及,浠庡師鏈?5 娆?cold(~17500 input tokens 鍏ㄦ敹閽?鈫?绗?1 娆?cold + 鍚?4 娆″懡涓?~3500 token system,**璇?caller 鎬绘垚鏈珛鍗崇爫 60-70%**
- 鍚屾晥鏋滈€傜敤浜?`recommendation.evaluate_batch` / `_expression` / `_delight_reason` / `_content_evaluation`
- OpenAI 50% / Claude 90% / Gemini 75% cache 鎶樻墸,鑷姩娲?DeepSeek/OpenAI/涓浆绔?鏃犻渶鏀?SDK 璋冪敤,鏄惧紡娲?Claude)鐢?ClaudeProvider 鍐呴儴鑷姩娉ㄥ叆 marker
- 璺戜竴娈垫椂闂村悗 `openbiliclaw cost --by caller --days 7` 搴旇鑳界湅鍒伴《灞?caller 鐨勫懡涓巼浠?0 璺冲埌 60-80%

### 涓嬩竴姝?

- Gemini 鏄惧紡 Context Caching 绛夋暟鎹┍鍔ㄥ喅绛?瑙佷笂 deferred 娈?
- 鏁版嵁椹卞姩鐨勪紭鍖?鐪?`--by caller` 鍛戒腑鐜?< 60% 鐨?caller,閫愪釜 audit 鏄笉鏄柊鍔犵殑 builder 娌￠伒瀹?cache 鍏害

---

## v0.3.28: LLM 璐圭敤瑙傛祴鍏ㄩ摼璺墦閫氾紙caller 鏍囩 + 瀹炴椂鏃ュ織 + per-init 鎬荤粨锛夛紙2026-05-02锛?

涔嬪墠 `UsageRecorder` 鐨?`caller` 瀛楁铏界劧鍦ㄨ〃缁撴瀯 + recorder API + DB 鏌ヨ閲岄兘宸插氨浣?浣?*鏁翠釜浠ｇ爜搴撻噷娌℃湁涓€涓?LLM 璋冪敤鐐圭湡鐨勪紶 `caller="<module>"`** 鈥斺€?鎵€鏈夎鐨?caller 閮芥槸绌哄瓧绗︿覆,鎰忓懗鐫€褰撳勾璁捐鐨?per-module 璐圭敤 attribution 瀹屽叏澶辨晥,`openbiliclaw cost` 鑳界湅鍒?by-day / by-provider/model 浣嗙湅涓嶅嚭"閽辫姳鍦ㄥ摢涓€灞?,杩欐槸鐢ㄦ埛鏈€鍏冲績鐨勮瑙掋€傝ˉ鍏?

### 鏂板

- **27 涓?LLM 璋冪敤鐐瑰叏閮?wire 涓?caller 鏍囩** 鈥斺€?瑕嗙洊 `recommendation.evaluate_batch / .delight_reason / .write_expression / .expression`銆乣discovery.trending.rids / .search.queries / .explore.queries / .evaluate_single / .evaluate_batch`銆乣eval.scenario_gen / .relevance / .specificity / .query_quality`銆乣soul.preference / .preference.chunk / .profile_build / .insight / .awareness / .role_update / .values_update / .core_update / .speculate / .dialogue / .dialogue.tools / .dialogue.tool_followup / .dialogue_insight`銆乣sources.{platform}.extract / sources.xhs.keyword_gen`銆乣api.sentiment`銆傝繕鎶?`LLMService.complete_with_tools` / `complete_socratic_dialogue` 涔熷姞浜?`caller` 褰㈠弬骞?forward 鍒板唴閮?`complete_with_core_memory` 鈥斺€?涔嬪墠杩欎袱涓柟娉曟紡鎺?`caller`,璁?dialogue 璺緞鐨勮垂鐢ㄥ叏褰掑埌 untagged
- **`UsageRecorder.record()` 姣忔 LLM 璋冪敤鎵?INFO 鏃ュ織** 鈥斺€?`[llm-cost] caller=discovery.evaluate_batch model=deepseek-v4-flash tokens=850鈫?30 鈮?楼0.0010`銆倀ail daemon 鏃ュ織 (`journalctl -fu openbiliclaw` / `docker logs -f openbiliclaw-backend`) 灏辫兘鐪嬭垂鐢ㄥ疄鏃剁疮绉?涓嶇敤绛夎窇瀹屾墠鏌?
- **鍗曟璋冪敤瓒呴槇鍊兼椂鎵?WARN** 鈥斺€?榛樿 楼0.10 闃堝€?鍙€氳繃 `OPENBILICLAW_LLM_EXPENSIVE_CNY` 鐜鍙橀噺璋?銆傛姄 runaway prompt(蹇樹簡鎴柇鍘嗗彶 / 璇紑 reasoning_effort=max / 鍗?batch 澶ぇ)鐢?WARN 琛屽寘鍚?caller / model / token / 瀹為檯鑺辫垂,瀹氫綅寰堝揩
- **`openbiliclaw cost --by caller`** 鈥斺€?`cost` CLI 鍔犱簡绗笁涓〃(by-caller),灞曠ず鎸夋ā鍧楃殑璐圭敤鍗犳瘮 + token 鏁般€俙--by all`(榛樿) / `--by day` / `--by provider` / `--by caller` 鍥涙。
- **init 缁撴潫鏃惰嚜鍔ㄦ墦鍗版湰娆?init 鐨?cost summary** 鈥斺€?涓嶇敤鍐嶆墜鍔?`openbiliclaw cost`,init 瀹屾垚鍚庣洿鎺ユ樉绀烘寜 caller 鎷嗗垎鐨勮垂鐢ㄥ崰姣?鏈 init 鎬?N 娆¤皟鐢?鈮?楼X,鍏朵腑 discovery.evaluate_batch 鍗?60% / soul.profile_build 鍗?15% 绛?銆傞潬 `Database.max_llm_usage_id() / query_llm_usage_since_id()` 鍦?init 鍏ュ彛蹇収琛?id,鍑哄彛鍙嶆煡,鎶婄疮绉?usage 闄愬畾鍒版湰娆?init 绐楀彛
- `pricing.py` 鍔犲父閲?`EXPENSIVE_CALL_CNY_THRESHOLD = 0.10`(鍙幆澧冨彉閲忚鐩?

### 淇敼

- `Database.query_llm_usage_by_caller(days=N)` 鏂版柟娉?SQL 鎸?caller 鍒嗙粍鑱氬悎,`ORDER BY cost_cny DESC` 璁╂渶璐电殑璋冪敤鎺掔涓€
- `LLMService.complete_with_tools` / `complete_socratic_dialogue` 绛惧悕鍔?`caller: str = ""`,forward 鍒?inner `complete_with_core_memory(caller=caller)`

### 娴嬭瘯

- 淇簡 ~30 涓祴璇?fake 璁╁畠浠殑 `complete_*` 绛惧悕涔熸帴 `caller` 褰㈠弬(鍚﹀垯鐢熶骇璋冪敤鐐逛紶 `caller=...` 浼氳 fake 鎶?TypeError)銆傛壒閲忔敼浜?17 涓祴璇曟枃浠?
- 鍏ㄥ娴嬭瘯 16 澶辫触 / 931 閫氳繃,璺?baseline 瀹屽叏涓€鑷?鈥斺€?0 鏂板洖褰?

---

## v0.3.27: 瀹夎鏂囨。鍏ㄩ潰鍚屾鑷?init wizard 褰撳墠褰㈡€?+ DeepSeek V4 榛樿妯″瀷锛?026-05-02锛?

### 淇敼

- `docs/openclaw-quickstart.md` 鈥斺€?鎶?`init` 4 闃舵鍚戝鎻忚堪鍚屾鍒?v0.3.27+ 褰撳墠褰㈡€?Phase 1 LLM(DeepSeek 榛樿 / Ollama+缃戝叧鏀惰繘楂樼骇)銆丳hase 2 閰嶇疆銆丳hase 3 Embedding(Ollama bge-m3 榛樿)銆丳hase 4 Per-module 瑕嗙洊銆傛柊澧炵嫭绔嬬殑 馃尭 灏忕孩涔︽暟鎹彲閫夐棶棰?鍦?wizard 涔嬪悗銆佹暟鎹媺鍙栦箣鍓?,骞舵槑纭?鎵╁睍浼氬湪娴忚鍣ㄥ紑鍓嶅彴 tab 鎶竴娆＄劍鐐?鐨勭湡瀹炶涓恒€俙init` 闃舵鍒楄〃鏂板鍙€夊皬绾功鎷夊彇姝?骞舵彁绀虹敤 `openbiliclaw cost` 鏌ョ湅鑺辫垂
- **DeepSeek 榛樿妯″瀷 `deepseek-chat` 鈫?`deepseek-v4-flash`** 鈥斺€?鏃?`deepseek-chat` / `deepseek-reasoner` DeepSeek 瀹樻柟灏嗕簬 2026/07/24 寮冪敤銆俙config.example.toml` 鏃╁氨鎸囧悜 v4-flash,浣?`cli.py` `_PROVIDER_DEFAULTS` 杩樺湪鍐?`deepseek-chat`,瀵艰嚧 init 鍚戝缁欏嚭杩囨湡鐨勯粯璁ゅ€笺€備慨澶嶇偣:`_PROVIDER_DEFAULTS["deepseek"].model`銆乣_LLM_MENU` hint銆丳hase 2 閰嶇疆闃舵鏂板 `_PROVIDER_MODEL_HINT` 琛?姣忎釜 provider 鍦?prompt 妯″瀷鍚嶅墠鏄剧ず涓€琛屽彲閫夋竻鍗?DeepSeek 閭ｈ鏄庣‘鍒?v4-flash / v4-pro 涓ゆ。 + 鏃у悕寮冪敤鏃ユ湡),璁╃敤鎴锋槑纭‘璁よ€屼笉鏄洖杞﹁烦杩囦竴涓湅涓嶆噦鐨勫瓧绗︿覆銆傚悓姝ユ洿鏂?`docs/{openclaw-quickstart,docker-deployment,agent-install,agent-deployment,modules/config,modules/llm}.md`銆乣scripts/agent_bootstrap.py` 绀轰緥銆乣extension/popup/popup.html` placeholder銆乣pricing.py` 鍔?`deepseek-v4-pro` 琛?
- **OpenAI 鍗忚鍏煎: 9-preset 瀛愯彍鍗?(Kimi / MiniMax / 閫氫箟 / 鏅鸿氨 / Yi / 涓浆绔?/ 鑷缓 / Azure / 鍏跺畠)** 鈥斺€?涔嬪墠閫夌 7 椤?"OpenAI 鍗忚鍏煎" 灏辨帀鍒颁竴涓鐢ㄦ埛鎵嬪～ Base URL + 妯″瀷鍚嶇殑瑁?prompt,鏅€氱敤鎴蜂笉鐭ラ亾姣忓鐨?endpoint 闀夸粈涔堟牱,涓浆绔?/ Azure / vLLM 涓夌鐢ㄦ硶鐨勫樊寮備篃娌¤娓呫€傛柊澧?`_OPENAI_COMPAT_PRESETS` 琛?+ `_prompt_openai_compat()` helper:閫夌 7 椤瑰悗寮瑰嚭 9 琛屽瓙鑿滃崟,**Base URL + 榛樿妯″瀷鎸?preset 鑷姩濉ソ**(Kimi `api.moonshot.cn/v1` + `moonshot-v1-8k`;MiniMax `api.minimaxi.chat/v1` + `abab6.5s-chat`;閫氫箟 `dashscope.aliyuncs.com/compatible-mode/v1` + `qwen-plus`;鏅鸿氨 `open.bigmodel.cn/api/paas/v4` + `glm-4-flash`;Yi `api.lingyiwanwu.com/v1` + `yi-medium`;涓浆绔?/ Azure / vLLM-LMStudio 涔熼兘鍚勮嚜鏈夊悎鐞嗙殑 prompt 寮曞)銆傛瘡涓?preset 鍦?prompt 妯″瀷鍚嶅墠鏄剧ず璇ュ鐨?鍙€夋ā鍨?娓呭崟銆傚悓姝?`docs/{openclaw-quickstart,docker-deployment,agent-install}.md` 鍏ㄩ儴灞曞紑 9 涓?preset 鐨勬竻鍗?AI agent 娉ㄩ噴閲屽姞"鐪嬪埌 Kimi / 閫氫箟 / 鏅鸿氨 / Yi / Moonshot / MiniMax / Qwen / GLM / 涓浆绔?/ OneAPI / Azure / vLLM / LMStudio 绛夊叧閿瘝鏃?浼樺厛寮曞璧扮 7 椤瑰瓙鑿滃崟"
- **榛樿妯″瀷鍏ㄩ潰鍒锋柊鍒?2026-05 褰撳墠绾夸笂(涔嬪墠鍑犱箮鍏ㄩ儴杩囨湡)** 鈥斺€?鐢ㄦ埛瀹炴祴鍙戠幇 init 鍚戝鎺ㄧ殑榛樿妯″瀷鍑犱箮閮藉凡鍋滄湇鎴栬鏇夸唬銆俉eb 鎼滅储纭姣忓褰撳墠绾夸笂鎯呭喌鍚?閫愰」鏇存柊 `_PROVIDER_DEFAULTS`銆乣_LLM_MENU` hint銆乣_PROVIDER_MODEL_HINT`銆乣_OPENAI_COMPAT_PRESETS`銆乣config.example.toml`銆乣pricing.py`:
  - **OpenAI**: `gpt-4o-mini` 鈫?`gpt-5-nano`(GPT-5 nano 鏄綋鍓嶆渶渚垮疁娆?$0.05/$0.4 per M;gpt-4o 绯诲垪 2026-02 宸蹭粠 ChatGPT 閫€褰?銆傚畬鏁村彲閫? gpt-5-nano / gpt-5.4-nano / gpt-5.4-mini / gpt-5.5(4/2026 鏃楄埌)/ gpt-5.5-pro
  - **Claude**: `claude-sonnet-4-5-20250929` 鈫?`claude-sonnet-4-6`(Sonnet 4.6 1M ctx)銆傚畬鏁? claude-haiku-4-5(渚垮疁)/ sonnet-4-6(榛樿)/ opus-4-7(鏃楄埌 / agentic 鏈€寮?
  - **Gemini**: `gemini-2.0-flash-exp` 鈫?`gemini-2.5-flash`(2.0-flash-exp 宸叉窐姹?銆傚畬鏁? 2.5-flash(榛樿)/ 3-flash-preview(鏂?/ 3.1-pro(鏃楄埌)/ 3.1-flash-lite-preview(鏈€渚垮疁)
  - **OpenRouter**: `openai/gpt-4o-mini` 鈫?`openai/gpt-5-nano`(瀵归綈 OpenAI 榛樿)
  - **Ollama**: `llama3` 鈫?`qwen2.5:7b`(椤圭洰涓枃浼樺厛,qwen2.5 姣斿悓灏哄 llama3 涓枃濂藉緱澶?
  - **Kimi**: `moonshot-v1-8k`(2026-05-25 鍋滄湇)鈫?`kimi-k2.6`(鏈€鏂?/ 256K ctx / 澶氭ā鎬?銆侭ase URL `api.moonshot.cn/v1` 鈫?`api.moonshot.ai/v1`(鍥介檯绔欎负涓?
  - **MiniMax**: `abab6.5s-chat`(宸茶 M 绯诲垪鏇夸唬)鈫?`MiniMax-M2.7`(4/2026 / 228K ctx / $0.30 ~ $1.20 per M)銆侭ase URL `api.minimaxi.chat/v1` 鈫?`api.minimax.io/v1`
  - **閫氫箟**: 浠嶇敤 `qwen-plus` 鍒悕(鑷姩璺熸渶鏂板揩鐓?褰撳墠 鈫?qwen3.6-plus)銆俥ndpoint 涓嶅彉
  - **鏅鸿氨 ChatGLM**: `glm-4-flash` 鈫?`glm-4.7-flash`(1/2026 鍙戝竷鐨勫厤璐规棗鑸?/ 200K ctx);鍙€?`glm-5`(2/2026 浠樿垂鏃楄埌 / 745B MoE)
  - **Yi**: 浠嶇敤 `yi-medium`,鍦?hint 閲屽姞涓?`yi-lightning`(鏂?/ 蹇?
  - **DeepSeek**: 鉁?涔嬪墠淇浜?浠嶆槸 `deepseek-v4-flash`/`deepseek-v4-pro`
  - **pricing.py**: 鍔?GPT-5 / Claude 4.6+ / Gemini 3.x / Kimi K2.6 / MiniMax M2.7 / Qwen flash-plus-max / GLM 4.7-flash + 5 / Yi spark-medium-large 鐨勫崟浠疯,鏃?V3/V4o/Sonnet 4.5 绛変繚鐣欏吋瀹?
- **OpenAI 鍗忚鍏煎寮曞娣卞害琛ュ己** 鈥斺€?涔嬪墠 9-preset 瀛愯彍鍗曞彧瑙ｅ喅浜?"Base URL + 妯″瀷鑷姩濉? 涓€灞?鐢ㄦ埛瀹為檯杩樹細鍗″湪"鍦ㄥ摢閲岀敵璇?Key / 杩欏鏈嶅姟鍒板簳鏄共鍢涚殑 / 閫夊畬涔嬪悗 embedding 鎬庝箞鍔?杩欎笁涓棶棰樸€傛瘡涓?preset metadata 鎵╁睍涓?`description` / `signup_url` / `domain_alt` / `supports_embedding` / `embedding_alt`,`_prompt_openai_compat()` 閲嶅啓涓哄洓娈靛紡寮曞:
  - **閫夊畬鍚庡睍绀轰竴娈垫湇鍔′粙缁?*(Kimi 鈫?"鍥戒骇闀夸笂涓嬫枃鑰佺墝 256K ctx,闀挎枃妗ｇ悊瑙ｅ己";MiniMax 鈫?"浠ｇ爜 / agent 鍦烘櫙 SOTA,$0.30/$1.20 per M";鏅鸿氨 鈫?"GLM-4.7-Flash 瀹屽叏鍏嶈垂,GLM-5 鏄?Claude Opus 绾?)
  - **鐩存帴鎵撳嵃 Key 鐢宠閾炬帴**(鍥藉唴/鍥介檯涓や釜鍦板潃閮藉垪),鐢ㄦ埛 cmd-click 灏辫兘鍘绘敞鍐?
  - **鍥藉唴鍩熷悕鏇夸唬鎻愮ず**(Kimi `api.moonshot.cn/v1`;MiniMax `api.minimaxi.com/v1`)
  - **棰勬彁閱?embedding 鎬庝箞鍔?*: Kimi / MiniMax / Yi / 鑷缓 娌?embedding endpoint(鎵撳嵃榛勮壊 鈸?鎻愰啋 Phase 3 鑷姩 fallback Ollama bge-m3,鍏嶈垂 / 绂荤嚎);Qwen / GLM / Azure / 涓浆绔?鏈?embedding(鎵撳嵃 馃挕 鎻愮ず Phase 3 楂樼骇閫夐」鍙寚鍚戝悓涓€ base_url)
  - **缁撳熬鎵撳嵃灏嗗啓鍏ョ殑 (base_url, model) 浜屽厓缁?*,catch typo
- **`scripts/agent_bootstrap.py --llm-preset {kimi,minimax,qwen,zhipu,yi,self-hosted,relay,azure,custom}`** 鈥斺€?AI agent 椹卞姩鐨勯潪浜や簰寮忓畨瑁呰矾寰勮ˉ涓€鍒€銆備箣鍓?AI agent 鐢?`--llm-base-url` + `--llm-model` 閰?OpenAI 鍏煎鏈嶅姟鏃?寰楄嚜宸辫浣忔瘡瀹剁殑 endpoint(缁忓父鍐欓敊);鐜板湪 `--llm-preset kimi` 涓€鍙ヨ瘽鎼炲畾,base_url 鍜岄粯璁ゆā鍨嬩粠 `LLM_PRESETS` 琛ㄩ噷鍙?鍜?cli.py 鐨?`_OPENAI_COMPAT_PRESETS` 鍚屾)銆傞殣寮忛攣 `--provider=openai`,鏄惧紡浼犱笉鍚?provider 浼氬啿绐佹姤閿欍€俙--llm-base-url` / `--llm-model` 鍙互 per-field 瑕嗙洊 preset 榛樿銆俙docs/agent-install.md` 鍔?8 琛岀ず渚?姣忓鏈嶅姟涓€琛?
- **OpenAI 鍗忚鍏煎瀛愯彍鍗?鈥?涓浆绔?relay) 鎻愬埌绗?1 浣?+ 涓昏彍鍗曠 7 椤?label 绐佸嚭"涓浆绔?** 鈥斺€?澶嶇洏鍙戠幇鍗忚鍏煎閫夐」鐨勭湡姝ｄ富娴佸満鏅槸"鎴戜拱浜嗕腑杞珯 / OneAPI Key,鎯崇敤浜烘皯甯佷粯閽辫窇 OpenAI/Claude/鍥戒骇妯″瀷"銆備箣鍓嶈彍鍗曟寜"鍥戒骇瀹樻柟 鈫?鑷缓 鈫?涓浆绔?鈫?Azure 鈫?鍏跺畠"鎺掑簭,鎶婃渶甯歌鐨勪腑杞珯鍩嬪湪绗?7 涓?鏅€氱敤鎴峰緱鍏堢炕杩?5 涓浗浜у畼鏂归」鎵嶇湅鍒拌嚜宸辩殑閫夐」銆傞噸鎺掍负:relay 绗?1 浣?default,甯?鈽?鏍囪 + "澶у鏁颁汉閫夎繖涓?鏍囨敞) 鈫?Kimi/MiniMax/Qwen/Zhipu/Yi 鍥戒骇瀹樻柟 鈫?Azure 鈫?鑷缓 鈫?custom 鍏滃簳銆傚悓姝?涓昏彍鍗曠 7 椤?label 鏀逛负"涓浆绔?/ OpenAI 鍗忚鍏煎鏈嶅姟(OneAPI / 鍥㈤槦缃戝叧 / 鍥戒骇瀹樻柟 / Azure / 鑷缓)";瀛愯彍鍗?intro 鏄惧紡鍖哄垎涓夌被鐢ㄦ埛(涓浆绔?/ 鍥戒骇瀹樻柟 / 浼佷笟 Azure-鑷缓);`docs/{openclaw-quickstart,docker-deployment,agent-install}.md` 鍚屾閲嶆帓琛ㄦ牸 + 琛?鍥藉唴缁濆ぇ澶氭暟涓浗鐢ㄦ埛閫夎繖涓氨瀵逛簡"妗嗘灦

---

## v0.3.26: LLM 璁¤垂妯″潡 + 榛樿閰嶇疆鎴愭湰璋冧紭锛?026-05-02锛?

鏂板鏈湴 LLM 鐢ㄩ噺涓庤姳璐硅拷韪?椤烘墜鎶?`config.example.toml` 閲屽嚑涓細璁╂柊瑁呯敤鎴风珛鍒荤儳閽辩殑榛樿鍊兼敼浜嗐€傞噸鍚?daemon 鍚?璺?`openbiliclaw cost` 灏辫兘鐪嬫瘡澶╁疄闄呰姳浜嗗灏戙€?

### 鏂板

- **`openbiliclaw cost` CLI 鍛戒护** 鈥斺€?鏄剧ず鏈€杩?N 澶?LLM 璋冪敤鐨勬寜澶?/ 鎸?provider/model 鍒嗗竷,浠ュ強浼扮畻鑺辫垂銆傛瘡娆℃垚鍔?LLM 璋冪敤閮戒細鍐欎竴鏉″埌 `llm_usage` 琛?timestamp / provider / model / caller / tokens / 浼扮畻鍗曚环)銆俙UsageRecorder` 鏄崟鐐?hook,鎸傚湪 `LLMService.complete_with_core_memory` 涔嬪悗,澶辫触琚悶,涓嶅奖鍝嶄笟鍔＄儹璺緞
- `src/openbiliclaw/llm/pricing.py` 鈥斺€?DeepSeek / OpenAI / Claude / Gemini / OpenRouter / Ollama 鐨?CNY 鍗曚环琛?USD 绯婚涔?7.2 璁╄处闈㈢粺涓€銆傛湭鐭?provider 璧伴€氱敤 fallback 鑰屼笉鏄潤榛?0
- `Database.insert_llm_usage` / `query_llm_usage_by_day` / `query_llm_usage_by_provider` / `query_llm_usage_total` 鈥斺€?鏂拌〃 `llm_usage` + 4 涓煡璇㈡柟娉?SQL 棰勮仛鍚堟寜鏃ユ湡/provider 鍒嗙粍
- `LLMService` 鍔犲彲閫?`usage_recorder` 瀛楁 + `caller` 鍙傛暟(棰勭暀缁欐湭鏉ユ寜妯″潡褰掑洜);daemon 璺緞(`runtime_context`)鑷姩娉ㄥ叆

### 淇敼 default 鍊?褰卞搷鏂拌鐢ㄦ埛)

- **`reasoning_effort = "max"` 鈫?`""`** 鈥斺€?涔嬪墠榛樿寮€鍚?thinking 妯″紡,DeepSeek 姣忔鎸?32K tokens 棰勭畻璁¤垂,鍦?discovery 璇勪及杩欑鎵撳垎绫婚珮棰戝皬浠诲姟涓婂畬鍏ㄦ病蹇呰,鏃ヨ姳璐硅鏀惧ぇ 5-10x銆傛柊瑁呬粠姝や笉鍐嶈鍧?鏃х敤鎴?config.toml 涓嶄細鑷姩鏀?闇€瑕佹墜宸ョ紪杈戞垨鍒?`config.toml` 閲嶆柊璧?init
- **`discovery_cron = "0 */4 * * *"` 鈫?`"0 */8 * * *"`** 鈥斺€?8 灏忔椂涓€娆″彂鐜?vs 4 灏忔椂涓€娆?LLM 璇勪及璋冪敤鍑忓崐,UI 涓婃崲涓€鎵圭殑"鏂伴矞搴?鍩烘湰鏃犳劅(pool 濮嬬粓淇濇寔 600 涓€欓€?銆傞渶瑕佹洿棰戠箒鍙墜宸ヨ皟鍥?

### 娴嬭瘯

- `tests/test_llm_usage.py` 鈥斺€?13 涓崟娴嬭鐩?pricing 鏁板銆丏B round-trip銆乁sageRecorder 杈圭晫(sink=None / sink 鎶涢敊 / response 鏃?usage 瀛楁绛?

---

## v0.3.25: discovery 鎴愭湰浼樺寲(reasoning_effort + pool-aware + batch_size)锛?026-05-02锛?

閽堝 daemon 杩愯涓€澶╃儳 楼10-20 鐨勯棶棰?鎸栧埌涓変釜鐪熷疄鎴愭湰婧?閫愪竴鍘嬪钩銆傜患鍚堜笅鏉ユ棩鑺辫垂浠?楼21 闄嶅埌 楼0.5 宸﹀彸銆?

### 淇 / 浼樺寲

- **discovery 鍐呭璇勪及 batch_size 浠?10 鍗囧埌 30** 鈥斺€?璇勪及鍣ㄥ凡缁忓湪鎵归噺璋冪敤,浣嗛粯璁?batch=10 瀵艰嚧姣忎釜绛栫暐 30 涓€欓€夎鎷?3 娆?LLM 璋冪敤,~3500 tokens 鐨?system prompt 閲嶅浠?3 娆°€傚崌鍒?30(閰嶅悎鐜版湁 `_EVALUATE_BATCH_HARD_CAP=30`)鍋氬埌 1 娆¤瘎浼版悶瀹氫竴涓瓥鐣?token 鎬婚噺闄?54%銆俙max_tokens` 鍚屾浠?8192 鍗囧埌 16384 缁欒緭鍑虹暀 10x 澶寸┖闂淬€傚洖褰掓祴璇?`test_evaluate_content_batch_default_size_30_uses_single_llm_call` 閽夋"25 鍊欓€?= 1 涓?LLM 璋冪敤"
- **pool-aware refresh limit** 鈥斺€?`_requested_refresh_limit` 涔嬪墠姘歌繙 floor 鍦?30,鎰忓懗鐫€ pool 鍦?595/600 鏃惰繕瑕佹瘡涓瓥鐣ヨ姹?30 涓€欓€?鐒跺悗 trim_pool_to_target_count 鎶婂浣欑殑鍏ㄦ爣 suppressed銆傛敼鎴愭寜 gap 缂╂斁:`per_strategy_target = max(5, gap * 3 // 4)`,gap 灏忔椂璇锋眰灏?鐩存帴鐪?50-77% 鐨?LLM 璇勪及璋冪敤銆傜敓浜ф暟鎹?13 澶?11K 缂撳瓨)璇佹槑 88% 璇勪及閮芥槸鑺卞湪琚珛鍗?suppressed 鐨勫唴瀹逛笂鐨勬氮璐?

### 褰卞搷

- 鍗曠函鏀?default `reasoning_effort` 宸茬粡鎶婃棩鑺辫垂浠?楼21 闄嶅埌 楼3.5
- 閰嶅悎 `discovery_cron 8h` + pool-aware sizing + batch_size=30,steady state 鏃ヨ姳璐归檷鍒?楼0.5
- 鍙敤 `openbiliclaw cost` (v0.3.26 鏂板) 瀹為檯楠岃瘉

---

## v0.3.24: 璺ㄦ簮浜嬩欢鏍煎紡缁熶竴 + soul prompt 鎺ュ叆 context锛?026-05-02锛?

鎶?B 绔?/ 灏忕孩涔?/ 鎵╁睍鐐瑰嚮 / 鍙嶉绛夋墍鏈変簨浠舵簮缁熶竴鍒颁竴涓?`build_event()` 鏋勯€犲櫒閲?鎵€鏈?LLM 娑堣垂鑰?preference / awareness / profile_builder)閮界湅涓€浠藉甫鑷劧璇█ `context` 鐨勬爣鍑嗗寲鏁版嵁銆?

### 鏂板

- **`src/openbiliclaw/sources/event_format.py`** 鈥斺€?`build_event()` + `format_event_context()` 鍗曠偣鍏ュ彛,鎵€鏈?producer 閮借蛋瀹?`SOURCE_BILIBILI / SOURCE_XIAOHONGSHU / SOURCE_WEB` 甯搁噺
- **缁熶竴 shape**: `{event_type, title, url?, context: str, metadata: {source_platform, author, ...}}`,`context` 鏄腑鏂囦竴鍙ヨ瘽鎻忚堪(濡?"鍦˙ 绔欑湅浜嗐€婅閫忓巻鍙插彊浜嬨€?浣滆€?鍘嗗彶瀹為獙瀹?),LLM 鐩存帴璇讳笉闇€瑕?schema-aware 缈昏瘧

### 淇敼

- 鎵€鏈変簨浠?producer 閲嶅啓璧?`build_event`:`_history_item_to_event`銆佹敹钘忋€佸叧娉ㄣ€乣xhs_bootstrap_notes_to_events`銆乣/api/events`銆乣/api/feedback`銆乣/api/recommendations/{id}/click`
- `_summarize_history` 杈撳嚭鏂板 `contexts` / `recent_contexts` / `older_contexts`,profile_builder prompt 鍔?rule 13 寮曞 LLM 浼樺厛鐢?context 鐞嗚В琛屼负
- preference / awareness 鍒嗘瀽 prompt 鍔?rule 8/9/5 鍚屾牱寮曞

### 淇

- **DB context 鍒楀弻閲?JSON 缂栫爜 bug** 鈥斺€?`insert_event` 涔嬪墠 unconditional 鎶?string 涔?json.dumps 鍖呬竴灞傚紩鍙?LLM 鐪嬪埌 `\"鍐呭\"`(triple-escaped 鍦?prompt 閲?;鐜板湪 string 鐩村瓨,dict/list 鎵嶇紪鐮?`MemoryManager` 榛樿鍊?`{}` 鈫?`""`

### 娴嬭瘯

- `tests/test_event_format.py` 鈥斺€?15 涓祴璇曡鐩?producer 涓€鑷存€с€乺ound-trip 涓嶅啀 double-encode銆乴egacy dict 鍏煎
- `tests/test_profile_builder.py` 鈥斺€?4 涓祴璇曡鐩栨柊 contexts 杈撳嚭 + B 绔?raw history 鑷姩鍚堟垚 fallback

---

## v0.3.23: xhs 婊氬姩鏀硅繘 + 鎺ㄨ崘绠＄嚎灏忎慨琛ワ紙2026-05-02锛?

- xhs `bootstrap_profile` 婊氬姩鍨嬩换鍔℃敼涓哄墠鍙?tab 鎵ц(鍚庡彴 tab 鍦ㄥ皬绾功涓婂彧娓叉煋娴呭眰 wrapper,瑙﹀彂涓嶅埌瀹屾暣鐎戝竷娴佹噿鍔犺浇);闈炴粴鍔ㄤ换鍔′繚鎸佸悗鍙?
- 婊氬姩瀹瑰櫒鎺㈡祴浠庡浐瀹?`document/window` 鍗囩骇涓轰紭鍏堝皬绾功 feed/waterfall/masonry 瀹瑰櫒,鎺掗櫎闆堕珮搴?wrapper 鍜?sidebar
- 鏀惰棌/鐐硅禐鍒嗙粍瀵煎叆瀵归綈寮€婧愬疄鐜?`profile.user.notes[1]` 鏀惰棌銆乣[2]` 鐐硅禐;profile state 瑙ｆ瀽琛ラ綈 `displayTitle` / `cover.urlDefault`

---

## v0.3.22: xhs init 鏁版嵁鐪熸杩涚敾鍍?+ UX 鍙嶉瀹屽杽锛?026-05-01锛?

`openbiliclaw init` 绔埌绔璁″悗淇澶氫釜璁╁皬绾功鏁版嵁鍩烘湰鏃犳晥鐨?bug銆?

### 淇

- **CLI 绛夊緟 8s 澶煭** 鈫?鎷?enqueue/collect API,enqueue 鍦?B 绔欐媺鏁版嵁鍓嶅彂鍑?B 绔欐媺鏁版嵁鏈熼棿鎵╁睍骞惰璺?绛夐渶瑕佹暟鎹椂閫氬父宸茬粡濂戒簡銆俥nv var `OPENBILICLAW_XHS_BOOTSTRAP_WAIT_SECONDS` 榛樿 30s
- **`max_scroll_rounds=0` 纭紪鐮?* 鈫?榛樿 3,env `OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS`;`max_items_per_scope` 20 鈫?50
- **5 绉嶅畬鎴愮姸鎬佸垎鍒墦鍙嶉** 鈥斺€?ok / empty / timeout / failed / skipped 閮界粰鐢ㄦ埛鐪嬪緱鎳傜殑涓枃娑堟伅;涔嬪墠瀹屾垚浣?0 notes 鐨勬儏鍐甸潤榛?鐜板湪浼氭彁绀?鎵╁睍璺戦€氫絾娌℃嬁鍒?notes(鍙兘鏈櫥褰曞皬绾功 / 涓汉涓婚〉娌℃湁鍏紑鏀惰棌)"

### 娴嬭瘯

- `tests/test_cli.py` 鍔?3 涓洖褰?`test_collect_xhs_bootstrap_events_status_branches`銆乣test_enqueue_xhs_bootstrap_task_uses_env_overrides`銆佹洿鏂板凡鏈?init 闆嗘垚娴嬭瘯

---

## v0.3.21: 瑁呮満娴佺▼ docker / PowerShell / CLI 鍚戝瀵归綈 v0.3.20锛?026-05-01锛?

v0.3.20 鐨?UX 鏀瑰姩鍙湪 Bash + AI 鏅鸿兘浣撹矾寰勭敓鏁?Docker 閮ㄧ讲鏂囨。 / Windows PowerShell 瀹夎鍣?/ 鐩磋窇 CLI 鍚戝浠嶆槸鏃у绾︹€斺€斿悓涓€涓」鐩笁绉嶈杈炪€傛湰娆″榻?

- `docs/docker-deployment.md` Phase 1 涓绘帹鏀规垚 DeepSeek 榛樿,Ollama 鍔?16GB+ 纭欢闂ㄦ,鑷缓缃戝叧鎸埌"楂樼骇"鎶樺彔鑺?Phase 3 embedding 鏀规垚"3 閫?1 + 榛樿鎺ㄨ崘"
- `scripts/install.ps1` 闀滃儚 install.sh 鐨?D4 (cookie-only 缁垮瓧 backend ready) + B4 (REUSE_FROM 璀﹀憡) 淇
- `cli.py` `_LLM_MENU` 閲嶆帓:DeepSeek 绗竴,Ollama 绗叚鍔犻棬妲?缃戝叧绗竷"(楂樼骇)";`_interactive_embedding_setup` 浠?4 閫?1 閲嶅啓鎴愰粯璁?Ollama bge-m3 + Gemini 鍙栬垗 + follow + 2 涓珮绾ч€夐」

---

## v0.3.20: 瑁呮満娴佺▼ UX 淇 + Embedding 鑷姩 fallback锛?026-05-01锛?

閽堝"涓€鍙ヨ瘽缁欐櫤鑳戒綋瀹夎"娴佺▼浠庢櫘閫氱敤鎴疯瑙掑仛浜嗚嫢骞蹭慨澶嶏細3 涓湡 bug锛圕laude/DeepSeek/OpenRouter 涓绘ā鍨?+ 璺熼殢 LLM 鐨?embedding 闈欓粯澶辫触銆乣base_url` 娈嬬暀銆佸鐢ㄦ棫 Key 鏃犳牎楠岋級鍜?5 涓?UX 鏀硅繘锛堜富鑿滃崟鍘绘帀鑷缓缃戝叧 / Embedding 鏀规垚"鏈夐粯璁ゅ€肩殑鍙栬垗鎻愰棶" / 鐘舵€佸潡杞寲 / README 鍔?AI Agent 鍓嶇疆 / Ollama 鍔犵‖浠堕棬妲涜鏄庯級銆?

### 淇

- **B1 鐪?bug**锛歚build_embedding_service` 鐜板湪鐢ㄦ柊澧炵殑 `LLMProvider.supports_embedding` 鏍囧織鍋?fallback锛岃€屼笉鏄剢寮辩殑 `hasattr(provider, "embed")`銆侰laude / DeepSeek / OpenRouter 鏍囪涓?`False`锛堝墠涓や釜娌?embedding API銆丱penRouter 璺敱瑕嗙洊涓嶅叏锛夛紱OpenAI / Gemini / Ollama 鏍囪涓?`True`銆傚綋涓?LLM 鏃?embedding 鑳藉姏鏃惰嚜鍔ㄥ洖閫€鍒?ollama 鈫?gemini 鈫?openai 閾句腑绗竴涓兘鐢ㄧ殑锛岃€屼笉鏄繑鍥?`None` 璁╂帹鑽愮绾垮湪杩愯鏃剁偢銆傚悓鏃?`OpenAIProvider` 鏂板 `embed()` 璧?`/v1/embeddings`锛屼负涔嬪墠 OpenAI 鐢ㄦ埛娌℃樉寮忛厤 embedding 鏃剁殑鍚屾牱闈欓粯 None bug 琛ヤ笂涓€鍒€
- **B1 閰嶅**锛歚agent_bootstrap.py` 鍦ㄤ富 LLM 鏄?Claude / DeepSeek / OpenRouter 涓旂敤鎴锋病鏄惧紡浼?`--embedding-*` 鏃讹紝鑷姩鍐?`[llm.embedding] provider="ollama" model="bge-m3"`锛屽苟鎶?`bge-m3` 鍔犺繘 ollama 妯″瀷棰勬媺娓呭崟锛岃棣栨瑁呮満灏辨妸妯″瀷鎷夊ソ鈥斺€斾笉鍐?瑁呭畬浜嗘墠鍙戠幇 embedding 娌℃媺妯″瀷"
- **B2 鐪?bug**锛歚set_toml_string_value` 涔嬪墠鍙洿鏂颁笉鍒犻櫎锛屼粠鑷缓缃戝叧锛坥ption 4锛夊垏鍥?OpenAI 瀹樻柟锛坥ption 2锛変細鐣?`base_url` 娈嬬暀锛岃姹傜户缁墦鑰佺綉鍏炽€傛柊澧?`clear_toml_string_value` / `clear_config_value`锛涘綋 `--provider openai` 鏄惧紡缁欏嚭涓?`--llm-base-url` 鏈粰鏃讹紝鑷姩娓呯┖ `[llm.openai] base_url`锛岃 SDK 鍥炲埌 `https://api.openai.com/v1`锛屽苟鍙?`base_url_reset` 浜嬩欢
- **B4 鎻愮ず**锛歚install.sh` 澶嶇敤鏃㈡湁 checkout 鐨?API Key 鏃舵憳瑕侀噷鍔犱竴娈?鈸?鎻愮ず锛岃鏄庡鐢?Key 涓嶄細鍋氭牎楠岋紝401 鏃舵€庝箞鐢?`REUSE_FROM=` 璺宠繃銆傚鐢ㄦ湰韬繚鎸佸師琛屼负锛堟棤渚靛叆锛夛紝鍙妸"淇℃伅鍙鎬?浠庨殣寮忔姮鍒版樉寮?

### 浣撻獙

- **D1 / D3 涓昏彍鍗?*锛歚docs/agent-install.md` Step 1 鎶?OpenAI 鍗忚鍏煎鑷缓缃戝叧"浠庡钩绾?4 閫?1 绉诲埌 "Advanced" 鎶樺彔鑺傦紝涓昏彍鍗曞彧鍓?3 椤癸紱鏂颁富鎺ㄦ敼鎴?DeepSeek锛埪?.001/鍗?token锛屽嚑涔庡厤璐癸級锛孫llama 鏀瑰洖"瀹屽叏绂荤嚎 / 涓嶈 Key"璺緞骞舵槑纭姞涓?16GB+ 鍐呭瓨 / CPU 鎺ㄧ悊鎱㈢殑纭欢闂ㄦ鈥斺€斾笉鍐嶈瀵兼柊鎵嬫妸 Ollama 褰?闆舵懇鎿?
- **D2 Embedding 鏀规垚"鏈夐粯璁ょ殑鍙栬垗鎻愰棶"**锛氭棭鏈熺増鏈槸"涓夐€変竴璁╃敤鎴疯 200 瀛楄В閲?锛屾湰娆℃敼 v1锛堝畬鍏ㄩ殣钘忥級鍙戠幇闇搁亾锛屾渶缁堣惤鍦?v2 鈥斺€擲tep 3 浠嶇劧闂紝浣嗘瘡涓€夐」鏈夋竻鏅扮殑鍙栬垗璇存槑 + 榛樿鎺ㄨ崘"涓嶇‘瀹氬氨鍥?1"锛氣憼 鏈湴 Ollama bge-m3锛堥粯璁?/ 鍏嶈垂 / 绂荤嚎锛夆憽 浜戠 Gemini锛堣川閲忔洿楂?/ 璺ㄨ瑷€鏇寸ǔ / 闇€瑕?Key锛夆憿 璺熼殢涓?LLM銆傚悓鏃朵繚鐣?鐢ㄦ埛璺宠繃 / 閫夐」 3 + 涓?LLM 鏄?Claude/DeepSeek/OpenRouter"鏃?bootstrap 鐨勮嚜鍔ㄥ啓 Ollama 鍏滃簳锛岄伩鍏嶈繍琛屾椂闈欓粯澶辫触
- **D4 鐘舵€佹枃妗?*锛歚install.sh` 鎽樿鍦?鍙己 B 绔?Cookie"杩欑璧版墿灞曡嚜鍔ㄥ悓姝ヨ矾寰勭殑棰勬湡鐘舵€佷笅锛屼笉鍐嶆墦鍗伴粍瀛?`partial / credentials still missing`锛堟櫘閫氱敤鎴疯鎴?瑁呭け璐ヤ簡"锛夛紝鏀逛负缁垮瓧 `backend ready 鈥?waiting for browser extension to sync B绔?Cookie`锛屽苟鎶?Next steps 鏀规垚涓撻棬鐨勬墿灞曞畨瑁呭紩瀵?
- **D5 README 鍓嶇疆**锛歚README.md` / `README_EN.md` 鍦?澶嶅埗绮樿创缁?AI 鏅鸿兘浣撲竴閿儴缃?涓婃柟鍔?馃搶 鍓嶇疆璇存槑鈥斺€斾綘闇€瑕佸厛鏈?Claude Code / Codex CLI / Cursor / Windsurf 浠讳竴锛涙病鏈夌殑鐢ㄦ埛鐩存帴鐪嬩笅鏂?鑷繁璺戜竴鍙ヨ瘽瑁呮満鑴氭湰"锛岃€屼笉鏄鍔ㄥ崱鍦?AI 鏅鸿兘浣撴槸鍟?涓?

### 娴嬭瘯

- `tests/test_llm_registry.py` 鏂板 4 涓洖褰掓祴璇曪細`test_build_embedding_service_falls_back_when_claude_is_default`锛圕laude 鈫?Ollama 鑷姩鍥為€€锛夈€乣..._when_deepseek_is_default`锛堝悓涓婏紝閲嶇偣楠岃瘉 DeepSeek 鍗充究缁ф壙浜?OpenAIProvider.embed 涔熶細琚?`supports_embedding=False` 鎺掗櫎锛夈€乣..._returns_none_with_no_capable_provider`锛堟棤鍙敤 embedding provider 鏃?None 鑰屼笉鏄穿锛夈€乣test_openai_provider_supports_embedding_flag_is_set`锛堝叚涓?provider 鐨?supports_embedding 鏍囧織姝ｇ‘锛?

### 褰卞搷鑼冨洿

- 淇敼鏂囦欢锛歚src/openbiliclaw/llm/{base,openai_provider,openrouter_provider,gemini_provider,registry}.py`銆乣scripts/{agent_bootstrap.py,install.sh}`銆乣docs/agent-install.md`銆乣README.md`銆乣README_EN.md`
- 琛屼负鍙樺寲锛氫箣鍓?OpenAI 鐢ㄦ埛娌℃樉寮忛厤 embedding 涔熶細闈欓粯杩斿洖 None锛涜繖娆?OpenAI 鐢ㄦ埛浼氳嚜鍔ㄧ敤 OpenAI 鐨?`text-embedding-3-small`锛屼細灏戦噺璁¤垂銆傚鏋滄兂鐪?quota 鏄惧紡浼?`--embedding-provider ollama --embedding-model bge-m3`

---

## v0.3.19: 鍒濆鍖栫敾鍍忔贩鍏ュ皬绾功淇″彿锛?026-05-01锛?

鏈鎶婂皬绾功鍒濆鍖栫敾鍍忓鍏ユ帴鍒扮幇鏈変簨浠跺眰锛歚openbiliclaw init` 浼氱户缁媺 B 绔欏巻鍙?/ 鏀惰棌 / 鍏虫敞锛屽悓鏃?best-effort 绛夊緟娴忚鍣ㄦ彃浠舵墽琛?`bootstrap_profile` 浠诲姟锛屾妸灏忕孩涔︽敹钘忋€佺偣璧炲拰灏忕孩涔﹂〉闈㈠唴娴忚璁板綍淇″彿娣峰叆棣栬疆鍋忓ソ鍒嗘瀽涓庣敾鍍忕敓鎴愩€?

### 鏂板

- 鍚庣 `XhsTaskQueue` 鏀寔杩斿洖 task id 鐨勫叆闃熸柟娉曪紝骞舵柊澧?`xhs_bootstrap_notes_to_events()`锛歚saved -> favorite`銆乣liked -> like`銆乣xhs_history -> view`锛宮etadata 缁熶竴甯?`source_platform="xiaohongshu"`銆乣note_id`銆乣xsec_token`銆乣import_source` 鍜?`signal_strength`
- `/api/sources/xhs/task-result` 瀵?`bootstrap_profile` result 浼氱紦瀛?notes銆佷繚鐣?task result锛屽苟鎶婅浆鎹㈠悗鐨勪簨浠跺啓鍏?memory event layer
- 鎻掍欢鏂板 `src/content/xhs/bootstrap.ts`锛屼粠灏忕孩涔﹂〉闈㈠凡娓叉煋 state 瑙ｆ瀽 scoped notes锛涘悗鍙?dispatcher 璇嗗埆 `bootstrap_profile`锛屽厛鎵撳紑 `/explore` 鎵惧綋鍓嶇櫥褰曠敤鎴风殑 profile URL锛屽啀鍦ㄥ悓涓€ tab 璺冲埌涓汉涓婚〉璇诲彇 `user.notes` 鍒嗙粍
- 鏀惰棌 / 鐐硅禐瀵煎叆瀵归綈寮€婧愬疄鐜帮細profile 椤?`user.notes` 鐨?`[1]` 浣滀负鏀惰棌銆乣[2]` 浣滀负璧炶繃锛涘鏋滃垎缁勫皻鏈姞杞斤紝鎻掍欢浼氱偣鍑?profile 椤靛搴?tab 绛夊緟椤甸潰鑷繁琛ラ綈 state
- profile state 瑙ｆ瀽琛ラ綈灏忕孩涔?noteCard 瀛楁锛歚displayTitle`銆乣user.nickName`銆乣cover.urlDefault`锛涘彈鎺ф粴鍔ㄦ瘡杞細鍚堝苟 state + DOM锛屽啀鍙戦€佹柊澧?partial锛屽噺灏戣櫄鎷熷垪琛ㄥ鑷寸殑婕忛噰
- `bootstrap_profile` 鏀寔鏄惧紡 `max_scroll_rounds` 鐨勫彈鎺ф粴鍔紱content script 浼氭妸棣栨壒鍜屾粴鍔ㄦ柊澧?notes 浠?`status="partial"` 鍒嗘壒鍥炰紶锛宐ackground 绛夊悗绔?`/task-result` 纭鍚庡啀缁х画婊氬姩锛屾渶鍚庣敤 `status="ok"` 瀹屾垚浠诲姟
- 婊氬姩鍨?`bootstrap_profile` 浼氫互鍓嶅彴 tab 鎵撳紑 `/explore`锛岀敱 content script 鍦ㄩ〉闈㈠唴鐐瑰嚮瀵艰埅鏍忊€滄垜鈥濊繘鍏?profile锛沚ackground 鏀跺埌 `next_url_clicked=true` 鍚庝笉鍐?`tabs.update(profileUrl)`锛屽彧绛夊緟鍚屼竴 tab 瀵艰埅瀹屾垚骞堕噸鏂颁笅鍙戜换鍔★紝閬垮厤鐩存帴璺?profile 瑙﹀彂楠岃瘉鐮併€備笉婊氬姩浠诲姟浠嶄繚鎸佸悗鍙版墽琛岋紱鍙湁鎵句笉鍒板彲鐐瑰嚮鍏ュ彛銆佸彧鑳戒粠 state 鎺ㄥ嚭 profile URL 鏃舵墠鍥為€€鍒扮洿鎺ュ鑸?
- profile 浜屾鎵ц鍓嶄細绛夊緟灏忕孩涔?React 椤甸潰鐪熸娓叉煋鍑?profile state銆佹敹钘?璧炶繃 tab 鏂囨鎴?note 鍗＄墖锛岄伩鍏?`tabs.onUpdated complete` 鏃╀簬椤甸潰鍐呭鍔犺浇鏃剁洿鎺ヨ繑鍥?0 鏉?
- 鍚庣浠诲姟 payload 鍙帶鍒舵粴鍔ㄨ妭濂忥細`scroll_wait_ms` 鎺у埗姣忚疆婊氬姩鍚庣殑鍋滅暀绛夊緟锛宍max_stagnant_scroll_rounds` 鎺у埗杩炵画鏃犳柊澧炲灏戣疆鍚庡仠姝紱鎻掍欢绔細鍋氫笂涓嬮檺瑁佸壀锛宒ispatcher 浼氭寜鏇撮暱绛夊緟鏀惧浠诲姟 timeout
- 婊氬姩 partial 鎵规鐜板湪浼氭寜 `max_items_per_scope` 鐨勫墿浣欏悕棰濊鍓紝閬垮厤鏈€鍚庝竴杞〉闈竴娆℃柊澧炲鏉℃椂鍒嗘壒鍥炰紶瓒呰繃 scope 涓婇檺
- profile 婊氬姩鐩爣浠庡浐瀹?`document/window` 鍗囩骇涓轰紭鍏堟帰娴嬪皬绾功 feed / waterfall / masonry 瀹瑰櫒锛屽苟鎺掗櫎闆堕珮搴︺€乣overflow-y` 闈炴粴鍔ㄥ紡鐨勬櫘閫?wrapper 鍜?`channel-list` / sidebar 杩欑被闈炲唴瀹逛晶鏍忥紱娌℃湁鍐呭瀹瑰櫒鏃朵細閫€鍥炲埌绐楀彛绾у皬姝?`wheel` / `scrollBy`锛岃创杩戠敤鎴锋墜鍔ㄥ墠鍙版粴鍔ㄣ€俤ebug 浼氬悓鏃惰褰曟帓鍚嶉潬鍓嶇殑 `scroll_candidates` 鍜屾瘡杞?target銆乻crollTop銆乻crollHeight銆乧lientHeight銆乥efore/after top銆佹柊澧炴暟锛屼究浜庡垽鏂槸鍚︾湡姝ｈЕ鍙戠€戝竷娴佸姞杞?
- `openbiliclaw init` 浼氭妸 XHS bootstrap 浜嬩欢鍔犲叆 `SoulEngine.analyze_events()` 鐨勫悓鎵硅緭鍏ワ紝骞舵妸瀵瑰簲 notes 杩藉姞鍒?`build_initial_profile()` 鐨?history

### 绾︽潫

- 鍚庣浠嶄笉鐩存帴鐧诲綍銆佺埇鍙栨垨璋冪敤灏忕孩涔︾鏈夋帴鍙ｏ紱灏忕孩涔︽暟鎹彧鏉ヨ嚜鐢ㄦ埛娴忚鍣ㄩ噷鐨勬彃浠?
- `xhs_history` 鎸囧皬绾功缃戦〉鑷繁鏄庣‘鏆撮湶鐨勬祻瑙堣褰?瓒宠抗 state锛屼笉鏄鍙?Chrome browser history锛涙櫘閫?`/explore` 鎺ㄨ崘娴佷笉浼氬啀琚綋鎴愭祻瑙堣褰曞鍏?
- 鏀惰棌銆佺偣璧炪€佹祻瑙堣褰曚笁涓?scope 閮芥槸 best-effort锛氭彃浠舵湭杩炴帴銆佹湭鐧诲綍鎴栭〉闈笉鏆撮湶鏁版嵁鏃讹紝鍒濆鍖栫户缁娇鐢?B 绔欐暟鎹畬鎴愶紱婊氬姩涔熷彧鍦ㄤ换鍔℃樉寮忚姹傛椂鍚敤

### 娴嬭瘯

- `tests/test_xhs_tasks.py`
- `tests/test_api_xhs_ingest.py::TestXhsTaskResults::test_xhs_bootstrap_task_result_records_events`
- `tests/test_api_xhs_ingest.py::TestXhsTaskResults::test_xhs_bootstrap_partial_results_accumulate_until_final`
- `tests/test_cli.py::test_init_includes_xhs_bootstrap_events`
- `extension/tests/xhs-task-executor.test.ts`
- `extension/tests/xhs-task-dispatcher.test.ts`

---

## v0.3.18: 鎶?franchise_key 鍗囨垚涓€绛夊瓧娈碉紝鎾ゆ帀 v0.3.17 鐨勬爣棰橀粦鍚嶅崟锛?026-04-30锛?

v0.3.17 鐢ㄤ簡**纭紪鐮?IP 鍒悕琛?+ 鏍囬瀛愪覆鍖归厤**鍋?franchise 鍒ゅ畾銆傜ぞ鍖哄弽棣堣杩欑榛戠櫧鍚嶅崟鍋氭硶鍦ㄩ暱鏈熶笉鍙寔缁€斺€旇鐩栦笉鍏ㄣ€佷汉宸ョ淮鎶ゆ垚鏈珮銆佸 LLM 缂栧嚭鏂板啓娉曪紙"鎻愮摝鐗?閲嶅埗"銆?鍘熺 4.5 椤诲讥"锛夊鏄撴紡鍒ゆ垨璇垽銆傝繖娆℃挙鎺夛紝鏀规垚**璁?LLM 鍦ㄥ唴瀹硅瘎浼伴樁娈电洿鎺ユ墦 IP 鏍囩**锛屼綔涓?`content_cache` 鐨勪竴绛夊瓧娈垫寔涔呭寲銆?

### 鎾ゆ帀鐨?

- `src/openbiliclaw/recommendation/franchise.py`锛?3 涓?IP 鐨勭‖缂栫爜 alias 琛?+ `extract_franchise()` heuristic锛?
- `tests/test_franchise.py`
- `_FEEDBACK_DISLIKE_FRANCHISE_PENALTY` 鍦?curator 閲屼緷鐒朵繚鐣欙紝浣嗗疄鐜板簳鐩樻崲浜?

### 鏂板鐨勶細`franchise_key` 浣滀负涓€绛夊瓧娈?

**Schema**锛坄storage/database.py`锛夛細

- `content_cache` 琛ㄦ柊澧?`franchise_key TEXT DEFAULT ''` 鍒?
- `_ensure_content_cache_topic_columns()` 鍔?`ALTER TABLE` 杩佺Щ锛岃€佸簱鏃犵棝鍗囩骇
- `cache_content` INSERT/UPDATE 鎶?`franchise_key` 绾冲叆锛宍COALESCE(NULLIF(excluded.x, ''), content_cache.x)` 妯″紡鈥斺€旈伩鍏嶈 0 鍊艰鐩?
- `get_recommendations` SELECT 澶氬甫 `c.franchise_key` 鍑烘潵锛岀粰 API dedup 鐢?
- `get_feedback_signals` SELECT 澶氬甫 `c.franchise_key`锛岀粰 curator dislike 浼犳挱鐢?

**LLM prompt**锛坄llm/prompts.py`锛夛細

`build_batch_content_evaluation_prompt` + 鍗?item 璇勪及鐨?prompt 閮藉姞浜?franchise_key 瀛楁锛?

```
7. franchise_key 瑙勫垯锛氬唴瀹瑰鏋滄槑纭睘浜庢煇涓叿浣?IP / 绯诲垪 / 浣滃搧 / 鍝佺墝锛?
   濉畠鐨勮鑼冨悕锛堜腑鏂囦紭鍏堬級锛岀敤浜庤法 topic_group 鐨勫悓 IP 鍘婚噸銆備緥锛?
   - 銆孉I 閲嶇粯鍘熺鍦板浘銆嶃€屾彁鐡︾壒鎽勫奖銆嶃€岃挋寰疯鑹茬湡瀹炲寲銆?鈫?"鍘熺"
   - 銆屾槦绌归搧閬?1.6 瀹炴垬銆嶃€屽穿閾?瑙掕壊鍏绘垚銆?鈫?"宕╁潖:鏄熺┕閾侀亾"
   - 銆孋hatGPT 宸ヤ綔娴併€嶃€孫penAI 鏂版ā鍨嬨€?鈫?"ChatGPT"
   - 銆岀暘鑼勭倰铔?5 鍒嗛挓鏁欑▼銆?鈫?""锛堜竴鑸鏅?/ 缇庨 / 閫氱敤璧勮閮藉～绌哄瓧绗︿覆锛屼笉瑕佺‖鍑戯級
   - 鍚屼竴 IP 蹇呴』鐢ㄧ浉鍚屽啓娉曘€?
```

LLM 宸茬粡鐪嬩簡 title + description + topic + style锛岃瀹冮『鎵嬪啀鏍囦竴涓?IP 鍑犱箮闆堕澶栧欢杩熴€傛瘮 heuristic 鍑嗗緢澶氣€斺€斻€屾彁鐡︾壒鎽勫奖銆嶈繖绉嶉殣鎬у紩鐢?LLM 鑳借瘑鍒紝纭紪鐮佽〃鐓т笉鍒般€?

**Pipeline**锛坄discovery/engine.py`锛夛細

- `DiscoveredContent` 鏂板 `franchise_key: str = ""` field
- `to_cache_kwargs()` 鎶婂畠甯﹁繃鍘?
- `_evaluate_batch` 瑙ｆ瀽 LLM 鍝嶅簲閲岀殑 `franchise_key`锛屽啓鍏?`content.franchise_key` + 璇勪及缂撳瓨鍏冪粍
- 缂撳瓨鍏冪粍浠?4-tuple 鍗囧埌 5-tuple锛岃€?4-tuple 鍏煎闄嶇骇锛堢粫杩囧崌绾ф湡 in-flight 杩涚▼宕╂簝锛?
- `evaluate_content`锛堝崟 item 鐗堬級鍚屾澶勭悊

**Curator**锛坄recommendation/curator.py`锛夛細

- `FeedbackSignals.disliked_franchises` 鏉ユ簮鎹㈡垚 `row.get("franchise_key")`锛圖B 閲岀殑鐪熷€硷級锛屼笉鍐嶄粠 title 鎻?
- `_feedback_adjustment` 姣旇緝 `item.franchise_key`锛堜篃鏄?DB 閲岀殑鐪熷€硷級锛屼笉鍐嶈皟 heuristic 鎶藉彇
- 缃氬垎甯搁噺淇濈暀 0.07锛坔euristic vs LLM 涓嶅奖鍝嶈繖涓€肩殑鍚堢悊鎬э級

**API**锛坄api/app.py`锛夛細

- `_cap_by_franchise()` 鍐呰仈鍦?app.py锛屾寜 row 鐨?`franchise_key` 鍒楀仛绐楀彛鍐呭幓閲嶏紝涓嶄緷璧栨爣棰?
- 绌?`franchise_key` 姘歌繙閫忎紶鈥斺€斾竴鑸唴瀹逛笉琚檺娴?

### 娴嬭瘯

- `tests/test_pool_curator.py` 鏂板 3 涓細`disliked_franchises={"鍘熺"}` 鏃讹紝candidate `franchise_key="鍘熺"` 鎵ｅ垎锛沗franchise_key="濉炲皵杈句紶璇?` 涓嶆墸锛沗franchise_key=""` 涓嶆墸锛堜繚鎶?LLM 杩樻病鏍囩殑鍐呭锛?
- `tests/test_api_app.py` 鏂板 2 涓細`_cap_by_franchise` 鍗曞厓娴嬶紱`/api/recommendations` 绔埌绔€斺€? 鏉?`franchise_key="鍘熺"` 琛?+ 1 鏉?`""`锛屽搷搴旈噷鍙墿 2 鏉″師绁?+ 鐣寗鐐掕泲

### 鑷磋阿

绀惧尯鍙嶉銆屼笉瑕佸仛榛戠櫧鍚嶅崟銆嶏紝鏂瑰悜瀹屽叏姝ｇ‘銆傛妸 franchise 鍗囨垚涓€绛夊瓧娈垫槸姝ｈВ鈥斺€斿悗缁繕鑳借 `RelatedChainStrategy` 鎸?`franchise_key` 闄愬埗鍚?IP 閾捐矾娣卞害銆佽 SQL 灞?`trim_topic_group_overflow` 澶氬姞涓€涓酱锛屽叏閮介潬杩欎竴鍒楀睍寮€銆?

---

## v0.3.17: 淇帹鑽愭祦杩囧害娉涘寲 IP锛堜竴灞?5 鏉″師绁?/ 鎻愮摝鐗癸級锛?026-04-30锛?

绀惧尯鎶ュ憡锛氱偣浜嗕竴鏉°€孉I 閲嶇粯鍘熺鍦板浘銆嶄箣鍚庯紝鎺ㄨ崘寮圭獥杩炵画鍑?5 鏉″師绁?/ 鎻愮摝鐗?/ 钂欏痉瑙嗛銆傛繁搴﹀垎鏋愬畾浣嶄簡 5 涓眰绾х殑闂锛屾湰娆″厛淇渶褰卞搷瑙嗚浣撻獙鐨?3 涓細

### 鏍瑰洜锛堢ぞ鍖哄垎鏋愶紝鍏ㄩ儴浠ｇ爜楠岃瘉杩囷級

1. **姝ｅ弽棣堟硾鍖栬繃寮?*锛氬崟娆?`recommendation_click` 灏辫兘璁?PreferenceAnalyzer 鎶娿€屽師绁炪€嶅啓鍏?`interests` 鏉冮噸 0.6锛堝湪 `preference.json` line 348 瀹為檯鍛戒腑锛?
2. **璐熷弽棣堟硾鍖栦笉瓒?*锛氱偣韪╂煇鏉″師绁炶棰戝彧璁?`topic_key` 绾?dislike锛屽師绁炶繖涓?IP 涓嶄細琚檷鏉冿紙`curator.py:130-148` 楠岃瘉锛?
3. **澶氭牱鎬х淮搴﹀お绮?*锛氬綋鍓嶇敤 `topic_group` 闄愭祦锛屼絾鍚屼竴 IP 琚?LLM 鎷嗗埌銆屾父鎴忋€嶃€屾父鎴忓姩婕€嶃€屼汉宸ユ櫤鑳姐€嶃€屾父鎴忔憚褰便€嶃€屾父鎴忕洏鐐广€? 涓?group锛岀粫杩囬檺娴侊紙`engine.py` 楠岃瘉锛?
4. **`/api/recommendations` 鏃犳渶缁堝幓閲?*锛歚LIMIT 20 ORDER BY DESC`锛? 鏉″師绁炲湪鍓嶅垯鍏ㄦ暟閫忎紶锛坄app.py:606`锛?
5. **`related_chain` 缂?IP 涓婇檺**锛氬彧鎸?seed_index 闄愭祦锛屾部鍘熺 seed 婊?5 涓偦灞?= 鍏ㄦ槸鍘熺锛坄related_chain.py:159` 楠岃瘉锛?

### 鏈増鏈慨澶嶏紙focused subset锛?

鏂板 `src/openbiliclaw/recommendation/franchise.py`锛氬熀浜庢爣棰樼殑 heuristic franchise 鎻愬彇鍣ㄣ€傞缃?13 涓珮棰?IP 鐨?alias 琛紙鍘熺 / 鏄熺┕閾侀亾 / 宕╁潖 3 / 缁濆尯闆?/ 楦ｆ疆 / 鏄庢棩鏂硅垷 / 榛戠璇?/ 濉炲皵杈?/ 鎴戠殑涓栫晫 / Apex / 鑻遍泟鑱旂洘 / ChatGPT / DeepSeek锛夛紝涓枃鍒悕璧板瓙涓插尮閰嶏紝鑻辨枃璧?`\b` 璇嶈竟鐣岋紙閬垮厤銆宭ol銆嶅尮閰嶆櫘閫氱瑧鍙嶅簲锛夈€?

鎺ュ叆 2 涓偣锛?

1. **`/api/recommendations` 鏈€缁堝幓閲?*锛坒ix 鏍瑰洜 #4锛夛細鎷?40 鏉″€欓€夛紝璋?`dedup_by_franchise(max_per_franchise=2)` 闄愬悓涓€ IP 鍦ㄧ獥鍙ｉ噷鏈€澶氬嚭鐜?2 娆★紝鍐嶆埅鍒?20 杩斿洖
2. **Curator 鐨?`disliked_franchises` 闆嗗悎**锛坒ix 鏍瑰洜 #2锛夛細`PoolCurator.build_context` 鐜板湪鍦ㄥ鐞?dislike 鍙嶉鏃讹紝浠庤韪?item 鐨?title 鎻愬彇 franchise 鍔犲叆 set锛沗_feedback_adjustment` 瀵?title 鍛戒腑鍚?franchise 鐨勫€欓€夋墸 `_FEEDBACK_DISLIKE_FRANCHISE_PENALTY = 0.07`锛堟瘮 topic 杞竴妗ｏ紝閬垮厤涓€鏉¤俯姘镐箙灏?IP锛?

`storage/database.py` 鐨?`get_feedback_signals` 鍚屾鍔?`c.title` 鍒版煡璇紝鍥犱负 franchise 鎻愬彇闇€瑕?title銆?

### 娌′慨鐨勶紙鐣欎綔鍚庣画锛?

- 鏍瑰洜 #1锛堢偣鍑?鈫?IP 鍏磋叮杩囧害寮哄寲锛夛細闇€瑕佹敼 PreferenceAnalyzer 鐨?prompt 鎴栧姞 TTL/鏈€灏忕‘璁ゆ鏁?
- 鏍瑰洜 #3锛坱opic_group 澶氭牱鎬х淮搴﹀お绮楋級锛氶渶瑕佸湪 content_cache 鍔?`franchise_key` 瀛楁骞剁敱 LLM 璇勪及鏃跺～锛岄厤鍚?SQL 闄愭祦
- 鏍瑰洜 #5锛坮elated_chain IP 涓婇檺锛夛細鍚屼笂锛岄渶瑕?`franchise_key` 鎵嶈兘鍦?strategy 鍐呴儴闄?

杩欎笁涓殑姝ｈВ閮芥槸鎶?franchise 涓婂崌涓轰竴绛夊瓧娈碉紙DB column + LLM tag锛夛紝鑰屼笉鏄仠鐣欏湪 title heuristic銆傛湰娆″厛鐢?heuristic 瑙ｆ帀鐢ㄦ埛鏈€鐩存帴鐪嬪埌鐨勯棶棰橈紝franchise_key 瀛楁鏂规闅忓悗瑙勫垝銆?

### 娴嬭瘯

- `tests/test_franchise.py`锛?0 涓級锛氬師绁?/ 鎻愮摝鐗?/ 钂欏痉 / 鏋腹 / Genshin 閮芥槧灏勫埌鍚屼竴 canonical key锛沗lol` 涓嶄細璇尮閰嶏紱澶?franchise 鏃舵寜澹版槑椤哄簭鍙栭锛涙棤 franchise 鐨勫唴瀹圭洿鎺ラ€忎紶
- `tests/test_pool_curator.py` 鏂板 2 涓細disliked_franchises 鍚€屽師绁炪€嶆椂锛屻€屾彁鐡︾壒鎽勫奖闆嗛敠銆嶏紙涓嶅悓 topic_key + 涓嶅悓 up_mid锛夋墸鍒嗭紱`濉炲皵杈綻 涓嶄細琚畠鍙?

### 缂栫爜涔辩爜椋庨櫓

绀惧尯杩樻彁鍒伴儴鍒?B 绔欐爣棰樺湪鏁版嵁搴撻噷鏈夌紪鐮佽抗璞★紝鍙兘瀵艰嚧鍏抽敭璇嶈繃婊や笉绋炽€?*杩欐娌″姩**鈥斺€斾絾 v0.3.14 淇繃 memory JSON 鐨?GBK鈫扷TF-8锛屾柟鍚戠被浼笺€傚鏋滅敤鎴疯兘澶嶇幇鍏蜂綋鐨勪贡鐮佸瓧娈碉紝鍙互鍐嶅紑 issue 鍗曠嫭淇€?

### 鑷磋阿

绀惧尯璇婃柇璐ㄩ噺鏋侀珮锛? 涓牴鍥?+ 5 涓叿浣撹鍙?+ 5 涓慨澶嶅缓璁紝鏈淇瀹屽叏鎸夌収鍏朵腑鍙墽琛屽瓙闆嗚惤鍦般€?

---

## v0.3.16: README 鎺ㄨ崘椤哄簭璋冩暣 + 澶氭簮鐧诲綍鍓嶇疆璇存槑锛?026-04-30锛?

涓や釜 README/瀹夎鏂囨。灞傞潰鐨勮皟鏁达紝娌″姩浠ｇ爜锛?

### 1. README 鍚庣瀹夎鏂瑰紡閲嶆帓锛氫竴鍙ヨ瘽瑁呮満浼樺厛锛屾闈㈠寘鍚庣疆

涔嬪墠涓や唤 README 閮芥妸銆屼笅杞藉悗绔闈㈠寘銆嶆斁绗竴浣嶏紝銆孉I 涓€鍙ヨ瘽瑁呮満銆嶇浜屼綅锛屻€岃嚜宸辫窇鑴氭湰銆嶇涓変綅锛屻€孌ocker銆嶆贩鍦ㄤ腑闂淬€備絾棣栫増妗岄潰鍖呮湭绛惧悕锛屼細瑙﹀彂 macOS Gatekeeper / Windows SmartScreen锛屽鏅€氱敤鎴峰叾瀹炴渶涓嶅弸濂姐€傛柊椤哄簭鎸夈€屽疄闄呭彲鐢ㄥ害銆嶆帓锛?

1. **棣栭€?*锛氳 AI agent 璺?`agent-install.md`锛堥浂鎽╂摝锛宎gent 鎶?LLM/Embedding/Cookie 閮介棶鍏?+ 鑷姩璺?init锛?
2. **鎴?*锛欰I agent + Docker锛坴0.3.11+ 鑷甫 Ollama embedding sidecar锛?
3. **鎴?*锛氳嚜宸辫窇 `install.sh` / `install.ps1`锛堝悓涓€浠借剼鏈級
4. **鏈綅**锛堟姌鍙犲湪 `<details>` 閲岋級锛氫笅杞芥湭绛惧悕妗岄潰鍖咃紝瑕佺偣銆屽彸閿?鈫?鎵撳紑銆嶇粫杩?Gatekeeper

### 2. README 澧炲姞銆屽婧愮櫥褰曞墠缃€嶆

寰堝鐢ㄦ埛瑁呭ソ鎵╁睍鍚庡彂鐜般€屼负浠€涔堟病鏈夊皬绾功鍐呭锛熴€嶁€斺€斿師鍥犳槸鍚庣涓嶇埇灏忕孩涔︼紝鍙戠幇/璇︽儏閮介潬鎵╁睍鍦ㄧ敤鎴风櫥褰曟€佺殑娴忚鍣ㄩ噷璺戙€傛柊澧炰竴寮犺〃锛屾槑纭瘡涓簮鐨勭櫥褰曡姹?+ 涓嶇櫥褰曠殑鍚庢灉锛?

| 婧?| 鐧诲綍鏂瑰紡 | 涓嶇櫥褰曠殑鍚庢灉 |
|---|---|---|
| B 绔?| 娴忚鍣ㄧ櫥褰?https://www.bilibili.com锛坴0.3.12+ 鎵╁睍鑷姩鍚屾 Cookie锛?| 鎷変笉鍒板巻鍙?鏀惰棌/鍏虫敞锛岀敾鍍忕己澶憋紝鎺ㄨ崘闄嶇骇涓哄叕鍏辩儹闂?|
| 灏忕孩涔?| 娴忚鍣ㄧ櫥褰?https://www.xiaohongshu.com | **瀹屽叏娌℃湁灏忕孩涔﹀唴瀹?*锛堝悗绔笉鐩存帴鎶擄級 |
| 閫氱敤 Web 婧?| 璇ョ珯鐐规甯哥櫥褰?| 鍚屼笂 |

骞跺己鐑堟帹鑽愬皬绾功鐢?CDP 妯″紡 Chrome 澶嶇敤鐧诲綍鎬侊紙`--remote-debugging-port=9222` + `[sources.browser] cdp_url`锛夛紝閬垮厤鍙嶇埇銆?

`docs/docker-deployment.md` 涔熷姞浜嗗悓鏍风殑澶氭簮鐧诲綍鍓嶇疆娈碉紝骞舵妸 CDP url 鏀规垚 `host.docker.internal:9222`锛屾柟渚垮鍣ㄨ闂涓绘満鐨?CDP 绔彛銆?

### 3. README_EN 鍚屾缈昏瘧

涓や唤 README 涓ユ牸涓€鑷淬€?
---

## v0.3.15: 涓€杩炰覆 Windows 瑁呮満韪╁潙淇 + Ollama embedding-only 涓嶅簲鍋?chat fallback锛?026-04-30锛?

绀惧尯鍙嶉浜嗕竴缁?Windows 鍘熺敓璺緞鐨勫潙锛岄泦涓慨澶嶏細

### 1. CLI 鍦?GBK 鎺у埗鍙版墦 emoji 鐩存帴宕?

`openbiliclaw init` 寮€鍦烘墦鐨勩€屸彵銆嶅湪绠€浣撲腑鏂?Windows 榛樿 GBK 鎺у埗鍙拌Е鍙?`UnicodeEncodeError: 'gbk' codec can't encode character '鈴?`銆備慨澶嶏細鍦?`cli.py` 椤堕儴鍔?`_force_utf8_stdout_on_windows()`锛?

- `os.name == "nt"` 鏃惰 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`锛堣繖淇╁瀛愯繘绋嬩篃鐢熸晥锛?
- 鐢?`sys.stdout.reconfigure(encoding="utf-8", errors="replace")` 鎶婃祦鐨?codec 鎹㈡垚 UTF-8 + 鏇挎崲閿欒澶勭悊

POSIX 涓婂畬鍏ㄦ槸 no-op銆俙errors="replace"` 鏄渶鍚庝竴閬撳厹搴曗€斺€斿嵆浣挎湁灏戞暟瀛楃璇戜笉鍔紝涔熷彧浼氭樉绀?`?` 鑰屼笉鏄穿婧冦€?

### 2. install.ps1 鐨?`python -c '...f"{...}"...'` 鍦?PS 5.1 涓嬭鍓ュ紩鍙?

PowerShell 5.1 鎶婂崟寮曞彿 PS 瀛楃涓查噷鐨勫唴宓?`"..."` 浼犵粰 native command 鏃朵細涓㈠唴灞傚紩鍙枫€傜粨鏋?`python -c 'print(f"{x}.{y}")'` 瀹為檯鎵ц `python -c print(fx.y)` 鈫?SyntaxError 鈫?瀹夎鍣ㄨ鎶ャ€孭ython 3.11+ is required銆嶃€?

淇锛氬幓鎺?f-string 鍜屽唴宓屽紩鍙凤紝鐢?`print(sys.version_info[0], sys.version_info[1])`锛岃緭鍑?`3 11` 鐢ㄧ┖鏍煎垏鍒嗐€侾ython 绔笉鍐嶆湁 `f"..."`锛孭S 5.1 寮曞彿 bug 瑙﹀彂涓嶅埌銆?

### 3. Bash 鍦?Windows 涓婅韪?WSL

`docs/agent-install.md` 璁?AI agent 鍦?Windows 璺?`curl ... | bash`锛屼絾 Windows 涓?`bash` 榛樿鎸囧悜 `C:\Windows\System32\bash.exe`锛圵SL 鍚姩鍣級銆俉SL 娌¤鏃舵姤 `execvpe(/bin/bash) failed: No such file or directory`銆?

淇锛歛gent-install.md 鍔犳樉鐪艰鍛婏紝鍛婅瘔 AI agent 鍦?Windows 榛樿璧?PowerShell锛涘蹇呴』鐢?bash锛屾樉寮忚皟 `& "C:\Program Files\Git\bin\bash.exe" -c "..."`銆?

### 4. 鍚庣 Ollama embedding-only 娉ㄥ唽涓嶅簲杩涘叆 chat fallback chain

鏈€涓ラ噸鐨勪竴涓細鐢ㄦ埛鏃ュ織閲屽嚭鐜?`All providers failed (openai, ollama). Last error: ollama request failed: 404 page not found`銆傛牴鍥犫€斺€擿[llm.embedding] provider="ollama"` 瑙﹀彂 `_maybe_ollama_provider` 娉ㄥ唽涓€涓粎鏈?`bge-m3`锛坋mbedding 妯″瀷锛夌殑 Ollama provider銆俙LLMRegistry.register()` 涓嶅尯鍒?chat/embedding 鐢ㄩ€旓紝涓?provider 澶辫触鏃?fallback chain 鎶婂畠褰撴垚 chat provider 鐢紝鎵?`/api/chat?model=llama3` 鈫?404锛岃繕鎶?404 璇綊鍥犮€宖allback 涔熸寕浜嗐€嶃€?

淇锛?

- `LLMRegistry.register()` 鍔?`chat_capable: bool = True` 鍙傛暟 + 鍐呴儴 `_chat_disabled` 闆嗗悎
- `_fallback_order()` 璺宠繃 `_chat_disabled` 閲岀殑 provider
- `build_llm_registry()` 璋?`_ollama_is_chat_capable(config)` 鍒ゅ畾锛氱敤鎴峰繀椤诲湪 `[llm.ollama] model` 鏄惧紡缁欎簡 chat 妯″瀷锛屾垨鎶?ollama 璁炬垚榛樿/浠讳竴妯″潡鐨?provider锛屽惁鍒欒浣?embedding-only锛屾敞鍐屾椂浼?`chat_capable=False`

鍥炲綊娴嬭瘯锛?

- `tests/test_llm_registry.py::test_embedding_only_ollama_is_excluded_from_chat_fallback` 鈥斺€?妯℃嫙銆屼富 OpenAI 鎸備簡 + Ollama 鍙厤浜?embedding銆嶅満鏅紝鏂█ chat 閾鹃噷**娌℃湁** ollama锛屾柇瑷€涓?provider 鐨勯敊璇瀹炴姏鍑猴紙涓嶄細鍐嶈銆宱llama 涔熸寕浜嗐€嶆帺鐩栵級
- `test_ollama_with_explicit_chat_model_is_chat_capable` 鈥斺€?鍙嶅悜楠岃瘉锛氱敤鎴风粰浜?`[llm.ollama] model="llama3"` 鏃讹紝Ollama 浠嶇劧鍦?fallback 閾鹃噷锛岀鍚堥鏈?

### 5. UTF-8 鎸佷箙鍖栵紙v0.3.14 宸蹭慨锛岃繖閲屽彧鏄叧鑱斿紩鐢級

绀惧尯鎶ュ憡閲屽悓鏃舵彁鍒?`MemoryLayer.load/save` 娌℃寚瀹?UTF-8 鈥斺€?*宸茬粡鍦?v0.3.14 淇簡**锛岃繖閲屼笉閲嶅銆?

### 鑷磋阿

闈炲父鎰熻阿绀惧尯鐨勭粏鑷村鐜?+ 绯荤粺鎬ф€荤粨銆備竴浠芥姤鍛婅В閿佸洓涓嫭绔?bug + 涓€涓灦鏋勯棶棰橈紝PR 绾ц川閲忋€?

---

## v0.3.14: 淇?Windows GBK 榛樿缂栫爜瀵艰嚧鎺ュ彛 500锛?026-04-30锛?

绀惧尯鍙嶉鍦ㄧ畝浣撲腑鏂?Windows 涓婂悗绔敤榛樿 GBK locale 鍚姩鏃讹紝鎵╁睍璇锋眰 `/api/delight/pending-batch?limit=20`銆乣/api/activity-feed?limit=10` 绛夋帴鍙ｉ兘浼氳繑鍥?500锛屾牴鍥犳槸 `MemoryLayer.load()` / `save()` 鍦?`src/openbiliclaw/memory/manager.py` 鐢ㄤ簡涓嶅甫 `encoding=` 鐨?`open()`锛?

```python
with open(self.storage_path) as f:        # 鈫?娌℃寚瀹氱紪鐮?
    self._data = json.load(f)             # GBK 瑙ｇ爜 UTF-8 鏂囦欢 鈫?鎶ラ敊
```

`/api/health` 鏄父閲忓瓧绗︿覆銆佷笉璇?memory 鏂囦欢锛屾墍浠ヤ粛鐒?200鈥斺€攂ug 鍙湪涓氬姟鎺ュ彛鐜拌韩銆?

### 淇

- `MemoryLayer.load()` / `save()` 鏄惧紡 `encoding="utf-8"`
- `BilibiliAuthManager.load_cookie()` / `_save_cookie()` 涔熻ˉ涓婏紙cookie 褰撳墠鏄?ASCII 涓嶅彈褰卞搷锛屼絾鍚屾牱涓嶈渚濊禆骞冲彴榛樿缂栫爜锛?
- 椤圭洰閲屽叾浠栨枃鏈ā寮?`open(...)` 鍏ㄩ儴 audit 杩団€斺€擿config.py` 鐨勪袱澶勭敤 `"rb"` 璧?`tomllib`锛屾纭紱鍏朵綑閮藉凡缁忔樉寮?UTF-8

### 鍥炲綊娴嬭瘯

`tests/test_memory_manager.py::test_memory_layer_load_uses_utf8_even_when_default_locale_is_gbk`锛?

閫氳繃 monkeypatch `builtins.open`锛岃浠讳綍涓嶅甫 `encoding=` 鐨?text-mode 璋冪敤鍥為€€鍒?GBK鈥斺€旂簿鍑嗘ā鎷熺畝浣撲腑鏂?Windows 鐨勯粯璁よ涓恒€傞獙璇侊細

- `MemoryLayer.load()` 浠嶈兘姝ｇ‘璇诲彇鍚腑鏂?+ emoji 鐨?UTF-8 鏂囦欢
- `MemoryLayer.save()` 涔熶笉浼氳Е鍙?`UnicodeEncodeError`
- 鏂囦欢鏈€缁堜粛鏄悎娉?UTF-8

鎾ゅ洖 `manager.py` 鐨?fix 鏃讹紝杩欎釜娴嬭瘯浼氱簿纭姤鍑?`UnicodeDecodeError: 'gbk' codec can't decode byte 0x80`鈥斺€斿拰 prod 澶嶇幇鐨勯敊璇竴瀛椾笉宸€?

### 鑷磋阿

闈炲父鎰熻阿绀惧尯鎶ュ憡鈥斺€攂ug 鎽樿銆佹牴鍥犲畾浣嶃€佷慨澶嶆€濊矾銆佹湰鍦伴獙璇佸叏璺戦€氾紝鏁寸悊寰楅潪甯告竻妤氾紝PR 绾у埆鐨勬姤鍛娿€?

---

## v0.3.13: 鍚勭瀹夎璺緞閮芥妸銆岃鎵╁睍鑷姩鍚屾銆嶆斁鍒?Cookie 姝ラ鐨勯閫夛紙2026-04-30锛?

v0.3.12 鍔犱簡鎵╁睍鑷姩鍚屾 Cookie锛屼絾鍚勪釜瀹夎璺緞鐨勫紩瀵硷紙鍚戝 / 鏂囨。 / install.sh / install.ps1锛夐兘杩樻寜 F12 閭ｅ鑰佹祦绋嬪湪闂€傛柊鐢ㄦ埛鏍规湰涓嶇煡閬撴湁鏇寸畝鍗曠殑璺緞锛岀粨鏋滆繕鍦ㄦ墜鍔ㄨ创 Cookie銆?

淇簡 5 澶勶細

- **`scripts/install.sh`** 鐘舵€佸潡缂?`bilibili.cookie` 鏃讹紝鍏堟墦鍗?`(A) [recommended] Install the browser extension and let it auto-sync` 鏁欑▼ + 閾炬帴锛屽啀鍒?`(B) F12 浜旀` 鍏滃簳
- **`scripts/install.ps1`** 鍚屾牱鐨?(A)/(B) 浜岄€変竴寮曞
- **`docs/agent-install.md` Step 4** 瀹屽叏閲嶅啓锛氭槑纭憡璇?AI agent 榛樿璧版墿灞曡矾寰勶紝涓嶅啀涓婃潵灏辫鐢ㄦ埛 F12锛涘鏋滅敤鎴烽€夋墿灞曪紝agent 涓嶄紶 `--bilibili-cookie`锛岃 bootstrap 璧?`running_with_missing_secrets` 鐘舵€侊紝鍐嶅憡璇夌敤鎴枫€岃鎵╁睍锛岀瓑鍚屾銆嶏紝鏈€鍚庡啀璁?agent 鑷繁璺?`openbiliclaw init`
- **`src/openbiliclaw/cli.py` 鐨?`_interactive_auth_setup`** 鏀规垚 2 閫?1锛?) 瑁呮墿灞曡嚜鍔ㄥ悓姝ワ紙榛樿锛岄€変簡鐩存帴 `typer.Exit(0)`锛屾彁绀轰箣鍚庢墿灞曞悓姝ュソ鍐嶈窇 `openbiliclaw init`锛?2) 鐜板満鎵嬭创
- **`docs/docker-deployment.md` / `docs/openclaw-quickstart.md`** 鍚屾鎶婃墿灞曟斁鍒?Cookie 姝ラ鐨勯閫?

鏁堟灉锛氳鎵╁睍鏄粯璁よ矾寰勶紝F12 鏄€屾娲讳笉鎯宠鎵╁睍銆嶆椂鐨勫厹搴曘€俛gent-install.md 缁?AI agent 鐨勬寚浠や篃鍙樹簡锛氶粯璁や笉瑕佽拷闂?Cookie锛岄紦鍔辩敤鎴疯鎵╁睍锛屾墿灞曞悓姝ュ畬鍚庣画 init 灏遍綈娲讳簡銆?

---

## v0.3.12: 娴忚鍣ㄦ墿灞曡嚜鍔ㄥ悓姝?B 绔?Cookie 鍒板悗绔紝鍐嶄篃涓嶇敤 F12锛?026-04-30锛?

涔嬪墠鐢ㄦ埛閰?B 绔?Cookie 蹇呴』鑷繁 F12 鈫?Network 鈫?澶嶅埗 Cookie 澶?鈫?绮樺埌鍚戝閲屻€傝繖涓綋楠屽鍒氭帴瑙︽湰椤圭洰鐨勪汉鏋佷笉鍙嬪ソ锛岃€屼笖 Cookie 杩囨湡/鍒锋柊鍚庤繕寰楅噸鍋氥€傚叾瀹炴墿灞曟湰鏉ュ氨璺戝湪 bilibili.com 涓婏紝鑳界洿鎺ヨ鐢ㄦ埛鐨?Cookie锛屾妸杩欎釜娴佺▼鑷姩鍖栨槸澶╃劧鐨勩€?

### Backend锛氭柊澧?`POST /api/bilibili/cookie`

鍦?`src/openbiliclaw/api/app.py` 鍔犱簡涓€涓鐐癸紝鎺ユ敹鎵╁睍鎺ㄨ繃鏉ョ殑 Cookie锛?

1. **鏍￠獙**锛氬厛鐢?`AuthManager.validate_cookie` 鎵撲竴娆?`api.bilibili.com/x/web-interface/nav`锛岀‘璁?Cookie 鐪熺殑澶勪簬鐧诲綍鐘舵€佲€斺€旈伩鍏嶆棤鏁?Cookie 瑕嗙洊涓€涓繕鍦ㄥ伐浣滅殑鏃?Cookie
2. **鎸佷箙鍖?*锛氬啓鍒?`data/bilibili_cookie.json`锛堣繍琛屾椂鐪熸鐢ㄧ殑婧愶級+ `config.toml` 鐨?`[bilibili].cookie`锛堥暅鍍忥紝缁?`config-show` 鐢級
3. **鐑噸杞?*锛氳皟 `RuntimeContext.rebuild_from_config` 鍘熷瓙鎹㈡帀 BilibiliAPIClient锛屼笅涓€娆?API 璋冪敤灏辩敤鏂?Cookie
4. **骞挎挱**锛氶€氳繃 WebSocket runtime-stream 鍙?`bilibili_cookie_synced` 浜嬩欢锛屾墿灞?popup 鍙互鍋滄帀銆岃鐧诲綍銆嶆彁绀?

璇锋眰 model 鍦?`api/models.py` 鏂板锛歚BilibiliCookieIn`锛坄cookie`, `source`, `validate_with_bilibili`锛? `BilibiliCookieResponse`锛坄ok`, `authenticated`, `username`, `user_id`, `message`锛夈€?

### Extension锛氳嚜鍔ㄨ + 鎺?

`extension/src/background/cookie-sync.ts` 鏂版枃浠讹紝service-worker 鍚姩鏃舵寕涓婏細

- **瑙﹀彂鍦烘櫙**
  - `chrome.runtime.onInstalled` / `onStartup` 鈫?鍚姩涓€娆″悓姝?
  - `chrome.cookies.onChanged` 鐩戝惉鍣紙domain 鏀跺熬鍖归厤 `bilibili.com`锛夆啋 鐢ㄦ埛鐧诲綍/鐧诲嚭/Cookie 鍒锋柊绔嬪嵆鍚屾銆俤ebounce 2s 閬垮厤涓€娆＄櫥褰曡Е鍙?6-10 娆?POST
  - 姣忓皬鏃朵竴娆?alarm 鍏滃簳锛堥槻姝?service worker 鍗歌浇鏈熼棿婕忔帀 onChanged 浜嬩欢锛?

- **鍙帹鏈夋剰涔夌殑 Cookie**锛歚SESSDATA` / `bili_jct` / `DedeUserID` 涓変欢濂楃己涓€涓嶅彂锛岄伩鍏嶅悗绔仛鏃犺皳鐨?nav 鏍￠獙

- **鍙湪鐢ㄦ埛鐧诲綍鏃舵帹**锛氭湭鐧诲綍鐩存帴 `return false`锛屼笉鎵撴壈鍚庣

`manifest.json` 鍔?`cookies` 鏉冮檺 + 鐗堟湰 0.3.1 鈫?0.3.2銆?

### 瀹夊叏妯″瀷

- 鍚庣榛樿缁?`127.0.0.1`锛屽缃戞懜涓嶅埌杩欎釜绔偣
- Cookie 鍏ㄧ▼鍦ㄧ敤鎴锋湰鏈猴細娴忚鍣?鈫?service worker 鈫?localhost backend 鈫?鏈湴纾佺洏
- CORS 鐜扮姸鏄?`*`锛屽 localhost 鍚庣鏉ヨ娌℃剰涔夛紙浠讳綍鎵撳埌 127.0.0.1 鐨勮姹傛湰鏉ュ氨鏉ヨ嚜鏈満锛?
- 鐢ㄦ埛鏀规垚 `--host 0.0.0.0` 搴旇鑷繁鍔?auth 灞傦紙杩欐槸鍘嗗彶 stance锛屾病鏀癸級

### 鐢ㄦ埛鎰熺煡

- 瑁呭ソ鎵╁睍 鈫?鍑犵鍐呰嚜鍔ㄥ悓姝?鈫?鍚庣鏃ュ織鐪嬪埌 `cookie_synced`锛宍/api/runtime-status` 杩斿洖鐧诲綍鎬?
- Cookie 杩囨湡浜嗭紵鎵╁睍浼氬湪涓嬫 `chrome.cookies.onChanged` 鑷姩鎺ㄦ柊鐨勶紝鏃犻渶鎵嬪姩鎿嶄綔
- 涓€鍙ヨ瘽瑁呮満鐨?wizard 閲屼粛淇濈暀 cookie prompt 浣滀负鍏滃簳锛岀粰涓嶈鎵╁睍鐨勭敤鎴风敤

---

## v0.3.11: Docker 鑷甫 Ollama embedding sidecar + CLI 鍚戝涔熻兘鑷姩瑁?Ollama锛?026-04-30锛?

v0.3.10 鎶婁竴鍙ヨ瘽瑁呮満锛坕nstall.sh / install.ps1 鈫?agent_bootstrap.py锛夌殑 Ollama 鑷姩瀹夎鍋氶綈浜嗭紝浣嗚繕鏈変袱鏉¤矾寰勬紡浜嗭細

1. **Docker 妯″紡**锛氱敤鎴疯窇 `docker compose up -d --build` 鍚庯紝embedding 娈甸粯璁ょ┖鐫€锛岀涓€娆″彂璇锋眰鎵嶅彂鐜般€屽挦锛岄渶瑕佷釜 embedding API key 鎴栦竴涓?host 涓婅窇鐨?Ollama銆?
2. **鎵嬪姩瀹夎** + 鐩存帴璺?`openbiliclaw init`锛欳LI 鍚戝鍙細妫€娴?Ollama锛屾病瑁呯殑璇濇彁绀虹敤鎴峰幓瑁咃紝娌″惎鐢ㄣ€屾垜甯綘瑁呫€?

### 1. `docker-compose.yml` 澶氫簡 `ollama` sidecar

```yaml
services:
  ollama:
    image: ollama/ollama:latest
    # 鍚姩鏃舵媺 bge-m3锛宒aemon 涓€鐩磋窇
    # healthcheck 绛夊埌 bge-m3 灏辩华鎵嶆姤 healthy
  openbiliclaw-backend:
    depends_on: { ollama: { condition: service_healthy } }
    environment:
      OPENBILICLAW_SEED_OLLAMA_DEFAULTS: "1"
      OPENBILICLAW_OLLAMA_BASE_URL: "http://ollama:11434/v1"
      OPENBILICLAW_EMBEDDING_MODEL: "bge-m3"
volumes:
  openbiliclaw_ollama:  # bge-m3 鎸佷箙鍖栵紝閲嶅缓瀹瑰櫒涓嶉噸鎷?
```

### 2. `docker_runtime.py` 鍚姩鏃舵寜 env 鑷姩鍐?embedding 榛樿

`bootstrap_runtime_root` 澶嶅埗 `config.example.toml` 鍒?volume 鍚庯紝濡傛灉 `OPENBILICLAW_SEED_OLLAMA_DEFAULTS` 涓虹湡锛屽氨鎶婅繖涓変釜鍊煎～杩涘幓锛?
- `[llm.ollama] base_url = http://ollama:11434/v1`
- `[llm.embedding] provider = ollama`
- `[llm.embedding] model = bge-m3`

宸叉湁鐨?`config.toml` 涓嶄細琚鐩栤€斺€旂敤鎴锋敼杩囩殑鍋忓ソ閮戒細淇濈暀銆?

鏁堟灉锛氱敤鎴疯窇 `docker compose up -d --build` 鍚庯紝**鍙渶瑕佷竴涓?chat 妯″瀷鐨?API Key**锛宔mbedding 瀹屽叏鍏嶈垂 + 绂荤嚎 + 鐢ㄥ畬鍗宠蛋銆傜涓€娆″惎鍔ㄥ鑺?2鈥? 鍒嗛挓涓嬭浇 bge-m3锛垀568MB锛夛紝鍚庣画浠?named volume `openbiliclaw_ollama` 鐩存帴澶嶇敤銆?

涓嶈 sidecar 鐨勭敤鎴凤細鎶?`docker-compose.yml` 鐨?`ollama` 鏈嶅姟鍧楀拰鍚庣鐨?`OPENBILICLAW_SEED_OLLAMA_DEFAULTS` env 鍒犳帀灏辫銆?

### 3. CLI 鍚戝锛坄openbiliclaw init` 鐩存帴璺戯級涔熸敮鎸佽嚜鍔ㄨ Ollama

鏂板涓や釜 helper锛?
- `_ollama_install_if_missing()`锛氭娴?鈫?璇㈤棶鐢ㄦ埛 鈫?brew/winget/install.sh
- `_ollama_start_serve_background()`锛氬悗鍙板惎鍔?daemon锛岃疆璇?`/api/version` 绛?15s

Phase 1锛堥€?Ollama 鍋?chat锛夊拰 Phase 3 閫夐」 2锛堥€?Ollama 鍋?embedding锛夐兘鎺ュ叆浜嗚繖濂楋細鐢ㄦ埛涓嶅啀闇€瑕佸厛鍘诲闈㈣ Ollama锛屽悜瀵间竴鏉￠緳鎼炲畾銆?

---

## v0.3.10: 閫?Ollama 鏃朵竴鍙ヨ瘽瑁呮満鑷繁瑁?Ollama + 鎷夋ā鍨嬶紙2026-04-30锛?

v0.3.6 鎶?Ollama 鎺ㄨ崘鎴愩€屾柊鎵嬮粯璁ゃ€嶉€夐」鍚庯紝鏂伴棶棰樻潵浜嗭細鐢ㄦ埛鍦ㄥ悜瀵奸噷閫変簡 Ollama锛屼絾瀹為檯涓婅繕寰楄嚜宸?`brew install ollama` / 瑁?Windows 瀹夎鍖?/ 璺?install.sh锛屽啀 `ollama pull llama3` 鈥斺€?鍚﹀垯鍚庣鍚姩浼氬崱鍦ㄣ€孫llama not running銆嶃€傝繖褰诲簳杩濆弽浜嗐€屼竴鍙ヨ瘽瑁呮満銆嶇殑鎵胯銆?

`agent_bootstrap.py` 鐜板湪鍐呯疆 4 闃舵 Ollama 鑷姩鍖栵細

1. **妫€娴?*锛歚shutil.which('ollama')` 鎵句簩杩涘埗
2. **瀹夎**锛堝鏋滄病瑁咃級锛?
   - macOS 鈫?`brew install ollama`锛堟病 brew 鏃舵姤閿欏苟缁欏嚭 https://ollama.com/download锛?
   - Windows 鈫?`winget install -e --id Ollama.Ollama`锛堣嚜鍔ㄦ帴鍙?EULA锛涙病 winget 鏃舵姤閿欑粰 URL锛?
   - Linux 鈫?`curl -fsSL https://ollama.com/install.sh | sh`锛堝畼鏂硅剼鏈嚜甯?systemd 閰嶇疆锛?
3. **鍚姩 daemon**锛堝鏋滄病鍦ㄨ窇锛夛細鍚庡彴 spawn `ollama serve`锛岃疆璇?`/api/version` 绛夋渶澶?15s
4. **鎷夋ā鍨?*锛氭鏌?`/api/tags`锛屾病鎷夌殑灏?`ollama pull <name>`锛岃繘搴︽祦寮忔墦鍒?stdout

姣忎釜闃舵鍗曠嫭鍙?`BootstrapResult` 浜嬩欢锛坄ollama_installed` / `ollama_serving` / `ollama_model_pulled`锛夛紝AI agent 瑙ｆ瀽 JSON 娴佸氨鑳界簿纭煡閬撳崱鍦ㄥ摢涓€姝ャ€傛渶鍚庤繕浼氬彂涓€涓眹鎬?`ollama_ready` 浜嬩欢銆?

瑙﹀彂鏉′欢锛歚--provider ollama` 鎴?`--embedding-provider ollama` 浠讳竴涓虹湡锛屼笖 `mode != docker`锛圖ocker 妯″紡涓嬪悗绔蛋 `host.docker.internal:11434` 鎵惧涓?Ollama锛岃嚜鍔ㄨ鍒板鍣ㄥ唴鏄敊鐨勶級銆傛柊澧?`--skip-ollama-setup` 缁欐兂鑷繁绠?Ollama 鐨勭敤鎴峰厹搴曘€?

`docs/agent-install.md` 鍚屾锛歄ption 1锛圤llama锛夌殑鎸囧紩浠庛€岃鐢ㄦ埛鑷繁瑁呫€嶆敼鎴愩€屾垜浼氬府浣犺銆嶏紝embedding 娈典篃鏄庣‘鍛婅瘔 AI agent 涓嶈璁╃敤鎴锋墜鍔?`ollama pull bge-m3`銆?

---

## v0.3.9: 涓€鍙ヨ瘽瑁呮満閫傞厤 PowerShell 5.1锛圵in10/Win11 榛樿锛夛紙2026-04-30锛?

涔嬪墠鐨?`iwr <url> | iex` 涓€鍙ヨ瘽鍦?Windows 10 / 11 涓婃病瑁?PowerShell 7 鐨勭敤鎴烽偅閲岀洿鎺ユ寕鈥斺€擯S 5.1 榛樿璧?TLS 1.0/1.1锛屼絾 GitHub 鐜板湪鍙帴鍙?TLS 1.2+锛屾彙鎵嬪け璐ユ姤銆寀nderlying connection was closed銆嶏紝鏂版墜鏍规湰鐪嬩笉鎳傘€?

淇簡 4 浠朵簨锛?

1. **README.md / README_EN.md / docs/agent-install.md 涓€鍙ヨ瘽鍛戒护鍓嶇紑鍔?TLS 1.2 璁剧疆**锛?
   ```powershell
   [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; iwr https://...install.ps1 -UseBasicParsing | iex
   ```
   PS 7+ 鐢ㄦ埛鍙互鐪佹帀鍓嶇紑锛汸S 5.1 鐢ㄦ埛蹇呴』甯?

2. **`scripts/install.ps1` 鑷韩鍚姩鏃朵篃璁句竴娆?TLS 1.2**锛氳剼鏈竴鏃﹀紑濮嬭窇锛屽悗缁殑 git clone / pip / uv / Invoke-WebRequest 閮借鐩栧埌浜?

3. **淇?`?? '' ` 杩欎釜 PS 7-only 璇硶**锛歭ine 281 鐢ㄧ殑 null 鍚堝苟鎿嶄綔绗?PS 5.1 涓嶆敮鎸侊紝鏀规垚鏄惧紡 `if ($null -ne $ReuseFrom) { $ReuseFrom } else { '' }`

4. **`scripts/install.ps1` 鐨?.EXAMPLE 娉ㄩ噴鎷嗘垚 PS 5.1 / PS 7+ 涓や釜绀轰緥**锛岃鐢ㄦ埛涓€鐪艰兘鐪嬪嚭鍝釜瀵瑰簲鑷繁

`#requires -Version 5.1` 宸茬粡鍦ㄦ枃浠堕《閮紝浣?PS 瑙ｆ瀽鍣ㄥ彧鍦ㄨ剼鏈紑濮嬫墽琛屾椂妫€鏌ュ畠锛屽鑴氭湰涓嬭浇闃舵锛堝闈㈤偅涓?iwr锛夋棤鑳戒负鍔涳紝鎵€浠ヤ笅杞介樁娈靛繀椤婚潬鐢ㄦ埛棰勫厛璁惧ソ TLS銆?

---

## v0.3.8: init 鍚姩鍓嶆槑纭憡璇夌敤鎴烽璁＄敤鏃讹紙2026-04-30锛?

v0.3.7 鎶?init 鑷姩璺戜簡璧锋潵锛屼絾鐢ㄦ埛鐪嬪埌灞忓箷闈欓粯鍑犲崄绉掑氨寮€濮嬫€€鐤戙€屾槸涓嶆槸鍗′簡锛熴€嶃€傝繖娆＄粰 `init` 鍔犱簡涓€娈靛紑鍦虹櫧锛岃窇涔嬪墠鏄庣‘鍛婅瘔鐢ㄦ埛锛?

```
鈴? 杩欎竴姝ラ娆¤繍琛岄璁￠渶瑕?2鈥? 鍒嗛挓锛岃淇濇寔缃戠粶鐣呴€氬埆涓柇銆?
  鍥涗釜闃舵浼氫緷娆¤窇锛?
    1/4  鎷?B 绔欏巻鍙?/ 鏀惰棌 / 鍏虫敞锛堚増 20鈥?0s锛岀湅浣犵殑鍒楄〃澶у皬锛?
    2/4  鍒嗘瀽鍋忓ソ锛圠LM 璋冪敤锛屸増 30鈥?0s锛?
    3/4  鐢熸垚鐏甸瓊鐢诲儚锛圠LM 璋冪敤锛屸増 30鈥?0s锛?
    4/4  鍙戠幇棣栬疆鍐呭姹狅紙澶氱瓥鐣ュ苟鍙?+ LLM 璇勪及锛屸増 1鈥? 鍒嗛挓锛?
鍏ㄧ▼浼氭墦鍗拌繘搴︼紝涓嶈浠ヤ负鍗′綇浜嗏€斺€擫LM 鍗曟鍝嶅簲鍙兘灏辫 10鈥?0s銆?
```

姣忎釜闃舵鐨勮€楁椂鍖洪棿鏄寜瀹樻柟浜戞ā鍨嬶紙GPT-4o-mini / Gemini Flash锛? 鍥藉唴缃戠粶浼扮殑锛涙湰鍦?Ollama 浼氭洿鎱紝鐪嬬敤鎴锋満鍣ㄣ€?

---

## v0.3.7: 涓€鍙ヨ瘽瑁呮満閰嶉綈鍑嵁鍚庤嚜鍔ㄨ窇 init锛?026-04-30锛?

v0.3.6 鐨勪汉鏈虹晫闈㈣櫧鐒跺ソ浜嗭紝浣嗘湁涓祦绋嬫紡娲烇細鐢ㄦ埛缁欏畬鍑嵁鍚庯紝AI agent 鎸夋枃妗ｇ収鍋氬姞涓婁簡 `--skip-init`锛岀粨鏋滆鏈烘祦绋嬪湪銆宑onfig 鍐欏ソ銆佸仴搴锋鏌ラ€氳繃銆嶅氨鍋滀簡銆?*鐢ㄦ埛鎵撳紑鎵╁睍鐪嬩笉鍒颁换浣曚笢瑗?*鈥斺€旂敾鍍忔病鐢熸垚銆佸巻鍙叉病鎷夈€侀杞唴瀹规睜鏄┖鐨勶紝闇€瑕佸啀鎵嬪姩璺戜竴閬?`openbiliclaw init`銆傝繖褰诲簳杩濆弽浜嗐€屼竴鍙ヨ瘽瑁呮満銆嶇殑鎵胯銆?

### 淇鍐呭

1. **`docs/agent-install.md` Hard Rule 绗?3 鏉″交搴曞弽杞?*锛氬師鏉ユ槸銆孨ever run `openbiliclaw init` unless the user explicitly asks銆嶏紝鏂扮増鏄€孯un init by default 鈥?DO NOT pass `--skip-init`銆嶃€傜粰 AI agent 鐨勬寚浠ら潪甯告槑纭細鍑嵁榻愪簡灏辫 init 鑷姩璺?

2. **绀轰緥鍛戒护鍒犻櫎 `--skip-init`**锛歚docs/agent-install.md` 閲屼袱涓ず渚嬮兘涓嶅啀甯﹁繖涓?flag

3. **`agent_bootstrap.py` 鐨?auto-init 閫昏緫淇簡涓変釜 bug**锛?
   - 涔嬪墠 venv python 璺緞纭紪鐮?`.venv/bin/python`锛圥OSIX锛夛紝Windows 涓婃壘涓嶅埌鈥斺€旀敼鎴愭寜 `os.name == "nt"` 閫?`.venv/Scripts/python.exe` 鎴?`.venv/bin/python`
   - Docker 妯″紡涔嬪墠涓嶈窇 init鈥斺€旀柊鐗堢敤 `docker exec -i openbiliclaw-backend openbiliclaw init` 鍦ㄥ鍣ㄩ噷璺?
   - 鍏滃簳浠?`python3` 鏀规垚 `sys.executable`锛屾洿鍙潬

4. **`install.sh` / `install.ps1` 鐘舵€佸潡鍔犱竴娈佃鏄?*锛?
   ```
   This auto-runs 'openbiliclaw init' once credentials check out:
     - pulls your Bilibili history
     - generates the soul profile
     - runs the first content discovery pass
   Takes 2-5 minutes. Without this step the extension shows nothing.
   ```
   杩樺湪 follow-up 鍛戒护鏃佽竟鍔犱簡銆孌O NOT add --skip-init銆嶆彁绀猴紝閬垮厤 AI agent 鎸夋儻鎬у姞涓婅繖涓?flag

5. **agent-install.md 澧炲姞銆屾姤鍛婃渶缁堢姸鎬併€嶆竻鍗?*锛欰I agent 瑁呭畬鍚庡繀椤诲憡璇夌敤鎴凤細
   - 鉁?鍚庣宸插惎鍔?
   - 鉁?閰嶇疆宸插啓鍏?
   - 鉁?鍒濆鍖栧凡瀹屾垚锛堟媺鍘嗗彶銆佺敓鎴愮敾鍍忋€佽窇鍙戠幇锛?
   - 馃憠 涓嬩竴姝ワ細瑁呮祻瑙堝櫒鎵╁睍

   骞舵彁绀虹敤鎴?init 棣栨杩愯闇€ 2-5 鍒嗛挓锛岄伩鍏嶈浠ヤ负銆屽崱浣忎簡銆?

---

## v0.3.6: 瑁呮満鍚戝浠庢櫘閫氱敤鎴疯瑙掑交搴曢噸鍐欙紙2026-04-30锛?

v0.3.5 鐨勫悜瀵艰櫧鐒堕棶鍏ㄤ簡锛屼絾椤哄簭銆佹帾杈炲拰榛樿閮戒笉澶熷弸濂姐€傚熀浜庣嚎涓?AI agent 瀹為檯璺戝嚭鏉ョ殑鎻愰棶琚弽棣堛€屽お宸€嶏紝v0.3.6 鏁翠釜浜烘満鐣岄潰閲嶅啓锛?

### 1 鈥?Ollama 鎺掔涓€锛屼笉鍐嶆妸 OpenAI 褰撻粯璁?

涔嬪墠 `default="openai"`锛屼絾 OpenAI 鏄敹璐圭殑銆佽鍘荤敵璇?Key 鎵嶈兘鐢紝瀵瑰垰鎺ヨЕ鏈」鐩殑鐢ㄦ埛鏋佷笉鍙嬪ソ銆倂0.3.6锛?

- 鑿滃崟绗竴椤规槸 **鏈湴 Ollama**锛堝厤璐?/ 绂荤嚎 / 鏃犻渶 API Key锛夛紝鏄庣‘鏍囨敞銆屾帹鑽愭柊鎵嬨€?
- Tip 鐩存帴鍛婅瘔鐢ㄦ埛锛氥€屼笉鎯宠姳閽便€佸垰鎺ヨЕ鏈」鐩紝灏遍€?1銆?
- 榛樿鍊兼敼鎴?`1=Ollama`锛屽洖杞﹀嵆鐢?

### 2 鈥?銆孫penAI 瀹樻柟銆嶅拰銆孫penAI 鍗忚鍏煎鑷缓缃戝叧銆嶆媶鎴愪袱涓彍鍗曢」

涔嬪墠 `openai` 涓€涓」瑕佽鐩栥€孫penAI 鍏徃鐨勬湇鍔°€?銆孉zure / vLLM / LMStudio / OneAPI / 鑷缓缃戝叧銆嶏紝浠庣敤鎴峰績鏅烘ā鍨嬬湅瀹屽叏鏄袱浠朵簨銆侫I agent 涔熷垎涓嶆竻瑕佷笉瑕佽拷闂?base_url銆倂0.3.6 鎶婂畠浠媶寮€锛?

- **鑿滃崟 2 = OpenAI 瀹樻柟**锛氬彧闂?API Key锛宐ase_url 璧?`https://api.openai.com/v1`
- **鑿滃崟 7 = OpenAI 鍗忚鍏煎鑷缓缃戝叧**锛氬己鍒堕棶 Base URL锛堣繖鏄敮涓€鍖哄垎涓よ€呯殑瀛楁锛? API Key + 妯″瀷鍚?

搴曞眰閮借繕鏄啓鍒?`[llm.openai]` 娈碉紙鍏变韩 OpenAI 鍗忚瑙ｆ瀽鍣級锛屼絾鐢ㄦ埛鍜?AI agent 涓嶅啀闇€瑕佸湪蹇冮噷鍋氳繖涓槧灏?

### 3 鈥?Embedding 鍗曠嫭鎴愪竴涓竻鏅扮殑闂锛岄檮甯﹁В閲?

涔嬪墠鍚戝闂畬鑱婂ぉ妯″瀷鐩存帴鎺?embedding锛屾病鏈夋槑纭殑銆岃繖鏄彟涓€浠朵簨銆嶆爣璇嗐€倂0.3.6 鍦?embedding 闃舵鍏堟墦鍗拌В閲婏細

> Embedding 鏄拰鑱婂ぉ妯″瀷鍒嗗紑鐨勶細鎶婅棰戞爣棰?绠€浠嬪彉鎴愬悜閲忥紝鐢ㄤ簬璺ㄨ棰戝幓閲嶅拰鐩镐技搴﹀垽瀹氥€傞娆″緢楂橈紝鎵€浠ュ崟鐙嫀鍑烘潵閰嶃€?

鐒跺悗鎵嶈繘鍏?4 閫?1 鑿滃崟銆傛枃妗堜篃鏀逛簡锛氶€夐」 1 浠庛€岃窡闅忎富 provider銆嶆敼鎴愩€岃窡闅忎綘鍒氭墠閫夌殑 LLM锛堟渶鐪佷簨锛岄粯璁わ級銆?

### 4 鈥?B 绔?Cookie 鏁欑敤鎴锋€庝箞鎷匡紝涓嶆槸鍙涪涓€涓?prompt

涔嬪墠 `_interactive_auth_setup` 鍙棶銆岃杈撳叆 B 绔?Cookie:銆嶏紝鐢ㄦ埛鐪嬪畬涓€鑴告嚨鈥斺€擟ookie 鏄粈涔堬紵鎬庝箞鎷匡紵v0.3.6 鍦?prompt 涔嬪墠鍏堟墦鍗帮細

- **涓轰粈涔堥渶瑕?*锛氭媺鍘嗗彶璁敾鍍?+ 璋?B 绔?API 鎷胯棰戣鎯?
- **鏁版嵁瀹夊叏淇濊瘉**锛氬彧瀛樻湰鏈?`data/bilibili_cookie.json`锛屼笉涓婁紶浠讳綍鍦版柟
- **鎬庝箞鑾峰彇**锛氭祻瑙堝櫒 F12 鈫?Network 鈫?澶嶅埗 cookie 璇锋眰澶寸殑 5 姝ユ祦绋?
- **鏇寸畝鍗曠殑鏇夸唬**锛氳娴忚鍣ㄦ墿灞曡嚜鍔ㄥ鐢ㄧ櫥褰曟€?

### 5 鈥?姣忎釜瀛楁閮芥湁銆岃繖鏄共鍢涚殑銆嶄竴鍙ヨ瘽璇存槑

渚嬪鑿滃崟 7 閫夐」閰嶇疆鏃讹細

> 浣犵殑缃戝叧 Base URL锛堝繀濉紝渚?http://localhost:8000/v1锛?
> API Key锛堝鏋滅綉鍏充笉閴存潈鍙暀绌猴級
> 缃戝叧涓婂疄闄呴儴缃茬殑妯″瀷鍚嶏紙渚?meta-llama/Llama-3.1-70B锛?

鑰屼笉鏄喎鍐板啺鐨?`Base URL:` / `API Key:` / `model:`

### 6 鈥?`docs/agent-install.md` 鍚屾閲嶅啓銆孉sking the user the right questions銆嶆

AI agent锛圕laude / Codex / Cursor / OpenClaw锛夎窇涓€鍙ヨ瘽瑁呮満鏃朵細璇昏繖浠?contract銆傛柊鐗堢粰 agent 鐨勬寚浠ゆ槸锛?

- **涓嶈涓€娆℃€ф妸鎵€鏈夐棶棰樺€掔粰鐢ㄦ埛**锛屽垎 3 姝ヨ蛋锛圠LM 鈫?Embedding 鈫?Cookie锛?
- **瑙ｉ噴姣忎釜涓滆タ鍦ㄥ共鍢?*锛堝湪鐢ㄦ埛璇涓嬶級
- **鎸夐€夐」鍙棶璇ラ€夐」闇€瑕佺殑瀛楁**锛堥€?Ollama 灏卞埆闂?API Key锛涢€夊畼鏂瑰巶鍟嗗氨鍒棶 base_url锛?
- **Cookie 涓€瀹氳闄勮幏鍙栨楠?*

---

## v0.3.5: 瑁呮満鍚戝闂叏鎵€鏈夐棶棰橈紝涓嶅啀鍥犮€宱penai銆嶆涔夌寽閿欙紙2026-04-29锛?

### 4 闃舵瀹夎鍚戝锛坄init` / `setup-embedding`锛?

涔嬪墠鍚戝鍙棶銆宲rovider + api_key銆嶄袱浠朵簨锛屼絾 `openai` 鍦ㄦ垜浠繖閲屽叾瀹炴槸**鍗忚瀹舵棌**鈥斺€擜zure / vLLM / LMStudio / OneAPI / 鑷缓缃戝叧閮借蛋杩欎竴椤癸紝base_url 鍜?model 涓嶄竴鏍风瓟妗堝氨瀹屽叏涓嶅悓銆傚皯闂殑浠ｄ环鏄敤鎴烽厤瀹屽悗璺戜笉閫氾紝鍐嶈寮曞鍥炴潵鎵嬪姩鏀?`config.toml`銆倂0.3.5 鎶婂悜瀵兼敼鎴愶細

- **Phase 1 鈥?Provider 閫夋嫨**锛氬厛鎵撳嵃涓€寮?provider 鍗忚鏃忚〃锛屾槑纭憡璇夌敤鎴?`openai` 鏄崗璁鏃忎笉鏄巶鍟?
- **Phase 2 鈥?Provider 涓変欢濂?*锛歜ase_url / api_key / model锛屾瘡涓?provider 閮藉甫鍚堢悊榛樿锛涙寜鍥炶溅鎺ュ彈锛屼笉寮哄埗閲嶈緭
- **Phase 3 鈥?Embedding锛? 閫?1锛?*锛氳窡闅忎富 provider / 鏈湴 Ollama bge-m3 / 鑷畾涔?OpenAI 鍏煎鏈嶅姟锛坴LLM / OneAPI 绛夛級/ 鎸囧畾鍏朵粬宸茬煡 provider
- **Phase 4 鈥?Per-module 瑕嗙洊锛堝彲閫夛級**锛氭槑鏄炬爣娉ㄣ€岄珮绾э紝鍙烦杩囥€嶃€傜粰 soul / discovery / recommendation / evaluation 鍗曠嫭璁?provider/model锛堝吀鍨嬪満鏅細鍙戠幇 / 璇勪及璧颁究瀹滄ā鍨嬶紝鐢诲儚璧伴珮璐ㄩ噺妯″瀷锛?

### `agent_bootstrap.py` 鏂板 7 涓?flag锛孉I agent 涔熻兘闂叏

涔嬪墠 AI agent 鍙兘浼?`--llm-api-key` + `--bilibili-cookie`锛屼笉澶熻鐩栧悜瀵兼柊澧炵殑瀛楁銆倂0.3.5 鏂板锛?

| Flag | 鐢ㄩ€?|
|---|---|
| `--llm-base-url` | OpenAI 鍏煎鏈嶅姟鐨勫叆鍙?URL |
| `--llm-model` | 涓?provider 鐨?chat 妯″瀷鍚?|
| `--embedding-provider` | embedding provider锛堢┖瀛楃涓?= 璺熼殢涓?provider锛?|
| `--embedding-model` | embedding 妯″瀷鍚?|
| `--embedding-base-url` | 鑷墭绠?embedding 缃戝叧鐨?base_url |
| `--embedding-api-key` | 鑷墭绠?embedding 缃戝叧鐨?API Key |
| `--module-override MODULE=PROVIDER:MODEL` | 鍙噸澶嶏紝per-module 瑕嗙洊 |

`docs/agent-install.md` 鍚屾鍔犱簡涓€寮犮€屾渶灏忔彁闂〃銆嶏紝鏄庣‘鍛婅瘔 AI agent 鍝簺闂鍦ㄥ摢涓?flag 涓婁紶鈥斺€斾互鍚庝笉浼氬啀鍥犱负 OpenAI 鍏煎鏈嶅姟琚粯璁ゆ垚瀹樻柟 OpenAI 璺戞寕

### 淇锛氭祴璇曟薄鏌撳紑鍙戣€呯湡瀹?`config.toml`

涔嬪墠 4 涓?`_save_*` 鍗曞厓娴嬭瘯鍙?`monkeypatch.chdir(tmp_path)`锛屼絾 `_project_root()` 浼樺厛璇诲寘瀹夎璺緞锛岀粨鏋滄祴璇曞€硷紙`sk-new` / `gemini-2.0-flash-exp` / 鍋?`claude` 瑕嗙洊绛夛級浼氬啓杩涘紑鍙戣€呯殑鐪熷疄 `config.toml`銆倂0.3.5锛? 涓祴璇曟敼鐢?`monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", tmp_path)`锛岄厤鍚?chdir 鍙岄噸淇濋櫓

### 鏂囨。

- `docs/modules/cli.md`锛氳ˉ鍏?`init` 4 闃舵浜や簰寮?transcript + `setup-embedding` 4 閫?1 琛ㄦ牸
- `docs/modules/config.md`锛歚[llm.openai]` 寮鸿皟鍗忚瀹舵棌 + 鏂板 `[llm.<module>]` 娈佃鏄?
- `docs/agent-install.md`锛氭渶灏忔彁闂〃 + 瀹屾暣 flag 绀轰緥

---

## v0.3.4: 鍘熺敓 Windows 涓€鍙ヨ瘽瑁呮満锛?026-04-29锛?

### Windows 鍘熺敓鏀寔锛屾棤闇€ Docker / WSL2

- 鏂板 `scripts/install.ps1`锛岃涓哄榻?`install.sh`锛氬厠闅?/ 鑷姩鍗囩骇鐜版湁 checkout / 妫€娴?Python 3.11+ / 璋冪敤 `agent_bootstrap.py` / 杈撳嚭瀵归綈 sprintf 鏍煎紡鐨勭姸鎬佸潡
- 鐢ㄦ埛涓€鍙ヨ瘽瑁呮満锛?
  ```powershell
  iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex
  ```
- 涔嬪墠 `install.sh` 绗?107 琛岀洿鎺ユ嫆缁?`MINGW*/MSYS*/CYGWIN*` 璁?Windows 鐢ㄦ埛鍘昏 WSL2 鈥斺€?鐜板湪 PowerShell 鐢ㄦ埛璧?`install.ps1` 鍗冲彲

### `agent_bootstrap.py` Windows 閫傞厤

- `start_local_backend`锛歅OSIX 鐢?`start_new_session=True`锛學indows 鐢?`creationflags=DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP`锛岃 backend 鐪熸鑴辩鐖?console 璺?
- `_find_pids_on_port`锛歀inux/Mac 璧?`lsof`锛沇indows 瑙ｆ瀽 `netstat -ano` 鎵?LISTENING PID
- `_terminate_pids`锛歀inux/Mac 鐢?`os.kill(SIGTERM/SIGKILL)`锛沇indows shell out 鍒?`taskkill /PID /T [/F]`锛屾纭鐞?Windows 杩涚▼缁勫仠姝㈣涔?

### 鏂囨。

- `README.md` / `README_EN.md` 涓€閿懡浠ゅ垎鍙屽钩鍙板睍绀猴紝鍔?v0.3.4 鎻愮ず"鏃犻渶 Docker / WSL2"
- `docs/agent-install.md` 缁?AI agent 鍔犲钩鍙版娴嬫寚寮曪細鑳戒粠鐢ㄦ埛鐜鎺ㄦ柇灏卞埆闂?
- `docs/changelog.md` 鏂版潯鐩紙鏈妭锛?

> 浠呭悗绔彂鐗堬紙backend-v0.3.4锛夈€侲xtension 鑷?v0.3.1 闆舵敼鍔紝娌跨敤 extension-v0.3.1銆?

---

## v0.3.3: 淇鏈湴 Ollama embedding 鍏滃簳瀹為檯涓嶇敓鏁堬紙2026-04-29锛?

### 鍏抽敭 bug 淇

**鐥囩姸**锛歷0.3.0 寮曞叆鐨勬湰鍦?Ollama embedding 鍏滃簳鍔熻兘鍦ㄧ敤鎴疯窇 `setup-embedding` 閰嶅ソ鍚庣湅浼肩敓鏁堬紙`config.toml` 鍐欏叆 `[llm.embedding] provider="ollama"`锛夛紝浣嗗疄闄呮墍鏈?embedding 璋冪敤浠嶇劧鎵撳埌 Gemini銆傜嚎涓婃棩蹇楁樉绀?100% 鐨?embedding 閮藉湪 `generativelanguage.googleapis.com/v1beta/.../gemini-embedding-001:batchEmbedContents`锛?% 鍦?`localhost:11434`銆?

**鏍瑰洜**锛歚_maybe_ollama_provider` 鍙湪 `[llm.ollama] model` 鎴?`base_url` 鏈夊～鐨勬椂鍊欐墠娉ㄥ唽 ollama provider锛屼絾 `setup-embedding` 鍚戝鍙啓 `[llm.embedding]`锛屾病纰?`[llm.ollama]`銆侲mbedding 鏈嶅姟鎵句笉鍒?ollama provider锛岄潤榛樺洖閫€鍒?default LLM provider锛圙emini锛夈€?

**淇**锛?

- `_maybe_ollama_provider` 鐜板湪涔熷湪 `[llm.embedding].provider == "ollama"` 鏃惰嚜鍔ㄦ敞鍐?ollama锛屼娇鐢ㄩ粯璁?base_url `http://localhost:11434/v1`锛堜笉褰卞搷 default chat provider锛?
- `_save_embedding_provider_config` 鍦ㄥ啓 `[llm.embedding]` 鏃跺鏋?`[llm.ollama] base_url` 杩樻槸绌猴紝鑷姩濉?`http://localhost:11434/v1`锛岄伩鍏嶅悗缁厤缃瑙嗘椂 `[llm.ollama]` 鍏ㄧ┖甯︽潵鐨勭枒鎯?

绾夸笂 backend 閲嶅惎鍚庡疄娴?embedding 璋冪敤绔嬪埢鍒囧埌 `localhost:11434/api/embeddings` 鉁?

---

## v0.3.2: supergroup 鍚堝苟杩佺 serve 鐑矾寰勶紙2026-04-29锛?

### 鎺ㄨ崘 serve 璺緞闆?API 璋冪敤

- `RecommendationEngine` 鏂板 `_supergroup_canonical_map`锛岀敱 `prewarm_supergroup_embeddings` 鍦ㄦ瘡娆?refresh tick 鍚庡彴濉厖锛泂erve()` `_merge_topic_supergroups` 閫€鍖栦负绾?dict lookup锛堥浂 embedding API 璋冪敤锛岄浂 pairwise 姣旇緝锛?
- prewarm 鏃堕噸鏂板惎鐢?`"label | top-5 sample titles"` 鐨勮涔夋秷姝ц矾寰勨€斺€攖itles 鐢ㄦ潵鍖哄垎 embedding 绌洪棿閲岀湅浼肩浉浼肩殑鐭腑鏂?label锛堣禌鍗氭湅鍏?鈮?鍔ㄦ极 鍦ㄨ８ label 涓嬭兘鍒?sim 鈮?0.90锛夛紝浣嗗彧鍦ㄥ悗鍙颁粯浠ｄ环
- `Database.get_topic_group_samples` 缁?prewarmer 鎻愪緵甯?sample title 鐨勬睜瀛愭憳瑕?
- 淇鏃╂湡"label-only embedding 鍙兘璇悎骞剁煭 label"鐨勮川閲忛殣鎮ｏ紝鍚屾椂涓嶅奖鍝?popup 0.6s 鍝嶅簲寤惰繜

### 宸ョ▼

- `refresh.py` 鎶?prewarm 鐨?`with suppress(Exception)` 鎹㈡垚 `try/except + logger.exception(...)`锛屽け璐ョ幇鍦ㄤ細杩涙棩蹇楄€屼笉鏄鍚炴帀
- `uv.lock` 璺熻繘 0.3.1 鈫?0.3.2 鐗堟湰鍙?

> 浠呭悗绔彂鐗堬紙backend-v0.3.2锛夈€侲xtension 鑷?v0.3.1 闆舵敼鍔紝娌跨敤 extension-v0.3.1銆?

---

## v0.3.1: 鎺ㄨ崘涓板瘜搴︽敹灏?+ 瑁呮満/CI 淇锛?026-04-29锛?

### 鎺ㄨ崘涓板瘜搴︿簩杞不鐞?

- **SQL 灞傚姞 per-topic_group cap**锛歚get_pool_candidates` 鐢?ROW_NUMBER 鎶婃瘡涓?topic_group 鍦ㄥ€欓€夌獥鍙ｉ噷鐨勯」鏁板皝椤?3锛岃 270 涓睜瀛?group 涓殑闀垮熬 group 鐪熸杩涘緱鍒板€欓€夌獥鍙ｃ€傚悓鏃?over-fetch 鐢?`limit*5` 娑ㄥ埌 `limit*8`锛岀粰涓嬫父 balance 澶氱暀 headroom
- `_balance_pool_rows` 鍙栨秷 "len(rows) 鈮?limit 鐩存帴杩斿洖 SQL 椤哄簭" 鐨?shortcut锛屾敼鎴愬缁?round-robin锛岄伩鍏?SQL 鎶婂悓 topic 椤圭洰鍫嗗埌鍊欓€夊ご閮?
- **PoolCurator 鍙岃酱 fatigue**锛氬師鏈彧鐪?`topic_key`锛堢粏绮掑害锛夛紝鍔ㄦ极鏉傝皥/琛ョ暘/瑙ｈ琚綋鎴?3 涓嫭绔?topic 鍚勮嚜涓嶈Е鍙?fatigue銆傛柊澧?`recent_topic_groups` 缁村害锛岃法 key/group 鍙?max
- **fatigue 鏇茬嚎闄″寲**锛歚count/len*3` 鈫?`(count^1.5)/len*5`锛宑ount=2 鐨勬墸鍒嗕粠 0.20 鈫?0.47锛宑ount=3 浠?0.30 鈫?0.87锛沗topic_fatigue` 鏉冮噸 0.15 鈫?0.25
- 瀹炴祴锛氳繛缁笁鎵?鎹竴鎵?鐨?distinct topic 鏁颁粠 ~12-15 鎻愬崌鍒?~18-22锛屽師 3/3 鎵归兘闇稿睆鐨?topic 鐜板湪鏈€澶?1/3 鎵?

### 瑁呮満鍣?/ CI 淇

- `install.sh` 妫€娴嬪埌鐜版湁 checkout 鏃惰嚜鍔?`git fetch + git pull --ff-only`锛堜粎褰撳伐浣滄爲骞插噣锛夈€備箣鍓嶇敤鎴烽噸璺戜竴鍙ヨ瘽瑁呮案杩滃仠鐣欏湪鏃х増
- `agent_bootstrap.start_local_backend` 鍔犵鍙ｅ啿绐佹娴嬶細鏃?OBC backend 杩樺湪璺戝氨 SIGTERM 鏇挎崲锛涢潪 OBC 杩涚▼鍗犵潃绔彛灏辨姏 RuntimeError 璁╄皟鐢ㄦ柟鎶ユ竻妤?
- `.github/workflows/release-extension.yml`锛氭妸鏃犳晥鐨?`shell: node` 鏇挎崲鎴?`bash + jq`锛宔xtension release CI 瑙ｉ攣
- 淇簡 OpenClaw proactive e2e fake 鐨?`get_delight_candidates` 缂哄け鏂规硶

### 鍏朵粬

- 寮圭獥 probe 鍙嶉鍙鎬?fix锛堝欢杩?profile 閲嶆柊鎷夊彇锛?
- speculator 宸茬‘璁?speculation 鍦?popup 闅愯棌鐩村埌姝ｅ紡 promote
- README / 浠撳簱 About 閲嶆柊瀹氫綅涓洪€氱敤 Agent锛屽姞 release history 琛?

---

## v0.3.0: 澶氭簮鏋舵瀯鍥炲綊 + 鎺ㄨ崘绋虫€侀噸鍐欙紙2026-04-28锛?

### 澶氭簮锛坢ulti-source锛?

- 閲嶆柊鍚堝叆姝ゅ墠琚洖婊氱殑 Phase 0 + Phase 1 澶氭簮鏋舵瀯锛坈ontent_id 鍏煎灞?/ SourceAdapter / SourceRecipe / BilibiliAdapter锛夛紝骞跺彔鍔?Phase 2 瀹屾暣鎶曚骇
- 鏂板 `xiaohongshu_adapter` 涓?`web_adapter`锛屾敮鎸佸皬绾功涓庨€氱敤 web 婧?
- 娴忚鍣ㄦ彃浠跺姞 `host_permissions: *://*.xiaohongshu.com/*`锛屽苟鏂板瀵瑰簲 content scripts (`xiaohongshu.js`)銆乵ain-world token sniffer (`xhs-token-sniffer.js`)銆乥ackground `xhs-task-dispatcher`
- popup 鏂囨/鍔ㄤ綔闈€佽缃〉銆佹敹钘忓す/姒傝鍧囨寜澶氭簮鎺ュ叆鏇存柊

### 鎺ㄨ崘姹犲鏍锋€?/ discovery 娓犻亾骞宠　

- trending / explore 鍦ㄨ瘎浼板墠鎸?rid / domain 鍋?round-robin 浜ら敊锛岃 30 鏉?hard-cap 鍏钩瑕嗙洊鍚勫垎鍖?
- 鏂板 `Database.trim_topic_group_overflow`锛屾瘡 refresh tick 瑙﹀彂锛屾妸浠绘剰 `topic_group` 鍦?fresh pool 鐨勫崰姣斿帇鍦?~10% 浠ュ唴锛堝疄娴嬫妸 `浜哄伐鏅鸿兘 / related_chain` 鐨?207 鏉″帇鍥?60锛?
- `_build_source_replenishment_plan` 鎶婂叏閮ㄧ己璐?source 鍚堝苟鍒颁竴娆?`discover()` 骞惰 fan-out锛屽憡鍒?姣忚疆涓€绉?source"鐨?60s 涓茶
- `trim_pool_to_target_count` 鍔?`source_share_quotas`锛屼笁娈垫《锛坧rotected / negotiable_untracked / negotiable_tracked锛変繚鎶?under-quota 婧愪笉琚?score-only 淇壀璇激
- `cache_content` UPSERT 鏃舵妸 `pool_status='suppressed'` 鑷姩澶嶆椿涓?`'fresh'`锛岃 trending 杩欑被鎱㈡洿鏂版簮鑳藉鐢?B 绔?ranking 涓嶅彉鐨勬睜瀛?
- `_SOURCE_TARGET_SHARES` trending 姣斾緥 3 鈫?1锛屽尮閰嶅疄闄呯ǔ鎬侊紙~46锛夎€屼笉鏄?120 杩欎釜姘歌繙鎽镐笉鍒扮殑鐩爣

### 鎹竴鎵癸紙reshuffle锛夋€ц兘锛?.6s 鈫?0.6s

- `_merge_topic_supergroups` 鐨?embedding 璋冪敤 sequential await 鈫?`asyncio.gather`
- embedding cache key 鐢?`label | sample_titles`锛堟瘡杞彉 鈫?0% 鍛戒腑锛夋敼涓?`label only`锛堝懡涓巼 ~100%锛?
- popup 鐨?10 鏉?recommendation insert 鐢?10 娆＄嫭绔?commit 鍚堝苟涓哄崟 transaction锛堟秷闄?fsync 涓茶闃诲锛?
- 鍦ㄦ瘡涓?refresh tick 鍚?prewarm 鎵€鏈?`topic_group` 鐨?embedding 鈥斺€?鏂?label 杩涙睜鏃剁敱鍚庡彴浠?API round-trip 鑰屼笉鏄敤鎴风偣鍑绘椂

### 鏈湴 embedding 鍏滃簳

- `OllamaProvider.embed()`锛氶€氳繃 Ollama 鍘熺敓 `/api/embeddings` 鎷垮悜閲忥紝澶辫触杩旂┖闄嶇骇
- `build_embedding_service` 鎸?provider 閫夐粯璁?model锛歚gemini 鈫?gemini-embedding-001`锛宍openai 鈫?text-embedding-3-small`锛宍ollama 鈫?bge-m3`
- 鏂?CLI 鍛戒护 `openbiliclaw setup-embedding`锛氭帰娴?`localhost:11434`銆佹祦寮忔媺 `bge-m3`銆佸啓 `[llm.embedding]` 閰嶇疆锛涘悓鏍风殑 wizard 涔熷湪 `init` 鏈熬璇㈤棶
- `install.sh` / `agent-install.md` / `README.md` / `README_EN.md` / `docs/docker-deployment.md` 鍏ㄩ儴鍔犱簡"鍙€夊惎鐢ㄦ湰鍦?Ollama embedding"鎸囧紩

### 宸ョ▼

- 娴嬭瘯锛氭柊澧?trending/explore 鐨?interleave 鍥炲綊銆乣trim_topic_group_overflow` 璺ㄦ簮 cap銆乣trim_pool` 涓夋《淇濇姢銆乣cache_content` 澶嶆椿銆丱llama embed mock + URL 澶勭悊銆乺egistry 榛樿 model 閫夋嫨銆亀izard 鎺㈡祴/鎷夊彇/鎸佷箙鍖栧叡 ~20 涓柊娴嬭瘯
- 绫诲瀷锛氭墍鏈夋敼鍔ㄩ€氳繃 `mypy strict`
- 澶氱 lint 骞插噣锛坮uff + 鎵╁睍鐨?tsc/node test锛?

---

## M8: 鎻掍欢鍚庣 API锛堣繘琛屼腑锛?

### 鍏磋叮鎺㈤拡涓板瘜搴︿慨姝ｏ細淇濈暀澶ц儐鎺㈢储锛屼絾涓嶅啀濉屾垚鍚屼竴浣撻獙杞?

- **鐥囩姸**锛氬叴瓒ｆ帰閽堢殑鏂瑰悜铏界劧鍚嶄箟涓婅法 category锛屼絾鐢ㄦ埛浣撴劅涓婄粡甯告槸涓€鏁存壒鈥滈珮姒傚康銆侀噸鍏ュ彛銆佺煡璇嗚В閲婂瀷鈥濇柟鍚戯紝涓板瘜搴︿笉澶?
- **鏍瑰洜**锛歴peculation prompt 鍙己鍒跺绉?/ 妗ユ帴璺濈鍒嗘暎锛屾病鏈夌害鏉熺敤鎴蜂綋鎰熶笂鐨?`experience_mode` / `entry_load`锛沘ctive pool 涔熺己灏戝叆姹犲墠鐨勬湰鍦板钩琛＄瓫閫夛紱probe push 鍙湅 `confirmation_count`锛屼笉浼氶伩寮€鏈€杩戝凡缁忔帹杩囩殑浣撻獙杞?
- **淇**锛?
  1. `SpeculativeInterest` 鏂板 `experience_mode` 鍜?`entry_load`
  2. speculation generation 鏀逛负杩囬噰鏍峰悗鍐嶆湰鍦?balanced selection锛屼繚璇?active pool 鑷冲皯淇濈暀杞诲叆鍙ｅ拰闈炵煡璇嗚В閲婂瀷鍊欓€?
  3. runtime push 涓?OpenClaw `get_next_probe()` 鍏辩敤 probe selector锛氶獙璇佸帇鍔涚浉鍚岀殑鍊欓€夐噷锛屼紭鍏堥€夋嫨鏈€杩戞病鎺ㄨ繃鐨勪綋楠岃酱
  4. `discovery_runtime_state` 鏂板 `probed_axes`锛屼笌鏃㈡湁 `probed_domains` 涓€璧峰仛 probe 鍘婚噸
- **娴嬭瘯**锛氭柊澧?speculator 澶氭牱鎬у洖褰掋€乺untime / OpenClaw probe 杞村幓閲嶅洖褰掞紝骞舵墿灞曚富鍔ㄦ帹閫?E2E 鏍￠獙 `experience_mode` / `entry_load`

### 鎺ㄨ崘姹犵‖涓婇檺锛歚pool_target_count` 浠庤蒋鍦版澘鍗囦负纭ぉ鑺辨澘

- **鐥囩姸**锛氱敤鎴峰弽棣?popup 鏄剧ず 896 鏉″彲鎹紝杩滆秴閰嶇疆 `pool_target_count=600`銆傛帓鏌ュ彂鐜?600 鍙綔涓?浣庝簬瀹冨氨琛ヨ揣"鐨勫湴鏉匡紙floor锛夛紝`trending` 姣?3 灏忔椂 / `explore` 姣?12 灏忔椂 / 浜嬩欢闃堝€艰Е鍙戠殑 refresh 閮戒笉鐪嬫€婚噺锛屼細瓒婄嚎寰€姹犲瓙閲屽姞鍐呭銆俙_run_refresh_plan` 鐨勪腑閫?break 鏉′欢涔熷彧鍦?璧锋浣庝簬鐩爣"鏃剁敓鏁?
- **淇**锛坰ource-of-truth 鍦?`runtime/refresh.py`锛夛細
  1. 鏂板 `ContinuousRefreshController._enforce_pool_cap()`锛氬湪 `refresh_if_needed` 鍜?`force_refresh` 鍏ュ彛妫€鏌?pool 鈮?target 鍒欑洿鎺ヨ繑鍥?`{"refreshed": False, "reason": "pool_at_cap"}`锛屼笉鍐嶈Е鍙?discover銆俻ool > target 鏃跺厛璋冪敤鏂?DB 鏂规硶 `trim_pool_to_target_count` 鎶婃孩鍑洪儴鍒嗛檷涓?`suppressed`锛涙瘡娆¤Е鍙戦兘浼氬啓 INFO 鏃ュ織 `enforce_pool_cap: trimmed=..., pool_available=..., target=...`锛屽け璐ユ崟鑾峰苟 `logger.exception`
  2. `_run_refresh_plan` 涓€?break 鏉′欢浠?`initial_pool_below_target and current_pool_count >= target` 鏀逛负 `current_pool_count >= target`锛氫换浣曠瓥鐣ュ湪鎵ц杩囩▼涓妸姹犲瓙鎾戝埌鐩爣灏辩珛鍒诲仠
  3. 鏂?DB 鏂规硶 `Database.trim_pool_to_target_count(target)`锛氭寜 `relevance_score` 闄嶅簭 鈫?`last_scored_at` 闄嶅簭 鈫?闈?`explore` 浼樺厛 鈫?`bvid` 绋冲畾搴忔帓搴忥紝淇濈暀鍓?target 鏉★紝鍏朵綑鏍?`suppressed`銆傚彧鍔ㄥ綋鍓?`pool_status='fresh'` 涓旀湭杩涘叆 recommendations 鐨勬潯鐩?
- **鏂囨。涓€鑷存€?*锛歚docs/modules/config.md` 鐨?`pool_target_count` 鎻忚堪鍘熸湰鎵胯"鍒拌揪鐩爣鍚庝笉鍐嶈Е鍙戞柊 discover"锛屼笌鏃у疄鐜颁笉绗︺€傜幇鍦ㄨ涓哄拰鏂囨。瀵归綈
- **娴嬭瘯**锛氭柊澧?4 涓祴璇曡鐩?`refresh_if_needed` / `force_refresh` 鍦?cap 鏃惰繑鍥?`pool_at_cap`銆佸叆鍙ｈЕ鍙?trim銆佺瓥鐣ヤ腑閫斿懡涓?cap 灏卞仠锛涜皟鏁?6 涓師鏈緷璧?pool_count == target"鍋囪鐨勬祴璇曪紙闄嶅埌 pool_count=20 淇濇寔鍘熸剰鍥撅級锛沗test_refresh_controller_triggers_event_refresh_when_signal_threshold_reached` 閲嶅懡鍚嶄负 `_falls_back_to_full_plan_when_below_target`鈥斺€斿師娴嬭瘯瑕嗙洊鐨?pool 鈮?target 鏃朵簨浠堕槇鍊艰Е鍙?鍒嗘敮鐜板湪鏄笉鍙揪浠ｇ爜

### 鎯婂枩鎺ㄨ崘鍓嶇Щ鍒版帹鑽愰〉棣栧睆

- popup `recommend` tab 鏂板鐙珛鐨勬儕鍠滄帹鑽愰灞忓崱浣嶏紝涓嶅啀鍙兘渚濊禆绯荤粺閫氱煡鎴栦复鏃舵秷鎭墠鑳界湅鍒?delight 鍊欓€?
- popup 鍚姩銆佸悗绔噸杩炲拰 `init_completed` 鍚庝細涓诲姩璇诲彇 `/api/delight/pending`锛宺untime stream 鏀跺埌鏂扮殑 `delight.candidate` 涔熶細鍗虫椂鍒锋柊棣栧睆鍗?
- 鎯婂枩鎺ㄨ崘閫氱煡鐐瑰嚮鍚庝細鎵撳紑甯?`?tab=recommend&delight=<bvid>` 鐨勬彃浠堕〉闈紝鐩存帴钀藉埌瀵瑰簲鍊欓€夛紝鑰屼笉鏄彧鍥炲埌閫氱敤鎺ㄨ崘椤?
- 棣栧睆鎯婂枩鍗℃敮鎸?`鐪嬬湅 / 涓嶆劅鍏磋叮 / 鑱婁竴鑱?/ 绋嶅悗鐪媊 鍥涗釜鍔ㄤ綔锛屽苟浼氭妸鈥滃凡鎵撳紑 / 宸茶亰杩?/ 鍏堝皯鏉ョ偣鈥濅繚鐣欐垚鏈湴绋冲畾鎬侊紝鑰屼笉鏄珛鍒绘秷澶?

### 鎯婂枩鎺ㄨ崘杩愯鏃朵慨澶?

- delight 杩愯鏃跺拰鍚庡彴鎵撳垎涓嶅啀鍚勭敤涓€濂楅棬妲涳細鍏变韩闃堝€肩粺涓€鍒伴粯璁?`0.70`锛屾帰绱㈠紑鏀惧害浣庢椂鑷姩鎻愰珮鍒?`0.80`锛岄伩鍏嶇湡瀹炴暟鎹噷鍒嗘暟宸茬粡澶熼珮鍗存案杩滆繃涓嶄簡 `pending` 鏌ヨ
- `precompute_delight_scores()` 鐜板湪浼氬洖濉€滃凡鏈夐珮鍒嗕絾缂?`delight_reason / delight_hook`鈥濈殑 backlog锛屼笉鍐嶅彧澶勭悊 `delight_score = 0` 鐨勬柊鍊欓€?
- 鍚庡彴鍚姩鏃朵細棰濆璺戜竴娆?delight 棰勭儹锛屽嵆浣垮綋鍓嶆病鏈夋櫘閫氭帹鑽愭枃妗堣琛ワ紝涔熶細鎶婂彲鎺ㄩ€佺殑鎯婂枩鍊欓€夊噯澶囧ソ
- `pending delight` 鍙細鏆撮湶鏂囨宸插氨缁殑鍊欓€夛紱`suppressed` 鐨勯珮鍒嗗簱瀛樹篃鍏佽浣滀负鎯婂枩鎺ㄨ崘鍏ュ彛锛岄伩鍏嶈鏅€氭睜闄愭祦鍚庣洿鎺ヤ粠鎯婂枩閫氶亾閲屾秷澶?

### 婧愭棤鍏冲唴瀹瑰垎绫伙細XHS 鍐呭鍏ュ簱鍚庤嚜鍔?LLM 鍒嗙被

- **鐥囩姸**锛歑HS 鍐呭閫氳繃 `_cache_xhs_notes` 鐩存帴鍏ュ簱 `content_cache`锛岀粫杩囦簡 bilibili 鍐呭蹇呯粡鐨?LLM 璇勪及绠＄嚎锛屽鑷?`style_key` / `topic_group` / `relevance_score` 鍏ㄤ负绌恒€傛帹鑽愬鏍锋€ф満鍒跺穿婧冣€斺€旀墍鏈?XHS 鏉＄洰鍏变韩 `"unknown"` style 鍜屽崟涓€ `"xhs-extension-task"` topic token锛屼竴杞?10 鏉℃帹鑽愬畬鍏ㄨ XHS 鍗犳弧
- **淇**锛堟帹鑽愭ā鍧椾负婧愭棤鍏崇粺涓€鍏ュ彛锛夛細
  1. `recommendation/engine.py::classify_pool_backlog()`锛氭娴?pool 涓?`style_key` 鍜?`topic_group` 閮戒负绌虹殑鏉＄洰锛岃皟鐢ㄤ笌 bilibili 鍚屾鐨?LLM batch 璇勪及 prompt 鎵撲笂鍒嗙被鏍囩锛岀粨鏋滃洖鍐?DB銆傚垎绫诲悗鎵€鏈夊唴瀹瑰彧鏈夊唴瀹圭壒寰侊紙style / topic / score锛夛紝娌℃湁鏉ユ簮鏍囩
  2. `api/app.py::ingest_xhs_observed_urls`锛氬叆搴撳悗 `asyncio.create_task(_classify_new_pool_items())` 瑙﹀彂鍚庡彴鍒嗙被
  3. `asyncio.Lock` 闃叉骞跺彂閲嶅 LLM 璋冪敤锛涘け璐ユ爣 0.01 鍒嗛槻鏃犻檺閲嶈瘯
  4. `topic_key` 鑷姩浠?`topic_group` 鍥炲～锛岀‘淇?`_diversity_tokens` 鏈夊彲鐢?token
- **DB 淇濇姢**锛歚cache_content()` upsert 鐨?`topic_key` / `topic_group` / `style_key` / `relevance_score` / `relevance_reason` 鏀圭敤 `COALESCE(NULLIF(excluded.xxx, ''), existing, '')` 淇濇姢鈥斺€攅xtension 閲嶅彂鍚屼竴绗旇涓嶄細瑕嗙洊宸插垎绫诲瓧娈?
- **`author_name` 瀛楁淇**锛氬姞鍏?INSERT 瀛愬彞 + schema 杩佺Щ锛屼箣鍓嶈繖涓瓧娈靛啓浜嗙瓑浜庢病鍐?
- **`_diversity_tokens` 淇**锛氱Щ闄?`source_strategy` 浣滀负 topic fallback锛堟牴鍥狅級锛屾敼鐢ㄤ綔鑰呭悕 + 鏍囬涓枃/鑻辨枃鍏抽敭璇?
- **鍏变韩瀹氫箟**锛氭彁鍙?`VALID_STYLE_KEYS` 鍒?`discovery/engine.py` 妯″潡绾э紝`DiscoveredContent.to_cache_kwargs()` 浣滀负鍞竴鐨勫瓧娈垫槧灏勬簮锛屾秷闄?3 澶?`_VALID_STYLES` + 2 澶?20-kwarg `cache_content` 灞曞紑鐨勯噸澶?
- **绌烘爣棰樿繃婊?*锛歟xtension 绔?`extractNoteMetadataFromAnchor` 绌烘爣棰樿繑鍥?null锛涘悗绔?`_cache_xhs_notes` 璺宠繃绌烘爣棰樼瑪璁般€侱B 鍘嗗彶 46 鏉＄┖鏍囬琛屾爣涓?suppressed
- **娴嬭瘯**锛氭柊澧?12 涓祴璇曪紙5 涓?unit + 7 涓?E2E multi-source diversity suite锛夆€斺€旇鐩栧垎绫绘祦绋嬨€侀噸澶嶅叆搴撲繚鎶ゃ€佹贩鎺掑鏍锋€с€佸苟鍙戦攣銆佸け璐ラ噸璇曘€佺┖鏍囬杩囨护

### 鍏磋叮鎺㈤拡鐢ㄦ埛纭浜や簰

- **浜у搧褰㈡€?*锛歐ebSocket 鎺ㄩ€?`interest.probe` 浜嬩欢 鈫?Chrome 绯荤粺閫氱煡"闃緽 鎯崇‘璁わ細浣犲銆孹X銆嶆劅鍏磋叮鍚楋紵" 鈫?鐐瑰嚮鎵撳紑 popup Profile tab 鈫?鍗＄墖鏄剧ず鐚滄祴鏂瑰悜 + 鍏蜂綋瀛愭柟鍚?chips 鈫?涓夋寜閽氦浜掞細銆屾槸銆嶃€屼笉鏄€嶃€屽鑱婅亰銆?
- **鍚庣**锛?
  - `speculator.py::user_confirm_speculation(domain)`锛氱洿鎺?promote 鍒版寮忓叴瓒?
  - `speculator.py::user_reject_speculation(domain)`锛?0 澶╁喎鍗存湡
  - `api/app.py::POST /api/interest-probes/respond`锛氭帴鏀?confirm / reject / chat锛宑hat 杞彂鍒?dialogue 寮曟搸
- **鍘婚噸鍐峰嵈**锛歚_PROBE_COOLDOWN_HOURS = 4`锛屽悓涓€ domain 4 灏忔椂鍐呭彧鎺ㄤ竴娆★紝璁板綍鍦?`discovery_runtime_state["probed_domains"]`
- **鎺ㄩ€佹椂鏈轰慨澶?*锛歚_publish_delight_if_available` 鍜?`_publish_interest_probe_if_available` 浠?`_run_refresh_plan` 鍐呴儴绉诲埌 `run_forever` 涓诲惊鐜€斺€斾箣鍓?pool 婊℃椂涓嶈Е鍙?refresh plan锛屾帹閫佹案杩滃埌涓嶄簡瀹㈡埛绔?
- **鎻掍欢鍓嶇**锛歚popup.js::renderProbeCard()` + `handleProbeResponse()` + CSS 鍔ㄧ敾锛泂ervice-worker 澶勭悊 `interest.probe` 浜嬩欢鍒涘缓 Chrome 閫氱煡
- **CLI**锛歚openbiliclaw delight`锛堟墜鍔ㄦ煡鐪嬫儕鍠滄帹鑽愬€欓€夛級+ `openbiliclaw probe`锛堟墜鍔ㄥ垪鍑虹寽娴嬫柟鍚戙€佸簭鍙风‘璁?鎷掔粷锛?

### 鏋舵瀯鍥炬洿鏂?

- **discovery-architecture.html**锛氭柊澧?XHS 鍏ュ簱 + `classify_pool_backlog` 骞惰閫氶亾锛沗pool_target_count` 300鈫?00锛況efresh loop 鍔?`_tick_xhs_producer`
- **recommendation-architecture.html**锛歴erve() 绠￠亾鍔?`classify_pool_backlog` 瀹夊叏缃戞楠わ紱diversity 鎻忚堪鏇存柊涓烘簮鏃犲叧锛涜В鑰︽灦鏋勫浘鍔?XHS Extension 浣滀负绗簩鏁版嵁婧愮粡"婧愭棤鍏抽棬"鍏ユ睜锛涙ā鍧楄竟鐣屽姞 `VALID_STYLE_KEYS` 鍏变韩甯搁噺

### 淇鍔犲叆 xhs 鍚庢帹鑽愬垪琛ㄥ嚭鐜?xhs 鐙崰杞锛屼赴瀵屽害濉岄櫡

- **鐥囩姸**锛氬紩鍏ュ皬绾功鍐呭鍚庯紝涓€杞帹鑽愬伓灏斿叏鏄?xhs 绗旇鈥斺€擿picked summary` 鍑虹幇 `{"count":10,"styles":{"unknown":10},"sources":{"xhs-extension-task":10}}`锛岄鏍?/ 涓婚 / 骞冲彴閮藉崟涓€锛岀敤鎴锋瘡娆′笅鎷夐兘鐪嬪埌鍚屼竴绫荤煭瑙嗛
- **鏍瑰洜**锛歚_select_diversified_batch` 鐨?style cap 渚濊禆 `_style_token` 杩斿洖鐨勬《鍚嶏紝浣?xhs 绗旇鏅亶 `style_key=""`鈥斺€旂┖瀛楃涓茶褰撴垚"鏃?style"鐩存帴璺宠繃 style cap 妫€鏌ャ€傚涓?xhs 绗旇鍦ㄤ富寰幆鍜屽墠鍑犳。 try_fill 閲岄兘鑳戒互"绌?style"韬唤鍫嗗埌鍚屼竴鎵规锛涗竴鏃﹀墠闈?cascade 娌￠€夊锛屾渶鍚庝竴妗ｆ棤鏉′欢鍏滃簳鎶婃墍鏈夊墿浣欓」鍏ㄥ杩涙潵锛屽氨鍑戝嚭 10/10 xhs 鐙鍦?
- **璁捐鍘熷垯**锛氱敤鎴锋槑纭姹?浠讳綍鏉ユ簮骞崇瓑瑙嗕负鍐呭"鈥斺€斾笉璧板钩鍙伴粦鐧藉悕鍗曪紝鍙粠鍐呭缁村害锛坱opic / style锛変繚璇佷赴瀵屽害銆傚钩鍙版槸浜у湴鏍囩锛屼笉鏄瑙嗕緷鎹?
- **淇**锛坄recommendation/engine.py::_select_diversified_batch`锛夛細
  1. `_style_token` 鎶婄┖ `style_key` 鏄犲皠鎴?sentinel `"unknown"`鈥斺€旀湭鍒嗙被鍐呭鍙備笌 per-style cap锛屽拰鏈?style 鍒嗙被鐨勬潯鐩蛋鍚屼竴濂楅厤棰濋€昏緫锛屼笉鍐嶄韩鍙楃┖瀛楃涓?鍏嶆"
  2. 鏈€缁堝厹搴曟妸鍘熸湰鐨勬棤鏉′欢纭鎹㈡垚"broad-topic 鏉惧彛寰?锛歚fallback_broad_cap = 2 脳 broad_cap`銆倀opic 鎵嶆槸鍐呭涓板瘜搴︾殑鐪熶俊鍙封€斺€斿悓涓€涓?broad topic 鐨勬潯鐩嵆浣垮钩鍙?/ style 涓嶅悓涔熶細璁╃敤鎴锋劅鍒伴噸澶嶃€傛病鏈?topic 鐨勬潯鐩厑璁搁€氳繃锛岄伩鍏嶅€欓€夋睜钖勬椂杩斿洖绌烘壒娆?
  3. 瀹佸彲杩斿洖灏忔壒娆★紙姣斿 6 鏉?topic-diverse锛変篃涓嶅噾婊?10 鏉″崟涓€ topic
  4. `_build_debug_summary` 鍔?`platforms` 瀛楁锛屾棩蹇楅噷鑳界洿鎺ョ湅 bilibili / xhs 姣斾緥鈥斺€斾粎鍋氳娴嬶紝涓嶅弬涓庣瓫閫?
- **娴嬭瘯**锛?
  - `tests/test_recommendation_engine.py::test_monoculture_pool_capped_by_broad_topic_not_platform`鈥斺€旂函 xhs 鍚?topic 姹?13 鏉?鈫?鍏滃簳 broad-topic 澶╄姳鏉?6 鏉?
  - `test_content_diversity_treats_platforms_equally`鈥斺€攛hs + bili 娣锋睜鍚勮嚜 topic-rich 鈫?涓よ竟閮芥湁浠ｈ〃锛屼笉鍐嶄汉涓洪檺閲?
  - `test_pure_bilibili_rich_pool_fills_batch`鈥斺€旂函 bilibili 瀵屾睜浠嶅～婊?limit
  - `test_reshuffle_recommendations_backfills_to_requested_limit_when_style_is_dominant`鈥斺€斿悓 style 浣嗕笉鍚?topic 鈫?backfill 鍒?limit
  - 鍏ㄩ噺 28 passed锛坮ecommendation_engine.py锛?

### MAIN-world sniffer锛氫粠 xhs 鑷繁鐨?API 鍝嶅簲閲屾崬 `xsec_token`

- **鍔ㄦ満**锛氫笂涓€杞?token 鍥炲～淇簡"宸茬粡瑙佽繃 token 鐨?note 鑳藉榻?锛屼絾鎼滅储椤典粠澶村埌灏鹃兘涓嶈蛋鎺㈢储娴佺殑 note锛屽巻鍙蹭笂 `xhs_observed_urls` 鏍规湰娌″瓨杩囧畠鐨?token銆傜敤鎴风偣鍒扮殑 `69c7a7b000000000220030c9` 灏卞睘浜庤繖绫烩€斺€斾换浣曢€斿緞閮芥病鎹炲埌杩?token锛岀偣鍑荤洿鎺ユ挒 xhs 300031 鐧诲綍澧?
- **鎬濊矾**锛歺hs 鐨?Web 绔嚜宸变細鎷?token 鍙?`/api/sns/web/*` 璇锋眰锛宼oken 灏辫汉鍦?response JSON 閲屻€傚姭鎸?`window.fetch` / `XMLHttpRequest`锛屾壂 response body 閲屾墍鏈?`(note_id, xsec_token)` 瀵瑰瓙锛屽洖浼犵粰鍚庣 backfill
- **闅剧偣**锛歝ontent script 璺戝湪 isolated world锛宍window.fetch` 涓嶆槸椤甸潰鐨?fetch锛屽姭鎸佹病鐢ㄣ€傚繀椤荤敤 MV3 鐨?`world: "MAIN"` 澹版槑锛岃鑴氭湰鍜岄〉闈㈠叡浜悓涓€涓?realm
- **瀹炵幇**锛?
  1. `extension/src/main/xhs-token-sniffer.ts`锛堟柊鏂囦欢锛夛細MAIN-world 鑴氭湰锛寃rap `window.fetch` 鍜?`XMLHttpRequest.prototype.{open,send}`銆俙extractTokenPairs` 瀵逛换鎰?JSON 鍋氭繁搴︿紭鍏堟壂鎻忥紝璁?24-hex `note_id`/`noteId`/`id` + 闈炵┖ `xsec_token`/`xsecToken`銆傝 body 鍓嶅厛 `response.clone()`锛屼笉鍔ㄥ師濮嬫祦銆傚畨瑁呬唬鐮佺敤 `typeof window !== "undefined"` 瀹堟姢锛宯ode 娴嬭瘯鍙互鍙鍑?`extractTokenPairs` 鐢?
  2. `extension/manifest.json`锛氬姞绗簩鏉?`content_scripts` 缁?xhs鈥斺€擿world: "MAIN"`銆乣run_at: "document_start"`锛屾姠鍦?xhs 鑷繁娉ㄥ叆 fetch 涔嬪墠鎸傞挬
  3. `extension/src/content/xiaohongshu.ts`锛歩solated world 閲屽姞 `window.addEventListener("message")` bridge锛屾敹 `source: "obc-xhs-sniffer"` 鐨?postMessage 鍚庣紦鍐?1.5s 鍘婚噸锛屽啀 `chrome.runtime.sendMessage` 鍒?service worker
  4. `extension/src/background/service-worker.ts`锛歚XHS_TOKENS_OBSERVED` 娑堟伅 POST 鍒?`/api/sources/xhs/tokens`
  5. `api/app.py::ingest_xhs_tokens`锛氱敤 sniffed pairs 鍚堟垚 `https://www.xiaohongshu.com/explore/<id>?xsec_token=<tok>` 璧板凡鏈夌殑 `_backfill_xhs_tokens` UPDATE 璺緞鈥斺€斿拰鎺㈢储娴佺殑鍥炲～鍚堜竴锛屼笉璧版柊鍒嗘敮
- **闅愮杈圭晫**锛歴niffer 涓嶆敼璇锋眰銆佷笉鍋氭寚绾归噰闆嗐€佷笉澶栦紶浠讳綍闈?`(note_id, xsec_token)` 瀛楁銆傝繖涓や釜鍊煎浠讳綍鐧诲綍鎬?xhs session 鑰岃█閮芥槸鍏紑鍙鐨?
- **鏁堟灉**锛氱敤鎴锋瘡閫涗竴娆?xhs 浠绘剰椤甸潰锛堥椤?/ 鎼滅储 / 涓汉椤碉級锛屽悗鍙板氨浠?xhs 鐨?API 鍝嶅簲閲岃嚜鍔ㄦ妸鍙 note 鐨?token 鏀堕泦榻愩€備箣鍓嶅瓨鎴愯８ URL 鐨勫巻鍙叉暟鎹細閫愭琚崌绾ф垚甯?token 鐗堬紝鎺ㄨ崘鍗＄偣鍑诲懡涓?xhs 鐧诲綍澧欑殑姒傜巼闅忎箣涓嬮檷
- **娴嬭瘯**锛?
  - `extension/tests/xhs-token-sniffer.test.ts`锛?0 渚嬭鐩?`extractTokenPairs`鈥斺€攆lat/nested/arrays/dedupe/camelCase/reject 闈?24-hex/reject 绌?token/null 鍏ュ弬
  - `tests/test_api_xhs_ingest.py::TestXhsTokens`锛歚/api/sources/xhs/tokens` 绔偣鈥斺€攖oken 鑳?backfill 鍒板凡鍏ュ簱鐨?bare cache / 绌?pairs noop / malformed pair 琚涪
- **鎵嬪伐楠岃瘉**锛氶噸鏂?build extension + reload chrome extension 鍚庯紝闅忎究鎵撳紑涓€鏉?xhs note锛屽悗鍙版棩蹇楅噷鑳界湅鍒?`tokens upgraded=N` 鍑虹幇

### 淇 xhs 绗旇鍒嗕韩 URL 涓㈠け `xsec_token` 瀵艰嚧鐧诲綍澧欐嫤鎴?

- **鐥囩姸**锛氱紦瀛樼殑 xhs `content_url` 缁濆ぇ澶氭暟鏄８ `https://www.xiaohongshu.com/explore/<id>`锛屼笉甯?`xsec_token=...`銆侱B 鎶芥牱 260 鏉¤娴?URL 閲屽彧鏈?15 鏉★紙鍏ㄩ儴鏉ヨ嚜 `explore` 棣栭〉锛夊甫 token锛宍search` 椤碉紙133 鏉★級/ task 椤碉紙92 鏉★級鍏ㄦ槸瑁哥殑銆傚閾惧垎浜?/ 閫€鍑虹櫥褰曞悗鎵撳紑閮戒細琚?xhs 鎷﹀埌鐧诲綍澧?
- **鏍瑰洜**锛歺hs 鎼滅储缁撴灉椤电殑 React 缁勪欢鎶?`xsec_token` 鐣欏湪缁勪欢 props 閲岋紝涓嶅啓鍏?`<a href>`锛涘唴瀹硅剼鏈?`passive.ts::extractXhsNoteUrl` 鍙兘浠?href 鎹?token鈥斺€旀悳绱㈤〉澶╃劧鎹炰笉鍒般€傜瑪璁拌鎯呴〉鐨勬潈濞?token 鍏跺疄鍦?`window.location.search` 閲岋紝浣嗗師鍏堟牴鏈病琚鍙?
- **淇**锛氫笁澶勮仈鍔?
  1. `api/app.py::_pick_best_xhs_url`锛歚_cache_xhs_notes` 鍐?`content_url` 鍓嶅厛姣旇緝鈥斺€攊ncoming 鏈?token 灏辩洿鎺ョ敤锛涘惁鍒欏洖鏌?`xhs_observed_urls`锛堝巻鍙插甫 token 鐨勮娴嬶級鍜岀幇鏈?`content_cache` 琛岋紝閫変竴涓甫 token 鐨勫洖鏉ャ€傝繖鏍?xhs 鍏堥€?explore锛坱oken 鍒版墜锛夊啀鎼滃悓涓€鏉＄殑鍦烘櫙鑳芥妸 token 瀵归綈杩囧幓
  2. `api/app.py::_backfill_xhs_tokens`锛歚/api/sources/xhs/observed-urls` 鍜?`/api/sources/xhs/task-result` 鏀跺埌甯?token 鐨?URL 鏃讹紝涓€娆?UPDATE 鎶?`content_cache` 閲屽悓 note_id 鐨勮８ URL 鏀瑰啓鎴愬甫 token 鐗堚€斺€斾慨宸插瓨鍏ュ簱鐨勫巻鍙茶８ URL
  3. `extension/src/content/xiaohongshu.ts::selfNoteAnchor`锛氱敤鎴风洿鎺ュ潗鍦ㄧ瑪璁拌鎯呴〉鏃讹紝鍚堟垚涓€涓?鑷寚 anchor"濉炶繘 collector锛屾妸 `window.location.href` 閲岀殑鏉冨▉ token 涓婃姤缁欏悗绔€傛悳绱㈤〉缂虹殑 token 鍦ㄧ敤鎴风偣杩涗换鎰忎竴鏉＄瑪璁版椂绔嬪埢琛ュ叏
- **娴嬭瘯**锛?
  - `tests/test_api_xhs_ingest.py::test_tokenized_url_upgrades_existing_bare_cache_row`鈥斺€旇８ URL 鍏堝叆搴撱€佸甫 token 鐨勫悓 note_id 鍚庤娴嬶紝鏈€缁?DB 蹇呴』鏄甫 token 鐗?
  - `tests/test_api_xhs_ingest.py::test_cache_prefers_tokenized_url_from_prior_observation`鈥斺€斿厛瑙傛祴甯?token锛屽啀鏉ヨ８ URL + `notes` payload锛屼笉鍑嗗洖鍐欐垚瑁?
  - 鍏ㄩ噺 807 passed + 15 skipped

### 淇鎺ㄨ崘鍒楄〃閲?xhs 绗旇琚綋鎴?bilibili 瑙嗛鎵撳紑锛圲RL 閿欐寚锛?

- **鐥囩姸**锛歱opup 鎵撳紑 xhs 鎺ㄨ崘鍗＄墖鏃惰烦鍒?`https://www.bilibili.com/video/<24浣?xhs 绗旇 ID>`鈥斺€攂ilibili 涓婃牴鏈病杩欐潯瑙嗛锛岀偣寮€ 404銆倄hs 鍜?bilibili 鍐呭鐪嬩技"娣蜂簡"
- **鏍瑰洜**锛歚storage/database.py::get_recommendations` 鐨?SQL 鍙粠 `content_cache` 鎷?`title/up_name/cover_url`锛?*娌℃媺 `content_id`/`content_url`/`source_platform`**銆備笅娓?`/api/recommendations` 璇诲埌 `source_platform=""` 灏辨寜榛樿鍏滃簳鎴?`"bilibili"`锛岃鍒?`content_url=""` 鍚?popup 鐨?`buildContentUrl(item)` 鍙堣蛋 `bilibili.com/video/${bvid}` 鍏滃簳鈥斺€攛hs 绗旇 ID 琚‖濉炶繘 bilibili 鍛藉悕绌洪棿
- **淇**锛歚get_recommendations` SQL 琛ヤ笂 `c.content_id`銆乣c.content_url`銆乣c.source_platform`锛坄LEFT JOIN content_cache`锛寈hs / bilibili 閫氬悆锛夈€備箣鍓嶅嚑杞慨 `_cache_xhs_notes` / `_cache_results` 鍐欏叆璺緞鏃跺拷鐣ヤ簡"璇诲洖鎺ㄨ崘"杩欐潯閾捐矾
- **娴嬭瘯**锛歚tests/test_storage.py::test_get_recommendations_joins_multi_source_fields` 瀹堣繖涓夊瓧娈靛湪 join 涔嬪悗杩樿兘璇诲洖锛涘叏閲?51 passed锛坰torage + xhs ingest锛?

### 淇 xhs 绗旇鍏ュ簱鏃?`source` 涓虹┖銆乺escore 鍚?`source_platform` 琚鐩栨垚 `bilibili`

- **涓や釜鐩镐簰鏀惧ぇ鐨?bug**锛?
  1. `api/app.py::_cache_xhs_notes` 浼犵殑鏄?`source_strategy=f"xhs-extension-{page_type}"`锛屼絾 `Database.cache_content` 璇荤殑鏄?`source` kwarg锛岄敊鎷肩殑 key 琚?`kwargs.get("source", "")` 榛橀粯涓㈠純鈥斺€攛hs 鎵€鏈夊叆搴撶瑪璁?`source` 鍒楁案杩滄槸 `""`
  2. `discovery/engine.py::_cache_results` 鍙€忎紶 `source`锛?*娌￠€忎紶 `source_platform`/`content_id`/`content_url`/`author_name`**銆俢ache_content 鐨?upsert 鍒嗘敮 `source_platform = excluded.source_platform` 浼氭妸 xhs 琛岀殑 `source_platform` 鍥炲啓鎴愰粯璁ゅ€?`"bilibili"`锛屾瘡娆?rescore 杩囦竴閬?pool 灏辫瑕嗙洊涓€娆?
- **杩為攣鐜拌薄**锛欴B 閲屽嚭鐜?35 琛?`source_platform='bilibili'` 浣?`bvid` 鏄?24 瀛楃 xhs 绗旇 ID锛堝 `68580835000000002203315d`锛夈€乼itle 鍐欑潃"楦＄叢澶嶅埢 / 鏉€鎴皷濉旇繘闃?鐨?鍋?bilibili 琛?
- **淇**锛?
  - `api/app.py:972` 鎶?`source_strategy=` 鏀规垚 `source=`锛屽悓鏃舵敞閲婅鏄庨敊鎷?key 浼氳闈欓粯涓㈠純鐨勫潙
  - `discovery/engine.py::_cache_results` 棰濆閫忎紶 `source_platform`/`content_id`/`content_url`/`author_name`
  - 涓ゆ潯璇诲洖璺緞 `_backfill_candidates` 鍜?`recommendation/engine.py::_rows_to_discovered` 涔熻ˉ涓婁粠 DB 琛岃 `source_platform`/`content_id`/`content_url` 鐨勯€昏緫锛堜箣鍓嶈鍥炴椂涔熶涪瀛楁锛屽鑷村啀鍏ュ簱鏃跺張鏄粯璁ゅ€硷級
- **鍘嗗彶鏁版嵁淇**锛氫竴娆℃€?SQL 淇?169 琛屸€斺€旀妸 `source_platform='bilibili'` 涓?`bvid NOT LIKE 'BV%'` 鐨?35 琛屾敼鍥?`xiaohongshu`銆佽ˉ榻?`content_id`/`content_url`锛涙妸鎵€鏈?`source=''` 鐨?xhs 琛屾爣涓?`xhs-extension-task`
- **娴嬭瘯**锛?
  - `tests/test_api_xhs_ingest.py::test_notes_cache_populates_source_and_platform` 瀹?cache_content 姝ｇ‘ kwarg
  - `tests/test_discovery_engine.py::test_discovery_engine_cache_results_preserves_multi_source_fields` 瀹?rescore 涓嶄細鎶?xhs 琛屾墦鍥?bilibili
  - 鍏ㄩ噺 804 passed锛堜箣鍓?802 + 鏈 2锛?

### 淇 xhs 浠诲姟 100% 瓒呮椂锛堜涪澶?EXECUTE 鎻℃墜锛?

- **鐥囩姸**锛欳LI `discover --source xiaohongshu` 鍏ラ槦鍚庯紝鎵€鏈?`xhs_tasks` 閮藉湪 30s 鍚庤鍐欐垚 `status=failed`銆乣error=timeout`锛屽€欓€夋睜娌″鍔犱竴鏉″皬绾功绗旇
- **鏍瑰洜**锛歚extension/src/background/xhs-task-dispatcher.ts` 閲?`executeTask()` 鍙?`chrome.tabs.create` 寮€浜嗗悗鍙版爣绛撅紝浠庢湭缁欏唴瀹硅剼鏈彂 `XHS_TASK_EXECUTE`銆傚唴瀹硅剼鏈?`task-executor.ts` 鐨?`chrome.runtime.onMessage` 鐩戝惉鍣ㄦ案杩滅瓑涓嶅埌瑙﹀彂锛?0s 纭秴鏃跺繀鐒跺懡涓?
- **淇**锛歚tabs.create` 涔嬪悗娉ㄥ唽涓€娆?`chrome.tabs.onUpdated` 鐩戝惉锛岄〉闈?`status === 'complete'` 鍛戒腑鏃?`chrome.tabs.sendMessage(tabId, {action: "XHS_TASK_EXECUTE", data: {task_id, type}})` 鍐嶇珛鍗?`removeListener`锛堥伩鍏?SPA 鍐呭啀璺宠浆閲嶅鍙戯級锛沗sendMessage` 琚嫆锛堝唴瀹硅剼鏈己甯級鏃朵笂鎶?`error="sendMessage_failed"` 鑰岄潪闈欓粯瓒呮椂锛沗cleanupTask()` 涔熸竻鎺夋畫鐣欑洃鍚櫒
- **娴嬭瘯**锛歚extension/tests/xhs-task-dispatcher.test.ts` 鏂板涓ゆ潯 e2e锛堝畬鏁存彙鎵?+ `sendMessage` 澶辫触璺緞锛夛紝鎵嬫悡 `chrome.tabs` / `fetch` mock锛屼笉渚濊禆 jsdom銆? 鏉?dispatcher 娴嬭瘯鍏ㄧ豢

### 鍊欓€夋睜涓婇檺鎻愬埌 600

- `scheduler.pool_target_count` 榛樿鍊间粠 `300` 鎻愬埌 `600`锛屽厑璁歌寖鍥村悓姝ユ敼涓?`1..600`
- 杩愯鏃惰涓轰繚鎸佷笉鍙橈細鍊欓€夋睜杈惧埌鐩爣鍚庡仠姝?discover锛屾帀鍥炵洰鏍囦互涓嬪啀瑙﹀彂琛ヨ揣锛岄伩鍏嶆棤璋撶殑杩滅璋冪敤
- 鍚屾鏇存柊锛歚SchedulerConfig` / `RuntimeRefreshController` / API models / popup 璁剧疆闈㈡澘锛坄min/max/placeholder`锛? 鏂囨。 / 鐩稿叧娴嬭瘯

### 淇鎺ㄨ崘鍗＄墖灏侀潰鎸ゅ帇

- 渚ц竟鏍忓灞忎笅 `116px + 1fr` 鐨勪袱鍒?grid 鍙犲姞 `aspect-ratio: 16/10` 浼氳灏侀潰琚媺浼搞€佹枃瀛楄鎸ゆ垚涓€鏉°€傛敼鍥?flex 绾靛悜甯冨眬锛堝皝闈㈠叏瀹藉湪涓娿€佹枃瀛楀湪涓嬶級锛屽拰鏃╂湡鐗堟湰浣撻獙涓€鑷?
- 鍚屾椂鎶?520px 濯掍綋鏌ヨ閲岀殑 `grid-template-columns` 瑕嗗啓娓呮帀

### 鏃ュ織鎸夊ぇ灏忚嚜鍔ㄨ疆杞?

- **閬垮厤澶辨帶鐨?7GB 鏃ュ織鏂囦欢**锛氱敓浜т腑 DEBUG 绾у埆鍐欑殑 httpcore/httpx tracelog 浼氭妸 `logs/openbiliclaw.log` 鎾戝埌鍑犱釜 G銆傚垏鎹㈠埌 `logging.handlers.RotatingFileHandler`锛氬崟鏂囦欢鍒拌揪 `max_file_size_mb` 绔嬪埢杞浆鎴?`<filename>.1`锛岃秴鍑?`backup_count` 鐨勮€佷唤鐩存帴涓㈠純
- **鍚姩鏃舵竻鐞嗗巻鍙插ぇ鏃ュ織**锛氬厜鎹?handler 涓嶅鈥斺€擿RotatingFileHandler` 涓嶄細鍥炲ご澶勭悊宸茬粡瓒呮爣鐨勬棫鏂囦欢銆俙_enforce_size_budget_once` 鍦?`configure_logging` 寮€澶存鏌ヤ竴娆★細瓒呰繃 `max_file_size_mb` 鐨勫巻鍙叉枃浠朵細琚噸鍛藉悕鎴?`<filename>.1`锛堣鐩栨棫 `.1`锛夊啀璁?handler 浠庣┖鏂囦欢鍐欒捣锛岃繖姝ｅ搴旂敤鎴疯鐨?瓒呰繃 1G 灏辨竻鐞?
- **閰嶇疆**锛歚[logging]` 鏂板涓ゅ瓧娈?`max_file_size_mb`锛堥粯璁?1024锛夊拰 `backup_count`锛堥粯璁?1锛夈€俙max_file_size_mb=0` 閫€鍥炲師鏉ョ殑 `FileHandler`锛堜笉杞浆锛夛紱`backup_count<1` 鏃跺悓鏍峰洖閫€锛屽洜涓?stdlib 鐨?RotatingFileHandler 鍦?`backupCount=0` 鏃舵牴鏈笉浼氳疆杞?
- **纾佺洏鍗犵敤涓婇檺**锛氶粯璁ら厤缃笅 `openbiliclaw.log` + `openbiliclaw.log.1` 鍚堣涓嶈秴杩?~2GB
- **娴嬭瘯**锛歚tests/test_logging_setup.py` 鏂板 4 涓紙鍚敤杞浆 / size=0 绂佺敤 / 鍚姩鏃惰疆杞秴鏍囨枃浠?/ 灏忔枃浠朵笉鍔級锛宍tests/test_config.py` 鏂板 2 涓紙榛樿鍊笺€乀OML 瑙ｆ瀽锛夈€傚叏閲?802 passed

### CLI `discover` 鏀寔鎸夋潵婧?/ 绛栫暐瑙﹀彂

- `openbiliclaw discover` 澧炲姞 `--source {bilibili|xiaohongshu}` / `--strategy search,trending,鈥 / `--limit` / `--force` 鍥涗釜閫夐」锛屽厑璁稿崟鐙Е鍙戞煇涓笭閬撴垨 Bilibili 鍗曟潯绛栫暐
- `--source xiaohongshu` 璺緞澶嶇敤 `XhsTaskProducer.produce_if_due()`锛宍--force` 鏃?`min_interval_hours=0` 缁曡繃 4 灏忔椂鑺傛祦锛涚粨鏋滅洿鎺ュ啓鍏?`xhs_tasks` 琛ㄤ氦鐢辨墿灞曞悗鍙版姄鍙?
- `--source bilibili`锛堥粯璁わ級璧板師 `ContentDiscoveryEngine.discover()`锛宍--strategy` 閫忎紶涓?`strategies=[鈥`锛岀┖鍊兼椂绛変环浜庤窇鍏ㄧ瓥鐣?
- 鍙傛暟鏍￠獙锛氭湭鐭?source 鎴栨湭鐭?Bilibili 绛栫暐鍚嶇洿鎺?Typer `BadParameter` 閫€鍑虹爜 2锛泋hs 璺緞涓婂悓鏃朵紶 `--strategy` 浼氭墦鍗板弸濂芥彁绀虹劧鍚庡拷鐣?
- 鏂囨。锛歚docs/modules/cli.md` 鐨?`openbiliclaw discover` 绔犺妭閲嶅啓锛岀粰鍑?B 绔欏崟绛栫暐 / xhs / `--force` 涓変釜绀轰緥

### Soul 椹卞姩 xhs 鑷姩鍙戠幇锛坧roducer 鎺ヤ笂锛?

- **鍚庣 producer 钀藉湴**锛歚runtime/xhs_producer.py` 鐨?`XhsTaskProducer` 璇诲彇 SoulProfile 鈫?璋?LLM 鏀瑰啓鎴愬皬绾功椋庢牸鍏抽敭璇?鈫?`XhsTaskQueue.enqueue("search", {keyword})`銆傚唴缃渶灏忛棿闅旓紙榛樿 4h锛夐槻姝㈠弽澶嶆姠閰嶉锛涙瘡鏃ラ绠楃敱 `XhsTaskQueue.enqueue` 寮哄埗锛坄sources.xiaohongshu.daily_search_budget`锛岄粯璁?30锛?
- **LLM 鍏抽敭璇嶇敓鎴?*锛歚sources/xhs_keyword_gen.py` 鎶?B 绔欓鏍肩殑鍏磋叮鏍囩閲嶅啓鎴愮敓娲诲寲銆佸叿璞°€侀暱灏俱€佸甫鍦烘櫙鐨?xhs 鏌ヨ锛堥伩鍏嶅崟瀛楃被鐩瘝锛夈€侸SON 瑙ｆ瀽璧板閿欒矾寰勶紝LLM 澶辫触鍗宠烦杩囪杞?
- **鎸傛帴鐜版湁鍒锋柊寰幆**锛歚ContinuousRefreshController.run_forever` 姣忚疆璋冪敤 `_tick_xhs_producer()`锛屽拰 bilibili discovery 鍏辩敤鍚屼竴璋冨害鍣紝鏃犻渶棰濆 cron
- **闂幆鎵撻€?*锛歜ackend producer 鈫?`xhs_tasks` 琛?鈫?鎵╁睍 `xhs-task-dispatcher` 杞 鈫?`chrome.tabs.create({active:false})` 鍚庡彴鎵ц 鈫?`xhs/task-executor`锛堥灞忋€佷笉婊氬姩锛夊洖浼?URLs + 鍏冩暟鎹?鈫?`/api/sources/xhs/task-result` 鍐欏叆 `content_cache`
- **閰嶇疆**锛歚sources.xiaohongshu.daily_search_budget` 榛樿浠?20 鎻愬埌 30锛堝尮閰嶄骇鍝佺瀵?xhs 閲囨牱瀵嗗害鐨勯鏈燂級
- **娴嬭瘯**锛歚tests/test_xhs_producer.py` 鏂板 5 涓紙disabled / 棰勭畻鎴柇 / 鑺傛祦 / 绌哄叧閿瘝 / 鏃犵敾鍍忥級銆傚叏閲?796 passed

### 灏忕孩涔﹀畨鍏ㄥ彂鐜版灦鏋?(xhs-safe-discovery)

- **GPL 闅旂 sidecar**锛歚sidecar/xhs-downloader/` 灏?GPL-3.0 鐨?XHS-Downloader 灏佽鍦ㄧ嫭绔?Docker 瀹瑰櫒涓紝閫氳繃 HTTP锛坄POST /xhs/detail`锛変笌涓诲悗绔€氫俊锛岄伩鍏?GPL 浼犳煋銆侱ockerfile 鍥哄畾涓婃父 commit `5f9bd54` 纭繚鍙鐜版瀯寤?
- **鏂?XiaohongshuAdapter**锛氭浛鎹㈡棫鐨勬祻瑙堝櫒鎶撳彇閫傞厤鍣紝鏀逛负 HTTP 瀹㈡埛绔皟鐢?sidecar銆傚苟鍙戜笂闄?2锛屽崟 URL 澶辫触涓嶅奖鍝嶆壒娆°€傚悗绔笉鍐嶇洿鎺ユ悳绱㈠皬绾功锛堝畬鍏ㄧЩ闄?browser-based XiaohongshuAdapter锛?
- **鎵╁睍琚姩 URL 鏀堕泦**锛歚extension/src/content/xhs/passive.ts` 鍦ㄧ敤鎴疯嚜鐒舵祻瑙堟椂鎻愬彇瑙嗗彛鍐呭彲瑙佺殑绗旇 URL锛堝惈 `xsec_token`锛夛紝鍘婚噸鍚庨€氳繃 `POST /api/sources/xhs/observed-urls` 涓婃姤銆?*涓ユ牸涓嶈嚜鍔ㄦ粴鍔?*鈥斺€旇嚜鍔ㄦ粴鍔ㄦ槸灏忕孩涔﹂鎺х殑缁忓吀瑙﹀彂淇″彿
- **浠诲姟闃熷垪**锛氬悗绔?`xhs_tasks` 琛?+ `XhsTaskQueue` 绠＄悊鎼滅储/鍒涗綔鑰呬换鍔★紝鏀寔姣忔棩棰勭畻闄愬埗锛堟寜绫诲瀷鍒嗗紑璁℃暟锛夈€傛墿灞曢€氳繃 `GET /api/sources/xhs/next-task` 杞锛宍POST /api/sources/xhs/task-result` 鍥炴姤缁撴灉
- **鍚庡彴鏍囩椤佃皟搴﹀櫒**锛歚extension/src/background/xhs-task-dispatcher.ts` 浠?alarm 椹卞姩杞锛宍chrome.tabs.create({ active: false })` 鎵撳紑鍚庡彴鏍囩椤垫墽琛屼换鍔★紝30s 纭秴鏃讹紝浜掓枼閿佷繚璇佸崟浠诲姟椋炶
- **鏃犳粴鍔ㄦ墽琛屽櫒**锛歚extension/src/content/xhs/task-executor.ts` 鐢?MutationObserver + 杞绛夊緟鍗＄墖娓叉煋锛?s 涓婇檺锛夛紝鎻愬彇鍒濆瑙嗗彛鍐呮渶澶?20 涓?URL锛岀粷涓嶈皟鐢ㄤ换浣曟粴鍔ㄦ柟娉?
- **鍒涗綔鑰呰闃?*锛歚xhs_creator_subscriptions` 琛?+ CRUD API锛坄/api/sources/xhs/creators`锛夛紝鏀寔 `due_for_fetch` 鏌ヨ椹卞姩澶滈棿璋冨害
- **閰嶇疆**锛歚[sources.xiaohongshu]` 鏂板 `sidecar_url` / `daily_search_budget` / `daily_creator_budget` / `task_interval_seconds`锛沗OPENBILICLAW_XHS_SIDECAR_URL` 鐜鍙橀噺鏄惧紡瑕嗙洊锛堝洜閫氱敤 env 妯″紡鏃犳硶澶勭悊鍚笅鍒掔嚎鐨勫祵濂楅敭锛?
- **docker-compose**锛氭柊澧?`xhs-sidecar` 鏈嶅姟锛堝唴閮?expose 5556锛宧ealthcheck锛屽悗绔?depends_on healthy锛夛紝鍚庣鑷姩娉ㄥ叆 sidecar URL
- **娴嬭瘯**锛歚test_xiaohongshu_adapter.py`锛? 涓級銆乣test_api_xhs_ingest.py`锛? 涓級銆乣test_xhs_tasks.py`锛?6 涓級銆乣xhs-passive.test.ts`锛? 涓級銆乣xhs-task-dispatcher.test.ts`锛? 涓級銆乣xhs-task-executor.test.ts`锛? 涓級銆傚叏閲?797 passed backend / 107 passed extension

### 澶氭簮琛屼负閲囬泦锛氭彃浠惰法绔?MVP

- **PlatformAdapter 鎺ュ彛**锛歚extension/src/shared/types.ts` 鏂板 `PlatformAdapter` 濂戠害锛坄sourcePlatform` / `detectPageType` / `extractContentId` / `cardSelector` / `searchInputSelector` / `videoSelector` / `inferActionType` / `buildEventMetadata`锛夛紝浣滀负璺ㄧ珯閫傞厤鍞竴鍏ュ彛
- **Collector kernel 鎷嗗垎**锛氬師 `content/collector.ts` 鎷嗘垚 `content/kernel.ts`锛堝钩鍙版棤鍏崇殑 click / scroll / hover / search / navigation / video 瑙傚療鍣級+ 姣忎釜骞冲彴涓€涓?entry锛坄bilibili.ts` / `xiaohongshu.ts`锛夛紝鏋勫缓浜х墿鍙樻垚涓や唤 content script bundle
- **Shared 鎷嗚В**锛歚shared/behavior.ts` 鏀剁獎涓?DOM snapshot + `createBehaviorEvent` 鍐呮牳锛汢 绔欎笓鐢ㄩ€昏緫锛坄extractBvid` / 鍗＄墖閫夋嫨鍣?/ 鍔ㄤ綔鍏抽敭瀛楋級涓嬫矇鍒?`shared/platforms/bilibili.ts`锛屾柊澧?`shared/platforms/xiaohongshu.ts`锛坄extractNoteId` 瑕嗙洊 `/explore/{id}` / `/discovery/item/{id}` / `/search_result/{id}` 涓夌被 URL锛?
- **BehaviorEvent.source_platform**锛歍ypeScript + Pydantic 涓や晶閮藉姞涓?`source_platform` 瀛楁锛涙彃浠朵笂鎶ユ椂鐢?kernel 鑷姩濉紙`bilibili` / `xiaohongshu`锛夛紝鍚庣 `/api/events` 鎶婂畠骞跺叆 `metadata`锛岀┖涓?/ 鐣欑櫧鍥為€€ `bilibili` 淇濊瘉鏃ф墿灞曠増鏈吋瀹?
- **Manifest + 鏋勫缓**锛歚manifest.json` 鏂板 `*://*.xiaohongshu.com/*` host permission 鍜岀浜屾潯 content_script 鍖归厤锛沗scripts/build.mjs` 鏂板 xhs entry锛宍dist/content/{bilibili,xiaohongshu}.js` 涓€璧蜂骇鍑?
- **MVP 閲囬泦鑼冨洿**锛氬皬绾功渚у厛鎺?snapshot / click / scroll / search锛沗videoSelector = null` 鐨勯€傞厤鍣ㄧ洿鎺ヨ烦杩囪棰戞挱鏀惧櫒瑙傚療
- **xhs 寮轰俊鍙疯ˉ榻?*锛歚inferXiaohongshuActionType` 娌跨敤涓?B 绔欏叡浜殑涓枃鍔ㄤ綔璇嶏紙`鐐硅禐 / 鏀惰棌 / 璇勮`锛? 鑻辨枃鍥為€€锛屽懡涓悗鐢?`STRONG_SIGNAL_TYPES` 瑙﹀彂鍗虫椂涓婃姤锛泋hs 娌℃湁"鎶曞竵"锛宑oin 鍒嗘敮涓嶅仛鍖归厤
- **娴嬭瘯**锛歚extension/tests/collector-helpers.test.ts` 鏇挎崲涓哄弻骞冲彴鍗曟祴锛坆ilibili + xhs adapter锛岃鐩?like / favorite / comment 姝ｅ弽渚嬶級锛宍dist-module-specifiers.test.ts` 鏍￠獙涓や唤 bundle 鏃?ESM 娈嬬暀锛涘悗绔柊澧?`test_events_endpoint_preserves_source_platform` 楠岃瘉 xhs 浜嬩欢涓庡洖閫€琛屼负銆傚叏閲?87/87 extension 娴嬭瘯 + 752 passed backend

### 璺ㄦ簮鐢诲儚铻嶅悎锛歴ource_platform_mix

- **PreferenceLayer / OnionProfile 鏂板 `source_platform_mix: dict[str, float]`**锛氭寔涔呭寲璁板綍鍚勬潵婧愮殑琛屼负鍗犳瘮锛坣ormalized 鍒?1.0锛夛紝搴忓垪鍖?/ 鍙嶅簭鍒楀寲 / Onion鈫擫egacy 杞崲鍏ㄩ儴鎵撻€?
- **PreferenceAnalyzer 鑷姩璁＄畻**锛歚compute_source_platform_mix()` 浠庢壒娆′簨浠剁殑 `metadata.source_platform` 鎸夎鏁板綊涓€鍖栵紱`_merge_source_mix()` 鐢?EMA锛坅lpha=0.3锛変笌鍘嗗彶鐢诲儚铻嶅悎锛岄伩鍏嶄竴娆¤法绔欐祻瑙堝氨鎶规帀闀挎湡 B 绔欒褰曪紱浜嬩欢缂?`source_platform` 瀛楁鏃跺洖閫€ `bilibili`锛堣€佹暟鎹吋瀹癸級
- **LLM 涓婁笅鏂囪嚜鍔ㄦ敞鍏?*锛氬綋 `len(source_platform_mix) > 1` 鏃讹紝`SoulProfile.to_llm_context()` 鍜?`OnionProfile.to_llm_context()` 浼氳拷鍔?`## 鏉ユ簮鍒嗗竷` 灏忚妭锛坄bilibili 60% 路 xiaohongshu 40%` 椋庢牸锛夛紝涓嬫父鎺ㄨ崘 / 瀵硅瘽 prompts 鍗虫椂鐭ラ亾鐢ㄦ埛鏄婧愮敤鎴?
- **鏆備笉鍔?LLM prompt 鍐呯殑鐢诲儚鎶藉彇**锛歱reference prompt 浠嶄笉鍖哄垎鏉ユ簮锛屽叴瓒ｆ爣绛炬湭鎸夌珯鐐规墦鏍囷紱绛夊婧愯涓洪噺鍫嗚捣鏉ュ啀鏀?prompt锛岄伩鍏嶈繃鏃╀紭鍖?
- **娴嬭瘯**锛歚test_preference_analyzer.py` 鏂板 5 涓敤渚嬶紙mix 璁℃暟 / 绌轰簨浠?/ EMA 铻嶅悎 / 绌烘壒娆′繚鐣?prior / analyze_events 绔埌绔級锛宍test_soul_profile.py` 鏂板 7 涓敤渚嬶紙PreferenceLayer 寰€杩斻€丼oulProfile / OnionProfile 澶氭簮 context銆佸崟婧愪笉娓叉煋锛夈€傚叏閲?765 passed + 1 skipped backend

### Phase 7 鍙岀绔埌绔祴璇?

- **鍚庣 E2E**锛坄tests/test_phase7_e2e.py`锛夛細鐪?SQLite `Database` + 鐪?`MemoryManager` + Pydantic `BehaviorEventBatchIn` 鏍￠獙 + 鐪?`PreferenceAnalyzer`锛堜粎 LLM 鏈韩 stub锛? 鐪?`OnionProfile` 搴忓垪鍖栧線杩旓紝璧板畬娣峰悎 bilibili + xhs 鎵规 鈫?浜嬩欢鍏ュ簱 鈫?鍋忓ソ鎶藉彇 鈫?鐢诲儚钀界洏 鈫?LLM context 娓叉煋鐨勬暣鏉￠摼璺紝骞剁敤绗簩杞函 bilibili 鎵规楠岃瘉 EMA 铻嶅悎鑳戒繚鐣欏巻鍙?xhs 鍗犳瘮锛?.4 鈫?0.28锛夎€岄潪鎶规帀
- **鎵╁睍 E2E**锛坄extension/tests/phase7-e2e.test.ts`锛夛細鐢ㄧ湡 `createBehaviorEvent` + 鐪?`xiaohongshuAdapter` / `bilibiliAdapter` + 鐪?`enqueueBufferedEvent` / `shouldFlushImmediately`锛岃鐩?xhs 鐐硅禐 鈫?寮轰俊鍙峰嵆鏃?flush銆佸婧愪簨浠跺湪 buffer 涓叡瀛樹笉鎾?dedupe銆亁hs 闈炲姩浣滅偣鍑讳笉瑙﹀彂寮轰俊鍙蜂笁鏉¤矾寰?
- 鍏ㄩ噺 766 passed + 1 skipped backend / 90 passed extension

### 澶氭簮鍐呭閫傞厤锛欳DP 鐧诲綍鎬?+ URL 鍥炲～

- **澶氭簮鏋舵瀯钀藉湴**锛歚sources/` 鏂板 `SourceAdapter` 鍗忚 + `SourceRecipe` 鏁版嵁妯″瀷锛宍ContentDiscoveryEngine.register_adapter()` 璁?B 绔欎箣澶栫殑鍐呭婧愶紙灏忕孩涔︺€佺煡涔庛€乂2EX 绛夛級浠ュ悓涓€鎺ュ彛鎸傝浇
- **BilibiliAdapter**锛氭妸鍥涘ぇ B 绔欑瓥鐣ワ紙search / trending / related_chain / explore锛夊寘瑁呮垚 adapter锛屾帹杩?鍐呭婧?涓?绛栫暐"鐨勮В鑰?
- **WebSourceAdapter / XiaohongshuAdapter**锛氶€氱敤娴忚鍣?+ LLM 鎶藉彇閫氶亾锛岄粯璁よ蛋 CDP 杩?Chrome锛涙悳绱㈢粨鏋滈〉宸茬湡瀹?E2E 楠岃瘉锛?0/10 绗旇鎷垮埌 24 浣?hex note ID + 鍙偣鍑?URL锛?
- **BrowserManager 鍙屽悗绔?*锛?
  - CDP 鍚庣锛歅laywright `connect_over_cdp` 澶嶇敤棰勫惎鍔ㄧ殑鐧诲綍 Chrome锛屽敮涓€鑳界ǔ瀹氭姄灏忕孩涔︾殑璺緞
  - agent-browser 鍚庣锛氬尶鍚嶅洖閫€锛屽吋瀹规棫琛屼负
- **PageSnapshot + 閿氱偣鍥炲～**锛氫竴娆?CDP 寰€杩斿悓鏃舵嬁 `innerText` 鍜屾墍鏈?`<a>` 鐨?`(text, href)`銆俙WebSourceAdapter` 鎸夋爣棰樻ā绯婂尮閰嶉敋鐐癸紝鍥炲～ `content_url`锛涗粠 URL 璺緞娲剧敓 `content_id`銆傝В鍐充簡 `innerText` 涓㈠純 href 瀵艰嚧鍊欓€夋棤娉曠偣鍑荤殑闂
- **LLM 绌哄€间慨澶?*锛歚llm_extractor.py` 涔嬪墠鎶?LLM 杩斿洖鐨?JSON `null` 閫氳繃 `str(None)` 鍙樻垚瀛楃涓?`"None"`锛屾薄鏌撴瘡涓┖瀛楁鐨勭湡鍊煎垽鏂€傛敼涓?`str(x or "").strip()`
- **閰嶇疆**锛氭柊澧?`[sources.browser]` 娈碉紙`cdp_url` + `headed`锛夛紝涓?`[bilibili.browser]` 鐙珛
- **鍙€変緷璧?*锛歚playwright>=1.40` 杩涘叆 `[browser]` optional-dependencies group锛宍pip install 'openbiliclaw[browser]'` 鎸夐渶瀹夎
- **娴嬭瘯**锛歚tests/test_browser_manager.py`锛? 涓級+ `tests/test_web_adapter.py`锛? 涓紝鍚?URL 鍥炲～锛? `tests/test_xhs_e2e.py`锛坄@pytest.mark.integration`锛岀湡 Chrome + 鐪熷皬绾功锛夈€傚叏閲?751 passed

### B 绔?API 绌哄搷搴斿閿?

- 淇 `_json_object()` 瀵?`None` 鏃犻槻鎶ょ殑闂锛欱 绔?`ranking/v2` / `web-interface/view` 绛夋帴鍙ｅ湪闄愭祦鎴栫┖鍒嗗尯 / 鍒犳。瑙嗛鍦烘櫙浼氳繑鍥?`"data": null`锛屽鑷翠笅娓?`None.get(...)` 鎶?`AttributeError` / `KeyError`
- `_json_object()` 鏂板 `None 鈫?{}` 鐭矾鍒嗘敮锛屼笌 `_json_list()` 鐨?`None 鈫?[]` 瀵圭О锛屼竴娆℃€ц鐩?11 澶勮皟鐢ㄧ偣锛坮anking / comments / search WBI / favorites cursor / video info 绛夛級
- `get_video_info()` 灏嗙‖涓嬫爣 `payload["data"]` 鏀逛负 `.get("data")`锛宍"data": null` 鏃堕€€鍖栦负瀛楁鍏ㄩ粯璁ょ殑 `VideoInfo` 鑰岄潪宕╂簝
- Discovery 鍥涘ぇ绛栫暐锛坱rending / search / explore / related_chain锛夌殑寮傚父鏃ュ織浠?`logger.exception(..., exc_info=outcome)` 鏀逛负 `logger.error(..., exc_info=outcome, extra=...)`锛宨diomatic 涔嬪琛ヤ笂 `strategy` / `error_type` / query 绛夌粨鏋勫寲瀛楁锛屼究浜庤娴?
- 鏂板 2 鏉″洖褰掔敤渚嬶紙`test_get_ranking_returns_empty_list_when_data_is_null` / `test_get_video_info_returns_defaults_when_data_is_null`锛?

### 鍚庣 Release 鑷姩鍙戝寘

- 鏂板 tag 椹卞姩鐨?GitHub Actions release workflow锛氭帹閫?`v*` tag 鍚庝細鑷姩鏋勫缓 macOS / Windows 鍚庣妗岄潰鍖?
- 鍚庣 release 浜х墿鐜板凡缁熶竴涓婁紶鍒?GitHub Releases锛屽拰娴忚鍣ㄦ彃浠朵竴鏍疯蛋鈥滀笅杞介檮浠垛€濆垎鍙戣矾寰?
- 鏂板鐗堟湰鍖栧悗绔綊妗ｅ懡鍚嶈鍒欙紝渚嬪 `OpenBiliClaw-macos-v0.1.1.zip`銆乣OpenBiliClaw-windows-v0.1.1.zip`
- README / 鏂囨。瀵艰埅宸插悓姝ヨˉ鍏呪€滀粠 Releases 涓嬭浇鍚庣鈥濈殑鍏ュ彛璇存槑
- 棣栫増妗岄潰鍚庣鍖呮殏鏈鍚嶏紝鏂囨。涓凡鏄庣‘ macOS Gatekeeper / Windows SmartScreen 鍙兘鍑虹幇鐨勫畨鍏ㄦ彁绀?

### 鎻掍欢 / 鍚庣 Release 閫氶亾鎷嗗垎

- 鍚庣 Release workflow 鐜板湪鍙搷搴?`backend-v*` tag锛屽苟缁х画鑷姩鏋勫缓 macOS / Windows 妗岄潰鍖?
- 鏂板鎻掍欢涓撶敤 Release workflow锛屾彃浠剁幇鍦ㄩ€氳繃 `extension-v*` tag 鍗曠嫭鍙戝竷 `openbiliclaw-extension-v*.zip`
- 鍚庣鍜屾彃浠跺悇鑷垱寤鸿嚜宸辩殑 GitHub Release锛屼笉鍐嶆妸涓ょ被闄勪欢娣峰湪鍚屼竴涓?release 璇箟閲?
- README銆佹ā鍧楁枃妗ｅ拰鏂囨。瀵艰埅宸插悓姝ユ敼鎴愨€滄彃浠剁湅 `extension-v*`銆佸悗绔湅 `backend-v*`鈥濈殑涓嬭浇璇存槑
- 鍘嗗彶 `v0.1.0` / `v0.1.2` 鍙戝竷璁板綍淇濇寔涓嶅姩锛屾柊鍙戝竷浠庡弻閫氶亾绛栫暐寮€濮嬫墽琛?

### 鎺ㄨ崘寮曟搸瑙ｈ€﹂噸鏋?

- **鏂板 `serve()` 缁熶竴鍏ュ彛** (`recommendation/engine.py`)锛屾墍鏈夋帹鑽愯矾寰?(generate / reshuffle / append) 鍚堝苟涓轰竴涓柟娉曪紝閫氳繃 `expression_mode` 鍙傛暟鍖哄垎瀹炴椂 LLM 鍜岄缂撳瓨涓ょ妯″紡
- **搴熷純 `discovered` 鐩翠紶璺緞**锛歚generate_recommendations()` 涓嶅啀鎺ュ彈涓婃父浼犲叆鐨勫€欓€夊垪琛紝寮曟搸濮嬬粓浠?content_cache pool 鑷富鎷ｉ€夛紝涓?Discovery 瀹屽叏瑙ｈ€?
- **鏂板 `PoolCurator`** (`recommendation/curator.py`)锛屾帹鑽愪晶浜屾璇勫垎锛歚rec_score = 0.4脳relevance + 0.2脳freshness - 0.15脳topic_fatigue - 0.15脳source_monotony + 0.1脳serendipity 卤 feedback`
  - `_freshness_score()`锛歴igmoid 琛板噺锛屽崐琛版湡 3 澶?
  - `_topic_fatigue()`锛氳繎 N 鏉℃帹鑽愪腑鍚?topic 鐨勯鐜囨儵缃?
  - `_source_monotony()`锛氳繎 N 鏉℃帹鑽愪腑鍚?source 鐨勯鐜囨儵缃?
  - `_serendipity_bonus()`锛歟xplore 鏉ユ簮鍔犲垎
  - `FeedbackSignals`锛歞islike UP 鈫?-0.20, dislike topic 鈫?-0.10, like 鈫?+0.05
- **鑷姩琛ヨ揣鏈哄埗**锛歳eshuffle / append 鍚庢鏌?`needs_replenishment()`锛屾睜瀛愪綆浜?50 鏃惰嚜鍔ㄨЕ鍙?`trigger_manual_refresh()`
- **杩囨湡娣樻卑**锛氭柊澧?`evict_stale_pool_items()`锛?4 澶╂湭娑堣垂鐨?fresh 鍐呭鏍囪涓?stale锛屾瘡娆?refresh cycle 鑷姩娓呯悊
- **DB 鏂板鏌ヨ**锛歚get_recent_recommendation_signals()` 鍜?`get_feedback_signals()` 涓?Curator 鎻愪緵璇勫垎涓婁笅鏂?
- 鏂板 24 涓?PoolCurator 鍗曞厓娴嬭瘯锛屽叏閮?476 涓祴璇曢€氳繃

### Discovery 璇勪及浼樺寲妗嗘灦

- **鏂板 `DiscoveryEvaluator`** (`eval/discovery_evaluator.py`)锛屾敮鎸?7 缁磋川閲忚瘎浼帮細relevance銆乨iversity銆乻pecificity銆乹uery_quality銆乪xplanation_quality銆乶ovelty銆乶o_echo_chamber
- **鏂板 `DISCOVERY_FIELD_TO_PARAM` 褰掑洜鏄犲皠**锛?7 涓瘎浼扮淮搴﹀綊鍥犲埌 5 涓?prompt锛坄search_queries_prompt` / `trending_rids_prompt` / `content_evaluation_prompt` / `explore_domains_prompt` / `recommendation_expression_prompt`锛?
- **鏂板 `ScenarioGenerator` + `MockBilibiliClient`** (`eval/discovery_scenario.py`)锛屼负姣忎釜 persona 绂荤嚎鐢熸垚妯℃嫙 B 绔欏唴瀹瑰畤瀹欙紙60 鏉¤棰?+ 鎼滅储绱㈠紩 + 鎺掕姒?+ 鐩稿叧鍥?+ 琛屼负浜嬩欢锛夛紝MockBilibiliClient 婊¤冻绛栫暐鐨?3 涓?Protocol 鎺ュ彛
- **鏂板 `create_discovery_optimizer()`** (`eval/discovery_optimizer.py`)锛屽鐢?`PromptOptimizer` 鏍稿績浣嗘敞鍏?discovery 涓撳睘鍙傛暟娉ㄥ唽琛ㄥ拰鐧藉悕鍗?
- **鏂板 `run_discovery_optimizer_agent()`** (`eval/agents.py`)锛屽彂鐜扮郴缁熶笓鐢ㄤ紭鍖?agent锛屽彲鑷富璇绘枃浠跺苟鎻愬嚭 prompt diff
- **鏂板鑷姩浼樺寲鑴氭湰** (`scripts/run_discovery_auto_optimize.py`)锛孲GD 椋庢牸寰幆锛歱ersona 鈫?scenario 鈫?discover 鈫?7 缁磋瘎浼?鈫?exploit/explore 鈫?accept/rollback
- **鏂板浜哄伐璇勪及鑴氭湰** (`scripts/run_discovery_eval.py`)锛屼氦浜掑紡灞曠ず鍙戠幇缁撴灉鍜屼腑闂翠骇鐗╋紝浜哄伐鎵撳垎鍚庡彲瑙﹀彂浼樺寲
- **SearchStrategy 缁熶竴璧?LLM 璇勪及**锛氭柊澧?`llm_evaluation` 鍜?`score_threshold` 瀛楁锛岄粯璁ゅ紑鍚?`evaluate_content()` LLM 鎵撳垎锛屽幓鎺変簡 0.62 纭笂闄?
- **4 涓瓥鐣ユ柊澧?`last_intermediates`**锛氳繍琛屽悗鏆撮湶涓棿浜х墿锛堟悳绱㈣瘝/鍒嗗尯/绉嶅瓙/鍩燂級锛屼緵璇勪及绯荤粺鐙珛璇勪及鍐崇瓥璐ㄩ噺
- **`PromptOptimizer` 鍙傛暟鍖?*锛歚__init__` 鏂板 `modifiable_files` 鍜?`field_to_param` 鍙€夊弬鏁帮紝soul 鍜?discovery 鍏变韩 apply/commit/rollback 鏈哄埗
- 鏂板 39 涓崟鍏冩祴璇曡鐩栬瘎浼板櫒鎵撳垎鍑芥暟銆丮ockClient Protocol 鍏煎鎬с€丼cenarioPool 缂撳瓨

### 鐚滄祴鍏磋叮绯荤粺 (Speculative Interest Lifecycle)

- **鏂板 `InterestSpeculator` 寮曟搸** (`soul/speculator.py`)锛屽疄鐜扮寽娴嬪叴瓒ｇ殑瀹屾暣鐢熷懡鍛ㄦ湡锛氱敓鎴?鈫?瑙傛祴 鈫?杞/鎷掔粷 鈫?鍐峰嵈
- **楂橀鐢熸垚**锛氭瘡 10 鍒嗛挓妫€鏌ヤ竴娆★紝Init 鍜岃繘绋嬪惎鍔ㄦ椂閫氳繃 `force_tick()` 绔嬪嵆瑙﹀彂
- **鍏磋叮涓婇檺淇濇姢**锛氫竴绾у叴瓒ｏ紙鍩熸暟锛変笂闄?15銆佷簩绾у叴瓒ｏ紙缁嗛」鏁帮級涓婇檺 60锛岀‘璁ゅ叴瓒?+ 娲昏穬鐚滄祴杈惧埌涓婇檺鏃惰嚜鍔ㄨ烦杩囩敓鎴?
- **LLM 椹卞姩鐨勫叴瓒ｇ寽娴?*锛氬熀浜庡績鐞嗗妗ユ帴鎺ㄧ悊鐢熸垚 3-5 涓柊鍏磋叮鏂瑰悜锛屾帓闄ゅ喎鍗存湡鏂瑰悜
- **杞婚噺绾т簨浠惰娴?*锛氭瘡娆′簨浠?ingest 鏃堕€氳繃鍏抽敭璇嶅尮閰嶆鏌ユ槸鍚︿笌鐚滄祴鍏磋叮鐩稿叧锛屾棤闇€ LLM 璋冪敤
- **鑷姩杞**锛氱寽娴嬪叴瓒ｈ 3 娆′互涓婁簨浠剁‘璁ゅ悗鑷姩鎻愬崌涓烘寮忓叴瓒ｏ紙source="speculated", weight=0.3锛?
- **鎷掔粷 + 鍐峰嵈**锛歍TL锛堥粯璁?3 澶╋級鍒版湡鏈‘璁ょ殑鐚滄祴杩涘叆 7 澶╁喎鍗存湡锛屾湡闂翠笉鍐嶇寽娴嬭鏂瑰悜
- **鍙屾潵婧愮瀛?*锛歚PreferenceAnalyzer` 姣忔鍋忓ソ鍒嗘瀽闄勫甫浜у嚭鐨?`speculative_interests` 鐜拌淇濈暀骞舵敞鍏?speculator 浣滀负绉嶅瓙
- **Pipeline 闆嗘垚**锛歚ingest_batch()` 鑷姩瑙﹀彂瑙傛祴锛宍tick()` 鑷姩澶勭悊杩囨湡/杞/鐢熸垚
- **Discovery 闆嗘垚**锛歚SoulEngine.get_profile()` 闄勫姞 `_active_speculations`锛宍build_profile_summary()` 鑷姩鍖呭惈鐚滄祴鍏磋叮锛屾墍鏈夌瓥鐣?LLM prompt 鍙
- **API 闆嗘垚**锛歚GET /api/profile` 杩斿洖 `speculative_interests` 瀛楁
- **7 椤归厤缃」**锛歚speculation_interval_minutes / ttl_days / cooldown_days / confirmation_threshold / max_active / max_primary_interests / max_secondary_interests`
- 鏂板 27 涓崟鍏冩祴璇曡鐩栬娴嬪尮閰嶃€佽浆姝ｃ€佽繃鏈熷喎鍗淬€佸叴瓒ｄ笂闄愩€乫orce_tick銆侀棿闅斿崟浣嶇瓑

### SoulProfile 浜斿眰娲嬭懕妯″瀷閲嶆瀯

- **鏂板 OnionProfile 鏁版嵁缁撴瀯**锛屽皢骞抽潰 SoulProfile 閲嶆瀯涓轰簲灞傚祵濂楁ā鍨嬶細
  - **Core Layer**: 鏈€绋冲畾鐨勬牳蹇冪壒璐紙core_traits锛夈€佹繁灞傞渶姹傦紙deep_needs锛夊拰 MBTI 浜烘牸绫诲瀷鍙婄淮搴﹀己搴?
  - **Values Layer**: 浠峰€艰锛坴alues锛夊拰鍐呭湪椹卞姩鍔涳紙motivational_drivers锛?
  - **Interest Layer**: 鏍戝舰鍏磋叮缁撴瀯锛坉omain 鈫?specifics锛夛紝鏀寔"鍥介檯鏃朵簨 鈫?涓笢灞€鍔?/ 娆ф床鏀挎不"鐨勫灞傜骇缁勭粐锛涘悓鏃跺寘鍚?dislikes 鏍戝拰 favorite_up_users 鍒楄〃
  - **Role Layer**: 鐢熸椿闃舵锛坙ife_stage锛夊拰褰撳墠澶勫锛坈urrent_phase锛?
  - **Surface Layer**: 鍙瀵熺殑璁ょ煡椋庢牸锛坈ognitive_style锛夈€佸唴瀹瑰亸濂斤紙style锛夈€佷娇鐢ㄥ満鏅紙context锛夊拰鎺㈢储寮€鏀惧害锛坋xploration_openness锛?
- **MBTI 浜烘牸绫诲瀷**鐜板凡鍐呯疆 Core 灞傦紝鍖呭惈 4 涓淮搴︾殑鏋佸悜閫夋嫨鍜屽己搴﹁瘎鍒嗭紙0.0-1.0锛夛紝渚夸簬鏇寸簿鍑嗙殑涓€у寲鎺ㄨ崘
- **鏍戝舰鍏磋叮缁撴瀯**鎻愬崌浜嗙敾鍍忚〃杈捐兘鍔涳紝from_legacy() 鑷姩灏?v1 flat interests 杞崲鎴愰鍩熸爲锛屾敮鎸佸叴瓒ｈ仛鍚堜笌绮剧粏鍖栬〃杩?
- **鍙屽瓨鍌ㄦ柟妗?*锛歴oul_profile.json 瀛樺偍缁撴瀯鍖?OnionProfile v2锛宻oul_profile.md 闀滃儚浜虹被鍙鐗堟湰锛宻oul_changelog.md 璁板綍姣忔鐢诲儚鏇存柊鐨勬椂闂存埑銆佽Е鍙戞潵婧愩€佸彉鍖栨憳瑕佸拰褰卞搷鑼冨洿
- **鍚戝悗鍏煎鍨墖灞炴€?*锛歄nionProfile 鏆撮湶 core_traits / deep_needs / motivational_drivers / values / cognitive_style / life_stage / current_phase 绛夊灚鐗囧睘鎬э紝鏀寔鐜版湁浠ｇ爜鏃犱慨鏀瑰湴璁块棶鏃ф帴鍙?
- **鑷姩鏍煎紡杩佺Щ**锛歋oulEngine 鍜?ProfileBuilder 閫忔槑妫€娴?v1/v2 鏍煎紡锛宖rom_dict() 鑷姩璋冪敤 from_legacy() 杩佺Щ锛屽凡鍒濆鍖栫殑鐢诲儚鏃犵紳鍗囩骇鍒颁簲灞傜粨鏋?
- **鍏磋叮鏍戝彲瑙嗗寲**锛歩nterest.likes 鍜?interest.dislikes 鐜版敮鎸佸畬鏁寸殑 domain / specifics / weight / source 閾捐矾锛屼究浜庡墠绔睍绀哄叴瓒ｅ浘璋卞拰绮剧粏鍙嶉

### OpenClaw Adapter 闆嗘垚

- 鏂板 `src/openbiliclaw/integrations/openclaw/`锛屽湪涓嶆敼鍔ㄦ牳蹇冩帹鑽愪笌瀛︿範涓婚摼鐨勫墠鎻愪笅锛屼负 OpenClaw 鎻愪緵鐙珛 adapter 灞?
- 鏂板 bootstrap銆丏TO銆乷peration 鍜屽崗璁腑绔?skill descriptor锛屽彲瀵瑰鏆撮湶 `sync_account / get_profile / recommend / submit_feedback / get_runtime_status`
- 鏂板 `src/openbiliclaw/integrations/openclaw/cli.py` JSON CLI bridge锛屼互鍙婁粨搴撶骇 `skills/openbiliclaw-adapter/SKILL.md`锛屾寜 OpenClaw skill 鐩綍绾﹀畾鎻愪緵鐪熷疄鍙彂鐜版妧鑳?
- CLI bridge 鏂板 `doctor` 涓?`emit-skill-descriptors`锛屼究浜庤皟璇?OpenClaw skill pack 鍜屽鍑哄綋鍓?skill 瀹氫箟
- OpenClaw `recommend` 鐜板凡榛樿璧板揩璺緞锛屼笉鍐嶆棤鏉′欢瑙﹀彂 runtime refresh锛涘闇€鏄惧紡鍒锋柊锛屽彲浣跨敤 `--refresh-if-needed`
- 鏄惧紡 refresh 瓒呮椂鎴栧け璐ユ椂锛孫penClaw adapter 鐜颁細鑷姩鍥為€€鍒扮紦瀛樻帹鑽愶紝閬垮厤浜や簰鍏ュ彛闀挎椂闂存寕浣?
- 鏂板 adapter / skill 鍗曞厓娴嬭瘯锛屽苟琛ュ厖闆嗘垚灞傛枃妗ｃ€佹灦鏋勮鏄庡拰瀵艰埅鍏ュ彛
- 鏂板 `docs/openclaw-quickstart.md`锛屽苟鍦?`skills/openbiliclaw-adapter/SKILL.md` 涓ˉ鍏?Docker 浼樺厛 / 鏈湴鍏滃簳鐨勯儴缃插喅绛栥€侀娆?`openbiliclaw init` 鍜?`doctor` 鑷鎸囧紩锛屾柟渚?OpenClaw 鐩存帴钀藉湴鎺ュ叆

### B 绔欐悳绱?412 闄嶅櫔

- `BilibiliAPIClient.search()` 鐜板湪浼氬厛浠?`nav` 鑾峰彇 WBI key锛屽苟鍒囧埌 `/x/web-interface/wbi/search/type` 鍙戣捣绛惧悕鎼滅储璇锋眰
- 鎼滅储璇锋眰浼氶檮甯︽悳绱㈤〉 `Referer` 鍜?`Origin`锛屾洿璐磋繎娴忚鍣ㄧ湡瀹炴悳绱㈤摼璺?
- 鎼滅储鎺ュ彛杩斿洖 `412 Precondition Failed` 鏃讹紝瀹㈡埛绔細璁板綍鎼滅储鍙楅檺 warning 骞朵繚瀹堣繑鍥炵┖缁撴灉锛屼笉鍐嶆妸鍗曟 search 澶辫触鏀惧ぇ鎴愭暣杞?discover traceback

### discovery 鍏磋叮閿氬畾鏀跺彛

- `ExploreStrategy` 鐜板湪鍏佽鈥滄牳蹇冨叴瓒ｇ殑杩戦偦鎵╁睍鈥濓紝涓嶅啀鎶婂寘鍚珮鏉冮噸鍏磋叮璇嶇殑鏂瑰悜涓€寰嬭浣滆繃搴︾浉浼?
- 璺ㄥ煙澶栨帹鏂板纭害鏉燂細鑷冲皯浼樺厛淇濈暀 2 涓敋瀹氬墠 5 涓珮鏉冮噸鍏磋叮鐨勬柟鍚戯紝鐪熸涓嶇洿鎺ユ彁鍙婃牳蹇冨叴瓒ｈ瘝鐨勮繙閭绘柟鍚戞渶澶氫繚鐣?1 涓?
- `SearchStrategy` 鏄犲皠鎼滅储缁撴灉鏃朵細瀵归珮鏉冮噸鍏磋叮鍛戒腑缁欒捣濮嬮敋瀹氬垎锛屾妸鏇磋创杩戞牳蹇冨枩濂界殑 search 鍊欓€変粠浣庡垎姹犻噷鎷夊嚭鏉?
- `ExploreStrategy` 瀵规病鏈夌洿鎺ュ叴瓒ｉ敋鐐圭殑杩滈偦鏂瑰悜鏂板杞婚噺璺濈鎯╃綒锛岄伩鍏嶈繖绫诲唴瀹瑰湪鎺掑簭閲屽帇杩囨洿璐磋繎鐢ㄦ埛鍠滃ソ鐨勫€欓€?

### 鎺ㄨ崘鎹竴鎵规壒閲忎笌琛ヨ揣浣欓噺璋冩暣

- popup 鐨?`/api/recommendations/reshuffle` 榛樿鎵归噺浠?`5` 鎻愬埌 `10`锛屽崟娆♀€滄崲涓€鎵光€濅細灏介噺缁欏 10 鏉★紱姹犲瓙涓嶅鏃朵粛鍏佽灏戜簬 10 鏉?
- `RecommendationEngine.reshuffle_recommendations()` 鐨勯鏍煎鏍锋€у洖濉€昏緫宸蹭慨姝ｏ紝涓嶅啀鍥犱负鍓嶆帓鍊欓€夐兘灞炰簬鍚屼竴 `style_key` 灏辨妸鏁存壒鏁伴噺鍗″埌 2~4 鏉?
- `scheduler.pool_target_count` 榛樿鍊间粠 `30` 鎻愬埌 `150`锛屽悗鍙颁細涓?popup 杩炵画鎹竴鎵逛繚鐣欐洿澶х殑 discovery pool 浣欓噺
- 閰嶇疆鐜板凡涓?`scheduler.pool_target_count` 澧炲姞 `1..300` 鐨勮寖鍥存牎楠岋紱杩愯鏃跺崟杞?discover 琛ヨ揣璇锋眰涔熶細灏侀《鍦?`60`

### popup 鐢诲儚鍒嗙粍鍔犲帤涓庨伩闆烽」灞曠ず

- `/api/profile-summary` 鐜板湪浼氳繑鍥炴洿鍘氫竴浜涚殑鐢诲儚鍒嗙粍锛歚core_traits` 鏈€澶?`6` 鏉°€乣top_interests` 鏈€澶?`8` 鏉★紝骞舵柊澧?`disliked_topics`
- popup銆屾垜鐨勭敾鍍忋€嶉〉鏂板 `鏈€杩戞槑鏄句細閬垮紑` 鍒嗙粍锛屼笉鍐嶅彧鑳界湅鍒扳€滃枩娆粈涔堚€濓紝涔熻兘鐪嬪埌绋冲畾閬块浄鏂瑰悜
- 鐢诲儚鐢熸垚 prompt 閲?`core_traits` 鐨勫缓璁笂闄愪篃宸蹭粠 `5` 鏀惧鍒?`6`锛岄伩鍏嶅墠绔墿瀹瑰悗鍚庣闀挎湡浠嶅彧鍚愬浐瀹?3~5 鏉?

### popup 鐢诲儚澶氬眰璁ょ煡閲嶆瀯

- `SoulProfile` 鏂板 `cognitive_style / motivational_drivers / current_phase`锛岀敾鍍忕敓鎴愮幇鍦ㄤ細鍚屾椂娑堣垂 `history + preference + awareness + insights`
- `personality_portrait` 鐨?prompt 宸叉敼鎴愪紭鍏堟€荤粨鈥滄€庝箞澶勭悊淇℃伅 / 鍦ㄥ唴瀹归噷闀挎湡鍦ㄦ壘浠€涔?/ 鏈€杩戝浜庝粈涔堥樁娈碘€濓紝鍏磋叮 topic 鍙厑璁镐綔涓哄皯閲忚瘉鎹嚭鐜?
- `/api/profile-summary` 涓?popup 鐢诲儚 tab 宸插悓姝ユ帴鍏ヨ繖涓夊眰鏂板瓧娈碉紝涓嶅啀鍙睍绀轰竴娈?prose 鍔犲叴瓒?chips

### explore 澶栨帹鏂瑰悜澶氭牱鎬у寮?

- `build_explore_domains_prompt()` 鐜板湪浼氭槑纭姹傝法棰嗗煙澶栨帹鑷冲皯瑕嗙洊 3 绫讳笉鍚屽唴瀹规柟鍚戯紝閬垮厤鍏ㄩ儴钀藉湪鍚屼竴涓娊璞¤酱涓?
- prompt 鏂板鈥滃悓涓€姣嶉鎹㈢毊鍙兘淇濈暀 1 涓€濈殑绾︽潫锛岀敤鏉ュ帇浣?`鍗氬紙璁?/ 妗屾父鏈哄埗 / 绛栫暐妯″瀷` 杩欑被杩戜箟鎺㈢储鏂瑰悜杩炵画鐏屾睜
- `why_it_might_resonate` 鐜板湪琚姹傚厛鍥炲埌鐢ㄦ埛鐨勮鐭ラ渶姹傚拰淇℃伅澶勭悊鍋忓ソ锛屽啀瑙ｉ噴棰樻潗涓轰粈涔堝彲鑳芥墦鍔ㄤ粬

### explore 鍗曠皣鐏屾睜涓庤ˉ璐х姸鎬佽涔変慨姝?

- runtime refresh 鐜板湪浼氬湪琛ヨ揣鍚庢俯鍜屽帇涓€杞?`explore` 楂橀闄╁瓙绨囩殑杩囬噺 fresh 鍊欓€夛紝浼樺厛澶勭悊鍒堕€?/ 宸ヨ壓 / 鏉愭枡銆佸崥寮?/ 妗屾父 / 鏈哄埗杩欑被瀹规槗杩炵画鍒峰睆鐨勭浉閭绘柟鍚?
- discovery runtime state 鏂板 `last_discovered_count`锛岃ˉ璐х姸鎬佷笉鍐嶅彧鐢ㄢ€滃彲绔嬪嵆鎹㈠簱瀛樺噣澧炩€濇潵琛ㄨ揪鏈疆 refresh 鐨勭粨鏋?
- popup pool summary 鐜板湪浼氬尯鍒嗏€滄鍦ㄨˉ璐р€濃€滆繖杞壘鍒颁簡鍐呭浣嗗彲鎹㈠簱瀛樻病鍙樷€濃€滃垰琛ヨ繘 N 鏉♀€濓紝涓嶅啀鎶?refresh 杩涜涓拰涓婁竴杞噣鏂板涓?0 娣锋垚鍚屼竴鍙?

### popup 鎺ㄨ崘澶撮儴淇℃伅闈㈡澘鏁寸悊

- 鎺ㄨ崘 tab 澶撮儴宸蹭粠鈥滄爣棰?+ 鎸夐挳 + 涓夎姹犲瓙鐘舵€佲€濇敼鎴愬崟寮犺交閲忎俊鎭崱锛屼富鎿嶄綔鍜岀姸鎬佸眰绾ф洿娓呮
- 鍊欓€夋睜鎽樿鐜板湪鎷嗘垚 `褰撳墠鍙崲 / 鏈€杩戣ˉ杩?/ 鐜板湪鍦ㄥ繖` 涓夊潡璇箟闈㈡澘锛屼笉鍐嶅儚涓€娈佃繛缁棩蹇?
- 鐐瑰嚮 `鎹竴鎵筦 鏃讹紝杩涜涓殑鏂囨浼氱洿鎺ヨ繘鍏モ€滅幇鍦ㄥ湪蹇欌€濈姸鎬佸潡锛岄伩鍏嶆寜閽梺杈瑰啀婕備竴鏉＄嫭绔嬫彁绀哄鑷村竷灞€鎶栧姩
- 鎺ㄨ崘 tab 澶撮儴鐜板凡杩涗竴姝ユ敹鎴愮揣鍑戝弻灞傜粨鏋勶細鏍囬琛?+ 鐘舵€?chips 琛岋紝鏄庢樉鍑忓皯棣栧睆鍗犵敤锛岃鎺ㄨ崘鍐呭鏇存棭闇插嚭
- pool summary 鏂囨鍚屾鏀剁煭鎴?chip 鍙嬪ソ鐨勫舰寮忥紝渚嬪 `杩樻湁 151 鏉″彲鎹?/ 鍒氳ˉ杩?6 鏉?/ 杩欎細鍎垮厛涓嶈ˉ璐

### popup For You 缂栬緫寮忛噸鎺?

- 鎺ㄨ崘 tab 鐨?`For You` 鍖哄潡杩涗竴姝ユ敼鎴愬唴瀹逛紭鍏堢殑缂栬緫寮忓竷灞€锛屽ご閮ㄥ璇€佹睜瀛愭憳瑕佸拰棣栧紶鍐呭鍗＄殑灞傜骇鏄庢樉鍒嗗紑
- 鎺ㄨ崘鍗＄墖鏀规垚鏇存竻鏅扮殑绾靛悜淇℃伅鑺傚锛氫笂灞傛槸灏侀潰鍜屼富棰樻爣绛撅紝涓眰鏄爣棰樹笌鎺ㄨ崘鐞嗙敱锛屼笅灞傛槸 UP 涓讳俊鎭拰鍙嶉鎿嶄綔
- 瑙嗚涓婃敹鏁涗簡杩囬噸鐨勮楗板眰锛岄灞忔洿鍍忓唴瀹规帹鑽愭祦锛岃€屼笉鏄姸鎬侀潰鏉挎嫾瑁?

### discovery pool 棰勭敓鎴愭帹鑽愭枃妗?

- discovery pool 鐜板湪浼氬湪鍐呭鍏ユ睜鍚庡紓姝ユ壒閲忛鐢熸垚 `expression` 鍜?`topic_label`锛宍reshuffle/append` 涓嶅啀鐜板満鍏滃簳鐢熸垚鏁存壒缁熶竴鏂囨
- popup 鎺ㄨ崘鍗＄墖鏀规垚鈥滄湁棰勭敓鎴愭枃妗堝氨灞曠ず锛屾病鐢熸垚濂藉氨鍏堥殣钘忊€濓紝涓嶅啀鎶婄┖鍊艰ˉ鎴愬浐瀹氬崰浣嶆枃妗?
- runtime refresh 鍦ㄨˉ璐у悗浼氶『鎵嬭Е鍙戣繖杞?pool copy 棰勭敓鎴愶紝淇濊瘉鈥滄崲涓€鎵光€濈户缁繚鎸佺绾у搷搴?


### popup 鎺ㄨ崘鑷姩缁〉

- 鏂板 `POST /api/recommendations/append`锛宲opup 鎺ㄨ崘 tab 婊氬埌搴曟椂浼氱户缁粠 discovery pool 杩藉姞涓嬩竴鎵?10 鏉?
- 鑷姩缁〉浼氭妸褰撳墠宸插睍绀虹殑 `bvid` 浼犵粰鍚庣鎺掗櫎锛岄伩鍏嶈拷鍔犳椂鍜屽綋鍓嶅垪琛ㄩ噸澶?
- `鎹竴鎵筦 浠嶄繚鐣欎负鏁寸粍閲嶅紑锛涜嚜鍔ㄧ画椤靛彧璐熻矗鍦ㄥ綋鍓嶅垪琛ㄥ簳閮ㄧ户缁線涓嬫帴鍐呭
- 淇浜嗙画椤垫柊鍗＄墖灏侀潰鍋跺彂绌虹櫧鐨勯棶棰橈細popup API 鐜板湪浼氱粺涓€瑙勮寖鍖?`cover_url`锛屽悓鏃跺皝闈笉鍐嶄緷璧栦細璇激鍐呴儴婊氬姩瀹瑰櫒鐨勫師鐢?lazy loading

### SQLite 淇涓庨槻鎹熷潖鍔犲浐

- 鏂板 `openbiliclaw db-repair`锛屼細鍏堟鏌ュ畬鏁存€с€佹嫆缁濆甫鍗犵敤淇銆佸浠?`db/db-wal`锛屽啀灏濊瘯鎭㈠鍒?repaired 鍓湰骞跺垏鎹㈡寮忓簱
- `openbiliclaw start` 鐜板湪浼氬湪鍚姩鍓嶆鏌ユ暟鎹簱鍋ュ悍搴︼紱妫€娴嬪埌鎹熷潖鏃朵細鐩存帴闃绘鍚姩锛屽苟鎻愮ず鍏堟墽琛?`db-repair`
- 杩愯鏃跺鍔犻粯璁?24 灏忔椂鍐峰浠界瓥鐣ワ紝鑷姩鎶婂仴搴锋暟鎹簱澶囦唤鍒?`data/backups/`锛屽苟鎸夆€滄渶杩?7 浠芥棩澶?+ 4 浠藉懆澶団€濊疆杞?
- `Database` 鐨勬帹鑽愭洿鏂板啓璺緞鐜板凡缁熶竴璧板甫閿侀噸璇曠殑鍐欏叆鍙ｏ紝鍑忓皯 `database is locked` 鍚庡眬閮ㄨ８鍐欏甫鏉ョ殑椋庨櫓
- CLI / API 鐨勯珮娴侀噺璺緞寮€濮嬪叡浜悓涓€涓?SQLite 瀹炰緥锛岄伩鍏嶅悓杩涚▼閲嶅鍒濆鍖栧浠借繛鎺?

### Docker 涓€閿悗绔儴缃叉敮鎸?

- 鏂板 `Dockerfile`銆乣.dockerignore` 鍜屽崟鏈嶅姟 `docker-compose.yml`锛屾敮鎸?`docker compose up -d` 鍚姩鍚庣
- CLI `start` 鐜板湪鏀寔 `--host` / `--port`锛屽悓鏃舵柊澧?`serve-api` 浣滀负瀹瑰櫒鍙嬪ソ鐨勬樉寮忓惎鍔ㄥ叆鍙?
- 榛樿 compose 鐜板凡鏀逛负 Docker named volumes锛岄厤缃€佹暟鎹€佹棩蹇楅兘涓庡涓绘満椤圭洰鐩綍闅旂
- 淇瀹夎鍖呰繍琛屾椂鐨勬牴鐩綍瑙ｆ瀽闂锛屽鍣ㄥ唴鐜板湪浼氭纭鍙?`/app/runtime/config.toml` 骞舵妸鏁版嵁鍐欏叆 `/app/runtime/data`
- 瀹瑰櫒鍚姩鏃剁幇鍦ㄤ細鑷姩鎺㈡祴瀹夸富鏈?Clash HTTP 浠ｇ悊锛涢粯璁ゆ帰娴?`host.docker.internal:7897`锛屽彲杈惧垯閫忎紶浠ｇ悊锛屼笉鍙揪鍒欑户缁洿杩?
- `openbiliclaw init` 鐜板湪鏀寔浜や簰寮忓紩瀵硷細Docker 鐢ㄦ埛棣栨鎵ц鏃跺彲鐩存帴琛ラ綈榛樿 provider銆丄PI Key 鍜?B 绔?Cookie锛岀劧鍚庣户缁畬鎴愬垵濮嬪寲
- 瀹瑰櫒鍐呴€氳繃 `docker exec openbiliclaw ...` 鎵ц浠绘剰 CLI 鍛戒护鏃讹紝涔熶細閲嶅杩欏眰 runtime/bootstrap 閫昏緫锛岄伩鍏嶅彧鏈変富杩涚▼鏈変唬鐞嗐€佷氦浜掑懡浠ゅ嵈鐩磋繛澶辫触
- discovery 鍐呴儴宸茬粡鏀逛负淇濆畧鍙楁帶骞跺彂锛歋earch / Trending / Related / Explore 浼氬叡浜緝灏忕殑 B 绔欒姹備笌 LLM 璇勫垎骞跺彂涓婇檺锛屽噺灏戦杞?init/discover 鐨勬槑鏄句覆琛岃€楁椂
- `openbiliclaw init` 鐨?discover 闃舵鐜板湪浼氭寜 `search + related_chain -> trending -> explore` 鍒嗛樁娈佃ˉ璐э紝灏介噺鎶婇杞?fresh 鍊欓€夋睜琛ュ埌鑷冲皯 `100` 鏉★紝闄嶄綆绗竴娆?`recommend` 鐩存帴绌烘睜瀛愮殑姒傜巼
- `openbiliclaw init` 杩愯鏃朵細鍚屾鎵撳嵃姣忎釜琛ヨ揣闃舵鐨勫綋鍓嶆睜瀛愯繘搴﹀拰鏈疆璇锋眰涓婇檺锛岄杞瓑寰呮椂涓嶅啀鍙湁涓€涓潤鎬佲€滃彂鐜板唴瀹光€濇爣棰?
- 淇 `DiscoveryConcurrencyController` 鍦ㄥ娆?`asyncio.run(...)` 闂村鐢?semaphore 鐨勮法浜嬩欢寰幆闂锛孌ocker/CLI 棣栬疆鍒嗛樁娈佃ˉ璐т笉鍐嶅湪绗簩闃舵鎶?`Semaphore ... is bound to a different event loop`

### discovery pool 鐩爣鎵╁

- `scheduler.pool_target_count` 榛樿鍊肩幇宸蹭粠 `150` 鎻愬埌 `300`锛岃繍琛屾椂浼氭寔缁互 300 鏉?fresh 鍊欓€変负鐩爣琛ヨ揣
- `openbiliclaw init` 鐨勯杞ˉ璐х洰鏍囦繚鎸佷繚瀹堝垎灞傜瓥鐣ワ紝浣嗕繚搴曞€煎凡浠?`50` 鎻愬埌 `100`
- 鐜版湁鎶ゆ爮淇濇寔涓嶅彉锛歚pool_target_count` 浠嶉檺鍒跺湪 `1..300`锛屽崟杞?refresh discover 鍥炲～浠嶅皝椤?`60`

### 鍚屾壒鎺ㄨ崘澶氭牱鎬х害鏉?

- `generate_recommendations()` 鍜?`reshuffle_recommendations()` 鐜板湪涓嶄細鍙寜鍒嗘暟鐩村彇鍓?N
- 鍚屼竴鎵归噷浼氬閲嶅 `tags/topic` 鍋氳蒋闄愭祦锛屽敖閲忛伩鍏嶈繛缁嚭鐜板お澶氬悓涓€鏂瑰悜鐨勫唴瀹?
- 鍊欓€変笉瓒虫椂浠嶄細鍥炲～楂樺垎鍐呭锛屼繚璇佸鏍锋€х害鏉熶笉浼氭妸鎺ㄨ崘鏁伴噺鍗℃病

### topic_key 澶氭牱鎬у己鍖?

- `content_cache` 鐜板湪浼氭寔涔呭寲绋冲畾 `topic_key`锛屾帹鑽愬眰涓嶅啀鍙潬绌?`tags` 鐚?topic
- `SearchStrategy` 浼氭妸 query 娲剧敓鐨?`topic_key` 鍐欏叆鍊欓€夛紝`RelatedChainStrategy` 浼氭妸 seed chain 缁ф壙鎴?`topic_key`
- `generate_recommendations()` 鍜?`reshuffle_recommendations()` 鐜板湪浼樺厛鎸?`topic_key` 鍒嗘《锛屾瘡涓?topic 鍏堝嚭 1 鏉★紝鍐嶆寜鍒嗘暟鍥炲～
- `ContentDiscoveryEngine` 鍦ㄥ啓鍏?discovery pool 鍓嶄細鍏堝帇涓€杞悓 topic 閲嶅椤癸紝鍑忓皯鍗曚竴鐩稿叧鎺ㄨ崘閾炬妸姹犲瓙鐏屾弧鐨勬儏鍐?

### 椋庢牸澶氭牱鎬т笌蹇€熸枃妗堝寮?

- discovery 鍏ユ睜鏃朵細鎸夋爣棰樸€佹弿杩板拰鍩虹鐞嗙敱杞昏鍒欒ˉ `style_key`锛屽尯鍒?`deep_dive / news_brief / game_strategy / practical_guide / story_doc / visual_showcase / light_chat`
- `reshuffle_recommendations()` 鐜板湪浼氬悓鏃剁害鏉?`topic_key + style_key`锛岄伩鍏嶄竴鎵归噷铏界劧 topic 涓嶅悓锛屼絾鍏ㄦ槸鍚屼竴绉嶁€滃緢骞插緢瀛︽湳鈥濈殑鍐呭椋庢牸
- 蹇€熸崲涓€鎵圭殑 fallback 鏂囨涓嶅啀鐩存帴瑁哥敤 `relevance_reason`锛岃€屼細鎸?`style_key` 鐢熸垚鏇磋嚜鐒剁殑鑰丅鍙嬬煭鍙?

### 鍊欓€夌獥鍙ｆ潵婧愪氦閿欎笌 10 鏉℃壒娆＄‖涓婇檺

- `get_pool_candidates()` 鐜板湪浼氬 discovery pool 鍋氭潵婧愪氦閿欏彇鏍凤紝浼樺厛鎶?`search / trending / related_chain / explore` 娣疯繘鍚屼竴鍊欓€夌獥鍙ｏ紝鑰屼笉鏄厛鍚愬嚭涓€灞?`explore`
- `reshuffle_recommendations()` 鐜板湪浼氬悓鏃跺 `topic_key + style_key + source` 鍔犵‖涓婇檺锛?0 鏉′竴鎵规椂鍗曚竴鏉ユ簮鏈€澶?3 鏉★紝灏忔壒娆′篃浼氫紭鍏堜繚鐣欎笉鍚屾潵婧愶紝鍑忓皯鈥滄崲涓€鎵硅繕鏄悓涓€涓懗鈥濈殑鎯呭喌

### 鏉ユ簮浼樺厛琛ラ綈涓庨鏍艰鍒や慨姝?

- discovery 涓?recommendation 鐨勫鏍锋€ч€夋嫨鐜板湪浼氫紭鍏堣ˉ榻愪笉鍚?`source`锛屽啀鏂藉姞 `style` 涓婇檺锛岄伩鍏?`trending/search` 杩樻病鍑哄満灏辫閲嶅鐨?`explore` 鍊欓€夋尋鎺?
- `infer_style_key()` 琛ュ己浜嗚姱鐗?鏄惧井闀?绾崇背/鐞嗚/鍝插绛夌‖鏍歌В鏋愯瘝锛屼互鍙娾€滃叏杩囩▼ / 鍒堕€犺繃绋?/ 宸ヨ壓闅惧害鈥濈瓑绾綍鐗?宸ヤ笟娴佺▼璇嶏紝鍑忓皯澶ч噺纭唴瀹硅璇垽鎴?`light_chat`
- 鎺ㄨ崘鍊欓€変笌閫変腑鎽樿鏃ュ織鐜板湪鏇村鏄撳搴斺€滄潵婧愭槸鍚︾湡鐨勮琛ラ綈鈥濓紝渚夸簬缁х画瀹氫綅姹犲瓙涓婃父鍋忕Щ闂

### 鍊欓€夋睜鎸夋潵婧愮己鍙ｈˉ璐?

- runtime refresh 鍦ㄦ睜瀛愪綆浜?`pool_target_count` 鏃讹紝涓嶅啀涓€瑙嗗悓浠佸湴鎶婃墍鏈夌瓥鐣ュ悇璺戜竴杞紝鑰屾槸浼氬厛缁熻 `search / related_chain / trending / explore` 褰撳墠姹犲瓙鍗犳瘮
- 琛ヨ揣鐜板湪浼氫紭鍏堣ˉ瓒崇己鍙ｆ洿澶х殑鏉ユ簮锛涗緥濡?`trending` 涓?0銆乣explore` 宸茬粡瓒呮爣鏃讹紝浼氬厛琛?`search/related` 鍜?`trending`锛岃€屼笉浼氱户缁姞鐮?`explore`
- `database` 鏂板鎸夋潵婧愮粺璁?fresh pool 鐨勮兘鍔涳紝鍊欓€夋睜鐘舵€佺幇鍦ㄤ笉浠呯湅鎬婚噺锛屼篃鐪嬫潵婧愮粨鏋勬槸鍚﹀け琛?

### 姹犲瓙宸叉弧鏃剁殑鐘舵€佹枃妗堜慨姝?

- popup 鍊欓€夋睜鎽樿鐜板湪浼氬湪 `pool_available_count >= pool_target_count` 涓旀渶杩戞病鏈夋柊澧炲叆姹犳椂锛屾樉绀衡€滆繖浼氬効鍏堜笉琛ヨ揣锛屾睜瀛愰噷宸茬粡澶熶綘鎹簡鈥?
- 涓嶅啀鐢ㄢ€滃垰琛ヨ繘 0 鏉℃柊鐨勨€濊瀵肩敤鎴蜂互涓哄悗绔病鍦ㄥ伐浣?

### popup 鍔ㄦ€佺姸鎬佸崱涓庢椿鍔ㄥ巻鍙?

- popup 搴曢儴鎻愮ず鍖虹幇鍦ㄥ崌绾т负涓よ鍙睍寮€鍔ㄦ€佸崱锛岄粯璁ゆ樉绀衡€滅幇鍦ㄥ湪蹇欎粈涔?/ 鏈€杩戜竴娆″叧閿彉鍖栤€?
- 鏂板 `/api/activity-feed`锛岃仛鍚堣鐭ユ洿鏂般€佸弽棣堣褰曘€佹崲涓€鎵瑰拰鍊欓€夋睜琛ヨ揣绛夋渶杩戞椿鍔?
- 鐐?`鏇村` 鍚庝細灞曞紑鏈€杩戝巻鍙诧紝涓嶅啀鍙兘鐪嬪崟鏉＄灛鏃舵彁绀?

### 鐢诲儚璁ょ煡鍗＄墖鍘嗗彶鍒嗛〉

- `/api/profile-summary` 鐜板湪浼氳繑鍥炵粨鏋勫寲璁ょ煡鍗＄墖鍒嗛〉缁撴灉锛屾柊澧?`has_more_cognition_updates / next_cognition_cursor`锛宲opup 鍙户缁媺鍙栨洿鏃╃殑璁ょ煡鍙樺寲
- popup銆岄樋B 鏈€杩戞柊璁颁綇浜嗕粈涔堛€嶅崌绾т负鍙睍寮€鍗＄墖锛氶粯璁ょ湅涓€鍙ユ€荤粨锛屽睍寮€鍚庤兘鐪嬪埌鈥滆繖瀵圭敾鍍忕殑褰卞搷 / 涓轰粈涔堣繖涔堝垽鏂?/ 杩欐渚濇嵁鈥?
- 璇勮鍨嬭鐭ュ崱鐗囩幇鍦ㄤ細甯︿笂瀵瑰簲鍐呭鏍囬锛岄伩鍏嶅彧鐪嬪埌鈥滆繖涓緢濂界湅鈥濆嵈涓嶇煡閬撴槸鍦ㄨ瘎浠峰摢鏉″唴瀹?
- 鐢诲儚 tab 棣栧睆鍏堝睍绀?3 鏉¤鐭ュ彉鍖栵紝骞舵敮鎸佹粴鍔ㄨ嚜鍔ㄧ画椤碉紱搴曢儴淇濈暀鈥滃姞杞芥洿澶?/ 閲嶈瘯鍔犺浇鈥濇寜閽綔涓哄厹搴?

### 璁ょ煡鍗＄墖涓婁笅鏂囦笌灞曞紑鐘舵€佹緞娓?

- 璁ょ煡鍗＄墖榛樿鎬佺幇鍦ㄥ浐瀹氭樉绀衡€滅粨璁?+ 涓婁笅鏂?+ 鐘舵€佹彁绀衡€濓紝渚嬪 `鏉ヨ嚜锛氥€婃煇鏉″唴瀹广€媊銆乣鏉ヨ嚜鏈€杩戣繖杞亰澶╋細鈥銆乣鍩轰簬鏈€杩戜富棰橈細鈥
- `/api/profile-summary` 鏂板 `context_line / source_label / expand_hint`锛屽墠绔笉鍐嶆妸 `鐢诲儚瑙傚療` 杩欑被娉涙爣绛惧綋浣滈粯璁や笂涓嬫枃
- popup 浼氭樉寮忓尯鍒?`灞曞紑 / 鏀惰捣 / 浠呯粨璁篳锛屼笉鍙睍寮€鍗＄墖涓嶅啀鍋氭垚鍍忔寜閽殑鏍峰瓙锛涜仛鍚堝垽鏂嬁涓嶅埌鍙俊瀵硅薄鏃朵細淇濆畧鍥為€€涓衡€滃熀浜庢渶杩戝嚑鏉＄浉鍏冲唴瀹光€?

### 鎺ㄨ崘璇勮鍙戦€佺姸鎬佸彲瑙佸寲

- 鎺ㄨ崘鍗＄墖閲岀殑 `璇磋鍘熷洜 -> 鍙戝嚭鍘籤 鐜板湪浼氱珛鍒诲垏鍒?`鍙戦€佷腑...`锛屾垚鍔熷悗鏄剧ず `宸插彂鍑篳 骞跺洖鍐欐湰鍦扮姸鎬佹枃妗?
- 璇锋眰澶辫触鏃舵寜閽細鎭㈠鍙偣锛屽崱鐗囨湰鍦颁細鐩存帴鎻愮ず鈥滆繖鍙ヨ繕娌″彂鍑哄幓锛屽彲浠ュ啀璇曚竴娆♀€濓紝涓嶅啀鍙兘闈犲簳閮ㄦí鏉＄寽娴?

### 璐︽埛渚у畾鏃跺悓姝?鈥?`runtime/m115-account-sync`

- 鏈湴鍚庣杩愯鏃舵柊澧炰綆棰戣处鎴峰悓姝ラ摼璺紝浼氬畾鏈熸媺鍙?`history / favorites / following`
- 鏂版暟鎹細缁熶竴杞垚 `view / favorite / follow` 浜嬩欢锛屽啀澶嶇敤 `SoulEngine.analyze_events()` 鏇存柊鍋忓ソ涓庣敾鍍?
- 鏂板 `account_sync_state.json` 淇濆瓨鍘嗗彶娓告爣銆佹敹钘?鍏虫敞绛惧悕鍜屾渶杩戝悓姝ラ敊璇?
- `runtime-status` 鏂板 `last_account_sync_at` / `last_account_sync_error`锛屼究浜?popup 鎴栬瘖鏂〉灞曠ず璐︽埛鍚屾鐘舵€?

### 鑱婂ぉ鍗虫椂璁ょ煡闃堝€兼斁瀹?鈥?`runtime/m114-chat-cognition-threshold`

- popup/CLI 鑱婂ぉ鐜板湪瀵?`interest / value / goal / dislike` 杩欑被鍗曟潯涓珮缃俊淇″彿鏇存晱鎰燂紝浼氭洿鏃╄繘鍏ャ€岄樋B 鏈€杩戞柊璁颁綇浜嗕粈涔堛€?
- 鍋忓ソ閲嶅垎鏋愬拰鐢诲儚閲嶅缓浠嶄繚鐣欏師鏈夐噸澶嶅嚭鐜?绱闃堝€硷紝涓嶄細鍥犱负涓€鍙ラ殢鍙ｈ亰澶╁氨鏀瑰姩闀挎湡鐢诲儚

### 鍗曟潯寮鸿亰澶╁嵆鏃惰鐭ユ洿鏂?鈥?`runtime/m113-immediate-chat-cognition`

- 鍗曟潯楂樼疆淇″害鑱婂ぉ淇″彿鐜板湪涔熷彲鍗虫椂鍐欏叆杞婚噺 cognition update锛屼緵 popup銆岄樋B 鏈€杩戞柊璁颁綇浜嗕粈涔堛€嶄紭鍏堝睍绀?
- 澶ц妯″亸濂介噸鍒嗘瀽鍜岀敾鍍忛噸寤轰粛淇濈暀鍘熸湁鍊欓€夌疮璁￠槇鍊硷紝涓嶄細鍥犱负涓€娆¤亰澶╁氨閲嶅啓鏁村紶鐢诲儚

### popup 鐢诲儚鎽樿鍗虫椂鍒锋柊

- side panel 鍦ㄨ亰澶┿€乣澶氭潵鐐筦銆乣灏戞潵鐐筦銆乣璇磋鍘熷洜` 鎴愬姛鍚庯紝浼氬己鍒堕噸鎷?`/api/profile-summary`
- 淇鈥滈樋B 鏈€杩戞柊璁颁綇浜嗕粈涔堚€濆彧鍦ㄩ娆℃墦寮€鐢诲儚 tab 鏃跺姞杞斤紝涔嬪悗涓嶈窡鐫€鏂板弽棣?鏂拌亰澶╂洿鏂扮殑闂

### 寮哄弽棣堝嵆鏃惰鐭ユ洿鏂?鈥?`runtime/m112-immediate-cognition-feedback`

- 鍗曟潯 `dislike` / `comment` 鍙嶉鐜板湪浼氬嵆鏃跺啓鍏ヨ交閲?cognition update锛屼緵 popup銆岄樋B 鏈€杩戞柊璁颁綇浜嗕粈涔堛€嶇珛鍒诲睍绀?
- 鍋忓ソ閲嶅垎鏋愬拰鐢诲儚閲嶅缓浠嶄繚鎸佺幇鏈?`>= 3` 鏉″弽棣堥槇鍊硷紝涓嶄細鍥犱负涓€娆″弽棣堝氨閲嶅啓鏁村紶鐢诲儚

### 杩愯鏃跺疄鏃剁姸鎬佹祦 鈥?`runtime/m111-runtime-stream`

- 鏂板 `/api/runtime-stream` websocket锛宲opup 鎵撳紑鏈熼棿鍙寔缁帴鏀跺悗绔繍琛岄樁娈典簨浠?
- 鍒锋柊鍣ㄧ幇鍦ㄤ細骞挎挱鈥滃紑濮嬭ˉ鍊欓€?/ 褰撳墠绛栫暐 / 鍒氳ˉ杩涘嚑鏉℃柊鐨?/ 杩欐壒鍏堟崲濂戒簡 / 琛ヨ揣澶辫触鈥濈瓑鐘舵€?
- popup 搴曢儴鎻愮ず妯潯鍜屾睜瀛愭憳瑕佷細闅忕潃浜嬩欢娴佸嵆鏃舵洿鏂帮紝涓嶅啀鍙樉绀洪潤鎬佹暟瀛?

### Popup 搴曢儴鎻愮ず澧炲己 鈥?`extension/m110-hint-banner`

- popup 搴曢儴鎻愮ず鍖轰粠娣＄伆璇存槑鏂囨鍗囩骇涓哄甫鐘舵€佺偣鐨勬í鏉℃彁绀猴紝鎴愬姛 / 鎻愮ず / 閿欒涓夌鐘舵€佺幇鍦ㄦ洿瀹规槗鍖哄垎
- `鍠滄 / 涓嶅枩娆?/ 鍐欎竴鍙?/ 鎹竴鎵?/ 鑱婂ぉ鍙戦€乣 绛夊叧閿姩浣滈兘浼氬悓姝ュ垏鎹㈡彁绀鸿姘旓紝鍑忓皯鈥滄搷浣滄垚鍔熶簡浣嗕笉鏄庢樉鈥濈殑闂

### 鍊欓€夋睜瀹归噺涓庣姸鎬佸睍绀?鈥?`runtime/m107-pool-status-capacity`

- `scheduler.pool_target_count` 鐜板湪鍙互鎺у埗 discovery pool 鏈熸湜淇濇湁鐨勫彲鎹㈠€欓€夋暟閲忥紝鍚庡彴鍒锋柊鍣ㄤ細鎸佺画琛ヨ揣鐩村埌姹犲瓙鎺ヨ繎鐩爣
- `runtime-status` 鏂板 `pool_available_count`銆乣pool_target_count`銆乣last_replenished_count`銆乣recent_pool_topics`
- popup 鎺ㄨ崘 tab 浼氬睍绀衡€滃綋鍓嶆睜瀛愰噷杩樻湁澶氬皯鏉″彲鎹?/ 鍒氳ˉ杩涘灏戞潯鏂扮殑 / 鏈€杩戜富瑕佸湪琛ヤ粈涔堚€?
- discovery pool 鏌ヨ鐜板湪浼氭帓闄ゅ凡缁忚繘鍏?`recommendations` 鐨勫唴瀹癸紝鍑忓皯鈥滄崲涓€鎵硅繕鏄€侀潰瀛斺€濈殑鎯呭喌

### 鎺ㄨ崘鍗＄墖灏侀潰灞曠ず 鈥?`extension/m108-cover-cards`

- `/api/recommendations` 涓?`/api/recommendations/reshuffle` 鐜板湪閮戒細杩斿洖 `cover_url`
- popup 鎺ㄨ崘鍗＄墖鍗囩骇涓衡€滃皝闈?+ 鏂囨湰淇℃伅 + 鎿嶄綔鍖衡€濈粨鏋勶紝鎹竴鎵规椂鍙互鐩存帴鍏堢湅灏侀潰鍐嶅喅瀹氱偣涓嶇偣
- 灏侀潰缂哄け鎴栧姞杞藉け璐ユ椂浼氬洖閫€鍒板崰浣嶆€侊紝涓嶅奖鍝嶆崲涓€鎵广€佹墦寮€瑙嗛鍜屽弽棣堟祦绋?

### 灏侀潰鍦板潃瑙勮寖鍖栦慨澶?鈥?`extension/m109-cover-normalization`

- popup 鐜板湪浼氭妸 `//i*.hdslb.com/...` 鍜?`http://i*.hdslb.com/...` 缁熶竴瑙勮寖鎴?`https://...`
- 淇浜嗛儴鍒嗘帹鑽愬崱鐗囧洜涓哄崗璁浉瀵瑰湴鍧€鎴栦笉瀹夊叏鍦板潃瀵艰嚧灏侀潰鍔犺浇澶辫触鐨勯棶棰?

### 鎻掍欢渚ц竟鏍忔ā寮?鈥?`extension-sidepanel`

- 鎵╁睍鍏ュ彛浠?`action.default_popup` 鍒囧埌 `side_panel.default_path`锛岀偣鍑绘墿灞曞浘鏍囨椂浼氫紭鍏堟墦寮€渚ц竟鏍?
- service worker 鏂板缁熶竴鐨勬墿灞?UI 鎵撳紑閾撅紝閫氱煡鍜岃鐭ユ彁閱掍篃浼氫紭鍏堟妸鐢ㄦ埛甯﹀洖鎻掍欢渚ц竟鏍忎笂涓嬫枃
- 鐜版湁 `popup/` 椤甸潰缁х画澶嶇敤锛屼絾甯冨眬宸蹭粠鍥哄畾灏忓脊绐楁敼鎴愭洿閫傚悎渚ц竟鏍忔祻瑙堢殑闀块〉闈㈠鍣?

### 鍊欓€夋睜鍗虫椂鎹竴鎵?鈥?`runtime/m106-pool-reshuffle`

- popup 鎺ㄨ崘 tab 鐜板凡浠庘€滅珛鍗冲埛鏂板畬鏁磋ˉ璐р€濇敼鎴愨€滄崲涓€鎵光€濓紝鐩存帴璋冪敤 `/api/recommendations/reshuffle`
- `content_cache` 鐜板湪浣滀负鐪熸鐨?discovery pool 浣跨敤锛屽€欓€夐」鏂板 `pool_status`銆乣recommended_at`銆乣feedback_type`銆乣feedback_at`
- `RecommendationEngine.reshuffle_recommendations()` 浼氱洿鎺ヤ粠姹犲瓙閲屾嫞涓€鎵?`fresh` 鍊欓€夛紝涓嶇瓑寰呭畬鏁?discover 瀹屾垚
- popup 灞曠ず鏂囨浼氫紭鍏堜娇鐢ㄥ€欓€夋睜鑷甫鐨?`relevance_reason`锛屾湅鍙嬪紡 `expression` 鎴愪负澧炲己灞傦紝涓嶅啀闃诲鍗虫椂鎹㈢墖

### Popup 鎵嬪姩鍒锋柊鎺ㄨ崘 鈥?`extension/m86-manual-refresh`

- popup 鎺ㄨ崘 tab 鏂板鈥滅珛鍗冲埛鏂扳€濇寜閽紝鐐瑰嚮鍚庝細璋冪敤 `/api/recommendations/refresh` 瑙﹀彂涓€娆″畬鏁磋ˉ璐?
- 鍒锋柊鏈熼棿鎸夐挳浼氳繘鍏モ€滄鍦ㄨˉ璐р€︹€濈姸鎬侊紝鎴愬姛鍚庣珛鍗抽噸鎷夎繍琛岀姸鎬佸拰鎺ㄨ崘鍒楄〃
- 鍒锋柊澶辫触鏃朵繚鐣欏綋鍓嶆帹鑽愶紝涓嶆竻绌哄唴瀹癸紝鍙粰鍑鸿交閲忛敊璇彁绀?
- 鍚庣画淇锛氭墜鍔ㄥ埛鏂扮幇鍦ㄨ蛋 `force_refresh()`锛屼笉浼氬啀鍥犱负 `below_threshold` 琚煭璺?

### 鍊欓€変緵缁欏崌绾?鈥?`candidate-supply`

- `ContentDiscoveryEngine` 鐜板湪閲囩敤鈥滀富鍙戠幇 + backfill鈥濅袱闃舵娴佺▼锛氫富鍊欓€変笉瓒虫椂浼氭墿鎼滅储銆佹斁瀹介珮绮惧害绛栫暐闃堝€硷紝骞朵粠鍘嗗彶缂撳瓨琛ラ綈鍒扮洰鏍囦笂闄?
- `content_cache` 鏂板 `relevance_score`銆乣relevance_reason`銆乣candidate_tier`锛岀紦瀛樺€欓€変笌瀹炴椂鍙戠幇鍊欓€夌粓浜庡叡浜悓涓€濂楄川閲忎俊鍙?
- `RecommendationEngine` 鍜?`Database.get_unrecommended_content()` 鐜板凡缁熶竴鎸?`candidate_tier -> relevance_score -> last_scored_at -> view_count` 鎺掑簭锛岄伩鍏嶇紦瀛樺洖璇婚€€鍖栨垚鍙湅鎾斁閲?

### Popup 鎵嬪姩鍒锋柊寮傛鍖?鈥?`runtime/m105-manual-refresh-async`

- `/api/recommendations/refresh` 鐜板湪鍙礋璐ｈЕ鍙戝悗鍙版墜鍔ㄨˉ璐т换鍔★紝绔嬪嵆杩斿洖鎺ュ彈缁撴灉
- `runtime-status` 鏂板 `manual_refresh_state` 鍜?`manual_refresh_message`锛宲opup 浼氳疆璇㈠悗鍙扮姸鎬侊紝鑰屼笉鏄悓姝ョ瓑寰呮暣杞ˉ璐?
- 鎵嬪姩鍒锋柊鏈熼棿 popup 缁х画淇濈暀褰撳墠鎺ㄨ崘鍒楄〃锛岀瓑鍚庡彴琛ヨ揣瀹屾垚鍚庡啀缁熶竴閲嶆媺鎺ㄨ崘

### Gemini 鍙€変緷璧栧鍏ヤ慨澶?鈥?`fix/gemini-optional-import`

- `google-genai` 缂哄け鏃讹紝`openbiliclaw.llm` 鍜?`openbiliclaw.llm.registry` 鐜板湪浠嶅彲姝ｅ父瀵煎叆锛屼笉鍐嶅洜涓?Gemini 椤跺眰渚濊禆闃诲鏁翠釜娴嬭瘯鏀堕泦
- 鍙湁鐪熸瀹炰緥鍖?`GeminiProvider` 鏃舵墠浼氭姏鍑烘槑纭敊璇紝鎻愮ず瀹夎 `google-genai`
- Gemini 鍔熻兘娴嬭瘯鏀逛负鈥滄湁 SDK 鎵嶈窇鍔熻兘锛屾棤 SDK 鍒欓獙璇佸弸濂介檷绾р€濓紝鎭㈠涓荤嚎娴嬭瘯鍙繍琛屾€?

### 鍏抽敭璁ょ煡鍙樺寲鎻愰啋 鈥?`runtime/m104-cognition-notify`

- 鏂板 `cognition_updates.json`锛岃褰曞叧閿鐭ュ彉鍖栥€佹潵婧愩€佺疆淇″害鍜屽凡閫氱煡鐘舵€?
- 鍙嶉鍒锋柊涓庤亰澶╁涔犻摼璺幇鍦ㄤ細鐢熸垚 `interest_added`銆乣dislike_added`銆乣profile_shift` 涓夌被璁ょ煡鍙樺寲
- 鏂板 `/api/cognition-updates/pending` 涓?`/api/cognition-updates/seen`锛屼緵鎻掍欢鎷夊彇骞剁‘璁よ鐭ユ彁閱?
- service worker 鐜板湪浼氬湪鎺ㄨ崘閫氱煡涔嬪悗妫€鏌ヨ鐭ュ彉鍖栭€氱煡锛沺opup 鈥滄垜鐨勭敾鍍忊€?tab 浼氬睍绀衡€滈樋B 鏈€杩戞柊璁颁綇浜嗕粈涔堚€?

### 鎸佺画鍊欓€夋睜鍒锋柊涓庨€氱煡 鈥?`runtime/m103-continuous-refresh-notify`

- 鏂板 `ContinuousRefreshController`锛屽湪鏈湴 API 杩愯鏃舵寜鈥滀簨浠惰Е鍙?+ 瀹氭椂淇濆簳鈥濇寔缁埛鏂板€欓€夋睜锛屽苟鍒嗗眰璋冨害 Search/Related銆乀rending銆丒xplore 绛栫暐
- 鏂板 `discovery_runtime.json`锛屾寔涔呭寲鏈€杩戝埛鏂版椂闂淬€佹渶杩戝鐞嗕簨浠?ID 鍜屾渶杩戦€氱煡鏃堕棿
- `content_cache` 鏂板 `last_scored_at`銆乣notification_sent`銆乣notified_at`锛岀敤浜庡€欓€変繚椴滃拰閫氱煡鍘婚噸
- 鏂板 `/api/runtime-status` 涓?`/api/notifications/pending`銆乣/api/notifications/sent`锛宲opup 鍜?service worker 鍙垎鍒鍙栬繍琛岀姸鎬併€佹媺鍙栧緟鍙戦€氱煡骞剁‘璁ら€佽揪
- popup 鐜板湪浼氬尯鍒嗏€滄湭鍒濆鍖?/ 姝ｅ湪琛ヨ揣 / 鎺ㄨ崘鍙敤鈥濅笁鎬侊紝service worker 浼氬楂樼疆淇′笖鏈€氱煡鐨勬帹鑽愯Е鍙戞祻瑙堝櫒閫氱煡骞跺洖鍐欏凡鍙戦€佺姸鎬?

### Gemini Provider 鏀寔 鈥?`gemini-provider`

- 鏂板 `GeminiProvider`锛屾寜 Gemini 瀹樻柟 quickstart 鎺ュ叆 `google-genai` SDK锛屾敮鎸佺粺涓€鐨勭┖鍝嶅簲鏍￠獙銆侀敊璇綊涓€鍖栧拰 usage 鏍囧噯鍖?
- 閰嶇疆灞傛柊澧?`[llm.gemini]`锛屾敮鎸?`api_key` 涓?`model`锛岄粯璁ゆā鍨嬩负 `gemini-2.5-flash`
- `LLMRegistry` 鐜板湪鍙互鑷姩娉ㄥ唽 `gemini`锛屽苟鍦?`config.toml` 缂?key 鏃跺洖閫€璇诲彇 `GOOGLE_API_KEY` / `GEMINI_API_KEY`
### B绔欏姩鎬佽姘斾紭鍖?鈥?`tone/m94-bilibili-tone`

- 鏂板 `ToneProfile` 娲剧敓灞傦紝浠庣敾鍍忋€佸亸濂芥憳瑕佸拰杩戞湡鍙嶉鎺ㄦ柇 `density / warmth / playfulness / directness`
- 鎺ㄨ崘琛ㄨ揪銆佺敾鍍忔€荤粨鍜岃亰澶?prompt 缁熶竴鎺ュ叆杩欏眰璇皵绯荤粺锛屽熀纭€椋庢牸鏀逛负鈥滆€丅鍙嬧€濓紝浣嗕細闅忕敤鎴风悊瑙ｉ€愭缁嗚皟
- 鎺ㄨ崘鐞嗙敱鍑忓皯绠楁硶瑙ｉ噴鑵旓紝鐢诲儚鍑忓皯蹇冪悊鎶ュ憡鎰燂紝鑱婂ぉ淇濈暀杩介棶鑳藉姏浣嗘洿鍍忔噦 B 绔欒澧冪殑鑰佹湅鍙?

### OpenRouter Provider 鏀寔 鈥?`llm/openrouter-provider`

- 鏂板 `OpenRouterProvider`锛岄€氳繃 OpenAI-compatible 璋冪敤閾炬帴鍏ョ粺涓€鐨勮秴鏃躲€侀噸璇曘€侀敊璇綊涓€鍖栧拰 JSON mode
- 閰嶇疆灞傛柊澧?`[llm.openrouter]`锛屾敮鎸?`api_key`銆乣model`銆乣base_url` 浠ュ強鍙€夎姹傚ご `http_referer` / `x_title`
- `LLMRegistry` 鐜板湪鍙互鑷姩娉ㄥ唽 `openrouter`锛屽苟鏀寔鎶婂畠璁句负榛樿 provider

### Popup UI 鍒锋柊 鈥?`extension/popup-ui-refresh`

- popup 浠庢繁鑹插伐鍏烽潰鏉块噸鏋勪负浜壊涓?tab 鍙戠幇椤碉紝椤堕儴閲囩敤 hero + inline 鐘舵€佸窘鏍囷紝鏁翠綋鏇磋创杩?B 绔欏唴瀹逛骇鍝佹皵璐?
- 鎺ㄨ崘鍗＄墖銆佺敾鍍忓崱鍜岃亰澶╁尯缁熶竴涓哄悓涓€濂楁祬鑹插崱鐗囩郴缁燂紝鎺ㄨ崘鍐呭鎴愪负 popup 棣栧睆鐨勪富瑕佽瑙夌劍鐐?
- 淇濇寔鐜版湁鎺ㄨ崘銆佸弽棣堛€佺敾鍍忋€佽亰澶╅€昏緫涓嶅彉锛屼粎鍒锋柊缁撴瀯銆佸眰绾т笌浜や簰鍙嶉锛沞xtension 娴嬭瘯銆乼ypecheck 鍜?build 鍧囧凡閫氳繃

### 9.3 鑱婂ぉ瀛︿範閾捐矾 鈥?`soul/m93-chat-learning`

- 鑱婂ぉ鐜板湪浼氳惤 `dialogue` 浜嬩欢锛屽苟棰濆鎻愬彇 `interest / dislike / goal / value / state` 绫诲瀷鐨勫€欓€夐暱鏈熺悊瑙ｄ俊鍙?
- 鏂板 `insight_candidates.json` 浣滀负涓棿鐘舵€侊紝鍏堢疮璁¤亰澶╁€欓€夛紝鍐嶇敱闃堝€兼帶鍒舵槸鍚﹁繘鍏ュ亸濂藉眰
- 鍙湁楂樼疆淇″害涓旈噸澶嶅嚭鐜扮殑鑱婂ぉ鍊欓€夋墠浼氶┍鍔ㄥ亸濂介噸鍒嗘瀽锛屽苟鍦ㄥ彉鍖栨槑鏄炬椂閲嶅缓鐢诲儚
- CLI `chat` 涓?popup 鈥滃拰闃緽鑱婅亰鈥?鐜板湪鍏辩敤杩欐潯瀛︿範閾撅紝浣嗕粛淇濇寔鍙楁帶鏇存柊锛屼笉浼氬洜涓哄崟杞璇濈珛鍗虫敼鍐欑敾鍍?

### 杩愯鏃?Cookie 鍥為€€淇 鈥?`main`

- 淇 `auth login` 涓庤繍琛屾椂鍛戒护鑴辫妭鐨勯棶棰橈細`init`銆佹祻瑙堝櫒闆嗘垚鍜屾湰鍦版湇鍔＄幇鍦ㄤ細浼樺厛浣跨敤鏄惧紡閰嶇疆 cookie锛岀暀绌烘椂鑷姩鍥為€€鍒?`data/bilibili_cookie.json`
- 鐢ㄦ埛瀹屾垚涓€娆?`auth login` 鍚庯紝涓嶅啀闇€瑕佹妸鍚屼竴浠?cookie 閲嶅鎶勮繘 `config.toml`
- 鏂板璁よ瘉娴嬭瘯锛岄攣瀹氭樉寮?cookie 浼樺厛绾у拰宸蹭繚瀛?cookie 鍥為€€琛屼负

### Popup 鐢诲儚 / 鑱婂ぉ椤电澧炲己 鈥?`extension/m84-popup-tabs`

- popup 鏂板 `鎺ㄨ崘 / 鎴戠殑鐢诲儚 / 鍜岄樋B鑱婅亰` 涓変釜 tab锛屾帹鑽愪笉鍐嶆槸鍞竴鍏ュ彛
- 鏂板 `/api/profile-summary` 鍜?`/api/chat`锛宲opup 鍙洿鎺ユ煡鐪嬭交閲忕敾鍍忔憳瑕佸苟鍙戣捣瀵硅瘽
- 鎺ㄨ崘鍗＄墖浜や簰宸叉敹鍙ｄ负鏄惧紡鎵撳紑瑙嗛锛屼笉鍐嶅洜涓?`鍠滄 / 涓嶅枩娆?/ 鍐欎竴鍙 鎴栬緭鍏ユ鐐瑰嚮璇烦杞?
- popup 鍐呯殑鎺ㄨ崘鍙嶉銆佺敾鍍忔煡鐪嬪拰鑱婂ぉ鐜板湪鍏辩敤鍚屼竴濂楁湰鍦板悗绔繛鎺ョ姸鎬?

### 9.2 鐢诲儚鏇存柊 鈥?`feedback/m92-profile-refresh`

- 鏂板 `feedback_state.json`锛岃褰曞弽棣堥噸鍒嗘瀽澶勭悊娓告爣鍜屾渶杩戜竴娆″鐞嗘椂闂?
- 鍙嶉绱杈惧埌闃堝€煎悗锛屼細鑷姩瑙﹀彂鍋忓ソ灞傞噸鏂板垎鏋?
- 褰撻珮鏉冮噸鍏磋叮鎴栦笉鍠滄涓婚鍙樺寲鏄庢樉鏃讹紝浼氳嚜鍔ㄩ噸寤哄苟鎸佷箙鍖?`soul.json`
- CLI `feedback` 涓?API `/api/feedback` 鍦ㄥ弽棣堟垚鍔熷悗閮戒細鍚屾瑙﹀彂杩欐潯鏇存柊閾?

### 9.1 鍙嶉澶勭悊 鈥?`feedback/m91-processing`

- CLI `feedback` 鍛戒护鎵╁睍涓烘敮鎸?`like / dislike / comment`锛屽叾涓?`comment` 蹇呴』甯?`--note`
- 鏂板 `POST /api/feedback`锛岀粺涓€鏍￠獙鎺ㄨ崘瀛樺湪鎬с€佹洿鏂板弽棣堝瓧娈靛苟杩藉姞 `feedback` 浜嬩欢
- popup 鐨?`鍠滄 / 涓嶅枩娆?/ 鍐欎竴鍙 宸叉帴閫氱湡瀹炲悗绔紝鎻愪氦鍚庝細绔嬪嵆鍐欏洖鎺ㄨ崘璁板綍
- `9.1` 鐨勫弽棣堝啓鍏ラ摼璺幇宸插湪 CLI銆丄PI銆乸opup 涓夌缁熶竴

### 8.3 Popup 鈥?`extension/m83-popup`

- popup 浠庡崰浣嶉〉鍗囩骇涓虹湡瀹為潰鏉匡細鏄剧ず鍚庣杩炴帴鐘舵€佸拰鏈€鏂版帹鑽愬垪琛?
- 鏂板 popup helper锛岀粺涓€澶勭悊鎺ㄨ崘瀛楁 fallback銆乸opup 鐘舵€佸垽鏂拰 B 绔欒棰?URL 鏋勯€?
- 鐐瑰嚮鎺ㄨ崘鍗＄墖鎴栤€滄墦寮€瑙嗛鈥濇寜閽細鐩存帴璺宠浆鍒板搴?B 绔欒棰戦〉
- `鍠滄 / 涓嶅枩娆 鎸夐挳鏈疆鍏堜繚鐣?UI 鍗犱綅锛屽悗绔弽棣堝啓鍥炵暀缁欏悗缁换鍔?

### 8.1 琛屼负閲囬泦 鈥?`extension/m81-behavior-collection`

- `collector.ts` 浠庢渶灏?click/search 閲囬泦鍗囩骇涓哄琛屼负閲囬泦锛氱偣鍑汇€佹悳绱€侀〉闈㈠揩鐓с€佽棰?`view/pause/seek`銆乭over銆乻croll锛屼互鍙婅瘎璁?鐐硅禐/鎶曞竵/鏀惰棌鎰忓浘浜嬩欢
- 琛ラ綈 SPA 瀵艰埅鎰熺煡锛氬寘瑁?`history.pushState` / `replaceState` 骞剁洃鍚?`popstate`锛屽湪 URL 鍙樺寲鏃堕噸鏂板彂閫?`snapshot` 骞堕噸缁戦〉闈㈢洃鍚?
- 鏂板绾€昏緫 helper 鍜?Node 鍐呯疆娴嬭瘯锛岃鐩栭〉闈㈣瘑鍒€丅V 鎻愬彇銆佸姩浣滆瘑鍒€佺紦鍐插幓閲嶄笌寮轰俊鍙?flush 鍒ゆ柇
- `service-worker.ts` 鏀逛负甯﹀幓閲嶅拰澶辫触鍥炲～鐨勭紦鍐插彂閫佸櫒锛屽苟浣跨敤 `chrome.alarms` 浠ｆ浛鑴嗗急鐨?`setInterval`
- 鏂板 `extension/package.json`锛屾彁渚?`npm test`銆乣npm run typecheck`銆乣npm run build`锛岃鎻掍欢渚у叿澶囨渶灏忓彲楠岃瘉鏋勫缓閾捐矾
- 鑱旇皟淇锛氳ˉ榻?manifest 鍥炬爣璧勬簮锛屽苟鎶婅繍琛屾椂鑴氭湰鏀逛负 `esbuild` bundle 鍗曟枃浠讹紝瑙ｅ喅 Chrome content script / service worker 鐨勭湡瀹炲姞杞藉け璐?

### 8.2 鍚庣 API 鈥?`api/m82-backend-api`

- 鏂板 FastAPI 搴旂敤锛屾彁渚?`GET /api/health`銆乣POST /api/events`銆乣GET /api/recommendations`
- 鎻掍欢涓婃姤鐨勮涓轰簨浠朵細鏄犲皠鍒拌蹇嗙郴缁熶簨浠跺眰锛屽苟鍐欏叆 SQLite `events` 琛?
- 鎺ㄨ崘鎺ュ彛浼氳繑鍥炴帹鑽?ID銆丅V 鍙枫€佹爣棰樸€乁P 涓汇€佹帹鑽愭枃妗堜笌灞曠ず鐘舵€侊紝渚涙彃浠?popup 浣跨敤
- CLI `openbiliclaw start` 浠?stub 鍗囩骇涓虹湡瀹炴湰鍦?API 鏈嶅姟鍚姩鍏ュ彛锛岄粯璁ょ洃鍚?`127.0.0.1:8420`
- 鑱旇皟淇锛欰PI 鐜板凡鏀寔 extension 棰勬璇锋眰锛圕ORS锛夛紝骞舵妸 `/api/events` 鏀逛负 async 澶勭悊锛岄伩鍏?SQLite 绾跨▼閿欒

## M5: 鍐呭鍙戠幇寮曟搸锛堣繘琛屼腑锛?

## M7: CLI 浣撻獙 鉁?

### 7.1 chat 鍛戒护琛ュ钩 鈥?`cli/m71-chat-command`

- `openbiliclaw chat` 浠?stub 鍗囩骇涓轰氦浜掑紡 REPL锛屽鎺?`SocraticDialogue`
- 鏀寔澶氳疆瀵硅瘽锛岃緭鍏?`exit` / `quit` / 绌鸿鍗冲彲姝ｅ父缁撴潫
- 鏂板 CLI 娴嬭瘯锛岃鐩栫敾鍍忕己澶便€佸崟杞洖澶嶅拰閫€鍑鸿矾寰?

### 7.1 discover 鍛戒护琛ュ钩 鈥?`cli/m71-discover-command`

- `openbiliclaw discover` 浠?stub 鍗囩骇涓虹湡瀹炲懡浠わ細璇诲彇鐢诲儚銆佹墽琛?discovery engine銆佸睍绀哄彂鐜版憳瑕佷笌鍓?5 鏉￠瑙?
- 鍙戠幇缁撴灉缁х画鐢?`ContentDiscoveryEngine` 鍐欏叆 `content_cache`锛孋LI 鍙礋璐ｇ紪鎺掑拰灞曠ず
- 鏂板 CLI 娴嬭瘯锛岃鐩栫敾鍍忕己澶便€佺┖鍙戠幇缁撴灉鍜屾垚鍔熼瑙堜笁鏉′富璺緞

### 7.2 杈撳嚭鏍煎紡 鈥?`cli/m72-output-format`

- `cli.py` 鎶藉嚭缁熶竴 Rich 娓叉煋 helper锛氶〉闈㈡爣棰樸€佺姸鎬侀潰鏉裤€侀敭鍊艰〃銆佸崰浣嶆€併€佹帹鑽愬崱鐗?
- `init` / `profile` / `recommend` / `feedback` / `config-show` / `auth status` / `health-check` / `browser` 鍛戒护鍏ㄩ儴鍒囧埌缁熶竴灞曠ず椋庢牸
- `start` / `discover` / `chat` 鐨?stub 杈撳嚭缁熶竴鎴愨€滃紑鍙戜腑鈥濆崰浣嶆€侊紝骞堕檮涓嬩竴姝ユ彁绀?
- CLI 娴嬭瘯琛ュ厖杈撳嚭缁撴瀯鏂█锛岃鐩栫敾鍍忓垎鍖恒€佹帹鑽愬崱鐗囥€佸垵濮嬪寲鎽樿鍜岀姸鎬侀潰鏉胯涔?

### 5.6 鍙戠幇寮曟搸缂栨帓 鈥?`discovery/m56-engine-orchestration`

- `ContentDiscoveryEngine.discover()` 鏀逛负骞跺彂鎵ц澶氫釜 discovery strategy锛屽崟涓瓥鐣ュけ璐ヤ笉浼氫腑鏂暣浣撳彂鐜板懆鏈?
- 寮曟搸灞傚閲嶅 `bvid` 杩涜鍚堝苟锛屼繚鐣欐洿楂?`relevance_score` 鐨勭増鏈?
- 鏂板 `Database.get_cached_content()`锛屽苟鍦ㄥ彂鐜板畬鎴愬悗鎶婃渶缁堢粨鏋滃啓鍏?`content_cache`
- `evaluate_content()` 鐘舵€佸悓姝ユ敹鍙ｅ埌 `5.5`锛氬凡琚?Search / Trending / RelatedChain / Explore 澶嶇敤
- 鏂板 discovery/storage 娴嬭瘯锛岃鐩栧苟鍙戠紪鎺掋€佸け璐ュ閿欍€侀珮鍒嗗幓閲嶅拰缂撳瓨鍐欏叆璇诲洖

### 5.4 璺ㄩ鍩熸帰绱㈢瓥鐣?鈥?`discovery/m54-explore-strategy`

- `ExploreStrategy` 浠庣┖澹冲崌绾т负鍙繍琛岀瓥鐣ワ細鍏堢敓鎴愨€滈珮鐩稿叧浣嗘湁闄岀敓鎰熲€濈殑鎺㈢储棰嗗煙锛屽啀璋冪敤 B 绔欐悳绱?
- 鏂板缁撴瀯鍖?exploration prompt锛岃姹傝緭鍑?`domain` / `why_it_might_resonate` / `novelty_level` / `queries`
- 鏈湴杩囨护涓庣幇鏈夐珮鏉冮噸鍏磋叮杩囪繎鐨勯鍩燂紝閬垮厤鈥滄崲鐨悳绱⑩€?
- 鎼滅储鍊欓€夌粺涓€澶嶇敤 `ContentDiscoveryEngine.evaluate_content()`锛屽苟鍙犲姞鍩轰簬 `novelty_level` 涓?`exploration_openness` 鐨?exploration bonus
- 鏂板 explore 娴嬭瘯锛岃鐩栭鍩熻繃婊ゃ€乥onus銆佺敓鏁堥槇鍊笺€侀儴鍒嗗け璐ュ閿欏拰 engine 娉ㄥ唽杩愯

### 5.3 鐩稿叧鎺ㄨ崘閾剧瓥鐣?鈥?`discovery/m53-related-chain`

- `RelatedChainStrategy` 浠庣┖澹冲崌绾т负鍙繍琛岀瓥鐣ワ細浼樺厛浠庝簨浠跺眰涓殑 `view` / `favorite` / `like` 瑙嗛鎸戦€夌瀛?
- 绉嶅瓙涓嶈冻鏃讹紝鍏堢敤鍋忓ソ鏍囩鍜屽父鐪?UP 涓诲仛灏忚寖鍥存悳绱㈣ˉ绉嶅瓙锛屽啀鍥為€€鍒?Search/Trending 鐨勯珮鍒嗙粨鏋?
- 瀵规瘡涓瀛愯皟鐢?`get_related_videos()`锛屾部鐩稿叧鎺ㄨ崘閾炬渶澶氭墿灞?2 灞傦紝骞跺叏灞€鎸?`bvid` 鍘婚噸
- 缁熶竴澶嶇敤 `ContentDiscoveryEngine.evaluate_content()` 瀵圭浉鍏虫帹鑽愬€欓€夋墦鍒嗭紝骞舵寜闃堝€艰繃婊?
- 鏂板 related-chain 娴嬭瘯锛岃鐩栦簨浠剁瀛愪紭鍏堛€乫allback銆佷簩灞傛墿灞曘€佸幓閲嶃€佸け璐ュ閿欏拰 engine 娉ㄥ唽杩愯

### 5.2 鎺掕姒滅瓥鐣?鈥?`discovery/m52-trending-strategy`

- `TrendingStrategy` 浠庣┖澹冲崌绾т负鍙繍琛岀瓥鐣ワ細鎷夊彇鍏ㄧ珯姒?`rid=0` 鍜岀浉鍏冲垎鍖烘锛屽苟鎸?`bvid` 鍘婚噸
- 鏂板缁撴瀯鍖栧垎鍖洪€夋嫨 prompt锛岀粺涓€閫氳繃 `LLMService.complete_structured_task()` 閫夋嫨棰濆 `rid`
- `ContentDiscoveryEngine.evaluate_content()` 鐜板凡瀹炵幇锛氱敤 LLM 杈撳嚭 `score/reason` 骞跺啓鍥?`DiscoveredContent`
- `TrendingStrategy` 瀵规瘡鏉℃鍗曞唴瀹规墽琛岀浉鍏虫€ц瘎浼帮紝鍙繚鐣欓珮浜庨槇鍊肩殑缁撴灉
- 鏂板 discovery 灞傛祴璇曪紝瑕嗙洊鍒嗗尯閫夋嫨銆侀槇鍊艰繃婊ゃ€佸崟姒滃崟澶辫触涓嶄腑鏂拰鍐呭璇勪及鍐欏洖

### 5.1 鎼滅储绛栫暐 鈥?`discovery/m51-search-strategy`

- `SearchStrategy` 浠庣┖澹冲崌绾т负鍙繍琛岀瓥鐣ワ細鍩轰簬鐢诲儚鐢熸垚鎼滅储璇嶃€佽皟鐢?B 绔欐悳绱㈠苟杩斿洖 `DiscoveredContent`
- 鏂板缁撴瀯鍖栨悳绱?query prompt锛岀粺涓€閫氳繃 `LLMService.complete_structured_task()` 鐢熸垚 5 鍒?10 涓?B 绔欐悳绱㈣瘝
- 澧炲姞鏈湴 fallback query 鐢熸垚锛氬綋 LLM 杩斿洖鍧?JSON 鎴栫┖缁撴灉鏃讹紝浠庡叴瓒ｆ爣绛惧拰鏍稿績鐗硅川鍥為€€
- 瀵硅法 query 鎼滅储缁撴灉鎸?`bvid` 鍘婚噸锛屽苟鏄犲皠 `title` / `up_name` / `cover_url` / `duration` / `view_count` / `description`
- 鏂板 discovery 灞傛祴璇曪紝瑕嗙洊 query 鐢熸垚銆乫allback銆佸崟 query 澶辫触涓嶄腑鏂拰 engine 娉ㄥ唽杩愯

## M4: 璁板繂绯荤粺锛堣繘琛屼腑锛?

### 4.5 鏍稿績璁板繂鍔犺浇 鈥?`memory/m45-core-memory`

- `MemoryManager.get_core_memory()` 浠庡師濮嬪眰鏁版嵁鏀逛负绋冲畾瑁佸壀鎽樿锛岀粺涓€杈撳嚭 `soul_summary` / `preference_summary` / `recent_awareness` / `active_insights`
- `MemoryManager.render_core_memory_prompt()` 鏀逛负鍥哄畾鍖哄潡娓叉煋锛氱敤鎴风敾鍍忋€佸亸濂芥憳瑕併€佽繎鏈熻瀵熴€佸綋鍓嶆礊瀵?
- `LLMService` 鏂板 `complete_with_core_memory()` / `complete_structured_task()`锛岀粺涓€鑷姩娉ㄥ叆 core memory
- `ProfileBuilder`銆乣PreferenceAnalyzer`銆乣AwarenessAnalyzer`銆乣InsightAnalyzer` 杩愯鏃跺叏閮ㄦ敼璧扮粺涓€ service 娉ㄥ叆璺緞
- `SoulEngine` 鐜板湪鍐呯疆 `LLMService`锛屼繚璇佺敾鍍忋€佸亸濂姐€佽瀵熴€佹礊瀵熼摼璺兘鑳藉叡浜悓涓€浠芥牳蹇冭蹇嗕笂涓嬫枃
- 鍚庣画鏀跺彛淇宸茬Щ闄や笂杩?4 涓ā鍧楀鍘熷 `registry.complete(..., json_mode=True)` 鐨?fallback锛宑ore memory 娉ㄥ叆鐜板湪鏄己绾︽潫鑰岄潪榛樿璺緞

### 4.4 瑙夊療灞備笌娲炲療灞?鈥?`memory/m44-awareness-insight`

- 鏂板 `AwarenessAnalyzer`锛氳繎鏈熶簨浠?-> `AwarenessNote`锛屾敮鎸佸潖 JSON 淇濇姢鍜屽悓鏃ュ幓閲?
- 鏂板 `InsightAnalyzer`锛氳瀵?+ 鍋忓ソ + 鐢诲儚 -> `InsightHypothesis`锛屾敮鎸佸亣璁惧悎骞朵笌璇佹嵁鍘婚噸
- `SoulEngine.generate_awareness_note()` / `generate_insight()` 瀵规帴 analyzer锛屽苟鎸佷箙鍖栧埌 `awareness.json` / `insight.json`
- `SoulEngine.update_from_feedback()` 鐜板湪浼氬啓鍏?`feedback` 浜嬩欢锛屽苟鏇存柊鍖归厤娲炲療鐨?`validated` / `confidence`

### 4.3 鐏甸瓊灞?鈥?`memory/m43-soul-layer`

- 鏂板 `ProfileBuilder`锛氱粨鏋勫寲鐢诲儚 prompt銆丣SON 鏍￠獙鍜?`SoulProfile` 鏋勫缓
- `SoulEngine.build_initial_profile()` 浠?history + preference 鐢熸垚鍒濆鐢诲儚骞舵寔涔呭寲鍒?`data/memory/soul.json`
- `SoulEngine.get_profile()` 鏀寔璇诲彇宸蹭繚瀛樼敾鍍忥紝鏈垵濮嬪寲鏃舵姏 `SoulProfileNotInitializedError`
- `SoulProfile` 澧炲姞 `to_dict()` / `from_dict()` 鍙婂亸濂藉眰搴忓垪鍖栬緟鍔?
- CLI `profile` 鍛戒护浠?stub 鍗囩骇涓虹湡瀹炲睍绀猴紝缂哄け鐢诲儚鏃舵彁绀哄悗缁墽琛?`openbiliclaw init`

### 4.2 鍋忓ソ灞?鈥?`memory/m42-preference-layer`

- 鏂板 `PreferenceAnalyzer`锛歀LM structured extraction + JSON 瑙ｆ瀽 + 鍏磋叮鍚堝苟
- 鏂板 `build_preference_analysis_prompt()`锛氱粨鏋勫寲鍋忓ソ鎻愬彇 prompt
- `SoulEngine.analyze_events()` 瀵规帴 `PreferenceAnalyzer`锛屽亸濂芥寔涔呭寲鍒?JSON
- 鍏磋叮鏍囩甯︽椂闂磋“鍑忥紙`decay_factor_per_week=0.9`锛夊拰鏈€浣庢潈閲嶈繃婊?

### 4.1 浜嬩欢灞?鈥?`memory/m41-event-layer`

- `Database` 鏂板 `query_events()` 鍜?`count_events_by_type()` 
- `MemoryManager.propagate_event()` 浠?stub 鏀逛负 SQLite 鎸佷箙鍖?
- 浜嬩欢绫诲瀷鏋氫妇锛歚view`, `search`, `favorite`, `like`, `comment`, `click`, `feedback`
- 鏂板 `MemoryManager.query_events()` 鍜?`get_event_stats()` 濮旀墭鏂规硶

---

## M6: 鎺ㄨ崘寮曟搸锛堣繘琛屼腑锛?

### 6.3 鎺ㄨ崘鎸佷箙鍖?鈥?`recommendation/m63-persistence`

- `recommendations` 琛ㄨˉ榻愮粨鏋勫寲鍙嶉瀛楁锛歚feedback_type`銆乣feedback_note`銆乣feedback_at`
- 鏂板 `Database.get_recommendation_by_id()` 鍜?`update_recommendation_feedback()`锛屾敮鎸佹帹鑽愬弽棣堣鍐?
- `RecommendationEngine` 鏂板 `record_feedback()` / `get_recommendation()` 鍏ュ彛
- CLI 鏂板 `feedback <id> <like|dislike> [--note ...]`锛屾垚鍔熷悗浼氬悓姝ュ啓鍏ヤ竴鏉?`feedback` 浜嬩欢
- 鏂板 recommendation/storage/cli 娴嬭瘯锛岃鐩栧弽棣堟寔涔呭寲銆佷簨浠跺啓鍏ュ拰涓嶅瓨鍦ㄦ帹鑽愮殑閿欒璺緞

## M7: CLI 浜や粯锛堣繘琛屼腑锛?

### 7.1 鏍稿績鍛戒护 `init` 鈥?`cli/m71-init`

- 鏂板 `openbiliclaw init`锛屾墦閫氶娆¤繍琛岄摼璺細璁よ瘉妫€鏌ャ€佸巻鍙叉媺鍙栥€佷簨浠跺鍏ャ€佸亸濂藉垎鏋愩€佺敾鍍忕敓鎴愩€佽嚜鍔?discover
- 鏂板 `_build_bilibili_client()`銆乣_build_discovery_engine()` 鍜?`_history_item_to_event()`锛屾妸 CLI 缂栨帓杈圭晫鍥哄畾涓嬫潵
- `init` 鏀寔闃舵鎬ц繘搴﹁緭鍑猴紝骞跺湪 discover 澶辫触鏃剁粰鍑衡€滈儴鍒嗗畬鎴愨€濇彁绀猴紝涓嶄涪寮冨凡鐢熸垚鐨勭敾鍍?
- 鏂板 CLI 娴嬭瘯锛岃鐩栬璇佸け璐ャ€佸巻鍙蹭负绌恒€佸叏娴佺▼鎴愬姛鍜?discover 閮ㄥ垎澶辫触

### 6.2 鏈嬪弸寮忔帹鑽愯〃杈?鈥?`recommendation/m62-expression`

- `RecommendationEngine.generate_expression()` 浠?stub 鍗囩骇涓虹粨鏋勫寲 LLM 璋冪敤锛岃緭鍑?`expression` 鍜?`topic_label`
- `generate_recommendations()` 鐜板湪浼氫负姣忔潯鎺ㄨ崘琛ュ叏鏈嬪弸寮忔枃妗堬紝骞跺洖鍐欏埌 `recommendations` 琛?
- 鏂板 `Database.update_recommendation_content()` 鍜?`mark_recommendations_presented()`锛屾墦閫氭帹鑽愭枃妗堟洿鏂颁笌灞曠ず鐘舵€佹洿鏂?
- CLI `recommend` 浠?stub 鍗囩骇涓虹湡瀹炲睍绀哄叆鍙ｏ紝浼氳鍙栫敤鎴风敾鍍忋€佺敓鎴愭帹鑽愬苟鍦ㄨ緭鍑哄悗鏍囪宸插睍绀?
- 鏂板 recommendation/storage/cli 娴嬭瘯锛岃鐩栨枃妗堢敓鎴愩€佹帹鑽愬巻鍙插洖鍐欏拰灞曠ず鍚庣姸鎬佹洿鏂?

### 6.1 鎺ㄨ崘鎺掑簭 鈥?`recommendation/m61-ranking`

- `RecommendationEngine.generate_recommendations()` 浠?stub 鍗囩骇涓哄彲杩愯鎺掑簭鍏ュ彛
- 鏀寔涓ょ鏉ユ簮锛氭樉寮忎紶鍏?`discovered`锛屾垨鐩存帴浠?`content_cache` 璇诲彇鏈帹鑽愬唴瀹?
- 鏂板 `Database.get_unrecommended_content()`銆乣insert_recommendation()`銆乣get_recommendations()`
- 姣忔鐢熸垚鎺ㄨ崘鍚庯紝绔嬪嵆鍐欏叆鏈€灏忔帹鑽愬巻鍙茶褰曪紝閬垮厤涓嬩竴鎵归噸澶嶉€変腑鍚屼竴鍐呭
- 鏂板 recommendation/storage 娴嬭瘯锛岃鐩栨帓搴忋€佺紦瀛樿鍙栧拰鍘婚噸闂幆

## M3: Bilibili 鎺ュ叆灞?鉁?

### 3.3 agent-browser 闆嗘垚 鈥?`bili/m33-agent-browser`

- `BilibiliBrowser` 閲嶅啓锛歚BrowserCommandError` 寮傚父 + `open` 鈫?`snapshot -i --json` 娴佺▼
- CLI 鏂板 `browser status` / `browser open` / `browser content` 鍛戒护
- `is_available` 妫€娴?+ 瀹樻柟瀹夎鎻愮ず

### 3.2 鏍稿績 API 鈥?`bili/m32-core-api`

- `BilibiliAPIClient` 鏂板缁熶竴璇锋眰鍔╂墜 `_get_json()` + 杞婚噺闄愭祦 `_respect_rate_limit()`
- 鏂板 cursor-based `get_user_history(max_items=200)`
- 鏂板 `get_favorite_folders()` / `get_all_favorites()` 甯﹂绠楁帶鍒?
- 鏂板 `get_following()` / `get_video_comments()`
- 鏂板 `FavoriteFolder`, `FavoriteFolderWithItems`, `FollowingUser`, `CommentInfo` 鏁版嵁缁撴瀯
- 鏂板闆嗘垚娴嬭瘯楠ㄦ灦 `@pytest.mark.integration`

### 3.1 Cookie 璁よ瘉 鈥?`bili/m31-cookie-auth`

- `AuthManager`锛歝ookie 鎸佷箙鍖?+ nav API 楠岃瘉 + `SupportsNavClient` Protocol DI
- `BilibiliAPIClient.get_nav_info()`锛氳В鏋?`/x/web-interface/nav`
- CLI 鏂板 `auth login`锛堜氦浜掑紡 + `--cookie`锛夊拰 `auth status`

---

## M2: LLM 澶氭ā鍨嬫敮鎸?鉁?

### 2.3 Prompt 绠＄悊涓?LLM Service 鈥?`llm/m23-prompt-management`

- 鏂板 `prompts.py`锛歋ocratic 瀵硅瘽 prompt 鏋勫缓 + core memory 娉ㄥ叆
- 鏂板 `service.py`锛歚LLMService` 闂ㄩ潰锛坧rompt 缁勮 + registry 璋冪敤 + 绌哄搷搴旀牎楠岋級
- 鏂板 `MemoryManager.render_core_memory_prompt()`
- `SocraticDialogue.respond()` 瀵规帴 LLMService锛屾浛鎹?TODO stub

### 2.2 Provider Registry 鈥?`llm/m22-registry`

- 鏂板 `build_llm_registry()`锛氫粠 Config 鑷姩鏋勫缓 + provider fallback
- `LLMRegistry.complete()`锛歴equential fallback锛宍LLMResponseError` 涓嶈Е鍙?fallback
- CLI 鏂板 `health-check` 鍛戒护 + `config-show` 鏄剧ず宸叉敞鍐?provider

### 2.1 Provider 瀹炵幇 鈥?`llm/m21-providers`

- 鏂板缁熶竴寮傚父灞傜骇锛歚LLMProviderError` 鈫?`LLMRateLimitError` / `LLMTimeoutError` / `LLMResponseError`
- `OpenAIProvider` / `ClaudeProvider`锛歳etry + 瓒呮椂鏄犲皠 + 绌哄搷搴斾繚鎶?
- 鏂板 `OllamaProvider`锛堟湰鍦?LLM锛?
- 鏂板 `DeepSeekProvider`锛堢户鎵?OpenAI锛?

---

## M1: 鍩虹璁炬柦 鉁?

### 1.3 鏃ュ織绯荤粺 鈥?`infra/m13-logging-system`

- 鏂板 `logging_setup.py`锛歊ich 鎺у埗鍙?+ 鏂囦欢 handler锛岄槻閲嶅鍒濆鍖?
- `LoggingConfig`锛歭evel / file_level / directory / filename
- CLI 鍏ㄥ眬 `--log-level` 閫夐」

### 1.2 閰嶇疆绯荤粺 鈥?`infra/m12-config-system`

- `config.py` 澧炲己锛歚ConfigError` / `ConfigDiagnostics` / 涓ユ牸鏍￠獙
- CLI `config-show` 鏄剧ず閰嶇疆 + 寮曞鎻愮ず
- `config.example.toml` 瀹屾暣娉ㄩ噴

### 1.1 寮€鍙戠幆澧冨拰 CI 鈥?`infra-m1`

- Ruff + MyPy + Pytest 璐ㄩ噺闂ㄧ
- GitHub Actions CI 宸ヤ綔娴?
- `tomllib` 閰嶇疆鍔犺浇
