# 矩形纸张贯通式裁切排样（Paper Cutting Planner）

供小型印刷作坊使用的 Web 应用：输入刀缝、张数上限、搜索预算、各类成品
需求以及（可选的）多规格母纸库存，自动搜索一份 **guillotine（贯通式）**
排样方案——每一刀都贯穿当前正在处理的矩形余料，刀缝宽度被严格扣除，
成品不重叠、不越界，并遵守每类成品「能否旋转 90°」的限制。

支持两种母纸模式：

- **单规格模式（旧模式，语义完全不变）**：输入一种母纸长宽，库存不限，
  找到任意可行方案即返回（不追求最优）。
- **多规格库存模式**：库存中维护若干种母纸规格，每种记录长宽、可用张数
  与非负整数单张成本。在库存可用张数与总张数上限内，搜索 **总纸价最低**
  的贯通式可行排样；总纸价相同时先比较用纸张数（少者优先），再优先使用
  **录入顺序靠前**的规格（用量字典序）。

输出：

- 每张母纸的逐张排样图（SVG，标注所用规格/单价、成品名称、旋转标记、刀缝线）；
- 按实际执行顺序排列的贯通式裁切步骤（竖刀/横刀、坐标、刀长）；
- 各类成品的实际数量与需求对比；库存模式另附总成本与各规格用纸/小计。

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

镜像首次启动会自动创建/迁移 SQLite 表结构（幂等，旧版单规格库会就地
迁移为新模式，既有输入、方案与裁切单调用保持原语义，可安全重复执行）。

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

公共字段：

- 刀缝宽度：非负整数毫米，0–100；
- 张数上限、搜索状态数上限：正整数（分别 ≤ 10000、100000000）；
- 成品至少 1 种（最多 50 种）：长、宽为正整数，份数为正整数
  （≤1000000），「可旋转」必须是布尔值；
- 布尔值不接受数字伪装（`true` 不等于 1），尺寸不接受小数字符串。

**单规格模式**（请求体不含 `stocks` 字段，旧行为）：

- 母纸长、宽：正整数毫米，1–10000。

**多规格库存模式**（请求体含非空数组 `stocks`）：

- `stocks` 至少 1 种、至多 20 种规格；每种含：
  - `l` / `w`：长宽正整数毫米（1–10000）；
  - `qty`：可用张数，正整数（≤1000000）；
  - `cost`：单张成本，**非负整数**（0–1000000000）；
  - `name`：可选名称（不影响排样与版本指纹）。
- 该模式不得再给顶层 `paper_l` / `paper_w`（给了返回 400，避免“改了
  不生效”的误解）；张数上限对各规格合计用纸生效。
- 规格的**录入顺序也是输入**：它决定同价、同张数时的优先次序；调整
  顺序会产生新的输入版本。

## 搜索结果的状态（严格区分）

### 单规格模式（旧语义）

1. **找到可行方案 `found`**：满足全部份数，可查看排样图与裁切步骤并导出
   （不追求最优）。
2. **已穷尽且无解 `infeasible`**：完整搜索整棵状态树，确认不存在可行方案。
3. **搜索预算耗尽 `budget`**：状态数达到上限时仍未确定，**不表示无解**；
   不可导出。

### 多规格库存模式

1. **成本最优 `optimal`**：在预算内完整穷尽搜索树，所给可行排样的总纸价
   已证实最低（同价比张数、再比录入靠前规格的平局规则也已决胜）。
2. **可行但最优性未证实 `found`**：预算耗尽时已找到可行排样，**必须标明
   “最优性未证实”**；方案仍可查看与导出，裁切单上同样标注未证实。
3. **已穷尽且无解 `infeasible`**：完整穷尽后确认库存内不存在可行排样。
4. **预算耗尽且无方案 `budget`**：只能表示**未判定**，不表示无解，
   不可导出；调大搜索预算后重试。

只有完整穷尽（`optimal` / `infeasible`）才允许报告“最优”或“无解”。

搜索基于「角点放置 + 首刀归一化（竖切/横切）」的带记忆 DFS：库存模式
在此之上用分支限界枚举各规格的纸张组合（按下标非降序归一，避免同规格
纸张的重复排列），并以成本/张数乐观下界剪枝；显式枚举余料作废分支，
因此预算内完整遍历后给出的 `infeasible` / `optimal` 是可信的。

