# 本地 SDK → 主仓 Action（2026-11-25，未发布）

在 Intel macOS 用私库 `b2cc3f1`、Core `740e37eb` 的正式 wrap/build/manifest 代码及独立测试签名根，实际交叉编译五个平台。Linux x86_64 和 aarch64 是 ELF，最大要求的 GLIBC 版本不超过 2.35；两个 Mac 是对应架构的 Mach-O；Windows x64 是 LLVM-MinGW 的 PE32+。五个归档都以测试公钥验签，Intel 本机执行原生 `--check-policy` 成功。没有用测试材料冒充生产包，也没有在异构 OS 上运行这五个测试包。

公共交付入口使用真 Git 裸仓往返：错误来源和篡改失败，不写远端；重复交付同一份字节只产生同一输入提交；额外的私有文件不会被提交。实际从公共仓 raw 地址下载了一个固定 SHA 的现有文件并比对本地 Git 摘要；没有推送 fixture 二进制。发布、交付、安装、续期、策略测试共 119 项通过，Ruff、MyPy（419 文件）通过。Apple 公证账号的只读 preflight 成功，不等于生产 SDK 已公证。

生产验收顺序：本地正式凭据编译 → 两个 Mac 获得 Accepted → `handoff_local_sdk.py --push` → 审查并提交新 catalog → 主仓五平台原生自检与 Docker 验收 → 干净新装、关闭其他 provider 回退、真实 SiliconFlow 请求 → 正常打包发版。此流程不依赖私有仓库 Actions。
