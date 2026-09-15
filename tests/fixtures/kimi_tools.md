> ## Documentation Index
> Fetch the complete documentation index at: https://platform.kimi.com/docs/llms.txt
> Use this file to discover all available pages before exploring further.

# 联网搜索定价

> 查看 Kimi 联网搜索工具的调用价格、计费单位和使用说明。

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
{ title: "工具名称", width: "18%" },
{ title: "计费单位", width: "16%" },
{ title: "价格", width: "14%" },
{ title: "说明", width: "52%" },
]}
  rows={[
["联网搜索", "1 次", "￥0.03", <>触发 <code>$web_search</code> 工具调用，计费一次</>],
]}
/>

## 联网搜索计费逻辑

当你在 `tools` 中加入 `$web_search` 工具，并获得了一个 `finish_reason = tool_calls` 且 `tool_call.function.name = $web_search` 的响应时，我们收取联网搜索 `$web_search` 调用费用 0.03 元；当响应 `finish_reason = stop` 时，不会收取调用费用。

此外，在使用 `$web_search` 时，我们依然会按照不同的模型大小收取 `/chat/completions` 接口产生的 Tokens 费用，**额外值得注意的是，当触发了联网搜索 `$web_search` 工具调用，搜索结果也会被计入 Tokens 中，搜索结果占用的 Tokens 数量可以在返回的 `tool_call.function.arguments` 中获取**，例如：当你触发了联网搜索 `$web_search` 工具调用时，如果联网搜索的内容占用了 4k Tokens，这 4k Tokens 会在调用方**下次**调用 `/chat/completions` 接口时计入总 Tokens 中，此时总计费 Tokens 为：

```text theme={null}
total_tokens = prompt_tokens + search_tokens + completions_tokens
```

*注：如果你在触发了联网搜索 `$web_search` 时，不继续完成 `tool_calls`，而是就此停止，那么我们只会收取 ¥0.03 元的工具调用费用，联网搜索内容占用的 Tokens 将不会计费。*
