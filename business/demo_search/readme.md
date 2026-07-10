# demo_search 示例业务

该业务展示最小业务脚本如何复用 `core.playwright_base`。业务文件只保留 Bing 搜索和结果提取步骤，浏览器上下文、登录态、等待、重试、截图、异常捕获及 JSON 返回全部由公共层负责。

## 网关调用

```powershell
$body = @{
    kind = "web"
    business = "demo_search"
    params = @{ query = "Playwright Python"; limit = 5; profile = "demo" }
    max_retries = 1
    enable_self_healing = $true
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/tasks" -ContentType "application/json" -Body $body
```

返回中的 `data.task_id` 可用于查询状态或手动重试。`profile` 对应 `browser_profiles/<profile>/`，用于长期保存登录态。

## 参数

| 参数 | 必填 | 默认值 | 说明 |
|---|---:|---:|---|
| `query` | 是 | - | 搜索关键词 |
| `limit` | 否 | `5` | 返回标题数量，限制为 1~20 |
| `profile` | 否 | `demo` | 持久浏览器用户目录名称 |

