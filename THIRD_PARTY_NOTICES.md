# 第三方组件与许可说明

Chaos 的原创部分适用根目录 [LICENSE](LICENSE) 中的自定义“源码可用、禁止 AI 训练”许可。**该限制不改变第三方组件自身的许可，也不能撤销上游已授予的权利。** 原创贡献与第三方派生部分同时存在时，应分别遵守适用条件，不能把整个文件一概宣称为 Chaos 独占作品。

## 随仓库分发的第三方内容

| 组件与固定来源 | 仓库中的位置 | 适用许可与通知 |
| --- | --- | --- |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness/tree/5badb15009ae1756c3afe0ae0cef1faafc290ccc)，提交 `5badb15009ae1756c3afe0ae0cef1faafc290ccc` | `vendor/deepseek/upstream/` 中保留的源码；`chaos/plugins.py` 与 `web/dsh-state.js` 中标明来源的派生部分 | MIT，Copyright (c) 2026 DeepSeek；见 [原许可](vendor/deepseek/LICENSE)、[来源与改动说明](vendor/deepseek/UPSTREAM.md)、[逐文件摘要](vendor/deepseek/manifest.json) |
| DeepSeek 引入的 [Cordis](https://github.com/deepseek-ai/deepseek-harness/tree/5badb15009ae1756c3afe0ae0cef1faafc290ccc/vendor/cordis) | `vendor/deepseek/upstream/vendor/cordis/`，以及 `chaos/plugins.py` 中的相应派生部分 | MIT，Copyright (c) 2021-present Shigma；见 [原许可](vendor/deepseek/upstream/vendor/cordis/LICENSE) |
| [Thesys OpenUI](https://github.com/thesysdev/openui/tree/9c5c43e78b95b69a2220c9f0d4bc4919d29067c9) | `vendor/openui/reference-source/`；官方 npm `@openuidev/browser-bundle@0.1.7` 的 `web/vendor/openui-bundle.min.js` | MIT，Copyright (c) 2011-2024 Thesys Inc.；见 [原许可](vendor/openui/LICENSE)及 [来源摘要](vendor/openui/provenance.json)。参考提交和 npm 发行包是两个分别固定的来源，不宣称其构建完全相同 |
| OpenUI 浏览器包包含的依赖 | `web/vendor/openui-bundle.min.js`；包括 React、React DOM、Lucide、TanStack 等 | 各自保留 MIT、ISC、BSD 等上游许可；见 [依赖许可文本](vendor/openui/THIRD_PARTY_LICENSES.txt)及 [许可来源清单](vendor/openui/license-provenance.json)。Lucide 包含 Feather 的 MIT 部分，不能全部简写为 OpenUI MIT |
| CodeMirror 6 与 Lezer 及其依赖 | `web/vendor/codemirror.js` | MIT；完整文本与版权在 [web/vendor/LICENSES.txt](web/vendor/LICENSES.txt)，版本与完整性摘要在 [package-lock.json](web/vendor/package-lock.json) |
| esbuild 构建工具 | `web/vendor/build.cjs` 引用、`web/vendor/package-lock.json` 固定；发行包不包含 esbuild 可执行文件 | MIT，Copyright (c) 2020 Evan Wallace；通知保留于 [web/vendor/LICENSES.txt](web/vendor/LICENSES.txt) |

CodeMirror 通知覆盖 `@codemirror/autocomplete`、`commands`、`language`、`lint`、`search`、`state`、`view`，以及 `@lezer/common`、`highlight`、`lr`、`@marijn/find-cluster-break`、`crelt`、`style-mod`、`w3c-keyname`。`entry.js`、`build.cjs` 是 Chaos 的集成文件，不能据此把生成包中的上游实现改授 Chaos 许可。

OpenUI 依赖许可清单采用固定参考提交的生产依赖闭包及官方 npm 包中的原始许可证，保守保留可能被构建优化移除的依赖通知。该清单不是官方发行包逐模块构建证明；官方包没有随本项目提供构建 metafile 或 source map，不能把参考 lockfile 当成精确二进制 SBOM。未修改官方 bundle，保留其内嵌许可注释。升级该 bundle 时必须重新核对版本、摘要与所附通知。

## 安装时取得的依赖

`setup.cfg` 声明 Python 依赖及可选依赖，包括 OpenAI Python SDK、cryptography、packaging、markdown-it-py、Pillow、pyserial、llvmlite、PyTorch、Playwright。它们不因此适用 Chaos 的源码许可；安装器取得的各自发行包及依赖仍遵守其中的许可证与通知。项目包和便携源码包不包含用户环境中已安装的依赖、模型权重、浏览器安装包或操作系统运行时。重新分发一个已安装的环境时，还必须保留这些组件自己的通知并核对所选发行版本。

## 设计研究与外部协议

以下是文档记录的设计参考或外部协议，并不是整个上游项目被复制或嵌入 Chaos：OpenAI Codex、CopilotKit/OpenDots、defog-ai/opendot、DeepSeek-Bot、Agent-S、isl-org/OpenBot、nanoMuse、Mobile MCP、Harness²。具体固定提交与实现边界见 [Bot 语言设计](docs/BOT_LANGUAGE_DESIGN.md)、[团队](docs/TEAMS.md)、[移动设备](docs/MOBILE_COMPUTERS.md)、[原生机器人](docs/NATIVE_ROBOTS.md)、[Harness 演进](docs/HARNESS_EVOLUTION.md)。

尤其是 nanoMuse 的已研究版本采用 GPL-3.0-or-later；本项目的移动设备功能按协议独立实现，没有随此发布复制其 GPL 源码、头像或应用壳。不得将未来复制 GPL 代码的行为视为已由本文件或 Chaos 根许可授权。外部服务、设备程序或用户另行安装的软件也不因兼容接口而变成 Chaos 原创作品。

## 再分发时

保留根 LICENSE、本文件、上述第三方许可与原始版权通知。修改包含上游派生部分的文件时保留其来源说明。不能仅以根目录的禁止训练声明替代上游许可，也不能宣称禁止训练要求约束上游组件的独立使用。
