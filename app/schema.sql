-- 裁切排样应用的持久化结构（幂等：可重复执行）。
-- 单行工单（job）+ 成品种类 + 库存规格 + 最近一次方案。
-- mode='single'：旧单规格模式（paper_l/paper_w 非空）；
-- mode='stock' ：多规格库存模式（尺寸/库存/成本在 stock 表）。

CREATE TABLE IF NOT EXISTS job (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    mode            TEXT NOT NULL DEFAULT 'single',
    paper_l         INTEGER,
    paper_w         INTEGER,
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

CREATE TABLE IF NOT EXISTS stock (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    position    INTEGER NOT NULL,           -- 录入顺序（平局优先序）
    name        TEXT NOT NULL,
    l           INTEGER NOT NULL,
    w           INTEGER NOT NULL,
    qty         INTEGER NOT NULL,           -- 可用张数
    cost        INTEGER NOT NULL            -- 单张成本（非负整数）
);

CREATE TABLE IF NOT EXISTS solution (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    status          TEXT NOT NULL,          -- optimal/found/infeasible/budget
    input_hash      TEXT NOT NULL,          -- 方案所对应的输入版本
    result_json     TEXT NOT NULL,          -- solver 完整结果
    states_searched INTEGER NOT NULL,
    sheets_used     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