## 输入版本与方案失效

- 每次保存输入都会计算一份输入指纹（SHA-256）。库存模式的指纹覆盖刀缝、
  张数上限、搜索上限、全部成品以及**每种库存规格的长宽/可用张数/成本与
  录入顺序**；界面与裁切单上显示为 12 位「输入版本」。
- **保存方案后修改库存、成本或成品等任何影响排样的输入，旧方案立即标记
  为失效**；此时导出裁切单会被拒绝（HTTP 409，`stale: true`），必须重新
  排样——旧方案不会按新输入重新导出或复用。
- 仅修改成品或规格的*名称*不改变版本（不影响排样）。
- 未提交库存清单的输入一律按旧单规格语义处理：旧状态（`found`/
  `infeasible`/`budget`）含义、导出规则与裁切单格式保持不变。

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
| GET | `/api/export` | 下载裁切单（失效/无可行方案返回 409） |

### 调用示例：单规格模式（旧）

```bash
curl -sS -X PUT http://localhost:8000/api/inputs \
  -H 'Content-Type: application/json' \
  -d '{"paper_l":300,"paper_w":220,"kerf":3,"max_sheets":6,
       "max_states":100000,
       "products":[{"name":"封面","l":120,"w":90,"qty":6,
                    "rotatable":true}]}'

curl -sS -X POST http://localhost:8000/api/solve     # 搜索
curl -sS http://localhost:8000/api/state             # 查看状态/方案 JSON
curl -sS -OJ http://localhost:8000/api/export        # 导出裁切单 .txt
```

### 调用示例：多规格库存模式

```bash
curl -sS -X PUT http://localhost:8000/api/inputs \
  -H 'Content-Type: application/json' \
  -d '{"kerf":3,"max_sheets":6,"max_states":2000000,
       "stocks":[{"name":"大度纸","l":300,"w":220,"qty":4,"cost":120},
                 {"name":"小度纸","l":200,"w":180,"qty":8,"cost":55}],
       "products":[{"name":"封面","l":120,"w":90,"qty":6,
                    "rotatable":true},
                   {"name":"卡片","l":60,"w":60,"qty":10,
                    "rotatable":false}]}'

curl -sS -X POST http://localhost:8000/api/solve
```

返回中的 `solution.status` 为 `optimal` / `found` / `infeasible` /
`budget`；`solution.result` 在库存模式下额外包含：

```json
{
  "status": "optimal",
  "optimal": true,
  "total_cost": 230,
  "sheets_used": 3,
  "sheet_specs": [0, 1, 1],
  "pieces": [ {"sheet": 0, "product": 0, "x": 0, "y": 0,
               "w": 120, "h": 90, "rotated": false} ],
  "cuts":   [ {"seq": 1, "sheet": 0, "order": 0,
               "orientation": "V", "x": 120, "y": 0, "length": 220} ],
  "counts": [6, 10]
}
```

- `sheet_specs[i]`：第 i+1 张用纸对应 `stocks` 的下标（排样图与裁切步骤
  中的每张纸都按此标注规格、尺寸与单价）；
- `optimal:false` 且 `status:"found"` 即“可行但最优性未证实”；
- `status:"budget"` 表示未判定（可能有更优解，也可能无解）。

浏览器界面操作：顶部「母纸来源」选择 **单规格母纸** 或 **多规格库存**，
在库存表中逐行录入规格（长宽/可用张数/单价），「保存并排样」后查看逐张
排样图、裁切步骤与成本汇总；状态徽标区分“已证实成本最优 / 最优性未证实 /
无解 / 未判定”。

## 目录结构

```
app/
  __init__.py      Flask 路由与裁切单渲染（两种模式）
  solver.py        guillotine 排样 DFS + 库存成本最优分支限界
  validation.py    严格输入校验（单规格/库存）与输入指纹
  db.py            SQLite 读写、失效判定与旧库迁移
  schema.sql       建表脚本（幂等）
  templates/index.html  浏览器界面（模式切换/库存表/排样图/步骤/成本）
wsgi.py            waitress 入口
tests/test_all.py  求解器与 Web 业务规则测试
Dockerfile, docker-compose.yml, requirements.txt
```
