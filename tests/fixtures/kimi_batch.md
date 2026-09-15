> ## Documentation Index
> Fetch the complete documentation index at: https://platform.kimi.com/docs/llms.txt
> Use this file to discover all available pages before exploring further.

# 批量推理定价

> 查看 Kimi BatchJob 批量推理的输入、输出和缓存命中 token 价格及计费说明。

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

Batch API 即批量推理 API，批量推理 API 费用为标准模型价格的 **60%**，适合大规模、低实时性要求的任务场景。

<DocTable
  columns={[
{ title: "模型", width: "20%" },
{ title: "计费单位", width: "14%" },
{ title: "输入价格（缓存命中）", width: "20%" },
{ title: "输入价格（缓存未命中）", width: "20%" },
{ title: "输出价格", width: "13%" },
{ title: "上下文窗口", width: "13%" },
]}
  rows={[
["kimi-k2.7-code（Batch）", "1M tokens", "¥0.78", "¥3.90", "¥16.20", "262,144 tokens"],
["kimi-k2.6（Batch）", "1M tokens", "¥0.66", "¥3.90", "¥16.20", "262,144 tokens"],
["kimi-k2.5（Batch）", "1M tokens", "¥0.42", "¥2.40", "¥12.60", "262,144 tokens"],
]}
/>

此处 1M = 1,000,000，表格中的价格代表每消耗 1M tokens 的价格。

## 说明

* Batch API 支持 `kimi-k2.7-code`、`kimi-k2.6` 和 `kimi-k2.5` 模型
* Batch API 不受实时并发限制，适合大批量任务
* 任务需在指定的 `completion_window` 内完成，超时将变为 `expired` 状态
* 详细使用方法请参阅 [Batch API 指南](/docs/guide/use-batch-api)
