"""示例 MCP server（纯标准库，~40 行）。

协议：stdin 读一行 JSON -> 看 method -> 调函数 -> stdout 写一行 JSON。
注意：stdout 是协议通道，调试日志必须写 stderr。
"""
import json
import sys

TOOLS = {
    "get_weather": {
        "schema": {
            "name": "get_weather",
            "description": "查询某个城市的实时天气。当用户问冷热、下雨、穿衣时使用。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名，如：杭州"}
                },
                "required": ["city"],
            },
        },
        "fn": lambda **a: f"{a['city']}：多云，22℃，湿度 61%",
    },
}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        method = req["method"]

        if method == "initialize":
            resp = {"jsonrpc": "2.0", "id": req["id"], "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "weather", "version": "1.0.0"},
            }}
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": req["id"],
                    "result": {"tools": [t["schema"] for t in TOOLS.values()]}}
        elif method == "tools/call":
            name = req["params"]["name"]
            args = req["params"]["arguments"]
            try:
                text = TOOLS[name]["fn"](**args)
                resp = {"jsonrpc": "2.0", "id": req["id"],
                        "result": {"content": [{"type": "text", "text": text}]}}
            except Exception as e:  # 工具报错包成 isError 返回，不能把进程搞崩
                resp = {"jsonrpc": "2.0", "id": req["id"],
                        "result": {"content": [{"type": "text", "text": str(e)}], "isError": True}}
        else:
            resp = {"jsonrpc": "2.0", "id": req["id"],
                    "error": {"code": -32601, "message": f"unknown method {method}"}}

        print(json.dumps(resp, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
