-- 裁切排样应用的持久化结构（幂等：可重复执行）。
-- 单行工单（job）+ 成品种类 + 母纸库存（多规格模式）+ 最近一次方案。

CREATE TABLE IF NOT EXISTS job (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    mode            TEXT NOT NULL DEFAULT 'legacy',  -- legacy / stock
    paper_l         INTEGER NOT NULL,
    paper_w         INTEGER NOT NULL,
    kerf            INTEGER NOT NULL,
    max_sheets      INTEGER NOT NULL,
    max_states      INTEGER NOT NULL,
    input_hash      TEXT NOT NULL,          -- 当前输入版本指纹
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS product (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    position    INTEGER NOT NULL,           -- 在输入中的顺序，即成品类型 id
    name        TEXT NOT NULL,
    l           INTEGER NOT NULL,
    w           INTEGER NOT NULL,
    qty         INTEGER NOT NULL,
    rotatable   INTEGER NOT NULL CHECK (rotatable IN (0, 1))
);

-- 多规格母纸库存；legacy 模式下表为空。position 即规格录入顺序。
CREATE TABLE IF NOT EXISTS stock (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    position    INTEGER NOT NULL,
    name        TEXT NOT NULL,
    l           INTEGER NOT NULL,
    w           INTEGER NOT NULL,
    qty         INTEGER NOT NULL,           -- 可用张数
    cost        INTEGER NOT NULL            -- 单张成本（非负整数）
);

CREATE TABLE IF NOT EXISTS solution (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    status          TEXT NOT NULL,          -- legacy: found/infeasible/budget
                                            -- stock : optimal/infeasible/budget
    input_hash      TEXT NOT NULL,          -- 方案所对应的输入版本
    result_json     TEXT NOT NULL,
    states_searched INTEGER NOT NULL,
    sheets_used     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
