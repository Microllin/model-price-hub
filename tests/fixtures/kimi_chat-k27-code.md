> ## Documentation Index
> Fetch the complete documentation index at: https://platform.kimi.com/docs/llms.txt
> Use this file to discover all available pages before exploring further.

# 编程模型 Kimi K2.7 Code 定价

> 查看 Kimi K2.7 Code 及高速版的输入、输出和缓存命中 token 价格及计费说明。

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
["kimi-k2.7-code", "1M tokens", "¥1.30", "¥6.50", "¥27.00", "262,144 tokens"],
["kimi-k2.7-code-highspeed", "1M tokens", "¥2.60", "¥13.00", "¥54.00", "262,144 tokens"],
]}
/>

此处 1M = 1,000,000，表格中的价格代表每消耗 1M tokens 的价格。

## 模型说明

* Kimi K2.7 Code 是面向代码场景的 Coding 模型，在长上下文中更可靠地遵循指令，能以更高的成功率完成编程任务。同时支持文本、图片与视频输入，仅支持思考模式，对话与 Agent 任务
* Kimi K2.7 Code HighSpeed 是 Kimi K2.7 Code 的高速版模型，与 Kimi K2.7 Code 是相同模型，但输出速度约 180 Tokens/s，短上下文场景可达 260 Token/s，带来更极致的编程体验。
* 模型上下文长度 256k，支持长思考擅长深度推理
* 支持自动上下文缓存功能，[ToolCalls](/docs/guide/use-kimi-api-to-complete-tool-calls)、[JSON Mode](/docs/guide/use-json-mode-feature-of-kimi-api)、[Partial Mode](/docs/guide/use-partial-mode-feature-of-kimi-api)等能力
