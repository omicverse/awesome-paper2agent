# awesome-paper2agent

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE) [![Metadata schema v1](https://img.shields.io/badge/metadata-schema%20v1-blue.svg)](schema/package-v1.schema.json) [![Contributions: PR](https://img.shields.io/badge/contributions-PR-orange.svg)](CONTRIBUTING.md)

🇬🇧 **English version**: [README.md](README.md)

论文的价值不止于被阅读，但其方法多分散于代码、笔记与补充材料之中。要加以利用，需先理解实现、对齐依赖，并完整走通一遍流程。这构成了使用门槛，也意味着同一份封装工作会由不同的人重复完成。

我们建立了 Paper2Agent 社区，集中收录已封装好的论文 MCP 包；安装到 [OmicOS](https://omicos.cn/) 之后，论文中的方法与内置工具一样，可以直接在对话里调用。

---

## 快速开始

将下面这段提示词粘贴到任意 agent 对话中，附上论文，即可生成一个通过校验、可直接提交 PR 的包：

```text
按本仓库的 AGENTS.md，用附带的论文 PDF 构建一个包，上游代码仓库为 <公开仓库地址>，重点将 <方法或图表> 封装为工具。
```

Agent 构建规范统一维护在 [AGENTS.md](AGENTS.md)，单一事实来源。

## 一个包如何到达用户

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/flow-zh-dark.svg">
  <img src="docs/assets/flow-zh-light.svg" width="616"
       alt="公开源码 → Pull Request → 静态校验 → 维护者审核 → ZIP + 索引 → MCP 客户端">
</picture>

每个发布包都记录来源论文、上游仓库、精确的 commit、许可证以及归档文件的校验和。
`reviews.json` 将维护者审核与包内容绑定——代码或元数据一旦变动即需重新审核。
未经审核的包不会进入发布索引；若 `packages/` 下存在未审核的包，默认分支将拒绝发布。

## 仓库内容

| 路径 | 用途 |
|---|---|
| `packages/<package_id>/` | 已投稿的包；审核通过后发布 |
| `examples/sequence-stats/` | 贯通整个目录管线的合成示例 |
| `schema/package-v1.schema.json` | 投稿包的版本化元数据规范 |
| `tools/catalog.py` | 校验并确定性地打包源码 |
| `tools/from_paper2mcp.py` | 把 OmicOS 构建管线的交付物转成本仓库的包 |
| `docs/contract.md` | 消费者契约、归档布局与限制 |
| `AGENTS.md` | 供 agent 使用的构建规范 |

<details>
<summary>包目录结构</summary>

```text
packages/<package_id>/
  metadata.json          严格符合 schema，不含 schema 之外的字段
  USAGE.md               用途、输入、输出、限制与安装步骤
  VALIDATION.md          每个工具验证了什么、还有什么没验证
  LICENSE                该投稿自身的许可证
  NOTICE                 可选，用于上游声明
  src/requirements.txt   完全固定的依赖（包名==版本）
  src/<名称>_mcp.py      唯一的 MCP 服务入口
```

</details>

## 本地检查

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python tools/catalog.py validate
python tools/catalog.py demo          # 在 dist/demo/ 生成示例目录
python tools/catalog.py build --revision $(git rev-parse HEAD)
```

`validate --require-approved` 会在 `packages/` 中存在未获审批的包时直接报错。必需的
`release-gate` 检查会在 PR 和分支推送时运行此命令。
正式构建要求工作区干净（无未提交的改动），且传入的 revision 与 HEAD 一致。

## 示例运行时检查

`requirements-dev.txt` 只覆盖校验依赖；运行示例服务需要单独的环境：

```bash
python3.11 -m venv .runtime-venv
.runtime-venv/bin/pip install -r examples/sequence-stats/src/requirements.txt
PYTHONDONTWRITEBYTECODE=1 .runtime-venv/bin/python tools/smoke_example.py
```

## 许可

仓库自身内容——schema、校验器、工作流与文档——采用 Apache-2.0，见 [LICENSE](LICENSE)。
**投稿包不会因此被重新许可**：每个包以自身的 `packages/<package_id>/LICENSE` 为准，
且声明的许可证必须允许对包内全部内容的重新分发。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 参与贡献

投稿以 Pull Request 形式提交，经审核后合并。合并代码与通过内容审核是两个独立步骤：
只有 `reviews.json` 中存在对应审核记录，包才会被发布。
