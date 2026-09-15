> ## Documentation Index
> Fetch the complete documentation index at: https://platform.kimi.com/docs/llms.txt
> Use this file to discover all available pages before exploring further.

# 生成模型 Moonshot V1 定价

> 查看 Moonshot V1 生成与视觉模型的输入、输出和缓存命中 token 价格及计费说明。

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
{ title: "模型", width: "34%" },
{ title: "计费单位", width: "14%" },
{ title: "输入价格", width: "14%" },
{ title: "输出价格", width: "14%" },
{ title: "上下文窗口", width: "24%" },
]}
  rows={[
["moonshot-v1-8k", "1M tokens", "¥2.00", "¥10.00", "8,192 tokens"],
["moonshot-v1-32k", "1M tokens", "¥5.00", "¥20.00", "32,768 tokens"],
["moonshot-v1-128k", "1M tokens", "¥10.00", "¥30.00", "131,072 tokens"],
["moonshot-v1-8k-vision-preview", "1M tokens", "¥2.00", "¥10.00", "8,192 tokens"],
["moonshot-v1-32k-vision-preview", "1M tokens", "¥5.00", "¥20.00", "32,768 tokens"],
["moonshot-v1-128k-vision-preview", "1M tokens", "¥10.00", "¥30.00", "131,072 tokens"],
]}
/>

此处 1M = 1,000,000，表格中的价格代表每消耗 1M tokens 的价格。
