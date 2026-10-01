# 矩形纸张贯通式裁切排样（Paper Cutting Planner）

供小型印刷作坊使用的 Web 应用：输入刀缝、张数上限、搜索预算和各类成品
需求，自动搜索一份 **guillotine（贯通式）** 排样方案——每一刀都贯穿当前
正在处理的矩形余料，刀缝宽度被严格扣除，成品不重叠、不越界，并遵守每类
成品「能否旋转 90°」的限制。

支持两种母纸模式：

- **单一母纸（历史模式）**：给定一种母纸尺寸，找到任意可行方案即返回；
- **多规格库存模式**：维护多种母纸规格（长宽、可用张数、非负整数单张
  成本），在库存张数与总张数上限内搜索 **总纸价最低** 的贯通式可行排样；
  总纸价相同时，先比较 **用纸张数**（少者优先），仍相同时优先使用
  **录入顺序靠前** 的规格。

输出：

- 每张母纸的逐张排样图（SVG，含所属规格、成品名称、旋转标记、刀缝线）；
- 按实际执行顺序排列的贯通式裁切步骤（竖刀/横刀、坐标、刀长，标注所属规格）；
- 各类成品的实际数量与需求对比；
- 库存模式下各规格用纸数量、小计与 **总纸价** 汇总。

## 一键启动（Docker Compose）

```bash
docker compose up --build -d
```

启动后浏览器访问：

```
http://localhost:8000
```

停止与查看日志：

```bash
docker compose down          # 停止（数据保留在命名卷 cutting-data 中）
docker compose logs -f       # 查看日志
docker compose down -v       # 停止并同时删除数据卷
```

镜像首次启动会自动创建 SQLite 表结构（幂等，可安全重复执行；旧版数据库
启动时自动补列，既有单规格工单与方案继续按旧语义工作）。

## 本地直接运行（不用 Docker）

需要 Python 3.11+：

```bash
python -m pip install -r requirements.txt
python wsgi.py
# 开发模式（Flask 内置服务器，自动重载）：
FLASK_DEV=1 python wsgi.py
```

默认监听 `0.0.0.0:8000`，访问 http://localhost:8000 。

运行测试：

```bash
python tests/test_all.py
# 或 pytest（若已安装）
```

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` | `8000` | HTTP 监听端口 |
| `CUTTING_DB` | `app/data/cutting.db` | SQLite 数据库文件路径 |

Compose 中把 `/app/data` 挂载为命名卷 `cutting-data`，容器重建后输入与
方案数据不丢失。改端口时同时修改 `docker-compose.yml` 的端口映射与
`PORT` 环境变量。

## 输入规则（非法输入返回 400，绝不写库）

通用参数：

- 刀缝宽度：非负整数毫米，0–100；
- 总张数上限、搜索状态数上限：正整数（分别 ≤ 10000、100000000）；
- 成品至少 1 种（最多 50 种）：长、宽为正整数，份数为正整数
  （≤1000000），「可旋转」必须是布尔值。

单一母纸模式（请求体不含 `stocks`，历史调用原样支持）：

- 母纸长、宽：正整数毫米，1–10000。

多规格库存模式（请求体含 `stocks`）：

- 至少 1 种、至多 50 种母纸规格；
- 每种规格：长、宽正整数毫米（1–10000），可用张数正整数（1–10000），
  单张成本非负整数（0–1000000000）；
- `stocks` 数组顺序即 **规格录入顺序**，参与同价同张数时的次序比较；
- 该模式下顶层 `paper_l` / `paper_w` 不再使用。

- 布尔值不接受数字伪装（`true` 不等于 1），尺寸/成本不接受小数字符串。

## 搜索结果状态（严格区分）

**单一母纸模式**：

1. **找到可行方案 `found`**：满足全部份数，可查看排样图与裁切步骤并导出
   （找到即返回，不追求最优）。
2. **已穷尽且无解 `infeasible`**：在给定母纸、刀缝、张数上限与旋转限制
   下完整搜索了整棵状态树，确认不存在可行方案。
3. **搜索预算耗尽 `budget`**：状态数达到「搜索状态数上限」时仍未确定。
   **此状态不表示无解**，不可导出。

**多规格库存模式**：

1. **最优 `optimal`**：已完整穷尽搜索，证实该方案 **总纸价最低**
   （同价张数最少；再同则规格录入顺序最靠前），可查看与导出。
2. **已穷尽且无解 `infeasible`**：在库存张数、刀缝、总张数上限与旋转
   限制下完整搜索后确认不存在可行方案，不可导出。
3. **搜索预算耗尽 `budget`**：
   - 已找到可行排样时，方案照常展示并 **可导出**，但界面与裁切单都会
     明确标注 **「最优性未证实」**，不得声称总纸价最低；可调大搜索
     预算后重新排样；
   - 尚未找到任何排样时，结果 **只能表示「未判定」**，不表示无解，
     也不可导出。

两类搜索都基于「角点放置 + 首刀归一化（竖切/横切）」的带记忆 DFS，
显式枚举成品分离与余料作废分支；库存模式在同一枚举上做分支限界，因此
预算内完整遍历后给出的 `infeasible` / `optimal` 是可信的。

## 输入版本与方案失效

- 每次保存输入都会计算一份输入指纹（SHA-256）：
  - 单规格模式覆盖母纸、刀缝、张数上限、搜索上限和全部成品；
  - 库存模式覆盖 **全部库存规格的长宽/可用张数/单张成本及其录入顺序**、
    刀缝、张数上限、搜索上限和全部成品；
  - 界面与裁切单上显示为 12 位「输入版本」。
- **保存方案后修改任何影响排样的输入（包括库存清单或成本），旧方案立即
  标记为失效**；此时导出裁切单会被拒绝（HTTP 409，`stale: true`），
  必须重新排样——旧方案绝不会按新输入导出。
- 仅修改成品/规格名称不改变版本（不影响排样）。

## 刀缝与坐标约定

- 坐标系原点在母纸左上角，X 向右、Y 向下，单位毫米。
- 裁切步骤中的坐标是**刀缝起点**，刀缝占据 `[坐标, 坐标+刀缝宽)`；
  竖刀沿 Y 方向贯穿当前余料，横刀沿 X 方向贯穿。
- 最后一刀切到外缘时，刀缝可越过纸边（边缘修切），越界部分为废边。

## HTTP 接口（可供脚本调用）

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/` | 浏览器单页界面 |
| GET | `/api/state` | 当前输入、输入版本、方案及是否有效 |
| PUT | `/api/inputs` | 校验并保存输入（非法返回 400，不写库） |
| POST | `/api/solve` | 基于已保存输入运行搜索 |
| GET | `/api/export` | 下载裁切单（失效/不可导出方案返回 409） |

