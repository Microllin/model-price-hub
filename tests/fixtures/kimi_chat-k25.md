> ## Documentation Index
> Fetch the complete documentation index at: https://platform.kimi.com/docs/llms.txt
> Use this file to discover all available pages before exploring further.

# 多模态模型 Kimi K2.5 定价

> 查看 Kimi K2.5 多模态模型的输入、输出和缓存命中 token 价格及计费说明。

export const DocTable = ({columns = [], rows = []}) => {
  return <div className="doc-table-wrap">
      <table className="doc-table">
        {columns.length > 0 ? <colgroup>
            {columns.map((column, index) => <col key={index} style={column.width ? {
    width: column.width
  } : undefined} />)}
          </colgroup> : null}
        <thead>
          <tr>
            {columns.map((column, index) => <th key={index}>{column.title}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => <tr key={rowIndex}>
              {row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}
            </tr>)}
        </tbody>
      </table>
    </div>;
};

## 产品定价

<DocTable
  columns={[
{ title: "模型", width: "24%" },
{ title: "计费单位", width: "12%" },
{ title: "输入价格（缓存命中）", width: "16%" },
{ title: "输入价格（缓存未命中）", width: "16%" },
{ title: "输出价格", width: "14%" },
{ title: "上下文窗口", width: "18%" },
]}
  rows={[
["kimi-k2.5", "1M tokens", "¥0.70", "¥4.00", "¥21.00", "262,144 tokens"],
]}
/>

此处 1M = 1,000,000，表格中的价格代表每消耗 1M tokens 的价格。

## 模型说明

<Warning>
  联网搜索（`web_search`）正在更新升级中，近期不建议使用该功能，当前文档已经过时，请关注后续内容更新。
</Warning>

* Kimi K2.5 支持文本、图片与视频输入，思考与非思考模式，对话与 Agent 任务
* 模型上下文长度 256k，支持长思考擅长深度推理
* 支持自动上下文缓存功能，[ToolCalls](/docs/guide/use-kimi-api-to-complete-tool-calls)、[JSON Mode](/docs/guide/use-json-mode-feature-of-kimi-api)、[Partial Mode](/docs/guide/use-partial-mode-feature-of-kimi-api)、[联网搜索功能](/docs/guide/use-web-search)等能力