`/api/state` 返回的 `cfg.mode` 为 `legacy` 或 `stock`；库存模式下方案结果
额外包含 `total_cost`、`optimal`（布尔）、`sheets[]`（每张的规格下标/尺寸/
单价），且每个 `pieces[]`/`cuts[]` 元素带 `spec`。

### 调用示例：多规格库存模式（curl）

```bash
# 1) 保存输入（stocks 顺序即录入顺序）
curl -X PUT http://localhost:8000/api/inputs \
  -H 'Content-Type: application/json' \
  -d '{
    "kerf": 3,
    "max_sheets": 20,
    "max_states": 1000000,
    "stocks": [
      {"name": "对开铜版", "l": 597, "w": 444, "qty": 50, "cost": 180},
      {"name": "全开铜版", "l": 889, "w": 597, "qty": 20, "cost": 320}
    ],
    "products": [
      {"name": "封面", "l": 210, "w": 285, "qty": 120, "rotatable": true},
      {"name": "腰封", "l": 210, "w":  60, "qty": 120, "rotatable": false}
    ]
  }'

# 2) 排样（status: optimal / infeasible / budget）
curl -X POST http://localhost:8000/api/solve

# 3) 查看状态（含 total_cost / optimal / sheets / pieces / cuts）
curl http://localhost:8000/api/state

# 4) 导出文本裁切单（budget 但已有可行方案时同样可导出，
#    抬头会注明「最优性: 未证实」）
curl -OJ http://localhost:8000/api/export
```

### 调用示例：Python

```python
import requests

base = "http://localhost:8000"
requests.put(f"{base}/api/inputs", json={
    "kerf": 0, "max_sheets": 10, "max_states": 2_000_000,
    "stocks": [{"l": 100, "w": 100, "qty": 5, "cost": 30},
               {"l": 220, "w": 220, "qty": 5, "cost": 100}],
    "products": [{"l": 50, "w": 50, "qty": 9, "rotatable": True}],
})
sol = requests.post(f"{base}/api/solve").json()["state"]["solution"]
print(sol["status"], sol["result"]["total_cost"],      # optimal 90
      sol["result"]["sheets_used"], sol["result"]["optimal"])

# 历史调用（不带 stocks）完全保持旧语义：
requests.put(f"{base}/api/inputs", json={
    "paper_l": 100, "paper_w": 100, "kerf": 0,
    "max_sheets": 4, "max_states": 100000,
    "products": [{"l": 50, "w": 50, "qty": 4, "rotatable": True}],
})
sol = requests.post(f"{base}/api/solve").json()["state"]["solution"]
print(sol["status"])                                  # found
```

## 目录结构

```
app/
  __init__.py      Flask 路由与裁切单渲染（两种模式）
  solver.py        guillotine 排样 DFS：单规格 found/infeasible/budget，
                   多规格库存分支限界 optimal/infeasible/budget
  validation.py    严格输入校验与输入指纹（含库存规格）
  db.py            SQLite 读写、旧库迁移与失效判定
  schema.sql       建表脚本（幂等）
  templates/index.html  浏览器界面（排样图/步骤/数量/成本）
wsgi.py            waitress 入口
tests/test_all.py  求解器与 Web 业务规则测试
Dockerfile, docker-compose.yml, requirements.txt
```
